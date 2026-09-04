# utils/network.py - 网络工具函数：双栈连接、本机IP获取
import socket
import sys
import ipaddress
from client.lang import t


def get_local_ipv4():
    """获取本机IPv4地址（用于界面显示），失败返回空字符串。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        # 连接一个公网IPv4地址，获取本地出口IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def get_all_ipv6():
    """遍历全部网卡接口，收集所有IPv6地址（原始列表，用于调试显示）。
    多重回退：psutil -> getaddrinfo -> socket连接 -> 系统命令。
    """
    all_ips = []

    # 方法1：优先用psutil遍历网卡
    try:
        import psutil
        for iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET6:
                    ip = addr.address.split('%')[0]  # 去掉scope id
                    if ip and ip not in all_ips:
                        all_ips.append(ip)
        if all_ips:
            return all_ips
    except ImportError:
        pass
    except Exception:
        pass

    # 方法2：用getaddrinfo获取本机IPv6
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET6)
        for info in infos:
            ip = info[4][0].split('%')[0]
            if ip and ip not in all_ips:
                all_ips.append(ip)
        if all_ips:
            return all_ips
    except Exception:
        pass

    # 方法3：socket连接公网IPv6地址获取本地出口IP
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        s.settimeout(3)
        s.connect(("2001:4860:4860::8888", 80))
        ip = s.getsockname()[0].split('%')[0]
        s.close()
        if ip and ip not in all_ips:
            all_ips.append(ip)
        if all_ips:
            return all_ips
    except Exception:
        pass

    # 方法4：系统命令回退（Windows ipconfig / Linux ip -6 addr）
    try:
        import subprocess
        import re
        if sys.platform == "win32":
            result = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5)
            output = result.stdout
        else:
            result = subprocess.run(["ip", "-6", "addr"], capture_output=True, text=True, timeout=5)
            output = result.stdout
        # 匹配IPv6地址
        ipv6_pattern = r'([0-9a-fA-F:]+:+[0-9a-fA-F:]+)'
        matches = re.findall(ipv6_pattern, output)
        for ip in matches:
            ip = ip.split('%')[0]
            if ':' in ip and ip not in all_ips:
                try:
                    socket.inet_pton(socket.AF_INET6, ip)
                    all_ips.append(ip)
                except Exception:
                    pass
    except Exception:
        pass

    return all_ips


def get_local_ipv6():
    """获取本机公网/全局IPv6地址（可跨网段通信）。
    过滤：丢弃链路本地(fe80::/10)、唯一本地(fc00::/7)、回环(::1)、
    IPv4映射(::ffff:0:0/96)等非全局地址。
    没有则返回空字符串 ""。
    """
    all_ips = get_all_ipv6()
    for ip in all_ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (addr.is_private or addr.is_link_local or addr.is_loopback
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified
                or addr.ipv4_mapped is not None):
            continue
        return ip
    return ""


def get_local_ipv6_private():
    """获取本机内网IPv6地址（唯一本地地址 fc00::/7，局域网内可通信）。
    没有则返回空字符串 ""。
    """
    all_ips = get_all_ipv6()
    for ip in all_ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        # 唯一本地地址 fc00::/7：is_private 且不是链路本地/回环/组播等
        if (addr.is_private and not addr.is_link_local and not addr.is_loopback
                and not addr.is_multicast and not addr.is_reserved
                and not addr.is_unspecified and addr.ipv4_mapped is None):
            return ip
    return ""


def get_ipv6_type():
    """返回本机IPv6地址类型：'public'（公网）/ 'private'（内网）/ 'none'（无）。"""
    if get_local_ipv6():
        return "public"
    if get_local_ipv6_private():
        return "private"
    return "none"


def has_ipv6_connectivity():
    """检测本机是否有可用的IPv6网络（公网或内网唯一本地地址都算）。"""
    return bool(get_local_ipv6()) or bool(get_local_ipv6_private())


def dual_stack_connect(host, port, timeout=10, ip_mode="auto"):
    """双栈连接：支持自动/仅IPv4/仅IPv6三种模式，默认优先IPv4。

    参数:
        host: 目标地址（IPv4/IPv6/域名）
        port: 目标端口
        timeout: 连接超时秒数
        ip_mode: IP版本模式
            - "auto": 自动，优先IPv4，失败降级IPv6（默认）
            - "ipv4": 仅使用IPv4
            - "ipv6": 仅使用IPv6

    返回: 已连接的 socket 对象
    抛出: 所有地址族都连接失败时抛出最后一个异常
    """
    # 解析目标地址，获取所有地址族
    try:
        addrs = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ConnectionError(t("无法解析地址 {host}: {e}").format(host=host, e=e))

    ipv4_addrs = [a for a in addrs if a[0] == socket.AF_INET]
    ipv6_addrs = [a for a in addrs if a[0] == socket.AF_INET6]

    # 根据模式决定尝试顺序
    if ip_mode == "ipv4":
        ordered_addrs = ipv4_addrs
    elif ip_mode == "ipv6":
        ordered_addrs = ipv6_addrs
    else:  # auto：优先IPv4，失败降级IPv6
        ordered_addrs = ipv4_addrs + ipv6_addrs

    if not ordered_addrs:
        mode_name = {"auto": t("自动"), "ipv4": "IPv4", "ipv6": "IPv6"}.get(ip_mode, ip_mode)
        raise ConnectionError(t("地址 {host}:{port} 在{mode_name}模式下没有可用的IP记录").format(host=host, port=port, mode_name=mode_name))

    # 提前检测一次本机IPv6连通性（这个操作很重，不能在循环里每次都调）
    _has_v6 = has_ipv6_connectivity()

    last_error = None

    for family, socktype, proto, canonname, sockaddr in ordered_addrs:
        # auto模式下，本机没有IPv6时跳过IPv6尝试
        if ip_mode == "auto" and family == socket.AF_INET6 and not _has_v6:
            continue
        # ipv6模式下，本机没有IPv6直接报错（避免长时间超时）
        if ip_mode == "ipv6" and family == socket.AF_INET6 and not _has_v6:
            raise ConnectionError(t("本机没有IPv6网络，无法使用IPv6模式连接"))

        try:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            # 连接成功，关闭超时（后续收发由业务层控制）
            sock.settimeout(None)
            return sock
        except Exception as e:
            last_error = e
            try:
                sock.close()
            except Exception:
                pass
            continue

    # 所有地址都失败
    mode_name = {"auto": t("自动"), "ipv4": "IPv4", "ipv6": "IPv6"}.get(ip_mode, ip_mode)
    raise ConnectionError(t("连接 {host}:{port} 失败（{mode_name}模式）: {last_error}").format(host=host, port=port, mode_name=mode_name, last_error=last_error))

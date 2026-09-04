# client/client.py - 核心客户端：连接、登录、收发消息
import socket
import json
import threading
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.tools import pack_msg, send, recv
from client.lang import t
from utils.network import dual_stack_connect


class ChatClient:
    def __init__(self):
        self.sock = None
        self.username = ""
        self.token = None
        self.connected = False
        self.running = False
        self.on_message = None
        self.on_close = None
        self.on_kicked = None
        self.on_pong = None
        self.last_pong_time = None
        self.ip_mode = "auto"
        self.server_host = "127.0.0.1"
        self.server_port = 9999
        self._current_host = "127.0.0.1"
        self._current_port = 9999
        self._recv_thread = None
        self._main_send_lock = threading.Lock()  # 主连接发送锁，防止心跳与发消息并发交错

    def set_ip_mode(self, mode):
        """设置IP版本模式：auto / ipv4 / ipv6。"""
        if mode in ("auto", "ipv4", "ipv6"):
            self.ip_mode = mode

    def connect(self, host, port, ip_mode=None):
        """连接服务器（双栈：支持IPv4/IPv6，根据ip_mode选择）。"""
        # 关键：重连前先彻底关闭旧连接，否则旧接收线程可能收到
        # 服务器的顶号 kicked 消息，把刚重连成功的会话误踢回登录页
        self._do_close()
        if ip_mode:
            self.ip_mode = ip_mode
        self.server_host = host
        self.server_port = port
        self._current_host = host
        self._current_port = port
        self.sock = dual_stack_connect(host, port, ip_mode=self.ip_mode)
        self.connected = True
        self.running = True
        return True

    def login(self, username, password):
        """发送登录请求，等待响应（同步）。"""
        data = pack_msg("login", {"username": username, "password": password})
        self._send_main(data)
        result = json.loads(recv(self.sock).decode("utf-8"))
        payload = result.get("payload", {})
        if payload.get("ok"):
            self.username = username
            self.token = payload.get("token")
        return payload

    def register(self, username, password):
        """发送注册请求，等待响应（同步）。"""
        data = pack_msg("register", {"username": username, "password": password})
        self._send_main(data)
        result = json.loads(recv(self.sock).decode("utf-8"))
        return result.get("payload", {})

    def _send_main(self, data):
        """通过主连接线程安全发送（心跳线程与UI发消息可能并发）。"""
        with self._main_send_lock:
            if self.sock is None:
                raise ConnectionError(t("连接不存在"))
            send(self.sock, data)

    def send_ping(self):
        """发送心跳 ping（线程安全）。"""
        self._send_main(pack_msg("ping", {}))

    def send_message(self, text):
        """发送公共聊天消息。"""
        if not self.connected:
            return
        self._send_main(pack_msg("chat", {"text": text}))

    def send_private_message(self, target, text):
        """发送私聊消息（临时连接，带 token 认证）。"""
        sock = dual_stack_connect(self._current_host, self._current_port, ip_mode=self.ip_mode)
        try:
            sock.settimeout(10)
            payload = {"username": self.username, "token": self.token, "target": target, "text": text}
            send(sock, pack_msg("private_chat", payload))
            result = json.loads(recv(sock).decode("utf-8"))
            return result.get("payload", {})
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _temp_request(self, action, payload, timeout=10):
        """建立临时连接发送请求并返回响应（已登录时自动带 token 认证）。"""
        sock = dual_stack_connect(self._current_host, self._current_port, ip_mode=self.ip_mode)
        try:
            sock.settimeout(timeout)
            payload = dict(payload or {})
            # 只在已登录且调用方未指定 username 时补充认证信息
            if self.username and "username" not in payload:
                payload["username"] = self.username
            if self.token and "token" not in payload:
                payload["token"] = self.token
            send(sock, pack_msg(action, payload))
            result = json.loads(recv(sock).decode("utf-8"))
            resp = result.get("payload", {})
            # 防御：无论服务端返回什么，都保证返回 dict，避免上层 .get() 崩溃
            if not isinstance(resp, dict):
                return {"ok": False, "message": t("服务器响应格式异常: {resp!r}").format(resp=resp)}
            return resp
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def start_receive(self, on_message, on_close, on_kicked=None, on_pong=None):
        """启动接收线程（在后台持续接收消息）。"""
        # 防止重复启动：如果旧接收线程还活着，先等它退出
        if self._recv_thread is not None and self._recv_thread.is_alive():
            self.running = False
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self._recv_thread.join(timeout=3)
        self.on_message = on_message
        self.on_close = on_close
        self.on_kicked = on_kicked
        self.on_pong = on_pong
        self.running = True
        self._recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._recv_thread.start()

    def _receive_loop(self):
        while self.running:
            # 第一步：只负责从网络读一条完整消息。只有这里的异常才代表连接真的断了。
            try:
                data = recv(self.sock)
            except Exception:
                if self.running and self.on_close:
                    self.on_close()
                break
            # 第二步：解析与分发。这里的任何异常都绝不能被当成断线，否则会误触发重连
            try:
                obj = json.loads(data.decode("utf-8"))
                action = obj.get("action", "")
                payload = obj.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {}
                if action == "message":
                    if self.on_message:
                        self.on_message(payload)
                elif action == "pong":
                    self.last_pong_time = time.time()
                    if self.on_pong:
                        self.on_pong()
                elif action == "kicked":
                    if self.on_kicked:
                        self.on_kicked(payload.get("reason", t("您已被踢出")))
                    self._do_close()
                    break
                # 其他 action（login_result/register_result 等）在主连接上忽略
            except Exception as e:
                print(t("[网络] 消息处理异常（已忽略，不断开）: {e}").format(e=e))
                continue

    def _do_close(self):
        self.running = False
        self.connected = False
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        # 等待接收线程退出（避免在接收线程内部 join 自己导致死锁）
        if (self._recv_thread is not None
                and self._recv_thread.is_alive()
                and threading.current_thread() != self._recv_thread):
            self._recv_thread.join(timeout=3)

    def close(self):
        """关闭连接。"""
        self._do_close()

    def set_server(self, host, port):
        """记录当前服务器地址（供临时连接使用）。"""
        self._current_host = host
        self._current_port = port

    def set_server_addr(self, host, port):
        """set_server 的别名（兼容旧调用）。"""
        self.set_server(host, port)

    def send_text(self, text):
        """send_message 的别名（兼容旧调用）。"""
        self.send_message(text)

    def test_latency(self):
        """通过临时连接测试延迟（毫秒），失败返回 None。"""
        try:
            s = time.time()
            sock = dual_stack_connect(self._current_host, self._current_port, ip_mode=self.ip_mode)
            sock.settimeout(5)
            send(sock, pack_msg("ping", {}))
            # 必须用带长度前缀的 recv()，不能用原始 recv(128)，否则协议不匹配
            recv(sock)
            sock.close()
            return round((time.time() - s) * 1000, 1)
        except Exception:
            return None

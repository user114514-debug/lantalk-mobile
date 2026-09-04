# utils/tools.py - 公共工具函数（客户端/服务端共用）
import json
import struct
from client.lang import t

# 单条消息最大长度（16MB），防止恶意大包导致 OOM
MAX_MESSAGE_SIZE = 16 * 1024 * 1024


def pack_msg(action, payload):
    """把 action 和 payload 打包成 JSON 字节"""
    return json.dumps({"action": action, "payload": payload or {}}, ensure_ascii=False).encode("utf-8")


def send(sock, data_bytes):
    """发送带 4 字节长度前缀的数据"""
    sock.sendall(struct.pack(">I", len(data_bytes)))
    sock.sendall(data_bytes)


def recv_exact(sock, length):
    """精确接收 length 字节"""
    data = b""
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise ConnectionError(t("连接断开"))
        data += chunk
    return data


def recv(sock):
    """接收一条完整消息（含长度前缀），返回 JSON 字节"""
    raw_len = recv_exact(sock, 4)
    length = struct.unpack(">I", raw_len)[0]
    if length <= 0 or length > MAX_MESSAGE_SIZE:
        raise ConnectionError(t("消息长度异常: {length}").format(length=length))
    return recv_exact(sock, length)

# client/message.py - 消息打包与解析
import json
from utils.tools import pack_msg, send, recv

def send_action(sock, action, payload=None):
    send(sock, pack_msg(action, payload or {}))

def recv_action(sock):
    data = recv(sock)
    obj = json.loads(data.decode("utf-8"))
    return obj["action"], obj["payload"]

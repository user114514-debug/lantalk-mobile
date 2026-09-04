# client/ui/file_transfer.py - 文件传输客户端（发送/接收，支持IPv4/IPv6双栈）
import os
import json
import time
import base64
import hashlib
import socket

from client.message import pack_msg, send, recv
from utils.network import dual_stack_connect
from ..lang import t

CHUNK_SIZE = 4 * 1024 * 1024  # 每块 4MB


class FileTransferClient:
    """文件传输客户端：上传/下载文件，通过服务器中转。"""

    def __init__(self, client=None, get_server_info=None):
        """client: ChatClient 对象（优先）；get_server_info: 回调。"""
        self.client = client
        self.get_server_info = get_server_info
        self._file_progress = {}
        self._busy = False

    def is_busy(self):
        return self._busy

    def _server_info(self):
        if self.client is not None:
            return (getattr(self.client, "_current_host", None),
                    getattr(self.client, "_current_port", None),
                    getattr(self.client, "ip_mode", "auto"),
                    getattr(self.client, "username", ""),
                    getattr(self.client, "token", None))
        if self.get_server_info:
            return self.get_server_info()
        return None, None, "auto", "", None

    def _temp_request(self, action, payload, timeout=30):
        """建立临时连接发送请求并返回响应（IPv4/IPv6双栈，带认证）。"""
        host, port, ip_mode, username, token = self._server_info()
        sock = dual_stack_connect(host, port, ip_mode=ip_mode)
        try:
            sock.settimeout(timeout)
            payload = dict(payload or {})
            payload["username"] = username
            payload["token"] = token
            send(sock, pack_msg(action, payload))
            result = json.loads(recv(sock).decode("utf-8"))
            return result.get("payload", {})
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def upload_file(self, filepath, target_user="", on_progress=None):
        """上传文件到服务器（分块传输）。返回 (ok, file_id或None, filename, message)。"""
        self._busy = True
        try:
            return self._do_upload(filepath, target_user, on_progress)
        finally:
            self._busy = False

    def _do_upload(self, filepath, target_user="", on_progress=None):
        if not os.path.exists(filepath):
            return False, None, "", t("文件不存在")
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return False, None, filename, t("文件为空")
        if file_size > 200 * 1024 * 1024:
            return False, None, filename, t("文件过大（上限200MB）")
        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        md5_hash = hashlib.md5()
        with open(filepath, "rb") as f:
            while True:
                block = f.read(65536)
                if not block:
                    break
                md5_hash.update(block)
        file_md5 = md5_hash.hexdigest()
        result = self._temp_request("file_upload_begin", {
            "filename": filename, "size": file_size, "target_user": target_user,
        })
        if not result.get("ok"):
            return False, None, filename, result.get("message", t("上传失败"))
        file_id = result["file_id"]
        with open(filepath, "rb") as f:
            for i in range(total_chunks):
                chunk_data = f.read(CHUNK_SIZE)
                data_b64 = base64.b64encode(chunk_data).decode("ascii")
                result = self._temp_request("file_upload_chunk", {
                    "file_id": file_id, "chunk_index": i, "data": data_b64,
                }, timeout=60)
                if not result.get("ok"):
                    return False, file_id, filename, result.get("message", t("第 {i} 块上传失败").format(i=i+1))
                if on_progress:
                    on_progress(file_id, int((i + 1) / total_chunks * 100))
        result = self._temp_request("file_upload_end", {
            "file_id": file_id, "total_chunks": total_chunks, "md5": file_md5,
        })
        if not result.get("ok"):
            return False, file_id, filename, result.get("message", t("上传完成确认失败"))
        return True, file_id, filename, t("上传成功")

    def download_file(self, file_id, save_dir, on_progress=None):
        """从服务器下载文件（分块接收）。返回 (ok, filepath或None, message)。"""
        # 查询文件信息
        info = self._temp_request("file_info", {"file_id": file_id})
        if not info.get("ok"):
            return False, None, info.get("message", t("文件不存在"))
        filename = info["filename"]
        file_size = info["size"]
        total_chunks = info["total_chunks"]
        server_md5 = info.get("md5", "")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        # 同名文件处理
        if os.path.exists(save_path):
            name, ext = os.path.splitext(filename)
            save_path = os.path.join(save_dir, f"{name}_{int(time.time())}{ext}")
        # 分块下载
        with open(save_path, "wb") as f:
            pass
        try:
            for i in range(total_chunks):
                result = self._temp_request("file_download_chunk", {
                    "file_id": file_id,
                    "chunk_index": i,
                }, timeout=60)
                if not result.get("ok"):
                    return False, None, result.get("message", t("第 {i} 块下载失败").format(i=i+1))
                chunk_data = base64.b64decode(result["data"])
                with open(save_path, "r+b") as f:
                    f.seek(i * CHUNK_SIZE)
                    f.write(chunk_data)
                if on_progress:
                    on_progress(file_id, int((i + 1) / total_chunks * 100))
        except Exception as e:
            return False, None, t("下载失败: {e}").format(e=e)
        # MD5 校验
        if server_md5:
            h = hashlib.md5()
            with open(save_path, "rb") as f:
                while True:
                    block = f.read(65536)
                    if not block:
                        break
                    h.update(block)
            if h.hexdigest() != server_md5:
                return False, save_path, t("MD5校验失败，文件可能已损坏")
        return True, save_path, t("下载成功")

    @staticmethod
    def make_file_notify(file_id, filename):
        """生成文件通知消息文本（格式 __FILE__:file_id:filename，filename可含冒号）。"""
        return f"__FILE__:{file_id}:{filename}"

    @staticmethod
    def parse_file_notify(text):
        """解析文件通知消息。返回 (is_file, file_id, filename)。"""
        if not text or not text.startswith("__FILE__:"):
            return False, None, None
        # 用 maxsplit=2 确保文件名中的冒号不会被截断
        parts = text.split(":", 2)
        if len(parts) < 3:
            return False, None, None
        return True, parts[1], parts[2]

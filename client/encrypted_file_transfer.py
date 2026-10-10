# -*- coding: utf-8 -*-
"""
encrypted_file_transfer.py — 端到端加密文件传输客户端（继承原 FileTransferClient）

只重写上传/下载的数据编码层：
  - 上传：文件名加密、每个数据块 AES-GCM 加密后再 base64
  - 下载：base64 解码后逐块解密、还原文件名
  - 服务器只存密文，看不到文件名与文件内容；协议 action 完全不变
  - 未注入 FileCrypto 时自动降级为原明文传输（向后兼容旧文件）
"""

import os
import base64
import hashlib

from .ui.file_transfer import FileTransferClient, CHUNK_SIZE
from .lang import t


class EncryptedFileTransferClient(FileTransferClient):
    """支持端到端加密的文件传输客户端。file_crypto=None 时退化为明文传输。"""

    def __init__(self, *args, file_crypto=None, key_provider=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fc = file_crypto  # FileCrypto 或 None
        self._key_provider = key_provider  # callable(peer)->FileCrypto|None
        self._dl_peer = None  # 下载方向：接收方用户名（由 UI 下载前设置）

    def set_key_provider(self, provider):
        """设置按对端用户名动态获取 FileCrypto 的回调。"""
        self._key_provider = provider

    def set_download_peer(self, peer):
        """下载文件前告知对方是谁，以便取对应解密密钥。"""
        self._dl_peer = peer

    def _crypto_for_send(self, peer):
        """上传前根据目标用户动态取密钥；取不到则降级明文。"""
        if self._key_provider and peer:
            self.fc = self._key_provider(peer)
        return self.fc

    # ------------------------------------------------------------------
    # 上传
    # ------------------------------------------------------------------
    def _do_upload(self, filepath, target_user="", on_progress=None):
        self._crypto_for_send(target_user)  # 按对端取密钥，取不到则 fc=None 降级明文
        if not self.fc:
            return super()._do_upload(filepath, target_user, on_progress)
        if not os.path.exists(filepath):
            return False, None, "", t("文件不存在")
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return False, None, filename, t("文件为空")
        if file_size > 200 * 1024 * 1024:
            return False, None, filename, t("文件过大（上限200MB）")

        total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
        # 文件名加密后交给服务器（服务器原样存储，接收端本地解密）
        stored_name = self.fc.encrypt_name(filename)
        result = self._temp_request("file_upload_begin", {
            "filename": stored_name, "size": file_size, "target_user": target_user,
        })
        if not result.get("ok"):
            return False, None, filename, result.get("message", t("上传失败"))
        file_id = result["file_id"]

        md5_h = hashlib.md5()
        with open(filepath, "rb") as f:
            for i in range(total_chunks):
                chunk_data = f.read(CHUNK_SIZE)
                enc_packet = self.fc.encrypt_chunk(chunk_data)
                md5_h.update(enc_packet)  # 对密文流算 md5（服务器完整性校验）
                data_b64 = base64.b64encode(enc_packet).decode("ascii")
                result = self._temp_request("file_upload_chunk", {
                    "file_id": file_id, "chunk_index": i, "data": data_b64,
                }, timeout=120)
                if not result.get("ok"):
                    return False, file_id, filename, result.get("message", t("第 {i} 块上传失败").format(i=i+1))
                if on_progress:
                    on_progress(file_id, int((i + 1) / total_chunks * 100))
        result = self._temp_request("file_upload_end", {
            "file_id": file_id, "total_chunks": total_chunks, "md5": md5_h.hexdigest(),
        })
        if not result.get("ok"):
            return False, file_id, filename, result.get("message", t("上传完成确认失败"))
        return True, file_id, filename, t("上传成功")

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------
    def download_file(self, file_id, save_dir, on_progress=None):
        if self._key_provider and self._dl_peer:
            self.fc = self._key_provider(self._dl_peer)
        if not self.fc:
            return super().download_file(file_id, save_dir, on_progress)
        info = self._temp_request("file_info", {"file_id": file_id})
        if not info.get("ok"):
            return False, None, info.get("message", t("文件不存在"))
        stored_name = info["filename"]
        filename = self.fc.decrypt_name(stored_name)  # 还原真实文件名
        total_chunks = info["total_chunks"]
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, filename)
        if os.path.exists(save_path):
            name, ext = os.path.splitext(filename)
            save_path = os.path.join(save_dir, f"{name}_{int(__import__('time').time())}{ext}")

        try:
            with open(save_path, "wb") as out:
                for i in range(total_chunks):
                    result = self._temp_request("file_download_chunk", {
                        "file_id": file_id, "chunk_index": i,
                    }, timeout=120)
                    if not result.get("ok"):
                        return False, None, result.get("message", t("第 {i} 块下载失败").format(i=i+1))
                    enc_packet = base64.b64decode(result["data"])
                    plain_chunk = self.fc.decrypt_chunk(enc_packet)  # GCM Tag 校验完整性
                    out.write(plain_chunk)
                    if on_progress:
                        on_progress(file_id, int((i + 1) / total_chunks * 100))
        except Exception as e:
            return False, None, t("下载失败: {e}").format(e=e)
        return True, save_path, t("下载成功")

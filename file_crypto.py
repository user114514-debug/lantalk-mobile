# -*- coding: utf-8 -*-
"""
file_crypto.py — LanTalk 文件端到端加密（独立增量模块，不改原有文件传输协议）

思路
====
文件分片本就通过服务器中转（file_upload_chunk 的 data 字段是 base64）。
本模块在【客户端上传前加密、下载后解密】，服务器只看到 base64 密文：

  - 每个数据块：CryptoPacket.encrypt_packet(chunk) -> 密文包（含 nonce/tag/seq）
  - 文件名：   同样加密成 "ENC:<base64>"，服务器原样存储，接收端本地解密还原
  - 完整性：   每块 AES-GCM Tag 自带认证，损坏/篡改的块解密即抛 TamperError
  - 防重放：   CryptoPacket 内置 seq 校验

与私聊加密复用同一把 ECDH 协商出的 AES-256 密钥（CryptoSessionManager 派生）。
"""

import os
import sys
import base64

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from crypto.crypto_packet import CryptoPacket, TamperError, ReplayError


ENC_NAME_PREFIX = "ENC:"


class FileCrypto:
    """单个文件传输方向的加解密器（发送/接收各一个实例，seq 独立）。"""

    def __init__(self, aes_key: bytes, mode: str = "relay"):
        if len(aes_key) != 32:
            raise ValueError("AES 密钥必须为 32 字节")
        self._send = CryptoPacket(aes_key, mode=mode)
        self._recv = CryptoPacket(aes_key, mode=mode)

    # ------------------------------------------------------------------
    # 数据块
    # ------------------------------------------------------------------
    def encrypt_chunk(self, plain_chunk: bytes) -> bytes:
        """把一个明文数据块加密为密文包（含 nonce/tag/seq）。"""
        return self._send.encrypt_packet(plain_chunk)

    def decrypt_chunk(self, enc_packet: bytes) -> bytes:
        """解密一个密文数据块，返回原始明文块。篡改抛 TamperError，重放抛 ReplayError。"""
        payload, _seq = self._recv.decrypt_packet(enc_packet)
        return payload

    # ------------------------------------------------------------------
    # 文件名
    # ------------------------------------------------------------------
    def encrypt_name(self, filename: str) -> str:
        """把文件名加密成可直接交给服务器存储的字符串。"""
        blob = self._send.encrypt_packet(filename.encode("utf-8"))
        return ENC_NAME_PREFIX + base64.b64encode(blob).decode("ascii")

    def decrypt_name(self, stored_name: str) -> str:
        """还原文件名。若不是加密格式（旧版未加密文件），原样返回。"""
        if not isinstance(stored_name, str) or not stored_name.startswith(ENC_NAME_PREFIX):
            return stored_name
        try:
            blob = base64.b64decode(stored_name[len(ENC_NAME_PREFIX):])
            payload, _ = self._recv.decrypt_packet(blob)
            return payload.decode("utf-8", errors="replace")
        except Exception:
            return stored_name

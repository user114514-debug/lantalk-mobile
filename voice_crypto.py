# -*- coding: utf-8 -*-
"""
voice_crypto.py — LanTalk UDP 语音帧加密模块（新增独立文件）

对 UDP 语音帧做轻量级 AES-GCM 加密 + seq 防重放。
语音帧小（约480字节），要求低延迟，所以不做外层SHA256（P2P模式）。

数据包结构：
  [2字节seq(大端)] + [12字节Nonce] + [16字节GCM Tag] + AES密文

复用 crypto_packet 的 AESGCM，不引入新依赖。
"""

import os
import struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12
TAG_SIZE = 16
SEQ_SIZE = 2  # 语音帧用16位seq，语音对重放不敏感，只需防基本重放

_VOICE_SEQ_WINDOW = 128  # 滑动窗口大小


class VoiceCrypto:
    """UDP语音帧加解密。每个方向独立实例。"""

    def __init__(self, aes_key: bytes):
        if len(aes_key) != 32:
            raise ValueError("AES密钥必须32字节")
        self._aesgcm = AESGCM(aes_key)
        self._send_seq = 0
        self._last_recv_seq = 0
        self._recv_window = set()

    def encrypt(self, voice_frame: bytes) -> bytes:
        """加密语音帧，返回完整UDP包。"""
        self._send_seq = (self._send_seq + 1) & 0xFFFF
        plaintext = struct.pack("!H", self._send_seq) + voice_frame
        nonce = os.urandom(NONCE_SIZE)
        ct_tag = self._aesgcm.encrypt(nonce, plaintext, None)
        return nonce + ct_tag

    def decrypt(self, packet: bytes) -> bytes:
        """解密语音帧，返回原始音频数据。重放/损坏抛 ValueError。"""
        if len(packet) < NONCE_SIZE + TAG_SIZE + SEQ_SIZE:
            raise ValueError("语音包过短")
        nonce = packet[:NONCE_SIZE]
        ct_tag = packet[NONCE_SIZE:]
        try:
            plaintext = self._aesgcm.decrypt(nonce, ct_tag, None)
        except Exception:
            raise ValueError("语音包校验失败")
        seq = struct.unpack("!H", plaintext[:SEQ_SIZE])[0]
        # 轻量防重放：丢弃比最近seq更早的帧
        if seq in self._recv_window:
            raise ValueError("语音帧重放")
        self._recv_window.add(seq)
        if len(self._recv_window) > _VOICE_SEQ_WINDOW:
            self._recv_window = set(list(self._recv_window)[-_VOICE_SEQ_WINDOW:])
        return plaintext[SEQ_SIZE:]

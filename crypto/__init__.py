# -*- coding: utf-8 -*-
"""
LanTalk 加密通信模块（独立增量开发，不修改原有底层代码）

子模块：
  - ecdh_key:     ECDH密钥协商（secp256r1 + HKDF派生AES-256密钥）
  - crypto_packet: 双层数据包校验 + AES-GCM加密 + seq防重放

快速使用：
    from crypto import ECDHKeyExchange, CryptoPacket, TamperError, ReplayError, relay_verify_and_strip
"""

from crypto.ecdh_key import ECDHKeyExchange
from crypto.crypto_packet import (
    CryptoPacket,
    TamperError,
    ReplayError,
    relay_verify_and_strip,
    SHA256_SIZE,
    NONCE_SIZE,
    TAG_SIZE,
)

__all__ = [
    "ECDHKeyExchange",
    "CryptoPacket",
    "TamperError",
    "ReplayError",
    "relay_verify_and_strip",
    "SHA256_SIZE",
    "NONCE_SIZE",
    "TAG_SIZE",
]

# -*- coding: utf-8 -*-
"""
LanTalk ECDH 密钥协商模块（独立增量模块，不依赖原有代码）

功能：
  - 基于 secp256r1 (NIST P-256) 椭圆曲线生成密钥对
  - 客户端之间交换公钥（未压缩点格式，65字节）
  - ECDH 协商共享密钥，经 HKDF-SHA256 派生 32 字节 AES-256 会话密钥

依赖：cryptography（pip install cryptography）

使用方式：
    from crypto.ecdh_key import ECDHKeyExchange

    # 客户端A
    alice = ECDHKeyExchange()
    alice_pub = alice.get_public_key_bytes()   # 65字节，发给对方

    # 客户端B
    bob = ECDHKeyExchange()
    bob_pub = bob.get_public_key_bytes()       # 65字节，发给对方

    # 双方各自用对方公钥派生相同的AES密钥
    alice_key = alice.derive_shared_key(bob_pub)
    bob_key   = bob.derive_shared_key(alice_pub)
    assert alice_key == bob_key   # 32字节AES-256密钥
"""

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


# HKDF 派生时的上下文标签，确保不同用途不会复用密钥
_HKDF_INFO = b"LanTalk-ECDH-AES256-v1"
# 公钥未压缩点长度：1字节前缀 + 32字节X + 32字节Y = 65字节
PUBLIC_KEY_SIZE = 65


class ECDHKeyExchange:
    """ECDH 密钥协商：生成密钥对、交换公钥、派生AES-256会话密钥。"""

    def __init__(self):
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self._public_key = self._private_key.public_key()
        self._shared_key = None  # 派生后的32字节AES密钥，derive后填充

    # ------------------------------------------------------------------
    # 公钥序列化 / 反序列化
    # ------------------------------------------------------------------
    def get_public_key_bytes(self) -> bytes:
        """导出未压缩公钥点（65字节），通过现有TCP通道发给对方。"""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )

    @staticmethod
    def public_key_from_bytes(data: bytes):
        """从65字节未压缩公钥点还原 EllipticCurvePublicKey 对象。"""
        if len(data) != PUBLIC_KEY_SIZE:
            raise ValueError(f"公钥长度应为{PUBLIC_KEY_SIZE}字节，实际{len(data)}字节")
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), data)

    # ------------------------------------------------------------------
    # 密钥派生
    # ------------------------------------------------------------------
    def derive_shared_key(self, peer_public_key_bytes: bytes) -> bytes:
        """
        用对方公钥执行ECDH协商，经HKDF-SHA256派生32字节AES-256密钥。
        双方传入对方公钥后得到完全相同的密钥。
        """
        peer_key = self.public_key_from_bytes(peer_public_key_bytes)
        shared_secret = self._private_key.exchange(ec.ECDH(), peer_key)

        # HKDF：将ECDH共享秘密派生为固定长度的AES密钥
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_HKDF_INFO,
        )
        self._shared_key = hkdf.derive(shared_secret)
        return self._shared_key

    @property
    def shared_key(self):
        """派生后的AES-256密钥（32字节），未调用derive前为None。"""
        return self._shared_key

    def has_key(self) -> bool:
        """是否已完成密钥协商。"""
        return self._shared_key is not None

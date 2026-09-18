# -*- coding: utf-8 -*-
"""
identity_auth.py — LanTalk 身份认证与防MITM模块（新增独立文件）

问题：ECDH 公钥交换本身不验证对方身份，中间人可以替换公钥。
方案：每个用户有长期 Ed25519 身份密钥对，登录时服务端登记公钥。
      交换临时ECDH公钥时，用Ed25519私钥签名，对方验证签名后再信任ECDH公钥。
      这样中间人无法同时伪造双方的签名。

依赖：cryptography（Ed25519）

使用：
    from crypto.identity_auth import IdentityKey, sign_ephemeral_key, verify_ephemeral_signature

    # 生成长期身份密钥（首次注册时）
    identity = IdentityKey()
    identity_pub = identity.export_public()       # 32字节Ed25519公钥，发给服务端登记

    # 交换ECDH公钥时
    ecdh = ECDHKeyExchange()
    ecdh_pub = ecdh.get_public_key_bytes()
    signature = identity.sign(ecdh_pub)             # 64字节签名

    # 对方收到后
    verify_ephemeral_signature(identity_pub, ecdh_pub, signature)  # 失败→篡改/中间人，拒绝
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


IDENTITY_KEY_SIZE = 32     # Ed25519 公钥32字节
SIGNATURE_SIZE = 64        # Ed25519 签名64字节


class IdentityError(Exception):
    """身份验证失败。"""
    pass


class IdentityKey:
    """Ed25519 长期身份密钥对。"""

    def __init__(self, private_bytes: bytes = None):
        if private_bytes:
            self._private = Ed25519PrivateKey.from_private_bytes(private_bytes)
        else:
            self._private = Ed25519PrivateKey.generate()
        self._public = self._private.public_key()

    def export_private(self) -> bytes:
        """导出32字节私钥（应安全保存，如加密存储）。"""
        return self._private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )

    def export_public(self) -> bytes:
        """导出32字节公钥（发给服务端登记，公开传播）。"""
        return self._public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    def sign(self, data: bytes) -> bytes:
        """对数据签名，返回64字节签名。"""
        return self._private.sign(data)

    @staticmethod
    def verify(public_bytes: bytes, data: bytes, signature: bytes) -> bool:
        """验证签名，成功返回True，失败抛IdentityError。"""
        try:
            pub = Ed25519PublicKey.from_public_bytes(public_bytes)
            pub.verify(signature, data)
            return True
        except InvalidSignature:
            raise IdentityError("签名验证失败：中间人攻击或数据被篡改")
        except Exception as e:
            raise IdentityError(f"公钥格式错误: {e}")


def sign_ephemeral_key(identity: IdentityKey, ecdh_public_bytes: bytes) -> bytes:
    """
    用长期身份密钥对临时ECDH公钥签名。
    返回64字节签名，和ecdh_public_bytes一起发给对方。
    """
    return identity.sign(ecdh_public_bytes)


def verify_ephemeral_signature(identity_public_bytes: bytes,
                                ecdh_public_bytes: bytes,
                                signature: bytes) -> bool:
    """
    验证对方临时ECDH公钥的签名。
    identity_public_bytes: 对方的Ed25519公钥（应从服务端安全获取）
    ecdh_public_bytes: 对方发来的临时ECDH公钥
    signature: 对方的Ed25519签名

    成功返回True，失败抛IdentityError（拒绝连接）。
    """
    return IdentityKey.verify(identity_public_bytes, ecdh_public_bytes, signature)

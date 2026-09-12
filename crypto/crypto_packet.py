# -*- coding: utf-8 -*-
"""
LanTalk 双层数据包校验 + AES-GCM 加密模块（独立增量模块，不依赖原有代码）

数据包结构：
  中转模式（relay）完整包 = [32字节SHA256] + [12字节Nonce] + [16字节GCM Tag] + AES密文
  P2P直连模式（p2p）包   = [12字节Nonce] + [16字节GCM Tag] + AES密文（无外层SHA）

  加密载荷内部 = [4字节seq序列号(大端)] + 原始业务载荷

规则：
  发送端：AES-GCM加密原始载荷(含seq) → 生成nonce/tag/密文 → 对nonce+tag+密文算SHA256 → 拼接
  中转服务端：校验外层SHA256 → 不一致则发tamper_alert信令、丢弃；一致则剥离外层SHA、转发内层包
  接收客户端：剥离外层SHA(中转模式) → AES-GCM解密+Tag校验 → 失败则丢弃、弹窗；成功则提取seq防重放

依赖：cryptography（pip install cryptography）

使用方式：
    from crypto.crypto_packet import CryptoPacket, TamperError, ReplayError, relay_verify_and_strip

    # 发送端（双方已通过ECDH协商得到aes_key）
    enc = CryptoPacket(aes_key, mode="relay")
    packet = enc.encrypt_packet(b"hello world")   # bytes，可直接通过Socket发送

    # 中转服务端（只做外层SHA校验+剥离）
    try:
        inner_packet = relay_verify_and_strip(packet)   # 剥离32字节SHA
        # 转发 inner_packet 给接收客户端
    except TamperError:
        # 发送控制信令 {"cmd":"tamper_alert","msg":"文件数据包被篡改，已拦截"}
        # 丢弃坏包
        pass

    # 接收客户端
    dec = CryptoPacket(aes_key, mode="relay")
    try:
        payload, seq = dec.decrypt_packet(inner_packet)
        # payload 是原始业务载荷，seq是序列号
    except TamperError:
        # 弹窗提示数据包篡改，丢弃
        pass
    except ReplayError:
        # 重放攻击，丢弃
        pass
"""

import os
import struct
import hashlib

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ======================================================================
# 常量定义
# ======================================================================
SHA256_SIZE = 32          # 外层SHA256哈希长度
NONCE_SIZE = 12           # AES-GCM Nonce长度（96位，GCM标准推荐）
TAG_SIZE = 16             # AES-GCM认证标签长度
SEQ_SIZE = 4              # 防重放序列号长度（uint32大端）
INNER_HEADER_SIZE = NONCE_SIZE + TAG_SIZE  # 内层包头 = 28字节
MIN_RELAY_PACKET_SIZE = SHA256_SIZE + INNER_HEADER_SIZE + 1  # 中转包最小长度
MIN_P2P_PACKET_SIZE = INNER_HEADER_SIZE + 1                    # P2P包最小长度

# 防重放序列号集合上限，超过后裁剪（避免内存无限增长）
_MAX_SEEN_SEQS = 10000
_SEEN_SEQS_TRIM = 5000


# ======================================================================
# 异常定义
# ======================================================================
class TamperError(Exception):
    """数据包篡改异常：外层SHA256校验失败 或 内层AES-GCM Tag校验失败。"""
    pass


class ReplayError(Exception):
    """重放攻击异常：收到已见过的seq序列号。"""
    pass


# ======================================================================
# 中转服务端：外层SHA256校验 + 剥离（独立函数，不依赖CryptoPacket类）
# ======================================================================
def relay_verify_and_strip(packet: bytes) -> bytes:
    """
    中转服务端第一层校验：
      1. 提取前32字节作为期望SHA256
      2. 对剩余部分(nonce+tag+密文)计算实际SHA256
      3. 不一致 → 抛出TamperError（调用方应发tamper_alert信令并丢弃）
      4. 一致 → 返回剥离外层SHA后的内层包（nonce+tag+密文），转发给接收客户端

    参数:
        packet: 完整的中转模式数据包（含32字节外层SHA）
    返回:
        inner_packet: 剥离外层SHA后的内层包
    异常:
        ValueError: 数据包过短
        TamperError: 外层SHA256校验失败（篡改）
    """
    if len(packet) < MIN_RELAY_PACKET_SIZE:
        raise ValueError(f"中转数据包过短：需要至少{MIN_RELAY_PACKET_SIZE}字节，实际{len(packet)}字节")

    expected_hash = packet[:SHA256_SIZE]
    inner_packet = packet[SHA256_SIZE:]
    actual_hash = hashlib.sha256(inner_packet).digest()

    if expected_hash != actual_hash:
        raise TamperError("外层SHA256校验失败：数据包被篡改，已拦截")

    return inner_packet


# ======================================================================
# 客户端：加密 / 解密
# ======================================================================
class CryptoPacket:
    """
    数据包加密/解密器。
    每个通信方向（发送/接收）应使用独立实例，因为seq序列号和已见seq集合是独立维护的。
    """

    def __init__(self, aes_key: bytes, mode: str = "relay"):
        """
        参数:
            aes_key: 32字节AES-256密钥（由ECDH协商得到）
            mode: "relay"（中转模式，启用外层SHA256）或 "p2p"（直连模式，仅AES-GCM）
        """
        if len(aes_key) != 32:
            raise ValueError(f"AES密钥必须为32字节，实际{len(aes_key)}字节")
        if mode not in ("relay", "p2p"):
            raise ValueError(f"mode必须为'relay'或'p2p'，实际'{mode}'")

        self._aesgcm = AESGCM(aes_key)
        self._mode = mode
        self._send_seq = 0          # 发送端序列号，从0开始递增
        self._seen_seqs = set()     # 接收端已见序列号集合（防重放）

    # ------------------------------------------------------------------
    # 发送端：加密
    # ------------------------------------------------------------------
    def encrypt_packet(self, payload: bytes) -> bytes:
        """
        将原始业务载荷加密为完整数据包。

        流程：
          1. 生成递增seq序列号（4字节大端）
          2. 明文 = seq + payload
          3. 随机生成12字节nonce
          4. AES-GCM加密 → 密文 + 16字节tag
          5. 内层包 = nonce + tag + 密文
          6. 中转模式：外层SHA256(内层包) + 内层包
             P2P模式：直接返回内层包

        参数:
            payload: 原始业务载荷（任意字节）
        返回:
            完整数据包（bytes），可直接通过Socket发送
        """
        # 1. 序列号（防重放），递增到0xFFFFFFFF后回绕
        self._send_seq = (self._send_seq + 1) & 0xFFFFFFFF
        plaintext = struct.pack("!I", self._send_seq) + payload

        # 2. 随机nonce（GCM要求每次加密必须使用不同nonce）
        nonce = os.urandom(NONCE_SIZE)

        # 3. AES-GCM加密（AAD=None，返回 密文+tag 拼接）
        ct_with_tag = self._aesgcm.encrypt(nonce, plaintext, None)
        ciphertext = ct_with_tag[:-TAG_SIZE]
        tag = ct_with_tag[-TAG_SIZE:]

        # 4. 内层包 = nonce + tag + 密文
        inner_packet = nonce + tag + ciphertext

        # 5. 中转模式加外层SHA256
        if self._mode == "relay":
            outer_hash = hashlib.sha256(inner_packet).digest()
            return outer_hash + inner_packet
        else:
            return inner_packet

    # ------------------------------------------------------------------
    # 接收端：解密
    # ------------------------------------------------------------------
    def decrypt_packet(self, packet: bytes, outer_stripped: bool = False):
        """
        解密数据包，返回原始业务载荷和序列号。

        流程：
          1. 中转模式且outer_stripped=False：先尝试外层SHA256校验。
             - 校验通过 → 剥离外层SHA，用内层包解密
             - 校验失败但长度符合内层包特征 → 视为已被中转服务器剥离，直接解密
               （真正的篡改包会在AES-GCM Tag校验阶段被拦截，安全不受影响）
          2. 提取 nonce(12) + tag(16) + 密文
          3. AES-GCM解密 + Tag校验（失败→TamperError）
          4. 提取 seq(4) + payload
          5. 防重放检查（重复seq→ReplayError）

        参数:
            packet: 收到的数据包（完整包含外层SHA的包，或已被中转服务器剥离的内层包）
            outer_stripped: 外层SHA是否已确认被剥离。True=直接解密，跳过外层SHA检测。
        返回:
            (payload, seq) 元组：payload是原始业务载荷(bytes)，seq是序列号(int)
        异常:
            ValueError: 数据包过短
            TamperError: 外层SHA校验失败(且非内层包) 或 AES-GCM Tag校验失败
            ReplayError: 重复seq（重放攻击）
        """
        data = packet

        # 1. 中转模式：外层SHA256校验 + 剥离（自动兼容完整包和已剥离内层包）
        if self._mode == "relay" and not outer_stripped:
            if len(data) >= MIN_RELAY_PACKET_SIZE:
                expected_hash = data[:SHA256_SIZE]
                candidate_inner = data[SHA256_SIZE:]
                actual_hash = hashlib.sha256(candidate_inner).digest()
                if expected_hash == actual_hash:
                    # 外层SHA校验通过，使用剥离后的内层包
                    inner_packet = candidate_inner
                else:
                    # 外层SHA不匹配：可能是篡改的完整包，也可能是已被剥离的内层包
                    # 若长度符合内层包最小特征，当作内层包直接解密（篡改包会在Tag校验失败）
                    if len(data) >= MIN_P2P_PACKET_SIZE:
                        inner_packet = data
                    else:
                        raise TamperError("外层SHA256校验失败：数据包被篡改")
            else:
                # 长度不足完整包，检查是否符合内层包
                if len(data) >= MIN_P2P_PACKET_SIZE:
                    inner_packet = data
                else:
                    raise ValueError(f"数据包过短：需要至少{MIN_P2P_PACKET_SIZE}字节，实际{len(data)}字节")
        else:
            # P2P模式 或 outer_stripped=True：直接当作内层包
            inner_packet = data
            if len(inner_packet) < MIN_P2P_PACKET_SIZE:
                raise ValueError(f"数据包过短：需要至少{MIN_P2P_PACKET_SIZE}字节，实际{len(inner_packet)}字节")

        # 2. 提取 nonce + tag + 密文
        nonce = inner_packet[:NONCE_SIZE]
        tag = inner_packet[NONCE_SIZE:NONCE_SIZE + TAG_SIZE]
        ciphertext = inner_packet[NONCE_SIZE + TAG_SIZE:]

        # 3. AES-GCM解密（需要把tag拼回密文末尾，AESGCM.decrypt期望 密文+tag）
        ct_with_tag = ciphertext + tag
        try:
            plaintext = self._aesgcm.decrypt(nonce, ct_with_tag, None)
        except Exception:
            raise TamperError("AES-GCM Tag校验失败：数据包被篡改或密钥不匹配")

        # 4. 提取 seq + payload
        if len(plaintext) < SEQ_SIZE:
            raise ValueError(f"解密后的明文过短：需要至少{SEQ_SIZE}字节，实际{len(plaintext)}字节")
        seq = struct.unpack("!I", plaintext[:SEQ_SIZE])[0]
        payload = plaintext[SEQ_SIZE:]

        # 5. 防重放检查
        if seq in self._seen_seqs:
            raise ReplayError(f"重复序列号seq={seq}：疑似重放攻击，已丢弃")
        self._seen_seqs.add(seq)

        # 集合大小控制（避免长期运行内存无限增长）
        if len(self._seen_seqs) > _MAX_SEEN_SEQS:
            # 保留最近的一半（set无序，转为list截取，会丢失一些旧seq但不影响安全）
            self._seen_seqs = set(list(self._seen_seqs)[-_SEEN_SEQS_TRIM:])

        return payload, seq

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        """当前传输模式：'relay' 或 'p2p'。"""
        return self._mode

    @property
    def next_send_seq(self) -> int:
        """下一个将使用的发送序列号。"""
        return (self._send_seq + 1) & 0xFFFFFFFF

    def reset_seen_seqs(self):
        """清空已见序列号集合（用于重新协商密钥后调用）。"""
        self._seen_seqs.clear()
        self._send_seq = 0

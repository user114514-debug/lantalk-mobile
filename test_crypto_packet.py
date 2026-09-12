# -*- coding: utf-8 -*-
"""
LanTalk 加密模块测试 Demo（独立运行，不影响LanTalk原有程序）

运行方式：
    cd LanTalk_mobile_clean
    python test_crypto_packet.py

测试覆盖：
  1. ECDH密钥协商：双方派生相同的AES-256密钥
  2. 中转模式(relay)正常加解密往返
  3. P2P直连模式(p2p)正常加解密往返
  4. 中转服务端外层SHA256校验+剥离
  5. 外层SHA篡改拦截（修改哈希字节）
  6. 内层密文篡改拦截（修改密文字节，AES-GCM Tag校验失败）
  7. 防重放攻击（重复seq被拒绝）
  8. 数据包过短异常处理
  9. 密钥长度校验
"""

import sys
import os

# 确保能导入 crypto 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def _pass(msg):
    print(f"  [PASS] {msg}")


def _fail(msg):
    print(f"  [FAIL] {msg}")


def _section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ======================================================================
# 测试1：ECDH密钥协商
# ======================================================================
def test_ecdh_key_exchange():
    _section("测试1：ECDH密钥协商")

    alice = ECDHKeyExchange()
    bob = ECDHKeyExchange()

    # 导出公钥（65字节未压缩点）
    alice_pub = alice.get_public_key_bytes()
    bob_pub = bob.get_public_key_bytes()
    assert len(alice_pub) == 65, f"Alice公钥应为65字节，实际{len(alice_pub)}"
    assert len(bob_pub) == 65, f"Bob公钥应为65字节，实际{len(bob_pub)}"
    _pass(f"公钥导出成功，Alice={len(alice_pub)}字节, Bob={len(bob_pub)}字节")

    # 双方各自用对方公钥派生密钥
    alice_key = alice.derive_shared_key(bob_pub)
    bob_key = bob.derive_shared_key(alice_pub)

    assert len(alice_key) == 32, f"Alice派生密钥应为32字节，实际{len(alice_key)}"
    assert len(bob_key) == 32, f"Bob派生密钥应为32字节，实际{len(bob_key)}"
    assert alice_key == bob_key, "双方派生的AES密钥必须完全相同！"
    _pass(f"双方派生相同的AES-256密钥（32字节），密钥一致: {alice_key.hex()[:16]}...")

    assert alice.has_key() and bob.has_key()
    _pass("has_key() 状态正确")

    return alice_key  # 供后续测试使用


# ======================================================================
# 测试2：中转模式(relay)正常加解密
# ======================================================================
def test_relay_mode_roundtrip(aes_key):
    _section("测试2：中转模式(relay)正常加解密往返")

    sender = CryptoPacket(aes_key, mode="relay")
    receiver = CryptoPacket(aes_key, mode="relay")

    payload = b"Hello LanTalk! This is a test message for relay mode."
    packet = sender.encrypt_packet(payload)

    # 中转包结构：32(SHA) + 12(nonce) + 16(tag) + 密文
    min_size = SHA256_SIZE + NONCE_SIZE + TAG_SIZE + 4 + len(payload)
    assert len(packet) >= min_size, f"数据包长度不足，期望>={min_size}，实际{len(packet)}"
    _pass(f"加密成功，数据包长度={len(packet)}字节（>=最小{min_size}字节）")

    # 中转服务端校验+剥离
    inner_packet = relay_verify_and_strip(packet)
    assert len(inner_packet) == len(packet) - SHA256_SIZE
    _pass(f"中转服务端外层SHA校验通过，剥离后内层包长度={len(inner_packet)}字节")

    # 接收端解密（传入已剥离的内层包，也可直接传完整包）
    decrypted_payload, seq = receiver.decrypt_packet(inner_packet)
    assert decrypted_payload == payload, f"解密结果不匹配！期望{payload}，实际{decrypted_payload}"
    assert seq == 1, f"第一个包seq应为1，实际{seq}"
    _pass(f"解密成功，payload一致，seq={seq}")

    # 直接传完整包（含外层SHA）也能解密
    packet2 = sender.encrypt_packet(b"Second message")
    dec2, seq2 = receiver.decrypt_packet(packet2)  # 直接传完整包
    assert dec2 == b"Second message"
    assert seq2 == 2
    _pass(f"直接传完整包（含外层SHA）解密成功，seq={seq2}")


# ======================================================================
# 测试3：P2P直连模式(p2p)正常加解密
# ======================================================================
def test_p2p_mode_roundtrip(aes_key):
    _section("测试3：P2P直连模式(p2p)正常加解密往返")

    sender = CryptoPacket(aes_key, mode="p2p")
    receiver = CryptoPacket(aes_key, mode="p2p")

    payload = b"P2P direct message - no outer SHA256, lower latency."
    packet = sender.encrypt_packet(payload)

    # P2P包无外层SHA：12(nonce) + 16(tag) + 密文
    min_size = NONCE_SIZE + TAG_SIZE + 4 + len(payload)
    assert len(packet) >= min_size
    assert len(packet) < SHA256_SIZE + min_size + 10  # 确认没有外层32字节SHA
    _pass(f"P2P加密成功，数据包长度={len(packet)}字节（无外层SHA，比中转模式少32字节）")

    decrypted, seq = receiver.decrypt_packet(packet)
    assert decrypted == payload
    assert seq == 1
    _pass(f"P2P解密成功，payload一致，seq={seq}")


# ======================================================================
# 测试4：外层SHA篡改拦截
# ======================================================================
def test_outer_hash_tamper(aes_key):
    _section("测试4：外层SHA256篡改拦截（中转模式）")

    sender = CryptoPacket(aes_key, mode="relay")
    receiver = CryptoPacket(aes_key, mode="relay")

    packet = sender.encrypt_packet(b"Tamper test - outer hash")

    # 篡改：修改第5个字节（外层SHA范围内）
    tampered = bytearray(packet)
    tampered[5] ^= 0xFF  # 翻转一个字节
    tampered = bytes(tampered)

    # 中转服务端校验应失败
    try:
        relay_verify_and_strip(tampered)
        _fail("外层SHA篡改未被拦截！relay_verify_and_strip应抛出TamperError")
    except TamperError as e:
        _pass(f"中转服务端成功拦截外层SHA篡改: {e}")

    # 接收端直接解密也应失败
    try:
        receiver.decrypt_packet(tampered)
        _fail("接收端未拦截外层SHA篡改！")
    except TamperError as e:
        _pass(f"接收端成功拦截外层SHA篡改: {e}")


# ======================================================================
# 测试5：内层密文篡改拦截（AES-GCM Tag校验）
# ======================================================================
def test_inner_ciphertext_tamper(aes_key):
    _section("测试5：内层密文篡改拦截（AES-GCM Tag校验失败）")

    sender = CryptoPacket(aes_key, mode="relay")
    receiver = CryptoPacket(aes_key, mode="relay")

    packet = sender.encrypt_packet(b"Tamper test - inner ciphertext")

    # 先剥离外层SHA（模拟中转服务端校验通过）
    inner_packet = relay_verify_and_strip(packet)

    # 篡改：修改密文部分（跳过32SHA+12nonce+16tag=60字节后是密文）
    # 内层包结构：12(nonce) + 16(tag) + 密文，所以密文从第28字节开始
    tampered_inner = bytearray(inner_packet)
    ciphertext_offset = NONCE_SIZE + TAG_SIZE  # 28
    if len(tampered_inner) > ciphertext_offset + 2:
        tampered_inner[ciphertext_offset + 2] ^= 0xFF
    tampered_inner = bytes(tampered_inner)

    # 接收端解密应因Tag校验失败而抛出TamperError
    try:
        receiver.decrypt_packet(tampered_inner)
        _fail("内层密文篡改未被拦截！AES-GCM Tag校验应失败")
    except TamperError as e:
        _pass(f"AES-GCM Tag校验成功拦截密文篡改: {e}")


# ======================================================================
# 测试6：防重放攻击
# ======================================================================
def test_replay_attack(aes_key):
    _section("测试6：防重放攻击（重复seq序列号被拒绝）")

    sender = CryptoPacket(aes_key, mode="relay")
    receiver = CryptoPacket(aes_key, mode="relay")

    # 正常发送一个包
    packet = sender.encrypt_packet(b"Replay test message")
    inner = relay_verify_and_strip(packet)
    payload, seq = receiver.decrypt_packet(inner)
    assert payload == b"Replay test message"
    _pass(f"首次接收成功，seq={seq}")

    # 重放：再次发送同一个包（相同seq）
    try:
        receiver.decrypt_packet(inner)
        _fail("重放攻击未被拦截！重复seq应抛出ReplayError")
    except ReplayError as e:
        _pass(f"成功拦截重放攻击: {e}")

    # 新包（新seq）应正常接收
    packet2 = sender.encrypt_packet(b"New packet after replay")
    inner2 = relay_verify_and_strip(packet2)
    payload2, seq2 = receiver.decrypt_packet(inner2)
    assert payload2 == b"New packet after replay"
    assert seq2 > seq
    _pass(f"重放拦截后新包正常接收，seq={seq2}")


# ======================================================================
# 测试7：数据包过短异常
# ======================================================================
def test_short_packet(aes_key):
    _section("测试7：数据包过短异常处理")

    receiver = CryptoPacket(aes_key, mode="relay")

    # 空包
    try:
        receiver.decrypt_packet(b"")
        _fail("空包应抛出ValueError")
    except ValueError as e:
        _pass(f"空包正确抛出ValueError: {e}")

    # 只有外层SHA长度（32字节全零）：符合内层包最小长度，被当作内层包解密，AES-GCM应失败
    try:
        receiver.decrypt_packet(b"\x00" * SHA256_SIZE)
        _fail("32字节全零包应在AES-GCM阶段抛出TamperError")
    except TamperError as e:
        _pass(f"32字节全零包正确在AES-GCM阶段抛出TamperError: {e}")
    except ValueError as e:
        # 也可能因长度判断抛出ValueError，两种都算正确拦截
        _pass(f"32字节全零包正确抛出ValueError: {e}")

    # P2P模式过短
    receiver_p2p = CryptoPacket(aes_key, mode="p2p")
    try:
        receiver_p2p.decrypt_packet(b"\x00" * 10)
        _fail("P2P过短包应抛出ValueError")
    except ValueError as e:
        _pass(f"P2P过短包正确抛出ValueError: {e}")


# ======================================================================
# 测试8：密钥长度校验
# ======================================================================
def test_key_length_validation():
    _section("测试8：密钥长度校验")

    try:
        CryptoPacket(b"short_key", mode="relay")
        _fail("短密钥应抛出ValueError")
    except ValueError as e:
        _pass(f"短密钥正确抛出ValueError: {e}")

    try:
        CryptoPacket(b"\x00" * 32, mode="invalid")
        _fail("无效mode应抛出ValueError")
    except ValueError as e:
        _pass(f"无效mode正确抛出ValueError: {e}")

    # 正确密钥应成功
    enc = CryptoPacket(b"\x01" * 32, mode="relay")
    assert enc.mode == "relay"
    _pass("正确32字节密钥+relay模式初始化成功")


# ======================================================================
# 测试9：大文件载荷加解密
# ======================================================================
def test_large_payload(aes_key):
    _section("测试9：大载荷加解密（模拟文件分片）")

    sender = CryptoPacket(aes_key, mode="relay")
    receiver = CryptoPacket(aes_key, mode="relay")

    # 模拟4MB文件分片
    large_payload = os.urandom(4 * 1024 * 1024)  # 4MB
    packet = sender.encrypt_packet(large_payload)
    _pass(f"4MB载荷加密成功，密文包长度={len(packet)}字节")

    inner = relay_verify_and_strip(packet)
    decrypted, seq = receiver.decrypt_packet(inner)
    assert decrypted == large_payload, "4MB载荷解密后不匹配！"
    _pass(f"4MB载荷解密成功，数据完全一致，seq={seq}")


# ======================================================================
# 主函数
# ======================================================================
def main():
    print("=" * 60)
    print("  LanTalk 加密模块测试 Demo")
    print("  AES-GCM + SHA256双层校验 + ECDH密钥协商 + seq防重放")
    print("=" * 60)

    try:
        # 测试1：ECDH
        aes_key = test_ecdh_key_exchange()

        # 测试2-9
        test_relay_mode_roundtrip(aes_key)
        test_p2p_mode_roundtrip(aes_key)
        test_outer_hash_tamper(aes_key)
        test_inner_ciphertext_tamper(aes_key)
        test_replay_attack(aes_key)
        test_short_packet(aes_key)
        test_key_length_validation()
        test_large_payload(aes_key)

        print("\n" + "=" * 60)
        print("  全部测试通过！")
        print("=" * 60)
        return 0

    except AssertionError as e:
        print(f"\n  [ASSERTION FAILED] {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n  [UNEXPECTED ERROR] {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

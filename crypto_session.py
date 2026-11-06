# -*- coding: utf-8 -*-
"""
crypto_session.py — LanTalk 移动端 端到端加密会话桥接模块（独立增量，不改原有代码）

与 PC 端 client/client/crypto_session.py 同协议、同接口，复用手机端已有的
crypto/ecdh_key.py 与 crypto/crypto_packet.py（底层原语一致）。

之前手机端缺失本模块，导致对端发来的 LT1:KEY 公钥信令无人拦截，
被当成普通文字显示在气泡里。补上后：
  - LT1:KEY:<base64>   公钥交换信令（UI 不显示）
  - LT1:ENC:<base64>   密文信令（UI 解密后显示明文）

接入（只加调用入口，不改原有解析）：
  登录成功后：
      from crypto_session import CryptoSessionManager
      self.crypto = CryptoSessionManager(mode="relay")
      self.crypto.set_self_username(username)
      self.crypto.set_transport(<发送私聊的函数>)
  接收处用 wrap_on_message 包住原有消息回调。
"""

import os
import sys
import base64
import threading

# 本文件位于移动端根目录，与 crypto/ 同级；确保根目录在 sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from crypto.ecdh_key import ECDHKeyExchange, PUBLIC_KEY_SIZE
from crypto.crypto_packet import CryptoPacket, TamperError, ReplayError
from crypto.identity_auth import IdentityKey, IdentityError, IDENTITY_KEY_SIZE, SIGNATURE_SIZE


KEY_PREFIX = "LT1:KEY:"
ENC_PREFIX = "LT1:ENC:"


class CryptoSessionManager:
    """管理与多个对端用户的 ECDH 协商与 AES-GCM 加密会话。"""

    def __init__(self, mode: str = "relay"):
        if mode not in ("relay", "p2p"):
            raise ValueError("mode 必须是 'relay' 或 'p2p'")
        self.mode = mode
        self._lock = threading.RLock()
        self.ecdh = ECDHKeyExchange()
        self.username = ""
        self.sessions = {}
        self.sent_keys = set()
        self.pending = {}
        self._transport = None
        self.on_session_ready = None
        self.on_security_alert = None
        self.on_status = None
        # Ed25519 身份密钥（长期），首次生成后保存本地，TOFU 信任对端公钥
        self.identity = self._load_or_create_identity()
        self._known_identity_pubs = self._load_known_pubs()  # peer -> ed25519_pub(bytes)

    def _identity_path(self):
        return os.path.join(_ROOT, ".lt_identity.key")

    def _known_pubs_path(self):
        return os.path.join(_ROOT, ".lt_known_pubs.json")

    def _load_or_create_identity(self):
        """加载或生成 Ed25519 长期身份密钥。"""
        path = self._identity_path()
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    priv = f.read()
                if len(priv) == 32:
                    return IdentityKey(private_bytes=priv)
        except Exception:
            pass
        ident = IdentityKey()
        try:
            with open(path, "wb") as f:
                f.write(ident.export_private())
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception as e:
            print(f"[identity] 保存身份密钥失败: {e}")
        return ident

    def _load_known_pubs(self):
        """加载 TOFU 已知对端身份公钥 {username: b64(pub)}。"""
        path = self._known_pubs_path()
        try:
            if os.path.exists(path):
                import json
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return {u: base64.b64decode(p) for u, p in raw.items()}
        except Exception:
            pass
        return {}

    def _save_known_pubs(self):
        try:
            import json
            data = {u: base64.b64encode(p).decode("ascii") for u, p in self._known_identity_pubs.items()}
            with open(self._known_pubs_path(), "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[identity] 保存已知公钥失败: {e}")

    def set_self_username(self, username: str):
        self.username = username or ""

    def set_transport(self, transport):
        """注册发送私聊文本函数，签名 transport(target:str, text:str)。"""
        self._transport = transport

    def _emit_status(self, text):
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _emit_alert(self, peer, kind, msg):
        if self.on_security_alert:
            try:
                self.on_security_alert(peer, kind, msg)
            except Exception:
                pass

    def public_key_message(self) -> str:
        ecdh_pub = self.ecdh.get_public_key_bytes()
        ecdh_b64 = base64.b64encode(ecdh_pub).decode("ascii")
        ident_pub = self.identity.export_public()
        ident_b64 = base64.b64encode(ident_pub).decode("ascii")
        sig = self.identity.sign(ecdh_pub)
        sig_b64 = base64.b64encode(sig).decode("ascii")
        # 三段：ECDH公钥.身份公钥.签名
        return KEY_PREFIX + ecdh_b64 + "." + ident_b64 + "." + sig_b64

    def send_my_key(self, peer: str) -> bool:
        if not peer or not self._transport:
            return False
        with self._lock:
            self.sent_keys.add(peer)  # 先标记，防止同步 transport 嵌套时递归重发
        try:
            self._transport(peer, self.public_key_message())
            return True
        except Exception as e:
            self._emit_status(f"发送公钥失败: {e}")
            return False

    def _handle_peer_key(self, peer: str, pub_b64: str):
        try:
            # 新格式三段：ecdh_pub.identity_pub.signature（旧格式两段直接信任，兼容）
            parts = pub_b64.split(".")
            peer_ident_pub = None
            signature = None
            if len(parts) == 3:
                ecdh_b64, ident_b64, sig_b64 = parts
                peer_pub = base64.b64decode(ecdh_b64.strip())
                peer_ident_pub = base64.b64decode(ident_b64.strip())
                signature = base64.b64decode(sig_b64.strip())
            else:
                # 旧版客户端不支持验签，降级信任（兼容）
                peer_pub = base64.b64decode(pub_b64.strip())

            if len(peer_pub) != PUBLIC_KEY_SIZE:
                raise ValueError(f"公钥长度错误: {len(peer_pub)}")

            # Ed25519 验签（新格式才有）
            if peer_ident_pub and signature:
                if len(peer_ident_pub) != IDENTITY_KEY_SIZE:
                    raise IdentityError(f"对端身份公钥长度错误: {len(peer_ident_pub)}")
                # 验证签名：用对端身份公钥验证 ECDH 公钥的签名
                IdentityKey.verify(peer_ident_pub, peer_pub, signature)
                # TOFU：首次见则记录，再次见则对比，不一致=中间人攻击警告
                known = self._known_identity_pubs.get(peer)
                if known is None:
                    self._known_identity_pubs[peer] = peer_ident_pub
                    self._save_known_pubs()
                    self._emit_status(f"首次验证 {peer} 身份，已记录公钥指纹")
                elif known != peer_ident_pub:
                    self._emit_alert(peer, "MITM_WARNING",
                        f"⚠️ {peer} 的身份公钥已变更！可能遭遇中间人攻击。如非本人换设备，请勿信任！")

            aes_key = self.ecdh.derive_shared_key(peer_pub)
            with self._lock:
                self.sessions[peer] = {
                    "key": aes_key,
                    "send": CryptoPacket(aes_key, mode=self.mode),
                    "recv": CryptoPacket(aes_key, mode=self.mode),
                    "ready": True,
                    "verified": bool(peer_ident_pub),
                }
            self._emit_status(f"已与 {peer} 建立加密通道" + ("（已验签）" if peer_ident_pub else "（未验签-旧版）"))
            if self.on_session_ready:
                try:
                    self.on_session_ready(peer)
                except Exception:
                    pass
            if peer not in self.sent_keys:
                self.send_my_key(peer)
            self._flush_pending(peer)
        except IdentityError as e:
            self._emit_alert(peer, "IdentityError", f"身份验签失败: {e}")
        except Exception as e:
            self._emit_alert(peer, "KeyError", f"公钥协商失败: {e}")

    def _flush_pending(self, peer: str):
        with self._lock:
            queue = self.pending.pop(peer, [])
            sess_ready = bool(self.sessions.get(peer, {}).get("ready"))
        if not sess_ready:
            return
        for plain_text in queue:
            try:
                enc = self.encrypt_text(peer, plain_text)
                if self._transport:
                    self._transport(peer, enc)
            except Exception as e:
                self._emit_status(f"补发加密消息失败: {e}")

    def is_ready(self, peer: str) -> bool:
        with self._lock:
            return bool(self.sessions.get(peer, {}).get("ready"))

    def get_file_crypto(self, peer: str):
        """文件加密为可选能力：手机端若有 file_crypto 则返回，否则 None。"""
        try:
            from file_crypto import FileCrypto  # 手机端可选
        except Exception:
            return None
        with self._lock:
            sess = self.sessions.get(peer)
            if not sess or not sess["ready"]:
                return None
            key = sess["key"]
        return FileCrypto(key, mode=self.mode)

    def ensure_key(self, peer: str) -> bool:
        with self._lock:
            ready = bool(self.sessions.get(peer, {}).get("ready"))
            sent = peer in self.sent_keys
        if not sent:
            self.send_my_key(peer)
        return ready

    def encrypt_text(self, peer: str, plain_text: str) -> str:
        with self._lock:
            sess = self.sessions.get(peer)
            if not sess or not sess["ready"]:
                raise RuntimeError(f"与 {peer} 的加密通道尚未建立")
            packet = sess["send"].encrypt_packet(plain_text.encode("utf-8"))
        return ENC_PREFIX + base64.b64encode(packet).decode("ascii")

    def decrypt_text(self, peer: str, enc_b64: str) -> str:
        packet = base64.b64decode(enc_b64.strip())
        with self._lock:
            sess = self.sessions.get(peer)
            if not sess or not sess["ready"]:
                raise RuntimeError(f"与 {peer} 的加密通道尚未建立，无法解密")
            payload, _seq = sess["recv"].decrypt_packet(packet)
        return payload.decode("utf-8", errors="replace")

    def send_encrypted(self, peer: str, plain_text: str):
        if not self._transport:
            return False
        with self._lock:
            ready = bool(self.sessions.get(peer, {}).get("ready"))
        if not ready:
            # 先入队再发起公钥交换：兼容同步 transport 嵌套回调（flush 可能早于本函数返回）
            with self._lock:
                self.pending.setdefault(peer, []).append(plain_text)
            self.ensure_key(peer)
            self._emit_status(f"正在与 {peer} 建立加密通道，消息将自动加密发送")
            return "waiting_key"
        try:
            enc = self.encrypt_text(peer, plain_text)
            self._transport(peer, enc)
            return True
        except Exception as e:
            self._emit_status(f"加密发送失败: {e}")
            return False

    def wrap_on_message(self, original_on_message):
        """包装原有消息回调：拦截 KEY/ENC 信令、解密密文，普通消息原样透传。"""
        def wrapped(payload):
            try:
                if not isinstance(payload, dict):
                    return original_on_message(payload)
                text = payload.get("text", "")
                sender = payload.get("sender", "") or ""
                if not isinstance(text, str):
                    return original_on_message(payload)

                # 公共频道格式 "username: content"：sender 为空时剥出用户名和实际内容
                sig_text = text
                if not sender and ": " in text:
                    _s, _c = text.split(": ", 1)
                    sender, sig_text = _s, _c

                is_key = sig_text.startswith(KEY_PREFIX)
                is_enc = sig_text.startswith(ENC_PREFIX)

                if is_key or is_enc:
                    if self.username and sender == self.username:
                        return  # 忽略自己信令回声
                    if is_key:
                        self._handle_peer_key(sender, sig_text[len(KEY_PREFIX):])
                        return
                    try:
                        plain = self.decrypt_text(sender, sig_text[len(ENC_PREFIX):])
                    except TamperError as e:
                        self._emit_alert(sender, "TamperError", str(e))
                        return
                    except ReplayError as e:
                        self._emit_alert(sender, "ReplayError", str(e))
                        return
                    except Exception as e:
                        self._emit_alert(sender, "DecryptError", str(e))
                        return
                    new_payload = dict(payload)
                    # 公共格式需保留 "sender: " 前缀，私聊直接放明文
                    new_payload["text"] = (sender + ": " + plain) if (not payload.get("sender") and ": " in text) else plain
                    new_payload["encrypted"] = True
                    return original_on_message(new_payload)

                return original_on_message(payload)
            except Exception as e:
                self._emit_alert("", "WrapError", f"加密回调异常(已放行原消息): {e}")
                try:
                    return original_on_message(payload)
                except Exception:
                    return

        return wrapped

# client/ui/voice_udp.py - UDP语音通话客户端（服务端中转，支持IPv4/IPv6）
import socket
import threading
import struct
import time
from ..lang import t

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
FRAME_DURATION = 0.06
FRAME_SIZE = int(SAMPLE_RATE * FRAME_DURATION * CHANNELS * SAMPLE_WIDTH)

DEFAULT_RELAY_PORT = 5005


class VoiceCall:
    """UDP语音通话：采集麦克风发送到中转服务器，接收混音播放。

    兼容接口：
      VoiceCall(server_ip=..., room_id=..., on_status_change=..., on_packet_loss=...)
      vc.bind_socket() -> local_port
      vc.start() -> bool
      vc.stop()
      vc._running, vc.is_muted(), vc.set_muted()
    """

    def __init__(self, server_ip, room_id, on_status_change=None, on_packet_loss=None,
                 server_port=DEFAULT_RELAY_PORT, input_device_index=None):
        self.server_ip = server_ip
        self.server_port = server_port
        self.room_id = room_id
        self.on_status_change = on_status_change
        self.on_packet_loss = on_packet_loss
        self.input_device_index = input_device_index
        self._running = False
        self._muted = False
        self._speaker_on = True
        self.sock = None
        self.local_udp_port = 0
        self._seq = 0
        self.recv_thread = None
        self.send_thread = None
        self._stream_in = None
        self._stream_out = None
        self._py_audio = None
        self._audio_term = None
        self.packets_sent = 0
        self.packets_received = 0
        self.packets_lost = 0
        self._expected_seq = None
        self._loss_timer = None
        self._stop_event = threading.Event()
        self.last_error = ""

    def _is_ipv6(self):
        try:
            socket.inet_pton(socket.AF_INET6, self.server_ip)
            return True
        except (socket.error, OSError):
            return False

    def bind_socket(self):
        """创建UDP socket并绑定本地端口，返回端口号（避免临时socket竞态）。"""
        if self._is_ipv6():
            self.sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self._is_ipv6():
            self.sock.bind(("::", 0))
        else:
            self.sock.bind(("0.0.0.0", 0))
        self.local_udp_port = self.sock.getsockname()[1]
        return self.local_udp_port

    def start(self):
        """开始语音通话：启动收发线程。成功返回True，失败返回False（原因见 self.last_error）。"""
        if self.sock is None:
            self.bind_socket()
        self._running = True
        try:
            self._init_audio()
        except Exception as e:
            print(t("[语音] 音频初始化失败: {e}").format(e=e))
            self.last_error = str(e)
            self._running = False
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            if self.on_status_change:
                self.on_status_change(t("音频启动失败"))
            return False
        if not self._running:
            return False
        self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.send_thread = threading.Thread(target=self._send_loop, daemon=True)
        self.recv_thread.start()
        self.send_thread.start()
        self._start_loss_report()
        if self.on_status_change:
            self.on_status_change(t("通话中"))
        return True

    def stop(self):
        self._running = False
        # 丢包统计是普通 Thread（没有 cancel），用 Event 唤醒并 join 退出
        try:
            if getattr(self, "_stop_event", None):
                self._stop_event.set()
        except Exception:
            pass
        if self._loss_timer:
            loss_thread = self._loss_timer
            self._loss_timer = None
            try:
                loss_thread.join(timeout=1.5)
            except Exception:
                pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        for stream in [self._stream_in, self._stream_out]:
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        self._stream_in = None
        self._stream_out = None
        try:
            if self._audio_term:
                self._audio_term.terminate()
        except Exception:
            pass
        self._audio_term = None
        self._py_audio = None
        if self.on_status_change:
            self.on_status_change(t("已结束"))

    def set_muted(self, muted):
        self._muted = muted

    def is_muted(self):
        return self._muted

    def set_speaker_on(self, on):
        """开关扬声器播放（关闭后仍接收但不播放）。"""
        self._speaker_on = bool(on)

    def set_input_device(self, index):
        """设置输入设备索引（下次 start 生效）。"""
        self.input_device_index = index

    def is_speaker_on(self):
        return self._speaker_on

    def get_loss_rate(self):
        total = self.packets_received + self.packets_lost
        return self.packets_lost / total if total > 0 else 0.0

    def _start_loss_report(self):
        def tick():
            while self._running:
                if self.on_packet_loss:
                    try:
                        self.on_packet_loss(self.get_loss_rate())
                    except Exception:
                        pass
                # 用 Event.wait 代替 time.sleep，stop 时可立即唤醒退出
                if self._stop_event.wait(1.0):
                    break
        self._loss_timer = threading.Thread(target=tick, daemon=True)
        self._loss_timer.start()

    def _init_audio(self):
        # 音频后端由各平台自己的 audio_backend 提供（PC=pyaudio，Android=原生），接口一致
        from .audio_backend import create_audio_streams
        self._stream_in, self._stream_out, self._audio_term = create_audio_streams(
            SAMPLE_RATE, CHANNELS, FRAME_SIZE,
            input_device_index=getattr(self, "input_device_index", None))

    def _send_loop(self):
        room_bytes = self.room_id.encode("utf-8")
        room_hdr = struct.pack("!H", len(room_bytes)) + room_bytes
        while self._running:
            try:
                if self._muted or not self._stream_in:
                    time.sleep(FRAME_DURATION)
                    continue
                data = self._stream_in.read(FRAME_SIZE)
                if not data:
                    continue
                packet = room_hdr + struct.pack("!I", self._seq) + data
                if self._is_ipv6():
                    self.sock.sendto(packet, (self.server_ip, self.server_port, 0, 0))
                else:
                    self.sock.sendto(packet, (self.server_ip, self.server_port))
                self._seq = (self._seq + 1) & 0xFFFFFFFF
                self.packets_sent += 1
            except Exception as e:
                if self._running:
                    print(t("[语音] 发送异常: {e}").format(e=e))
                time.sleep(0.05)

    def _recv_loop(self):
        while self._running:
            try:
                self.sock.settimeout(0.5)
                data, addr = self.sock.recvfrom(65536)
                if len(data) < 4:
                    continue
                seq = struct.unpack("!I", data[:4])[0]
                pcm_data = data[4:]
                self._update_stats(seq)
                if self._stream_out and pcm_data and self._speaker_on:
                    self._stream_out.write(pcm_data)
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(t("[语音] 接收异常: {e}").format(e=e))

    def _update_stats(self, seq):
        self.packets_received += 1
        if self._expected_seq is None:
            self._expected_seq = (seq + 1) & 0xFFFFFFFF
            return
        diff = (seq - self._expected_seq) & 0xFFFFFFFF
        if diff == 0:
            self._expected_seq = (self._expected_seq + 1) & 0xFFFFFFFF
        elif diff < 0x80000000:
            self.packets_lost += diff
            self._expected_seq = (seq + 1) & 0xFFFFFFFF
        # diff >= 0x80000000: 乱序/重复包，不计丢包


# ==================== 语音信令文本 ====================
def build_voice_start(caller, callee):
    return f"[VOICE_START]:{caller}:{callee}:"

def build_voice_accept(caller, callee, port=0):
    return f"[VOICE_ACCEPT]:{caller}:{callee}:{port}"

def build_voice_reject(caller, callee):
    return f"[VOICE_REJECT]:{caller}:{callee}:"

def build_voice_end(caller, callee):
    return f"[VOICE_END]:{caller}:{callee}:"

def build_room_start(room_id, host):
    return f"[VOICE_ROOM_START]:{room_id}:{host}:"

def build_room_join(room_id, username, port=0):
    return f"[VOICE_ROOM_JOIN]:{room_id}:{username}:{port}"

def build_room_leave(room_id, username):
    return f"[VOICE_ROOM_LEAVE]:{room_id}:{username}:"

def build_room_end(room_id, host=""):
    return f"[VOICE_ROOM_END]:{room_id}:{host}:"


def parse_voice_signal(text):
    """解析语音信令。返回 (sig_type, caller, callee, extra) 或 None。
    sig_type 为小写短名：start/accept/reject/end/room_start/room_join/room_leave/room_end。
    """
    if not text or not text.startswith("[VOICE_"):
        return None
    try:
        bracket_end = text.index("]")
        raw_type = text[1:bracket_end]
        # VOICE_START -> start, VOICE_ROOM_START -> room_start
        sig_type = raw_type[len("VOICE_"):].lower()
        rest = text[bracket_end + 1:]
        if rest.startswith(":"):
            rest = rest[1:]
        parts = rest.split(":", 2)
        caller = parts[0] if len(parts) > 0 else ""
        callee = parts[1] if len(parts) > 1 else ""
        extra = parts[2] if len(parts) > 2 else ""
        return sig_type, caller, callee, extra
    except Exception:
        return None

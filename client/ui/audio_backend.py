# client/ui/audio_backend.py - 移动端音频采集/播放后端（运行时按平台自动选择）
#
# 移动端程序 mobile_main.py 有两种运行环境：
#   1) 打包成 Android APK 在真机运行 -> 用 pyjnius 调原生 AudioRecord/AudioTrack
#   2) 在电脑桌面用 flet 直接运行调试（Windows/Linux/macOS）-> 桌面后端
#      桌面优先 sounddevice（自带 PortAudio、兼容 Python 3.14 等新版本），pyaudio 回退。
# 各后端都在函数内延迟 import：APK 不加载桌面库，桌面运行也不 import jnius。
#
# 统一接口（voice_udp 无需区分平台）：
#   create_audio_streams(sample_rate, channels, frame_size_bytes, input_device_index=None)
#       -> (输入, 输出, 终止器)   终止器可能为 None
#   AudioInput.read(num_bytes)->bytes；AudioOutput.write(data)；两者都有 close()
#   list_input_devices() -> [(索引, 名称)]
import threading
from ..lang import t


class AudioUnavailableError(Exception):
    """音频设备不可用（无录音权限/麦克风被占用/后端缺失）。"""


def is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


# ==================== 桌面：sounddevice（首选） ====================
class _SDInput:
    def __init__(self, stream, bytes_per_frame):
        self._s = stream
        self._bpf = bytes_per_frame
        self._s.start()

    def read(self, num_bytes):
        frames = max(1, int(num_bytes) // self._bpf)
        data, _overflow = self._s.read(frames)
        return bytes(data)

    def close(self):
        for fn in ("stop", "close"):
            try:
                getattr(self._s, fn)()
            except Exception:
                pass


class _SDOutput:
    def __init__(self, stream):
        self._s = stream
        self._lock = threading.Lock()
        self._s.start()

    def write(self, data):
        if not data:
            return
        with self._lock:
            try:
                self._s.write(data)
            except Exception:
                pass

    def close(self):
        for fn in ("stop", "close"):
            try:
                getattr(self._s, fn)()
            except Exception:
                pass


def _create_sounddevice(sample_rate, channels, frame_size_bytes, input_device_index=None):
    try:
        import sounddevice as sd
    except Exception as e:
        raise AudioUnavailableError(
            t("缺少 sounddevice，请运行: python -m pip install sounddevice。原始错误: {e}").format(e=e)
        )
    bpf = 2 * channels
    block = max(1, int(frame_size_bytes) // bpf)
    in_dev = input_device_index if input_device_index is not None else None
    try:
        stream_in = sd.RawInputStream(samplerate=sample_rate, blocksize=block,
                                      dtype="int16", channels=channels, device=in_dev)
    except Exception as e:
        raise AudioUnavailableError(t("无法打开麦克风：{e}").format(e=e))
    try:
        stream_out = sd.RawOutputStream(samplerate=sample_rate, blocksize=block,
                                        dtype="int16", channels=channels, device=None)
    except Exception as e:
        try:
            stream_in.close()
        except Exception:
            pass
        raise AudioUnavailableError(t("无法打开扬声器：{e}").format(e=e))
    return _SDInput(stream_in, bpf), _SDOutput(stream_out), None


def _list_sounddevice():
    import sounddevice as sd
    out = []
    for i, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            out.append((i, str(info.get("name", t("设备{i}").format(i=i)))))
    return out


# ==================== 桌面：pyaudio（回退） ====================
class _PyAudioInput:
    def __init__(self, stream, pa, bytes_per_frame):
        self._stream = stream
        self._pa = pa
        self._bytes_per_frame = bytes_per_frame

    def read(self, num_bytes):
        frames = max(1, int(num_bytes) // self._bytes_per_frame)
        return self._stream.read(frames, exception_on_overflow=False)

    def close(self):
        for fn in ("stop_stream", "close"):
            try:
                getattr(self._stream, fn)()
            except Exception:
                pass


class _PyAudioOutput:
    def __init__(self, stream, pa):
        self._stream = stream
        self._pa = pa
        self._lock = threading.Lock()

    def write(self, data):
        if not data:
            return
        with self._lock:
            try:
                self._stream.write(data, exception_on_underflow=False)
            except Exception:
                pass

    def close(self):
        for fn in ("stop_stream", "close"):
            try:
                getattr(self._stream, fn)()
            except Exception:
                pass


def _create_pyaudio(sample_rate, channels, frame_size_bytes, input_device_index=None):
    try:
        import pyaudio
    except Exception as e:
        raise AudioUnavailableError(t("桌面端缺少音频库（sounddevice/pyaudio 均无）：{e}").format(e=e))
    pa = pyaudio.PyAudio()
    bpf = 2 * channels
    frames_per_buffer = max(1, int(frame_size_bytes) // bpf)
    try:
        kw = dict(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                  input=True, frames_per_buffer=frames_per_buffer)
        if input_device_index is not None:
            kw["input_device_index"] = input_device_index
        stream_in = pa.open(**kw)
    except Exception as e:
        try:
            pa.terminate()
        except Exception:
            pass
        raise AudioUnavailableError(t("无法打开麦克风：{e}").format(e=e))
    try:
        stream_out = pa.open(format=pyaudio.paInt16, channels=channels, rate=sample_rate,
                             output=True, frames_per_buffer=frames_per_buffer)
    except Exception as e:
        try:
            stream_in.close()
        except Exception:
            pass
        try:
            pa.terminate()
        except Exception:
            pass
        raise AudioUnavailableError(t("无法打开扬声器：{e}").format(e=e))
    return _PyAudioInput(stream_in, pa, bpf), _PyAudioOutput(stream_out, pa), pa


def _create_desktop(sample_rate, channels, frame_size_bytes, input_device_index=None):
    try:
        import sounddevice  # noqa: F401
        return _create_sounddevice(sample_rate, channels, frame_size_bytes, input_device_index)
    except ImportError:
        return _create_pyaudio(sample_rate, channels, frame_size_bytes, input_device_index)


# ============================ Android：pyjnius 原生 ============================
class _AndroidInput:
    def __init__(self, recorder, frame_bytes):
        self._recorder = recorder
        self._frame_bytes = frame_bytes
        from jnius import jarray
        self._buf = jarray.zeros(frame_bytes, "b")
        self._recorder.startRecording()

    def read(self, num_bytes):
        need = self._frame_bytes
        n = int(self._recorder.read(self._buf, 0, need))
        if n <= 0:
            return b"\x00" * need
        try:
            return bytes(bytearray(self._buf[:n]))
        except Exception:
            return bytes((self._buf[i] & 0xFF) for i in range(n))

    def close(self):
        for fn in ("stop", "release"):
            try:
                getattr(self._recorder, fn)()
            except Exception:
                pass


class _AndroidOutput:
    def __init__(self, track):
        self._track = track
        from jnius import jarray
        self._jarray = jarray
        self._track.play()
        self._lock = threading.Lock()

    def write(self, data):
        if not data:
            return
        with self._lock:
            buf = self._jarray.zeros(len(data), "b")
            ba = bytearray(data)
            for i, v in enumerate(ba):
                buf[i] = v if v < 128 else v - 256
            self._track.write(buf, 0, len(data))

    def close(self):
        for fn in ("stop", "release"):
            try:
                getattr(self._track, fn)()
            except Exception:
                pass


def _ensure_record_permission():
    try:
        from jnius import autoclass
        activity = None
        try:
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
        except Exception:
            activity = None
        if activity is None:
            try:
                activity = autoclass("com.example.serious_python.MainActivity").mActivity
            except Exception:
                return
        perm = "android.permission.RECORD_AUDIO"
        try:
            granted = activity.checkSelfPermission(perm)
            PM = autoclass("android.content.pm.PackageManager")
            if granted == PM.PERMISSION_GRANTED:
                return
        except Exception:
            pass
        try:
            activity.requestPermissions([perm], 1001)
        except Exception:
            pass
    except Exception:
        pass


def _create_android(sample_rate, channels, frame_size_bytes):
    try:
        from jnius import autoclass
    except Exception as e:
        raise AudioUnavailableError(t("Android pyjnius 不可用：{e}").format(e=e))
    _ensure_record_permission()
    AudioRecord = autoclass("android.media.AudioRecord")
    AudioTrack = autoclass("android.media.AudioTrack")
    AudioFormat = autoclass("android.media.AudioFormat")
    AudioManager = autoclass("android.media.AudioManager")
    MediaRecorder = autoclass("android.media.MediaRecorder")
    in_ch = AudioFormat.CHANNEL_IN_MONO if channels == 1 else AudioFormat.CHANNEL_IN_STEREO
    out_ch = AudioFormat.CHANNEL_OUT_MONO if channels == 1 else AudioFormat.CHANNEL_OUT_STEREO
    enc = AudioFormat.ENCODING_PCM_16BIT
    try:
        min_rec = int(AudioRecord.getMinBufferSize(sample_rate, in_ch, enc))
        if min_rec <= 0:
            min_rec = frame_size_bytes * 4
        recorder = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                               sample_rate, in_ch, enc, max(min_rec, frame_size_bytes * 4))
        if recorder.getState() != AudioRecord.STATE_INITIALIZED:
            raise AudioUnavailableError(t("麦克风初始化失败（可能未授予录音权限）"))
    except AudioUnavailableError:
        raise
    except Exception as e:
        raise AudioUnavailableError(t("无法打开麦克风：{e}").format(e=e))
    try:
        min_play = int(AudioTrack.getMinBufferSize(sample_rate, out_ch, enc))
        if min_play <= 0:
            min_play = frame_size_bytes * 4
        track = AudioTrack(AudioManager.STREAM_MUSIC, sample_rate, out_ch, enc,
                           max(min_play, frame_size_bytes * 4), AudioTrack.MODE_STREAM)
    except Exception as e:
        try:
            recorder.release()
        except Exception:
            pass
        raise AudioUnavailableError(t("无法打开扬声器：{e}").format(e=e))
    return _AndroidInput(recorder, frame_size_bytes), _AndroidOutput(track), None


# ============================ 统一入口 ============================
def create_audio_streams(sample_rate, channels, frame_size_bytes, input_device_index=None):
    if is_android():
        return _create_android(sample_rate, channels, frame_size_bytes)
    return _create_desktop(sample_rate, channels, frame_size_bytes, input_device_index)


def list_input_devices():
    if is_android():
        return [(-1, t("手机内置麦克风"))]
    try:
        try:
            import sounddevice
            devs = _list_sounddevice()
            if devs:
                return devs
        except Exception:
            pass
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            devices = []
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    devices.append((i, str(info.get("name", t("设备{i}").format(i=i)))))
            return devices if devices else [(-1, t("系统默认麦克风"))]
        finally:
            try:
                pa.terminate()
            except Exception:
                pass
    except Exception:
        return [(-1, t("系统默认麦克风"))]

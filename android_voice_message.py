# -*- coding: utf-8 -*-
"""
android_voice_message.py — LanTalk 语音消息（按住说话）录音与播放

不走实时通话（之前 AudioRecord 实时流在荣耀上录到静音），改成非实时文件：
  - 录音：MediaRecorder 录 AAC/m4a（普通录音源 MIC，不依赖通话模式，稳定）
  - 播放：MediaPlayer
录音文件走现有文件传输通道发送；接收方下载完根据文件名识别为语音消息，点击播放。

桌面端兜底：用 sounddevice 录 wav（测试用），播放用系统默认播放器/简单实现。
"""
import os
import sys
import time
import threading


def is_android():
    if "android" in sys.modules or hasattr(sys, "getandroidapilevel"):
        return True
    if os.environ.get("ANDROID_ROOT") == "/system":
        return True
    if os.path.exists("/system/bin/app_process"):
        return True
    try:
        import jnius  # noqa
        return True
    except Exception:
        return False


# ---------- 录音输出目录 ----------
def voice_tmp_dir():
    d = os.path.join(os.getcwd(), "voice_tmp")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


# =====================================================================
# Android 实现
# =====================================================================
class _AndroidRecorder:
    """MediaRecorder 录音。"""
    def __init__(self):
        self._rec = None
        self._path = None
        self._started_at = 0.0
        self._duration = 0.0

    def start(self, path):
        from jnius import autoclass
        MediaRecorder = autoclass("android.media.MediaRecorder")
        AudioSource = autoclass("android.media.MediaRecorder$AudioSource")
        OutputFormat = autoclass("android.media.MediaRecorder$OutputFormat")
        AudioEncoder = autoclass("android.media.MediaRecorder$AudioEncoder")

        rec = MediaRecorder()
        rec.setAudioSource(AudioSource.MIC)
        rec.setOutputFormat(OutputFormat.MPEG_4)
        rec.setAudioEncoder(AudioEncoder.AAC)
        try:
            rec.setAudioSamplingRate(16000)
            rec.setAudioEncodingBitRate(16000)
            rec.setAudioChannels(1)
        except Exception:
            pass
        rec.setOutputFile(path)
        rec.prepare()
        rec.start()
        self._rec = rec
        self._path = path
        self._started_at = time.time()
        self._duration = 0.0
        return True

    def stop(self):
        """停止并返回时长(秒)。"""
        dur = time.time() - self._started_at
        rec, self._rec = self._rec, None
        path, self._path = self._path, None
        if rec is not None:
            for fn in ("stop", "release"):
                try:
                    getattr(rec, fn)()
                except Exception:
                    pass
        self._duration = dur
        return dur

    def get_level(self):
        """实时录音音量 0..1（MediaRecorder.getMaxAmplitude）。"""
        rec = self._rec
        if rec is None:
            return 0.0
        try:
            amp = int(rec.getMaxAmplitude())  # 0..32767
            return max(0.0, min(1.0, amp / 12000.0))
        except Exception:
            return 0.0

    def cancel(self):
        rec, self._rec = self._rec, None
        path, self._path = self._path, None
        if rec is not None:
            for fn in ("stop", "release"):
                try:
                    getattr(rec, fn)()
                except Exception:
                    pass
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


class _AndroidPlayer:
    """MediaPlayer 播放，支持 on_done 回调。"""
    def __init__(self):
        self._mp = None
        self._on_done = None
        self._lock = threading.Lock()

    def _make_listener(self):
        from jnius import PythonJavaClass, java_method
        outer = self

        class _CB(PythonJavaClass):
            __javainterfaces__ = ["android/media/MediaPlayer$OnCompletionListener"]

            @java_method("(Landroid/media/MediaPlayer;)V")
            def onCompletion(self, mp):
                cb = outer._on_done
                outer._on_done = None
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
        return _CB()

    def play(self, path, on_done=None):
        self.stop()
        from jnius import autoclass
        MediaPlayer = autoclass("android.media.MediaPlayer")
        mp = MediaPlayer()
        mp.setDataSource(path)
        mp.prepare()
        self._on_done = on_done
        try:
            mp.setOnCompletionListener(self._make_listener())
        except Exception:
            pass
        mp.start()
        self._mp = mp
        return True

    def is_playing(self):
        try:
            return bool(self._mp.isPlaying())
        except Exception:
            return False

    def stop(self):
        mp, self._mp = self._mp, None
        if mp is not None:
            for fn in ("stop", "release"):
                try:
                    getattr(mp, fn)()
                except Exception:
                    pass


# =====================================================================
# 桌面端兜底（sounddevice 录 wav）
# =====================================================================
class _DesktopRecorder:
    def __init__(self):
        self._frames = []
        self._stream = None
        self._started_at = 0.0
        self._rate = 16000
        self._level = 0.0

    def start(self, path):
        import sounddevice as sd
        self._frames = []
        self._level = 0.0
        self._stream = sd.InputStream(samplerate=self._rate, channels=1, dtype="int16",
                                      callback=self._cb)
        self._stream.start()
        self._started_at = time.time()
        return True

    def _cb(self, indata, frames, time_info, status):
        try:
            self._frames.append(indata.copy())
            import numpy as _np
            x = indata.astype(_np.float32) / 32768.0
            rms = float(_np.sqrt(_np.mean(x * x)))
            # 峰值快速抬升、自然衰减
            self._level = max(self._level * 0.8, rms * 3.0)
        except Exception:
            pass

    def get_level(self):
        return max(0.0, min(1.0, self._level))

    def stop(self):
        import numpy as _np
        dur = time.time() - self._started_at
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        path = getattr(self, "_path", None)
        if path and self._frames:
            try:
                import wave
                data = _np.concatenate(self._frames, axis=0)
                pcm = data[:, 0] if getattr(data, "ndim", 2) == 2 else data
                pcm = pcm.astype(_np.int16)
                with wave.open(path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(self._rate)
                    wf.writeframes(pcm.tobytes())
            except Exception as e:
                print(f"[voice] desktop wav write failed: {e}")
        return dur

    def cancel(self):
        """取消录音：立即关掉输入流并删掉空文件，释放麦克风设备。"""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        path = getattr(self, "_path", None)
        if path:
            try:
                os.remove(path)
            except Exception:
                pass


# =====================================================================
# 对外统一接口
# =====================================================================
_recorder = None
_player = None


def start_recording():
    """开始录音，返回输出文件路径。失败抛异常。"""
    global _recorder
    ts = time.strftime("%Y%m%d_%H%M%S")
    ext = ".m4a" if is_android() else ".wav"
    path = os.path.join(voice_tmp_dir(), f"voice_{ts}_{int(time.time()*1000)%100000}{ext}")
    if is_android():
        _rec = _AndroidRecorder()
    else:
        _rec = _DesktopRecorder()
        _rec._path = path
    _rec.start(path)
    global _recorder
    _recorder = _rec
    return path


def stop_recording():
    """停止录音，返回 (时长秒, 文件路径)。"""
    global _recorder
    rec, _recorder = _recorder, None
    if rec is None:
        return 0.0, None
    # 先抓路径引用：stop() 内部可能把 _path 置 None
    path = getattr(rec, "_path", None)
    dur = rec.stop()
    return max(0.0, dur), path


def get_amplitude():
    """当前录音实时音量 0..1（供声纹动画）。"""
    global _recorder
    if _recorder is None:
        return 0.0
    try:
        return float(_recorder.get_level())
    except Exception:
        return 0.0


def cancel_recording():
    global _recorder
    rec, _recorder = _recorder, None
    if rec is not None and hasattr(rec, "cancel"):
        rec.cancel()


def play_voice(path, on_done=None):
    """播放语音文件。on_done: 播放结束回调。"""
    global _player
    if is_android():
        if _player is None:
            _player = _AndroidPlayer()
        else:
            _player.stop()
        return _player.play(path, on_done)
    # 桌面端：用系统默认方式播放（简单用 os.startfile）
    try:
        os.startfile(path)
    except Exception:
        pass
    if on_done:
        threading.Timer(1.0, on_done).start()
    return True


def stop_voice():
    global _player
    if _player is not None:
        _player.stop()


def is_playing():
    global _player
    if _player is not None:
        return _player.is_playing()
    return False

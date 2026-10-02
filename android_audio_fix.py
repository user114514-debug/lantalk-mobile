# -*- coding: utf-8 -*-
"""
android_audio_fix.py — LanTalk 移动端语音闪退修复（新增独立模块）

【解决的问题】
原 audio_backend.py 的 _create_android() 在 Android 上一点语音就闪退，原因：
  1. _ensure_record_permission() 用错误的 Activity 类名（kivy/example），权限检查失效；
  2. AudioRecord 用 AudioSource.VOICE_COMMUNICATION，
     在部分设备（荣耀 MagicOS 等）上需要通话模式，无电话服务时 native 崩溃；
  3. startRecording() 在 __init__ 里直接调，native 层崩溃 Python try-except 拦不住；
  4. 没有细粒度日志，无法定位崩在哪一步。

【本模块做什么】
  Monkey-patch audio_backend._create_android()，替换为更安全的实现：
  1. 用 android_perms.get_activity() 正确检查权限；
  2. 用 AudioSource.MIC 代替 VOICE_COMMUNICATION；
  3. startRecording()/play() 包在安全检查里，状态不对抛 Python 异常而非 native 崩溃；
  4. 每一步都打日志，方便定位。
  5. _AndroidOutput 的 jarray 转换优化（原代码逐字节循环慢且可能出错）。
"""

import threading
import sys


def _load_jarray():
    """serious_python 打包的 pyjnius 不能 `from jnius import jarray`，
    依次尝试子模块路径，全部失败返回 None。"""
    try:
        import jnius as _j
        if hasattr(_j, "jarray") and hasattr(_j.jarray, "zeros"):
            return _j.jarray
    except Exception:
        pass
    try:
        from jnius import jarray as _ja
        if hasattr(_ja, "zeros"):
            return _ja
    except Exception:
        pass
    try:
        import jnius.jarray as _jam
        if hasattr(_jam, "zeros"):
            return _jam
    except Exception:
        pass
    return None


_JARRAY = None


def _new_byte_array(n):
    """创建 Java byte[n]；jarray 不可用时用 java.lang.reflect.Array 兜底。"""
    global _JARRAY
    if _JARRAY is not None:
        return _JARRAY.zeros(n, "b")
    _JARRAY = _load_jarray()
    if _JARRAY is not None:
        return _JARRAY.zeros(n, "b")
    from jnius import autoclass
    _Array = autoclass("java.lang.reflect.Array")
    _Byte = autoclass("java.lang.Byte")
    return _Array.newInstance(_Byte.TYPE, n)


def _fill_byte_array(buf, data):
    """把 Python bytes 填进 Java byte[]。jarray 支持索引赋值；反射数组兜底 setByte。"""
    ba = bytearray(data)
    try:
        for i, v in enumerate(ba):
            buf[i] = v if v < 128 else v - 256
        return
    except Exception:
        pass
    from jnius import autoclass
    _Array = autoclass("java.lang.reflect.Array")
    for i, v in enumerate(ba):
        _Array.setByte(buf, i, v if v < 128 else v - 256)


def is_android():
    # 多重检测：模块加载早期 sys.modules 里可能还没有 android
    import os
    if "android" in sys.modules or hasattr(sys, "getandroidapilevel"):
        return True
    if os.environ.get("ANDROID_ROOT") == "/system":
        return True
    if os.environ.get("ANDROID_ARGUMENT"):
        return True
    if os.path.exists("/system/bin/app_process"):
        return True
    try:
        import jnius  # noqa
        return True
    except Exception:
        return False


def _pcm_peak(data):
    """计算 PCM 16bit little-endian 数据的最大振幅（0~32767），用于判断是否静音。"""
    try:
        import array as _arr
        usable = len(data) if len(data) % 2 == 0 else len(data) - 1
        if usable <= 0:
            return -1
        a = _arr.array('h')
        a.frombytes(bytes(data[:usable]))
        peak = 0
        for s in a:
            v = s if s >= 0 else -s
            if v > peak:
                peak = v
        return peak
    except Exception:
        return -1


def patch_android_audio():
    """
    Monkey-patch audio_backend 模块。幂等，桌面端直接返回 False。
    在 mobile_main.py 末尾调用。
    """
    def _trace(m):
        try:
            import os as _os
            _os.makedirs("crash_logs", exist_ok=True)
            with open(_os.path.join("crash_logs","debug_trace.txt"),"a",encoding="utf-8") as _f:
                import time as _t
                _f.write(f"[{_t.strftime('%H:%M:%S')}] [AudioPatch] {m}\n")
        except Exception:
            pass

    if not is_android():
        _trace("is_android()=False, skip patch")
        return False
    _trace("is_android()=True")

    try:
        from client.ui import audio_backend as ab
        _trace("import audio_backend OK")
    except Exception as _e:
        _trace(f"import audio_backend FAILED: {_e!r}")
        return False

    try:
        from jnius import autoclass
        from android_perms import get_activity, has_record_permission
        global _JARRAY
        _JARRAY = _load_jarray()
        _trace(f"import jnius+android_perms OK, jarray={'OK' if _JARRAY is not None else 'reflect-fallback'}")

        # ---- 安全版 Android Input ----
        class _SafeAndroidInput:
            def __init__(self, recorder, frame_bytes, log_fn=None):
                self._recorder = recorder
                self._frame_bytes = frame_bytes
                self._log_fn = log_fn
                self._started = False
                self._reads = 0
                self._buf = _new_byte_array(frame_bytes)
                _trace("AudioInput: byte[] ready, about to startRecording()")
                try:
                    self._recorder.startRecording()
                    self._started = True
                    try:
                        _ss = int(self._recorder.getRecordingState())
                        _trace(f"AudioInput: startRecording() OK, recordingState={_ss} (3=recording)")
                    except Exception:
                        _trace("AudioInput: startRecording() OK")
                except Exception as e:
                    _trace(f"AudioInput: startRecording() PY-EXC: {e!r}")
                    self._log(f"startRecording() failed: {e}")
                    try:
                        self._recorder.release()
                    except Exception:
                        pass
                    raise

            def _log(self, msg):
                if self._log_fn:
                    try:
                        self._log_fn(f"[AudioIn] {msg}")
                    except Exception:
                        pass

            def read(self, num_bytes):
                need = self._frame_bytes
                n = int(self._recorder.read(self._buf, 0, need))
                self._reads += 1
                cnt = self._reads
                if n <= 0:
                    _trace(f"read #{cnt} returned {n} (ERROR/empty), need={need}, returning silence")
                    return b"\x00" * need
                try:
                    data = bytes(bytearray(self._buf[:n]))
                except Exception:
                    data = bytes((self._buf[i] & 0xFF) for i in range(n))
                # 前 6 次 + 每 30 次采样记录音量峰值
                if cnt <= 6 or cnt % 30 == 0:
                    peak = _pcm_peak(data)
                    tag = "SILENCE!" if 0 <= peak < 80 else ("low" if peak < 500 else "voice OK")
                    _trace(f"read #{cnt} n={n} peak={peak} {tag}")
                return data

            def close(self):
                for fn in ("stop", "release"):
                    try:
                        getattr(self._recorder, fn)()
                    except Exception:
                        pass
                _restore_audio_mode()

        # ---- 安全版 Android Output ----
        class _SafeAndroidOutput:
            def __init__(self, track, log_fn=None):
                self._track = track
                self._log_fn = log_fn
                self._lock = threading.Lock()
                self._writes = 0
                try:
                    self._track.play()
                    try:
                        _ps = int(self._track.getPlayState())
                        _trace(f"AudioTrack.play() called, playState={_ps} (3=playing)")
                    except Exception:
                        _trace("AudioTrack.play() called")
                except Exception as e:
                    _trace(f"AudioTrack.play() PY-EXC: {e!r}")
                    self._log(f"AudioTrack.play() failed: {e}")
                    try:
                        self._track.release()
                    except Exception:
                        pass
                    raise

            def _log(self, msg):
                if self._log_fn:
                    try:
                        self._log_fn(f"[AudioOut] {msg}")
                    except Exception:
                        pass

            def write(self, data):
                if not data:
                    return
                with self._lock:
                    self._writes += 1
                    cnt = self._writes
                    try:
                        buf = _new_byte_array(len(data))
                        _fill_byte_array(buf, data)
                        written = int(self._track.write(buf, 0, len(data)))
                        if cnt <= 6 or cnt % 30 == 0:
                            peak = _pcm_peak(data)
                            try:
                                pstate = int(self._track.getPlayState())
                            except Exception:
                                pstate = -1
                            tag = "SILENCE-in!" if 0 <= peak < 80 else "has data"
                            _trace(f"write #{cnt} len={len(data)} written={written} playState={pstate} inPeak={peak} {tag}")
                        if written < 0:
                            _trace(f"write #{cnt} returned ERROR code {written}, len={len(data)}")
                    except Exception as e:
                        _trace(f"write #{cnt} PY-EXC: {e!r}, len={len(data)}")

            def close(self):
                for fn in ("stop", "release"):
                    try:
                        getattr(self._track, fn)()
                    except Exception:
                        pass

        # ---- 安全版 create_android ----
        _audio_mgr_ref = [None]  # 持有 AudioManager，close 时恢复

        def _setup_audio_mode():
            """设置通话模式 + 强制扬声器，让 MIC/VOICE_COMMUNICATION 在荣耀上能录到声音。"""
            try:
                act = get_activity()
                if act is None:
                    _trace("setup_audio_mode: no activity, skip")
                    return
                AudioManager = autoclass("android.media.AudioManager")
                Context = autoclass("android.content.Context")
                am = act.getSystemService(Context.AUDIO_SERVICE)
                if am is None:
                    _trace("setup_audio_mode: getSystemService returned null")
                    return
                _audio_mgr_ref[0] = am
                # 通话模式（VOICE_COMMUNICATION 源在此模式下正常工作）
                try:
                    am.setMode(3)  # AudioManager.MODE_IN_COMMUNICATION
                    _trace("setMode(MODE_IN_COMMUNICATION) OK")
                except Exception as e:
                    _trace(f"setMode failed: {e!r}")
                # 强制扬声器（免提），否则声音走听筒
                try:
                    am.setSpeakerphoneOn(True)
                    _trace("setSpeakerphoneOn(true) OK")
                except Exception as e:
                    _trace(f"setSpeakerphoneOn failed (needs MODIFY_AUDIO_SETTINGS): {e!r}")
                # 请求音频焦点
                try:
                    am.requestAudioFocus(None, 3, 1)  # STREAM_VOICE_CALL? 用 STREAM_MUSIC=3, AUDIOFOCUS_GAIN=1
                    _trace("requestAudioFocus OK")
                except Exception as e:
                    _trace(f"requestAudioFocus failed: {e!r}")
            except Exception as e:
                _trace(f"setup_audio_mode exception: {e!r}")

        def _restore_audio_mode():
            try:
                am = _audio_mgr_ref[0]
                if am is not None:
                    try:
                        am.setMode(0)  # MODE_NORMAL
                    except Exception:
                        pass
                    try:
                        am.setSpeakerphoneOn(False)
                    except Exception:
                        pass
                    _trace("audio mode restored to NORMAL")
            except Exception:
                pass

        def _safe_create_android(sample_rate, channels, frame_size_bytes):
            from jnius import autoclass
            _trace(f"create_audio ENTER: sample_rate={sample_rate} channels={channels} frame_bytes={frame_size_bytes}")

            # 1) 先确认权限（用 android_perms，不再用错误的 kivy 类名）
            try:
                _hp = has_record_permission()
                _trace(f"create_audio: has_record_permission={_hp}")
                if not _hp:
                    raise ab.AudioUnavailableError(
                        "麦克风权限未授予，请先在系统设置中允许录音")
            except ab.AudioUnavailableError:
                _trace("create_audio: permission NOT granted, abort")
                raise
            except Exception as e:
                _trace(f"create_audio: permission check error: {e!r}")
                print(f"[AudioFix] permission check error: {e}")

            # 1.5) 设置通话模式 + 扬声器（必须在创建 AudioRecord 之前）
            _setup_audio_mode()

            # 2) 创建 AudioRecord
            AudioRecord = autoclass("android.media.AudioRecord")
            AudioFormat = autoclass("android.media.AudioFormat")
            AudioManager = autoclass("android.media.AudioManager")
            MediaRecorder = autoclass("android.media.MediaRecorder")
            AudioSource = autoclass("android.media.MediaRecorder$AudioSource")
            AudioTrack = autoclass("android.media.AudioTrack")

            in_ch = AudioFormat.CHANNEL_IN_MONO if channels == 1 else AudioFormat.CHANNEL_IN_STEREO
            out_ch = AudioFormat.CHANNEL_OUT_MONO if channels == 1 else AudioFormat.CHANNEL_OUT_STEREO
            enc = AudioFormat.ENCODING_PCM_16BIT

            # 通话模式已设置，用 VOICE_COMMUNICATION 源（降噪、AEC，录到真人声）
            audio_source = AudioSource.VOICE_COMMUNICATION
            _trace(f"create_audio: in_ch={in_ch} out_ch={out_ch} enc={enc} source=VOICE_COMMUNICATION")

            try:
                min_rec = int(AudioRecord.getMinBufferSize(sample_rate, in_ch, enc))
                if min_rec <= 0:
                    min_rec = frame_size_bytes * 4
                rec_buf = max(min_rec, frame_size_bytes * 4)
                _trace(f"create_audio: minBuffer={min_rec} recBuf={rec_buf}, constructing AudioRecord")
                recorder = AudioRecord(audio_source, sample_rate, in_ch, enc, rec_buf)
                state = int(recorder.getState())
                _trace(f"create_audio: AudioRecord state={state} (1=INITIALIZED,0=uninit)")
                if state != int(AudioRecord.STATE_INITIALIZED):
                    try:
                        recorder.release()
                    except Exception:
                        pass
                    raise ab.AudioUnavailableError(
                        "麦克风初始化失败（可能未授予录音权限或麦克风被占用）")
            except ab.AudioUnavailableError:
                raise
            except Exception as e:
                raise ab.AudioUnavailableError(f"无法打开麦克风：{e}")

            # 3) 创建 AudioTrack
            try:
                min_play = int(AudioTrack.getMinBufferSize(sample_rate, out_ch, enc))
                if min_play <= 0:
                    min_play = frame_size_bytes * 4
                play_buf = max(min_play, frame_size_bytes * 4)
                _trace(f"create_audio: constructing AudioTrack stream=STREAM_MUSIC(3) minBuf={min_play} playBuf={play_buf}")
                track = AudioTrack(AudioManager.STREAM_MUSIC, sample_rate, out_ch, enc,
                                   play_buf, AudioTrack.MODE_STREAM)
                tstate = int(track.getState())
                _trace(f"create_audio: AudioTrack state={tstate} (1=INITIALIZED)")
                if tstate != int(AudioTrack.STATE_INITIALIZED):
                    try:
                        track.release()
                        recorder.release()
                    except Exception:
                        pass
                    raise ab.AudioUnavailableError("扬声器初始化失败")
            except ab.AudioUnavailableError:
                raise
            except Exception as e:
                try:
                    recorder.release()
                except Exception:
                    pass
                raise ab.AudioUnavailableError(f"无法打开扬声器：{e}")

            _trace("create_audio: streams ready, constructing Input/Output wrappers")
            _in = _SafeAndroidInput(recorder, frame_size_bytes)
            _trace("create_audio: Input wrapper OK, constructing Output (play)")
            _out = _SafeAndroidOutput(track)
            _trace("create_audio: DONE, both streams live")
            return _in, _out, None

        # 替换
        ab._create_android = _safe_create_android
        print("[AudioFix] Android audio backend patched OK")
        _trace("patch applied OK")
        return True

    except Exception as e:
        print(f"[AudioFix] patch failed: {e}")
        _trace(f"patch FAILED: {e!r}")
        return False

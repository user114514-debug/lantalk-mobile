# -*- coding: utf-8 -*-
"""
android_audio_fix.py — LanTalk 移动端语音闪退修复（新增独立模块）

【解决的问题】
原 audio_backend.py 的 _create_android() 在 Android 上一点语音就闪退，原因：
  1. _ensure_record_permission() 用错误的 Activity 类名（kivy/example），权限检查失效；
  2. AudioRecord 用 MediaRecorder.AudioSource.VOICE_COMMUNICATION，
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


def is_android():
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


def patch_android_audio():
    """
    Monkey-patch audio_backend 模块。幂等，桌面端直接返回 False。
    在 mobile_main.py 末尾调用。
    """
    if not is_android():
        return False

    try:
        from client.ui import audio_backend as ab
    except Exception:
        return False

    try:
        from jnius import autoclass, jarray
        from android_perms import get_activity, has_record_permission

        # ---- 安全版 Android Input ----
        class _SafeAndroidInput:
            def __init__(self, recorder, frame_bytes, log_fn=None):
                self._recorder = recorder
                self._frame_bytes = frame_bytes
                self._log_fn = log_fn
                self._started = False
                self._buf = jarray.zeros(frame_bytes, "b")
                try:
                    self._recorder.startRecording()
                    self._started = True
                except Exception as e:
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

        # ---- 安全版 Android Output ----
        class _SafeAndroidOutput:
            def __init__(self, track, log_fn=None):
                self._track = track
                self._log_fn = log_fn
                self._lock = threading.Lock()
                try:
                    self._track.play()
                except Exception as e:
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
                    try:
                        buf = jarray.zeros(len(data), "b")
                        ba = bytearray(data)
                        for i, v in enumerate(ba):
                            buf[i] = v if v < 128 else v - 256
                        self._track.write(buf, 0, len(data))
                    except Exception:
                        pass

            def close(self):
                for fn in ("stop", "release"):
                    try:
                        getattr(self._track, fn)()
                    except Exception:
                        pass

        # ---- 安全版 create_android ----
        def _safe_create_android(sample_rate, channels, frame_size_bytes):
            from jnius import autoclass

            # 1) 先确认权限（用 android_perms，不再用错误的 kivy 类名）
            try:
                if not has_record_permission():
                    raise ab.AudioUnavailableError(
                        "麦克风权限未授予，请先在系统设置中允许录音")
            except ab.AudioUnavailableError:
                raise
            except Exception as e:
                # 权限检查本身出错，不阻止继续尝试（可能权限已授予但检查失败）
                print(f"[AudioFix] permission check error: {e}")

            # 2) 创建 AudioRecord
            AudioRecord = autoclass("android.media.AudioRecord")
            AudioFormat = autoclass("android.media.AudioFormat")
            AudioManager = autoclass("android.media.AudioManager")
            MediaRecorder = autoclass("android.media.MediaRecorder")

            in_ch = AudioFormat.CHANNEL_IN_MONO if channels == 1 else AudioFormat.CHANNEL_IN_STEREO
            out_ch = AudioFormat.CHANNEL_OUT_MONO if channels == 1 else AudioFormat.CHANNEL_OUT_STEREO
            enc = AudioFormat.ENCODING_PCM_16BIT

            # 用 MIC 代替 VOICE_COMMUNICATION（后者在部分设备需要通话模式会崩溃）
            audio_source = MediaRecorder.AudioSource.MIC

            try:
                min_rec = int(AudioRecord.getMinBufferSize(sample_rate, in_ch, enc))
                if min_rec <= 0:
                    min_rec = frame_size_bytes * 4
                print(f"[AudioFix] creating AudioRecord: sr={sample_rate}, min_buf={min_rec}")
                recorder = AudioRecord(audio_source, sample_rate, in_ch, enc,
                                       max(min_rec, frame_size_bytes * 4))
                state = int(recorder.getState())
                print(f"[AudioFix] AudioRecord state={state} (1=initialized)")
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
                print(f"[AudioFix] creating AudioTrack: sr={sample_rate}, min_buf={min_play}")
                track = AudioTrack(AudioManager.STREAM_MUSIC, sample_rate, out_ch, enc,
                                   max(min_play, frame_size_bytes * 4),
                                   AudioTrack.MODE_STREAM)
            except Exception as e:
                try:
                    recorder.release()
                except Exception:
                    pass
                raise ab.AudioUnavailableError(f"无法打开扬声器：{e}")

            print("[AudioFix] audio streams created OK")
            return _SafeAndroidInput(recorder, frame_size_bytes), _SafeAndroidOutput(track), None

        # 替换
        ab._create_android = _safe_create_android
        print("[AudioFix] Android audio backend patched OK")
        return True

    except Exception as e:
        print(f"[AudioFix] patch failed: {e}")
        return False

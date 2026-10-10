# -*- coding: utf-8 -*-
"""
android_sound_boost.py — LanTalk 移动端音效强制最大声

【需求】
无论用户把手机系统音量调到多少，应用内的音效（点击、通知、来电等）都要最大声；
手机静音时不播放。

【实现原理】
Android 有多个独立音频流：STREAM_MUSIC（媒体）、STREAM_ALARM（闹钟）、
STREAM_RING（铃声）等。Flet 的 Audio 控件默认走 STREAM_MUSIC，受用户媒体音量控制。
本模块在播放音效前：
  1. 检测 ringer mode，SILENT/VIBRATE 视为静音，直接跳过播放；
  2. 非静音时，记录当前媒体音量，临时设为最大；
  3. 播放音效；
  4. 延迟 800ms 后恢复原媒体音量（音效通常 < 500ms，留余量）。

连续播放时用引用计数，避免频繁恢复/设置。
"""

import threading
import time

_am_ref = [None]       # AudioManager 单例
_original_vol = [None] # 保存的原媒体音量
_boost_count = [0]     # 当前有多少个音效在播放（引用计数）
_lock = threading.Lock()
_last_restore_time = [0]


def _get_audio_manager():
    """获取 Android AudioManager，缓存单例。"""
    if _am_ref[0] is not None:
        return _am_ref[0]
    try:
        from jnius import autoclass
        from android_perms import get_activity
        act = get_activity()
        if act is None:
            return None
        Context = autoclass("android.content.Context")
        am = act.getSystemService(Context.AUDIO_SERVICE)
        _am_ref[0] = am
        return am
    except Exception:
        return None


def is_silent():
    """检测手机是否处于静音/振动模式。静音返回 True，正常返回 False。"""
    am = _get_audio_manager()
    if am is None:
        return False
    try:
        mode = int(am.getRingerMode())
        # 0=RINGER_MODE_SILENT, 1=RINGER_MODE_VIBRATE, 2=RINGER_MODE_NORMAL
        return mode in (0, 1)
    except Exception:
        return False


def boost_media_volume():
    """临时把媒体音量拉到最大。返回 True 表示已提升（非静音），False 表示静音应跳过。"""
    am = _get_audio_manager()
    if am is None:
        return True  # 拿不到 AudioManager 时不阻断播放
    try:
        if is_silent():
            return False
    except Exception:
        pass
    with _lock:
        if _boost_count[0] == 0:
            try:
                AudioManager = __import__("jnius").autoclass("android.media.AudioManager")
                stream = AudioManager.STREAM_MUSIC
                cur = int(am.getStreamVolume(stream))
                mx = int(am.getStreamMaxVolume(stream))
                _original_vol[0] = cur
                if cur < mx:
                    am.setStreamVolume(stream, mx, 0)  # 0=不显示音量UI
            except Exception:
                pass
        _boost_count[0] += 1
    return True


def restore_media_volume(delay_ms=800):
    """延迟恢复原媒体音量。delay_ms 给音效播放留时间。"""
    def _do_restore():
        time.sleep(delay_ms / 1000.0)
        with _lock:
            _boost_count[0] = max(0, _boost_count[0] - 1)
            if _boost_count[0] > 0:
                return  # 还有其他音效在播放，不恢复
            am = _get_audio_manager()
            if am is None or _original_vol[0] is None:
                return
            try:
                from jnius import autoclass
                AudioManager = autoclass("android.media.AudioManager")
                stream = AudioManager.STREAM_MUSIC
                am.setStreamVolume(stream, _original_vol[0], 0)
                _original_vol[0] = None
            except Exception:
                pass
    threading.Thread(target=_do_restore, daemon=True).start()


def play_with_boost(play_func):
    """包装播放函数：静音跳过，非静音拉满音量播放后恢复。
    play_func 是无参可调用，真正执行播放动作。"""
    if not boost_media_volume():
        return  # 静音，不播放
    try:
        play_func()
    finally:
        restore_media_volume(800)

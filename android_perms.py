# -*- coding: utf-8 -*-
"""
android_perms.py — LanTalk 移动端 Android 运行时权限申请（新增独立模块）

【要解决的问题：一点语音就闪退】
Android 6.0+ 把 RECORD_AUDIO（录音）列为“危险权限”：即使 AndroidManifest.xml
声明了，也必须在运行时弹窗、由用户点“允许”后才能用。原 client/ui/audio_backend.py
里的 _ensure_record_permission() 只尝试了两个错误的 Activity 类名：
    org.kivy.android.PythonActivity            （kivy/python-for-android 的）
    com.example.serious_python.MainActivity    （示例占位包名）
而本项目用 Flet + serious_python 打包，真正的 Activity 持有类是
    com.flet.serious_python_android.PythonActivity （其静态字段 mActivity 持有主Activity）
serious_python 还会注入环境变量 MAIN_ACTIVITY_HOST_CLASS_NAME / MAIN_ACTIVITY_CLASS_NAME。
两个旧类名都找不到 -> 权限申请被整段静默跳过 -> 用户从未授权 ->
AudioRecord.startRecording() 在无录音权限时直接在 native 层崩溃（闪退），
这种崩溃发生在 JVM/native 层，Python 的 try/except 根本拦不住。

本模块用“正确的类名 + 环境变量 + 旧名兜底”多路获取 Activity，在 UI 线程弹出
系统权限对话框，并等待用户选择结果。所有 jnius 导入都延迟到函数内，桌面端
（无 jnius）直接返回 True，不影响电脑调试。

【线程模型】
- requestPermissions 必须在 Android 主线程调用 -> runOnUiThread；
- 等待用户选择在调用方后台线程轮询 checkSelfPermission，不阻塞 UI；
- 全部做了异常防御，任何失败都返回 False，由调用方决定提示，绝不闪退。
"""

import os
import time
import threading

RECORD_AUDIO = "android.permission.RECORD_AUDIO"

# serious_python 正确的 Activity 持有类（最高优先级），其后是环境变量与旧名兜底
_ACTIVITY_HOST_CANDIDATES = [
    "com.flet.serious_python_android.PythonActivity",
    "org.kivy.android.PythonActivity",
    "com.example.serious_python.MainActivity",
]


def is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


# ---------------------------------------------------------------------- #
# Activity 获取
# ---------------------------------------------------------------------- #
def _autoclass(name):
    from jnius import autoclass
    return autoclass(name)


def get_activity():
    """获取当前 Android 主 Activity，任何失败返回 None。"""
    if not is_android():
        return None
    # 1) 环境变量 MAIN_ACTIVITY_HOST_CLASS_NAME（serious_python 注入，最可靠）
    env_host = os.environ.get("MAIN_ACTIVITY_HOST_CLASS_NAME", "")
    # 2) 环境变量 MAIN_ACTIVITY_CLASS_NAME（真正 MainActivity 的类名）
    env_main = os.environ.get("MAIN_ACTIVITY_CLASS_NAME", "")
    ordered = []
    if env_host:
        ordered.append(env_host)
    ordered.extend(_ACTIVITY_HOST_CANDIDATES)
    if env_main and env_main not in ordered:
        ordered.append(env_main)

    for cls_name in ordered:
        try:
            cls = _autoclass(cls_name)
            act = getattr(cls, "mActivity", None)
            if act is not None:
                return act
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------- #
# 主线程调度
# ---------------------------------------------------------------------- #
class _UiRunnable:
    """延迟创建，避免桌面端 import jnius 失败。"""
    _cls = None

    @classmethod
    def create(cls, fn):
        from jnius import PythonJavaClass, java_method

        class _R(PythonJavaClass):
            __javainterfaces__ = ["java/lang/Runnable"]

            def __init__(self, callback):
                super().__init__()
                self._cb = callback

            @java_method("()V")
            def run(self):
                try:
                    self._cb()
                except Exception:
                    pass

        return _R(fn)


def _run_on_ui_thread(activity, fn):
    """把 fn 放到 Android 主线程执行。"""
    try:
        activity.runOnUiThread(_UiRunnable.create(fn))
        return True
    except Exception:
        try:
            Handler = _autoclass("android.os.Handler")
            Looper = _autoclass("android.os.Looper")
            Handler(Looper.getMainLooper()).post(_UiRunnable.create(fn))
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------- #
# 权限查询 / 申请
# ---------------------------------------------------------------------- #
def _is_granted(activity, permission=RECORD_AUDIO):
    try:
        PM = _autoclass("android.content.pm.PackageManager")
        return int(activity.checkSelfPermission(permission)) == int(
            PM.PERMISSION_GRANTED)
    except Exception:
        return False


def has_record_permission():
    """同步判断是否已获得录音权限。桌面端恒为 True。"""
    if not is_android():
        return True
    act = get_activity()
    if act is None:
        return False
    return _is_granted(act)


def _java_string_array(items):
    from jnius import jarray, autoclass
    JString = autoclass("java.lang.String")
    return jarray.array(list(items), JString)


def request_record_permission(timeout=30.0, request_code=20260913):
    """
    弹出系统权限对话框并等待用户选择，返回最终是否授权。
    - 已授权：立即返回 True；
    - 未授权：在UI线程 requestPermissions，后台轮询等待结果（默认最多30秒）；
    - 拿不到 Activity / 用户拒绝 / 超时：返回 False。
    必须在**后台线程**调用（内部会等待），不要在 UI/事件循环线程直接调用。
    """
    if not is_android():
        return True
    act = get_activity()
    if act is None:
        return False
    if _is_granted(act):
        return True

    requested = threading.Event()

    def _do_request():
        try:
            arr = _java_string_array([RECORD_AUDIO])
            act.requestPermissions(arr, int(request_code))
        except Exception:
            pass
        finally:
            requested.set()

    # requestPermissions 必须主线程
    if not _run_on_ui_thread(act, _do_request):
        # 无法切主线程时直接尝试一次（部分环境也能工作）
        _do_request()
    requested.wait(2.0)

    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        # 用户每次操作后 Activity 可能更新，重新取一次更稳
        cur = get_activity() or act
        if _is_granted(cur):
            return True
        time.sleep(0.25)
    return _is_granted(get_activity() or act)


def ensure_record_permission(timeout=30.0):
    """便捷入口：已授权直接 True，否则弹窗申请并等待结果。"""
    if not is_android():
        return True
    if has_record_permission():
        return True
    return request_record_permission(timeout=timeout)

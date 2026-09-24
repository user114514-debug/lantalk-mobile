# -*- coding: utf-8 -*-
"""
android_native_picker.py — LanTalk 移动端原生文件选择器（独立增量模块）

【解决的问题】
Flet 底层 file_picker 包在荣耀 MagicOS 等深度定制 ROM 上：
  FilePicker.pick_files() 返回 "picker launched" 但系统选择器不弹窗。
本模块用 AndroidX Activity Result API（registerForActivityResult）直接调
系统 ACTION_OPEN_DOCUMENT，完全绕过 Flutter file_picker 包。

【原理】
  activity.getActivityResultRegistry()
    .register("lantalk_open_doc", OpenDocument contract, callback)
    .launch(["*/*"])
不需要重写 Activity.onActivityResult，可在运行时注册回调。

【降级】
任何一步失败（Activity 不是 ComponentActivity、AndroidX 不可用等）都返回
False，调用方自动降级到原有 Flet FilePicker / tkinter。

【依赖】
  - pyjnius（移动端已有）
  - android_perms.get_activity()（已验证能拿到 serious_python 的 Activity）
  - android_saf_picker.resolve_content_uri()（把 content:// 复制为临时文件）

【使用】
    from android_native_picker import pick_file_native
    def on_result(path):
        if path:
            self._on_file_picked_path(path)
        else:
            # 用户取消或失败，降级到 Flet FilePicker
            self._pick_file_with_fallback(...)
    ok = pick_file_native(on_result, mime_types=["*/*"])
    if not ok:
        self._pick_file_with_fallback(...)
"""
import os
import threading
import time

# 单例 launcher（APP 运行期间只注册一次）
_launcher = None
_launcher_lock = threading.Lock()
_callback_ref = None  # 保持回调引用，防止被 GC


def _trace(msg):
    try:
        d = "crash_logs"
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        with open(os.path.join(d, "debug_trace.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] [NATIVE_PICK] {msg}\n")
    except Exception:
        pass


def is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


def _get_activity():
    try:
        from android_perms import get_activity
        return get_activity()
    except Exception as e:
        _trace(f"get_activity failed: {e!r}")
        return None


def _has_activity_result_registry(act):
    """检查 Activity 是否有 AndroidX ActivityResultRegistry（ComponentActivity）。"""
    try:
        return hasattr(act, "getActivityResultRegistry")
    except Exception:
        return False


def _make_callback(on_result):
    """
    用 pyjnius PythonJavaClass 实现 androidx.activity.result.ActivityResultCallback。
    回调参数是 Uri（可能为 null，表示用户取消）。
    """
    from jnius import PythonJavaClass, java_method

    class _ResultCallback(PythonJavaClass):
        __javainterfaces__ = ["androidx.activity.result.ActivityResultCallback"]

        def __init__(self, cb):
            super().__init__()
            self._cb = cb

        @java_method("(Ljava/lang/Object;)V")
        def onActivityResult(self, result):
            try:
                self._cb(result)
            except Exception as e:
                _trace(f"callback exception: {e!r}")

    return _ResultCallback(on_result)


def _ensure_launcher(act):
    """幂等地注册 ActivityResultLauncher，返回 launcher 或 None。"""
    global _launcher, _callback_ref
    with _launcher_lock:
        if _launcher is not None:
            return _launcher
        try:
            from jnius import autoclass

            registry = act.getActivityResultRegistry()
            if registry is None:
                _trace("getActivityResultRegistry returned None")
                return None

            # OpenDocument contract：ACTION_OPEN_DOCUMENT + CATEGORY_OPENABLE
            OpenDocument = autoclass(
                "androidx.activity.result.contract.ActivityResultContracts$OpenDocument"
            )
            contract = OpenDocument()

            # 回调：把 Uri 复制为临时文件后调用用户回调
            def _on_uri(uri):
                _trace(f"onActivityResult uri={uri!r}")
                if uri is None:
                    _callback_ref(None)
                    return
                try:
                    uri_str = uri.toString()
                    _trace(f"uri string: {uri_str}")
                    from android_saf_picker import resolve_content_uri
                    path = resolve_content_uri(uri_str, log_fn=_trace)
                    _trace(f"resolved path: {path!r}")
                    _callback_ref(path)
                except Exception as e:
                    _trace(f"resolve uri failed: {e!r}")
                    _callback_ref(None)

            _callback_ref = _on_uri
            callback = _make_callback(_on_uri)

            # register(key, contract, callback) -> ActivityResultLauncher
            _launcher = registry.register("lantalk_open_doc", contract, callback)
            _trace(f"launcher registered: {_launcher!r}")
            return _launcher
        except Exception as e:
            _trace(f"register launcher failed: {e!r}")
            _launcher = None
            return None


def pick_file_native(on_result, mime_types=None):
    """
    用 AndroidX Activity Result API 启动系统文件选择器。

    参数:
        on_result: 回调函数，签名 on_result(path)。
                   path 为临时文件路径（字符串），None 表示用户取消或失败。
        mime_types: MIME 类型列表，默认 ["*/*"]（所有文件）。
                    如 ["image/*", "video/*"] 只选媒体。

    返回:
        True  表示已成功启动选择器，结果将通过 on_result 回调；
        False 表示当前环境不支持原生选择器，调用方应降级到 Flet FilePicker。
    """
    if not is_android():
        _trace("not android, return False")
        return False

    act = _get_activity()
    if act is None:
        _trace("no activity, return False")
        return False

    if not _has_activity_result_registry(act):
        _trace("activity has no ActivityResultRegistry, return False")
        return False

    launcher = _ensure_launcher(act)
    if launcher is None:
        _trace("launcher is None, return False")
        return False

    if mime_types is None:
        mime_types = ["*/*"]

    try:
        from jnius import jarray, autoclass
        JString = autoclass("java.lang.String")
        input_arr = jarray.array(list(mime_types), JString)
        _trace(f"launching with mime_types={mime_types}")
        # 必须在 UI 线程 launch
        from android_perms import _run_on_ui_thread

        def _do_launch():
            try:
                launcher.launch(input_arr)
                _trace("launcher.launch called")
            except Exception as e:
                _trace(f"launch failed: {e!r}")
                on_result(None)

        if not _run_on_ui_thread(act, _do_launch):
            _do_launch()
        return True
    except Exception as e:
        _trace(f"pick_file_native exception: {e!r}")
        return False

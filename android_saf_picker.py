# -*- coding: utf-8 -*-
"""
android_saf_picker.py — LanTalk 移动端 SAF 文件选择/content URI 修复（新增独立模块）

【解决的问题】
原 android_photo_picker.py 和 mobile_main._resolve_android_content_uri 里都写死了
    org.kivy.android.PythonActivity
在 Flet + serious_python 打包环境下该类不存在，autoclass() 直接抛异常，
被 except 吞掉后导致：
  1. Photo Picker 启动失败 -> 降级到 Flet FilePicker
  2. content:// URI 复制临时文件失败 -> 返回空/原路径 -> os.path.exists() 失败
  3. 用户表现为"选了文件但发不出去"

【本模块做什么】
  1. 用 android_perms.get_activity() 多路获取正确的 Activity（复用已验证的逻辑）
  2. 对 content:// URI：先 takePersistableUriPermission 持久化权限，
     再用 openInputStream 复制到临时文件，返回真实路径
  3. 所有 jnius 调用后检查 Java 异常并清除，不再静默吞错
  4. 桌面端/非 Android 环境直接返回原路径，不影响电脑调试

【不改原有代码】本模块只提供函数，在 mobile_main.py 末尾通过 monkey-patch 挂载。
"""

import os
import tempfile
import threading


def is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


def get_resolver():
    """获取 ContentResolver，任何失败返回 None。"""
    try:
        from android_perms import get_activity
        act = get_activity()
        if act is None:
            return None, None
        return act, act.getContentResolver()
    except Exception:
        return None, None


def _clear_java_exception():
    """安全地清除当前线程挂起的 Java 异常。"""
    try:
        from jnius import get_exc
        exc = get_exc()
        if exc is not None:
            try:
                exc.clear()
            except Exception:
                pass
    except Exception:
        pass


def take_persistable_permission(uri_str):
    """
    对 content:// URI 尝试持久化读取权限（takePersistableUriPermission）。
    不支持时静默跳过（不影响本次读取，只是下次可能失效）。
    """
    if not uri_str or not uri_str.startswith("content://"):
        return
    try:
        from jnius import autoclass
        _, resolver = get_resolver()
        if resolver is None:
            return
        Uri = autoclass("android.net.Uri")
        uri = Uri.parse(uri_str)
        # FLAG_GRANT_READ_URI_PERMISSION = 1
        resolver.takePersistableUriPermission(uri, 1)
    except Exception:
        _clear_java_exception()


def resolve_content_uri(uri_str, log_fn=None):
    """
    将 content:// URI 复制到临时文件，返回临时文件路径。
    非 content:// 直接返回原路径。任何失败返回原路径（调用方自行判断 exists）。

    与旧版 _resolve_android_content_uri 的区别：
      - 用 android_perms.get_activity() 获取正确的 Activity（不再写死 kivy 类名）
      - 先 takePersistableUriPermission 持久化权限
      - 查询文件名时用 OpenableColumns 常量
      - 每个 jnius 调用后清除 Java 异常
    """
    def _log(msg):
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass

    def _saf_trace(msg):
        try:
            import os, time
            d = "crash_logs"
            try: os.makedirs(d, exist_ok=True)
            except Exception: pass
            with open(os.path.join(d, "debug_trace.txt"), "a", encoding="utf-8") as _f:
                _f.write(f"[{time.strftime('%H:%M:%S')}] [SAF] {msg}\n")
        except Exception: pass
    _saf_trace(f"resolve_content_uri called: {uri_str!r}")
    if not uri_str or not uri_str.startswith("content://"):
        _saf_trace(f"not content://, return as-is")
        return uri_str

    try:
        from jnius import autoclass
        _, resolver = get_resolver()
        if resolver is None:
            _log("SAF resolve: no ContentResolver")
            return uri_str

        # 1) 持久化权限
        take_persistable_permission(uri_str)

        # 2) 查询文件名
        display_name = "picked_file"
        try:
            Uri = autoclass("android.net.Uri")
            OpenableColumns = autoclass("android.provider.OpenableColumns")
            uri = Uri.parse(uri_str)
            cursor = resolver.query(uri, None, None, None, None)
            if cursor is not None and cursor.moveToFirst():
                name_idx = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if name_idx >= 0:
                    val = cursor.getString(name_idx)
                    if val:
                        display_name = val
                cursor.close()
        except Exception:
            _clear_java_exception()

        # 3) 复制到临时文件
        ext = os.path.splitext(display_name)[1] or ".dat"
        fd, tmp_path = tempfile.mkstemp(prefix="lantalk_saf_", suffix=ext)
        os.close(fd)

        input_stream = resolver.openInputStream(Uri.parse(uri_str))
        try:
            with open(tmp_path, "wb") as out:
                buf = bytearray(8192)
                while True:
                    n = input_stream.read(buf)
                    if n <= 0:
                        break
                    out.write(bytes(buf[:n]))
        finally:
            try:
                input_stream.close()
            except Exception:
                pass

        _log(f"SAF resolve OK: {uri_str} -> {tmp_path}")
        _saf_trace(f"OK -> {tmp_path} ({os.path.getsize(tmp_path)} bytes)")
        return tmp_path

    except Exception as e:
        _clear_java_exception()
        _log(f"SAF resolve failed: {e}")
        _saf_trace(f"FAILED: {e!r}")
        return uri_str


def patch_photo_picker_activity():
    """
    Monkey-patch android_photo_picker 模块里写死的 kivy Activity 类名。
    替换为通过 android_perms.get_activity() 获取正确的 Activity。
    在 mobile_main.py 末尾调用，幂等。
    """
    try:
        import android_photo_picker as app_mod
    except Exception:
        return False

    try:
        from android_perms import get_activity

        def _patched_get_activity():
            return get_activity()

        # patch AndroidPhotoPicker.pick() 里的 Activity 获取
        orig_pick = app_mod.AndroidPhotoPicker.pick

        def _patched_pick(self, callback, multi=False, max_images=10):
            if not callable(callback):
                return False
            if not self._bind_listener():
                return False

            self._request_code = self._next_code
            self._next_code += 1
            self._pending[self._request_code] = callback

            try:
                from jnius import autoclass
                act = get_activity()
                if act is None:
                    raise RuntimeError("No Activity available")
                Intent = autoclass("android.content.Intent")
                BuildVersion = autoclass("android.os.Build$VERSION")
                sdk_int = BuildVersion.SDK_INT

                if sdk_int >= 33:
                    MediaStore = autoclass("android.provider.MediaStore")
                    intent = Intent(MediaStore.ACTION_PICK_IMAGES)
                    if multi:
                        intent.putExtra(MediaStore.EXTRA_PICK_IMAGES_MAX, max_images)
                else:
                    intent = Intent(Intent.ACTION_GET_CONTENT)
                    intent.addCategory(Intent.CATEGORY_OPENABLE)
                    intent.setType("*/*")
                    if multi:
                        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)

                act.startActivityForResult(intent, self._request_code)
                return True
            except Exception:
                self._pending.pop(self._request_code, None)
                return False

        app_mod.AndroidPhotoPicker.pick = _patched_pick

        # patch _copy_uris_to_files 的 Activity 获取
        @staticmethod
        def _patched_copy_uris(uris):
            paths = []
            try:
                from jnius import autoclass
                act = get_activity()
                if act is None:
                    return paths
                resolver = act.getContentResolver()
                OpenableColumns = autoclass("android.provider.OpenableColumns")

                for index, uri in enumerate(uris):
                    try:
                        stream = resolver.openInputStream(uri)
                        if stream is None:
                            continue
                        ext = ".bin"
                        try:
                            cursor = resolver.query(
                                uri, [OpenableColumns.DISPLAY_NAME], None, None, None
                            )
                            if cursor is not None and cursor.moveToFirst():
                                name = cursor.getString(0)
                                if name:
                                    e = os.path.splitext(name)[1].lower()
                                    if e:
                                        ext = e
                            if cursor is not None:
                                cursor.close()
                        except Exception:
                            pass

                        fd, tmp_path = tempfile.mkstemp(
                            prefix=f"lantalk_pick_{index}_", suffix=ext
                        )
                        os.close(fd)
                        with open(tmp_path, "wb") as out:
                            buf = bytearray(8192)
                            while True:
                                n = stream.read(buf)
                                if n <= 0:
                                    break
                                out.write(bytes(buf[:n]))
                        stream.close()
                        paths.append(tmp_path)
                    except Exception:
                        continue
            except Exception:
                pass
            return paths

        app_mod.AndroidPhotoPicker._copy_uris_to_files = _patched_copy_uris
        return True
    except Exception:
        return False

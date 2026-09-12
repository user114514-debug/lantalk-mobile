# -*- coding: utf-8 -*-
"""
LanTalk Android 原生 Photo Picker 模块（独立增量模块，不依赖原有代码）

功能：
  - Android 13+ (API 33)：调用系统原生 Photo Picker（MediaStore.ACTION_PICK_IMAGES）
  - Android 7-12 (API 24-32)：降级为 ACTION_GET_CONTENT（image/* 系统选择器）
  - 自动将 content:// URI 复制为临时文件路径，返回可直接使用的文件系统路径
  - 支持单选/多选
  - 桌面端/非Android环境：pick() 返回False，调用方自行降级到Flet FilePicker或tkinter

依赖：
  - pyjnius（Android端已有，audio_backend.py中已使用）
  - python-for-android 的 android.activity 模块（用于on_activity_result回调）

使用方式（调用方在合适位置接入，不修改原有底层代码）：
    from android_photo_picker import AndroidPhotoPicker

    # 初始化（传入page用于UI线程回调，可选）
    self._photo_picker = AndroidPhotoPicker(page=self.page)

    # 选择图片（单选）
    def on_photos(paths):
        if paths:
            # paths 是临时文件路径列表，可直接用于发送
            self._on_file_picked_path(paths[0])
    ok = self._photo_picker.pick(on_photos, multi=False)
    if not ok:
        # Android Photo Picker不可用，降级到原有Flet FilePicker
        self._file_picker.pick_files(...)

数据包结构说明：本模块仅负责选择图片并返回文件路径，
后续加密/发送由调用方使用crypto模块处理，与本模块完全解耦。
"""

import os
import threading

# 请求码起始值，避免与其他startActivityForResult请求冲突
_FIRST_REQUEST_CODE = 7000


class AndroidPhotoPicker:
    """Android原生Photo Picker封装。每个实例处理一次选择请求。"""

    def __init__(self, page=None, ui_runner=None):
        """
        参数:
            page: Flet page对象，用于在UI线程执行回调（可选）
            ui_runner: 自定义UI线程执行函数，签名 ui_runner(func, *args)（可选）
                       若传入则优先使用；否则尝试page.update()或直接调用
        """
        self._page = page
        self._ui_runner = ui_runner
        self._callback = None
        self._request_code = None
        self._bound = False
        self._next_code = _FIRST_REQUEST_CODE
        self._pending = {}  # request_code -> callback（支持多实例共存）

    # ------------------------------------------------------------------
    # 绑定 on_activity_result 监听器（应用生命周期内只绑定一次）
    # ------------------------------------------------------------------
    def _bind_listener(self) -> bool:
        """
        绑定android.activity的on_activity_result回调。
        幂等：重复调用不会重复绑定。
        返回True表示绑定成功，False表示当前环境不支持（如桌面端）。
        """
        if self._bound:
            return True
        try:
            from android import activity
            activity.bind(on_activity_result=self._on_activity_result)
            self._bound = True
            return True
        except Exception:
            # 桌面端或非python-for-android环境，android.activity不可用
            return False

    # ------------------------------------------------------------------
    # 公开API：启动Photo Picker
    # ------------------------------------------------------------------
    def pick(self, callback, multi: bool = False, max_images: int = 10) -> bool:
        """
        启动Android原生Photo Picker。

        参数:
            callback: 选择完成后的回调函数，签名 callback(paths: list[str])
                      paths为空列表表示用户取消或选择失败
                      回调可能在后台线程触发，调用方需自行切换到UI线程
                      （若传入了page或ui_runner，则自动在UI线程执行）
            multi: 是否允许多选（默认False单选）
            max_images: 多选时最大图片数（API 33+ Photo Picker有效，默认10）

        返回:
            True表示已成功启动Photo Picker，结果将通过callback返回
            False表示当前环境不支持原生Photo Picker，调用方应降级到Flet FilePicker/tkinter
        """
        if not callable(callback):
            return False

        if not self._bind_listener():
            return False

        self._request_code = self._next_code
        self._next_code += 1
        self._pending[self._request_code] = callback

        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Intent = autoclass("android.content.Intent")
            BuildVersion = autoclass("android.os.Build$VERSION")
            act = PythonActivity.mActivity
            sdk_int = BuildVersion.SDK_INT

            if sdk_int >= 33:
                # Android 13+：原生Photo Picker（无需存储权限）
                MediaStore = autoclass("android.provider.MediaStore")
                intent = Intent(MediaStore.ACTION_PICK_IMAGES)
                if multi:
                    intent.putExtra(MediaStore.EXTRA_PICK_IMAGES_MAX, max_images)
            else:
                # Android 7-12：降级为ACTION_GET_CONTENT（通用系统选择器）
                intent = Intent(Intent.ACTION_GET_CONTENT)
                intent.addCategory(Intent.CATEGORY_OPENABLE)
                intent.setType("image/*")
                if multi:
                    intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, True)

            act.startActivityForResult(intent, self._request_code)
            return True

        except Exception as e:
            # 设备/ROM不支持Photo Picker（ActivityNotFoundException等）
            self._pending.pop(self._request_code, None)
            return False

    # ------------------------------------------------------------------
    # on_activity_result 回调（由android.activity分发，在UI线程执行）
    # ------------------------------------------------------------------
    def _on_activity_result(self, request_code: int, result_code: int, intent):
        """
        android.activity的on_activity_result回调。
        只处理本实例注册的request_code，其他忽略。
        """
        callback = self._pending.pop(request_code, None)
        if callback is None:
            return

        try:
            from jnius import autoclass
            Activity = autoclass("android.app.Activity")

            # 用户取消或返回异常
            if result_code != Activity.RESULT_OK or intent is None:
                self._dispatch_callback(callback, [])
                return

            # 解析选中的URI（兼容getData和ClipData两种方式）
            uris = self._parse_uris(intent)
            if not uris:
                self._dispatch_callback(callback, [])
                return

            # URI复制为文件是IO操作，放后台线程避免阻塞UI
            def worker():
                paths = self._copy_uris_to_files(uris)
                self._dispatch_callback(callback, paths)

            threading.Thread(target=worker, daemon=True).start()

        except Exception:
            self._dispatch_callback(callback, [])

    # ------------------------------------------------------------------
    # 解析Intent中的URI（兼容单选getData和多选ClipData）
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_uris(intent) -> list:
        """
        从Activity Result Intent中提取所有选中的URI。
        部分设备把第一个URI放在getData，其余放在ClipData，需要合并去重。
        """
        uris = []
        seen = set()

        def _add(uri):
            if uri is None:
                return
            key = uri.toString()
            if key in seen:
                return
            seen.add(key)
            uris.append(uri)

        # ClipData（多选时所有URI都在这里）
        try:
            clip_data = intent.getClipData()
            if clip_data is not None:
                count = clip_data.getItemCount()
                for i in range(count):
                    _add(clip_data.getItemAt(i).getUri())
        except Exception:
            pass

        # getData（单选时或部分设备的第一个URI）
        try:
            _add(intent.getData())
        except Exception:
            pass

        return uris

    # ------------------------------------------------------------------
    # 将content:// URI复制为临时文件
    # ------------------------------------------------------------------
    @staticmethod
    def _copy_uris_to_files(uris: list) -> list:
        """
        将Android content:// URI通过ContentResolver复制为临时文件，
        返回临时文件路径列表。复制失败的URI跳过。
        """
        paths = []
        try:
            from jnius import autoclass

            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()
            OpenableColumns = autoclass("android.provider.OpenableColumns")
            import tempfile

            for index, uri in enumerate(uris):
                try:
                    stream = resolver.openInputStream(uri)
                    if stream is None:
                        continue

                    # 从ContentResolver查询文件名和扩展名
                    ext = ".jpg"
                    try:
                        cursor = resolver.query(
                            uri, [OpenableColumns.DISPLAY_NAME], None, None, None
                        )
                        if cursor is not None and cursor.moveToFirst():
                            name = cursor.getString(0)
                            if name:
                                e = os.path.splitext(name)[1].lower()
                                if e in (".jpg", ".jpeg", ".png", ".gif", ".webp",
                                         ".bmp", ".heic", ".heif", ".avif"):
                                    ext = e
                        if cursor is not None:
                            cursor.close()
                    except Exception:
                        pass

                    # 创建临时文件
                    fd, tmp_path = tempfile.mkstemp(
                        prefix=f"lantalk_pick_{index}_", suffix=ext
                    )
                    os.close(fd)

                    # 流式复制
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

    # ------------------------------------------------------------------
    # 回调分发（自动切换到UI线程）
    # ------------------------------------------------------------------
    def _dispatch_callback(self, callback, paths):
        """
        将结果分发给回调函数。
        若设置了ui_runner或page，则在UI线程执行；否则直接调用（可能在后台线程）。
        """
        if self._ui_runner is not None:
            try:
                self._ui_runner(lambda: callback(paths))
                return
            except Exception:
                pass

        if self._page is not None:
            try:
                # Flet的page.update()可从任意线程调用，先更新再回调
                self._page.update()
            except Exception:
                pass

        callback(paths)

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    def cleanup(self):
        """清理待处理的请求（页面销毁时调用）。"""
        self._pending.clear()

# -*- coding: utf-8 -*-
"""
crash_export.py — LanTalk 移动端崩溃日志导出

Android 10+ 用 MediaStore.Downloads API 写入公共 Download 目录，
不需要存储权限，文件管理器可直接看到。
"""

import os
from datetime import datetime


def _is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


def _export_via_mediastore(log_files, log_dir):
    """
    Android 10+：用 MediaStore.Downloads 写入公共 Download 目录。
    返回 (成功条数, 提示文字)。
    """
    from jnius import autoclass
    from android_perms import get_activity

    act = get_activity()
    if act is None:
        return 0, "无法获取 Activity"

    try:
        ContentValues = autoclass("android.content.ContentValues")
        MediaStore = autoclass("android.provider.MediaStore")
        # pyjnius 内部类必须用 $ 分隔，不能用 . 访问
        MediaStoreDownloads = autoclass("android.provider.MediaStore$Downloads")
        ContentResolver = act.getContentResolver()
    except Exception as e:
        return 0, f"类加载失败: {e}"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rel_path = f"Download/LanTalk_crash_logs_{timestamp}"

    count = 0
    last_err = ""
    for fname in sorted(log_files, reverse=True):
        src = os.path.join(log_dir, fname)
        try:
            values = ContentValues()
            values.put(MediaStoreDownloads.DISPLAY_NAME, fname)
            values.put(MediaStoreDownloads.MIME_TYPE, "text/plain")
            values.put(MediaStoreDownloads.RELATIVE_PATH, rel_path)

            uri = ContentResolver.insert(
                MediaStoreDownloads.EXTERNAL_CONTENT_URI, values
            )
            if uri is None:
                last_err = "insert返回null"
                continue

            # 用 Java 字节流写入，避免 Python bytes 和 Java OutputStream 类型不兼容
            out_stream = ContentResolver.openOutputStream(uri)
            FileInputStream = autoclass("java.io.FileInputStream")
            fis = FileInputStream(src)
            buf = bytearray(8192)
            while True:
                n = fis.read(buf)
                if n <= 0:
                    break
                out_stream.write(buf, 0, n)
            fis.close()
            out_stream.close()
            count += 1
        except Exception as e:
            last_err = str(e)
            continue

    if count > 0:
        return count, f"Download/LanTalk_crash_logs_{timestamp}"
    return 0, f"MediaStore 写入失败: {last_err}"


def export_crash_logs(log_dir="crash_logs"):
    """
    导出所有崩溃日志到公共 Download 目录。
    返回 (成功条数, 目标位置, 错误信息)。
    """
    try:
        if not os.path.exists(log_dir):
            return 0, "", "崩溃日志目录不存在"

        log_files = [
            f for f in os.listdir(log_dir)
            if (f.startswith("crash_") or f == "debug_trace.txt") and f.endswith(".txt")
        ]
        if not log_files:
            return 0, "", "没有崩溃日志"

        # Android：用 MediaStore API
        if _is_android():
            count, loc = _export_via_mediastore(log_files, log_dir)
            if count > 0:
                return count, loc, ""
            return 0, "", "MediaStore 写入失败"

        # 桌面端：导出到 ~/Downloads/LanTalk_crash_logs_时间戳/
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        target_dir = os.path.join(
            dl, f"LanTalk_crash_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        os.makedirs(target_dir, exist_ok=True)
        import shutil
        count = 0
        for fname in sorted(log_files, reverse=True):
            try:
                shutil.copy2(os.path.join(log_dir, fname), os.path.join(target_dir, fname))
                count += 1
            except Exception:
                pass
        return count, target_dir, ""

    except Exception as e:
        return 0, "", str(e)


def share_log_file(filepath):
    """在 Android 上用系统分享 Intent 分享单个日志文件。"""
    if not _is_android() or not os.path.exists(filepath):
        return False
    try:
        from jnius import autoclass
        from android_perms import get_activity
        act = get_activity()
        if act is None:
            return False

        Intent = autoclass("android.content.Intent")
        FileProvider = autoclass("androidx.core.content.FileProvider")

        file = autoclass("java.io.File")(filepath)
        uri = FileProvider.getUriForFile(
            act,
            act.getPackageName() + ".fileprovider",
            file
        )

        intent = Intent(Intent.ACTION_SEND)
        intent.setType("text/plain")
        intent.putExtra(Intent.EXTRA_STREAM, uri)
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        act.startActivity(Intent.createChooser(intent, "分享崩溃日志"))
        return True
    except Exception:
        return False


def build_export_button(app):
    """构建【导出崩溃日志】按钮。"""
    import flet as ft

    def _on_export(ev):
        try:
            app._btn_sound()
        except Exception:
            pass
        try:
            count, target_dir, err = export_crash_logs()
            if count > 0:
                msg = f"已导出 {count} 个崩溃日志到:\n{target_dir}"
            else:
                msg = f"导出失败: {err}"
            try:
                app._append_system(msg)
            except Exception:
                pass
            try:
                dlg = ft.AlertDialog(
                    modal=True,
                    title=ft.Text("崩溃日志导出"),
                    content=ft.Text(msg, selectable=True),
                    actions=[ft.TextButton("确定", on_click=lambda x: app.page.pop_dialog())],
                )
                app.page.show_dialog(dlg)
            except Exception:
                pass
        except Exception as ex:
            try:
                app._append_system(f"导出异常: {ex}")
            except Exception:
                pass

    return ft.ElevatedButton(
        "导出崩溃日志",
        height=44,
        icon=ft.icons.BUG_REPORT,
        on_click=_on_export,
    )

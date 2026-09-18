# -*- coding: utf-8 -*-
"""
crash_export.py — LanTalk 移动端崩溃日志导出（新增独立模块）

【解决的问题】
崩溃日志存在应用私有目录 crash_logs/，荣耀 MagicOS 等封闭系统
文件管理器看不到 Android/data 目录，用户无法手动导出。

【本模块做什么】
  1. 把 crash_logs/ 里所有日志复制到公共 Download/LanTalk_crash_logs/ 目录
  2. 在 Android 上用 ACTION_SEND Intent 分享日志文件
  3. 提供 Flet 按钮构建函数，供设置页调用
"""

import os
import shutil
import time
from datetime import datetime


def _is_android():
    import sys
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


def get_download_dir():
    """获取公共下载目录路径。"""
    try:
        from android_perms import get_activity
        act = get_activity()
        if act is not None:
            # Environment.getExternalStoragePublicDirectory(DIRECTORY_DOWNLOADS)
            from jnius import autoclass
            Environment = autoclass("android.os.Environment")
            return Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS
            ).getAbsolutePath()
    except Exception:
        pass
    # 回退：Downloads 目录
    return os.path.join(os.path.expanduser("~"), "Downloads")


def export_crash_logs(log_dir="crash_logs"):
    """
    导出所有崩溃日志到公共下载目录。
    返回 (成功条数, 目标目录, 错误信息)。
    """
    try:
        if not os.path.exists(log_dir):
            return 0, "", "崩溃日志目录不存在"

        log_files = [
            f for f in os.listdir(log_dir)
            if f.startswith("crash_") and f.endswith(".txt")
        ]
        if not log_files:
            return 0, "", "没有崩溃日志"

        # 目标目录：Download/LanTalk_crash_logs_时间戳/
        dl = get_download_dir()
        target_dir = os.path.join(
            dl, f"LanTalk_crash_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        os.makedirs(target_dir, exist_ok=True)

        count = 0
        for fname in sorted(log_files, reverse=True):
            src = os.path.join(log_dir, fname)
            dst = os.path.join(target_dir, fname)
            try:
                shutil.copy2(src, dst)
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
        Uri = autoclass("android.net.Uri")
        FileProvider = autoclass("androidx.core.content.FileProvider")

        # 用 FileProvider 获取 content URI
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
    """
    构建【导出崩溃日志】Flet 按钮，点击后导出日志并提示结果。
    在设置对话框构建后调用，把按钮插入设置列表。
    """
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
            # 显示结果
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
        expand=True,
        height=44,
        icon=ft.icons.BUG_REPORT,
        on_click=_on_export,
    )

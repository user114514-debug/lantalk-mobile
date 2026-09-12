# -*- coding: utf-8 -*-
"""
crash_dialog.py — LanTalk 崩溃提示与“一键导出崩溃日志”弹窗（新增独立模块）

需求：程序一崩溃，弹出一个弹窗，点击按钮即可导出崩溃日志。

【两条触发路径，双保险】
1) 运行时崩溃（best-effort 立即弹窗）：
   包装已有 crash_logger.CrashLogger.log_crash —— 它把日志写入 crash_logs/ 后，
   本模块通过 Flet 事件循环线程安全地弹出 AlertDialog，内含
   [复制日志] [导出/分享] [关闭]。若崩溃已导致事件循环死亡（弹窗来不及），
   日志文件仍已落盘，交给路径2。
2) 下次启动检测（最可靠，行业通用做法）：
   真·闪退（尤其 native 层崩溃）时进程瞬间结束，运行时根本来不及弹窗。因此
   APP 每次进入主界面后调用 check_previous_crash()，若发现存在“上次没看过的”
   崩溃日志，就弹窗“检测到上次异常退出，是否导出日志”。这条路径UI一定可用。

【导出方式】
- 统一先把日志全文复制到系统剪贴板；
- Android：best-effort 另存到公共 Download 目录，并拉起系统分享面板
  （ACTION_SEND 纯文本，可发到微信/QQ/邮件/备忘录，不依赖 FileProvider）；
- 桌面(Windows)：另存到“下载”文件夹并打开所在目录。

【能力边界（务必知悉）】
native 层直接闪退（如之前无录音权限时 AudioRecord 在 JVM/native 崩溃）发生在
Python 解释器之外，Python 的 excepthook 来不及执行，也写不了日志、弹不了窗；
这类只能靠“消除崩溃源”解决（本次已由 android_perms 在使用麦克风前正确申请
RECORD_AUDIO 权限来根治）。本模块负责的是 Python 层未捕获异常的提示与导出。

本模块不改 crash_logger.py 及任何原有代码，只在 mobile_main 新增区调用 attach()。
"""

import os
import sys
import time
import threading

_LASTREAD_NAME = ".last_crash_read"


def is_android():
    return "android" in sys.modules or hasattr(sys, "getandroidapilevel")


class CrashDialogManager:
    def __init__(self):
        self._app_provider = None
        self._crash_logger = None
        self._showing = False          # 同一时刻只弹一个，防异常风暴
        self._wrapped = False

    # ------------------------------------------------------------------ #
    # 安装：包装 crash_logger.log_crash
    # ------------------------------------------------------------------ #
    def attach(self, crash_logger, app_provider):
        """
        crash_logger: 已 install() 的 CrashLogger 实例
        app_provider: 无参可调用，返回当前 MobileChatApp（可能为 None）
        """
        self._crash_logger = crash_logger
        self._app_provider = app_provider
        if self._wrapped or crash_logger is None:
            return
        try:
            orig_log_crash = crash_logger.log_crash

            def wrapped_log_crash(exc_type, exc_value, exc_tb, thread_name=None):
                path = orig_log_crash(exc_type, exc_value, exc_tb, thread_name)
                try:
                    summary = f"{getattr(exc_type, '__name__', 'Error')}: {exc_value}"
                    self._schedule_dialog(path, summary, runtime=True)
                except Exception:
                    pass
                return path

            crash_logger.log_crash = wrapped_log_crash
            self._wrapped = True
        except Exception:
            pass

    def _get_app(self):
        try:
            return self._app_provider() if self._app_provider else None
        except Exception:
            return None

    def _schedule_dialog(self, log_path, summary, runtime):
        """线程安全地把弹窗调度到 Flet 事件循环；失败静默（日志文件仍在）。"""
        app = self._get_app()
        loop = getattr(app, "_loop", None)
        if loop is None or getattr(app, "page", None) is None:
            return
        try:
            loop.call_soon_threadsafe(
                lambda: self._show(app, log_path, summary, runtime))
        except Exception:
            # 事件循环已关闭等情况：放弃运行时弹窗，等下次启动检测
            pass

    # ------------------------------------------------------------------ #
    # 下次启动检测
    # ------------------------------------------------------------------ #
    def _lastread_path(self):
        try:
            log_dir = getattr(self._crash_logger, "log_dir", "crash_logs")
            return os.path.join(log_dir, _LASTREAD_NAME)
        except Exception:
            return os.path.join("crash_logs", _LASTREAD_NAME)

    def _get_unread_crash(self):
        """返回未读的最新崩溃日志路径；没有则 None。"""
        try:
            files = self._crash_logger.get_log_files()
            if not files:
                return None
            latest = files[0]
            try:
                with open(self._lastread_path(), "r", encoding="utf-8") as f:
                    last = f.read().strip()
            except Exception:
                last = ""
            if os.path.basename(latest) == last:
                return None
            return latest
        except Exception:
            return None

    def _mark_read(self, log_path):
        try:
            with open(self._lastread_path(), "w", encoding="utf-8") as f:
                f.write(os.path.basename(log_path or ""))
        except Exception:
            pass

    def check_previous_crash(self):
        """进入主界面后调用：若上次有未读崩溃日志则弹窗。"""
        try:
            unread = self._get_unread_crash()
            if not unread:
                return
            app = self._get_app()
            if app is None or getattr(app, "page", None) is None:
                return
            summary = self._read_headline(unread)
            self._show(app, unread, summary, runtime=False)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 读取日志
    # ------------------------------------------------------------------ #
    def _read_text(self, log_path, limit=None):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                s = f.read()
            if limit and len(s) > limit:
                s = s[:limit] + "\n……(内容较长，导出后可查看完整日志)"
            return s
        except Exception:
            return "(读取崩溃日志失败)"

    def _read_headline(self, log_path):
        """从日志里抽取异常类型/消息作为摘要。"""
        try:
            text = self._read_text(log_path, 4000)
            for line in text.splitlines():
                if "异常消息:" in line:
                    return line.split("异常消息:", 1)[1].strip()
            for line in text.splitlines():
                if "异常类型:" in line:
                    return line.split("异常类型:", 1)[1].strip()
        except Exception:
            pass
        return os.path.basename(log_path or "")

    # ------------------------------------------------------------------ #
    # 弹窗
    # ------------------------------------------------------------------ #
    def _show(self, app, log_path, summary, runtime):
        if self._showing:
            return
        try:
            import flet as ft
        except Exception:
            return
        page = getattr(app, "page", None)
        if page is None:
            return
        self._showing = True
        dlg = {"ref": None}

        def close():
            try:
                page.pop_dialog()
            except Exception:
                pass
            self._showing = False
            self._mark_read(log_path)

        def do_copy(e=None):
            try:
                text = self._read_text(log_path)
                clip = getattr(page, "clipboard", None)
                if clip is not None:
                    clip.set(text)
                tip.value = "已复制完整日志到剪贴板"
                page.update()
            except Exception as ex:
                tip.value = f"复制失败: {ex}"
                page.update()

        def do_export(e=None):
            # 导出放后台线程，避免 jnius/文件IO 卡住界面
            def worker():
                msg = self.export_log(log_path, app=app)
                def back():
                    tip.value = msg
                    page.update()
                try:
                    app._ui(back)
                except Exception:
                    pass
            threading.Thread(target=worker, daemon=True).start()

        tip = ft.Text("", size=13, color=ft.Colors.GREEN_700)
        title = "程序遇到错误" if runtime else "检测到上次异常退出"
        body = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(title, size=17, weight=ft.FontWeight.W_600),
                    ft.Text(summary or "未知错误", size=13, selectable=True),
                    ft.Text(f"日志文件：{os.path.basename(log_path or '')}",
                            size=12, opacity=0.6),
                    tip,
                ],
                tight=True, spacing=8, scroll=ft.ScrollMode.AUTO,
            ),
            width=300,
        )
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("崩溃日志"),
            content=body,
            actions=[
                ft.TextButton("关闭", on_click=lambda e: close()),
                ft.TextButton("复制日志", on_click=do_copy),
                ft.ElevatedButton("导出崩溃日志", on_click=do_export),
            ],
        )
        dlg["ref"] = dialog
        try:
            self._active_dialog_for_app(app, dialog)
            page.show_dialog(dialog)
        except Exception:
            self._showing = False

    def _active_dialog_for_app(self, app, dialog):
        # 让深色换肤层等也能找到这个弹窗
        try:
            app._active_dialog = dialog
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 导出
    # ------------------------------------------------------------------ #
    def export_log(self, log_path, app=None):
        """导出指定崩溃日志，返回给用户看的结果说明。"""
        text = self._read_text(log_path)
        # 1) 剪贴板
        try:
            page = getattr(app, "page", None)
            clip = getattr(page, "clipboard", None)
            if clip is not None:
                clip.set(text)
        except Exception:
            pass

        fname = "LanTalk_" + os.path.basename(log_path or "crash.txt")
        if is_android():
            saved = self._save_android_download(text, fname)
            self._android_share(text)
            if saved:
                return f"已复制并保存到：{saved}，并打开系统分享"
            return "已复制日志并打开系统分享，可选择微信/邮件/保存到文件"
        return self._save_desktop(text, fname)

    def _save_android_download(self, text, fname):
        try:
            from jnius import autoclass
            candidates = [
                "/storage/emulated/0/Download",
                "/sdcard/Download",
            ]
            for d in candidates:
                try:
                    if os.path.isdir(d):
                        path = os.path.join(d, fname)
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                        return path
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _android_share(self, text):
        try:
            from jnius import autoclass
            host = os.environ.get(
                "MAIN_ACTIVITY_HOST_CLASS_NAME",
                "com.flet.serious_python_android.PythonActivity")
            activity = autoclass(host).mActivity
            Intent = autoclass("android.content.Intent")
            JString = autoclass("java.lang.String")
            intent = Intent(Intent.ACTION_SEND)
            intent.setType("text/plain")
            intent.putExtra(Intent.EXTRA_SUBJECT, JString("LanTalk crash log"))
            intent.putExtra(Intent.EXTRA_TEXT, JString(text))
            chooser = Intent.createChooser(intent, JString("导出崩溃日志"))
            chooser.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            activity.startActivity(chooser)
        except Exception:
            pass

    def _save_desktop(self, text, fname):
        try:
            home = os.path.expanduser("~")
            dl = os.path.join(home, "Downloads")
            if not os.path.isdir(dl):
                dl = home
            path = os.path.join(dl, fname)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            # Windows 打开所在目录并选中文件
            try:
                if sys.platform.startswith("win"):
                    os.startfile(os.path.dirname(path))  # noqa: S606
            except Exception:
                pass
            return f"已导出到：{path}"
        except Exception as ex:
            return f"导出失败：{ex}（日志已复制到剪贴板）"


# 全局单例
crash_dialog_manager = CrashDialogManager()

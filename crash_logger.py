# -*- coding: utf-8 -*-
"""
LanTalk 移动端崩溃日志模块（独立增量开发，不修改原有代码）

功能：
1. 全局捕获未处理异常（主线程 + 子线程）
2. 崩溃时自动在本地生成日志文件
3. 日志内容包括：
   - 崩溃时间
   - 应用版本
   - 设备/系统信息
   - 异常类型和异常消息
   - 完整堆栈跟踪
   - 异常代码位置详情（哪个文件、哪一行、哪个函数）
   - 异常发生时的局部变量（可选）

使用方式：
    # 在 mobile_main.py 末尾追加（不修改原有代码）：
    try:
        from crash_logger import CrashLogger
        _crash_logger = CrashLogger(app_version=VERSION)
        _crash_logger.install()
    except Exception as e:
        print(f"[CrashLogger] 安装失败: {e}")

崩溃日志文件位置：
    应用工作目录/crash_logs/crash_YYYYMMDD_HHMMSS.txt
"""

import os
import sys
import time
import traceback
import platform
import threading
from datetime import datetime
from typing import Optional, Type, List


class CrashLogger:
    """崩溃日志记录器：全局捕获异常，生成详细崩溃日志文件。"""

    DEFAULT_LOG_DIR = "crash_logs"

    def __init__(
        self,
        log_dir: str = DEFAULT_LOG_DIR,
        app_version: str = "unknown",
        app_name: str = "LanTalk",
        include_locals: bool = True,
        max_log_files: int = 50,
    ):
        """
        初始化崩溃日志记录器。

        Args:
            log_dir: 崩溃日志存放目录。
            app_version: 应用版本号。
            app_name: 应用名称。
            include_locals: 是否在日志中包含异常发生时的局部变量。
            max_log_files: 最多保留多少个日志文件，超过则删除最旧的。
        """
        self.log_dir = log_dir
        self.app_version = app_version
        self.app_name = app_name
        self.include_locals = include_locals
        self.max_log_files = max_log_files
        self._installed = False
        self._ensure_log_dir()

    def _ensure_log_dir(self):
        """确保日志目录存在。"""
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir, exist_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 核心：记录崩溃
    # ------------------------------------------------------------------

    def log_crash(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback,
        thread_name: Optional[str] = None,
    ) -> Optional[str]:
        """
        记录崩溃信息到日志文件。

        Args:
            exc_type: 异常类型。
            exc_value: 异常值。
            exc_traceback: 异常回溯对象。
            thread_name: 发生异常的线程名。

        Returns:
            str: 生成的日志文件路径，失败返回None。
        """
        try:
            self._ensure_log_dir()

            # 生成日志文件名
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            log_file = os.path.join(self.log_dir, f"crash_{timestamp}.txt")

            # 构建日志内容
            lines = self._build_log_content(
                exc_type, exc_value, exc_traceback, thread_name
            )

            # 写入文件
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))

            # 清理旧日志
            self._cleanup_old_logs()

            # 同时输出到控制台
            print(f"\n{'='*60}")
            print(f"[CrashLogger] 应用崩溃！日志已保存到: {log_file}")
            print(f"[CrashLogger] 异常: {exc_type.__name__}: {exc_value}")
            print(f"{'='*60}\n")

            return log_file

        except Exception as e:
            # 日志记录本身失败，输出到stderr
            print(f"[CrashLogger] 记录崩溃日志失败: {e}", file=sys.stderr)
            return None

    def _build_log_content(
        self,
        exc_type: Type[BaseException],
        exc_value: BaseException,
        exc_traceback,
        thread_name: Optional[str],
    ) -> List[str]:
        """构建崩溃日志内容。"""
        lines = []

        # 头部
        lines.append("=" * 70)
        lines.append(f"  {self.app_name} 移动端崩溃日志")
        lines.append("=" * 70)
        lines.append("")

        # 基本信息
        lines.append("-" * 70)
        lines.append("  基本信息")
        lines.append("-" * 70)
        lines.append(f"  崩溃时间:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}")
        lines.append(f"  应用名称:   {self.app_name}")
        lines.append(f"  应用版本:   {self.app_version}")
        lines.append(f"  崩溃线程:   {thread_name or threading.current_thread().name}")
        lines.append(f"  系统:       {platform.system()} {platform.release()} ({platform.machine()})")
        lines.append(f"  Python版本: {platform.python_version()}")
        lines.append(f"  可执行文件: {sys.executable}")
        lines.append(f"  工作目录:   {os.getcwd()}")
        lines.append("")

        # 异常信息
        lines.append("-" * 70)
        lines.append("  异常信息")
        lines.append("-" * 70)
        lines.append(f"  异常类型: {exc_type.__module__}.{exc_type.__name__}")
        lines.append(f"  异常消息: {str(exc_value)}")
        if exc_value.__cause__:
            lines.append(f"  原始异常: {type(exc_value.__cause__).__name__}: {exc_value.__cause__}")
        if exc_value.__context__:
            lines.append(f"  上下文异常: {type(exc_value.__context__).__name__}: {exc_value.__context__}")
        lines.append("")

        # 完整堆栈跟踪
        lines.append("-" * 70)
        lines.append("  完整堆栈跟踪")
        lines.append("-" * 70)
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        for line in tb_lines:
            for subline in line.rstrip().split("\n"):
                lines.append(f"  {subline}")
        lines.append("")

        # 异常代码位置详情（逐层分析）
        lines.append("-" * 70)
        lines.append("  异常代码位置详情（从外到内）")
        lines.append("-" * 70)

        tb = exc_traceback
        level = 1
        while tb is not None:
            frame = tb.tb_frame
            filename = frame.f_code.co_filename
            lineno = tb.tb_lineno
            funcname = frame.f_code.co_name
            code_obj = frame.f_code

            lines.append("")
            lines.append(f"  [{level}] 函数: {funcname}")
            lines.append(f"      文件: {filename}")
            lines.append(f"      行号: 第 {lineno} 行")
            lines.append(f"      代码对象: {code_obj.co_name} (定义于第 {code_obj.co_firstlineno} 行)")

            # 尝试读取出错行的源代码
            try:
                import linecache
                source_line = linecache.getline(filename, lineno).strip()
                if source_line:
                    lines.append(f"      出错代码: {source_line}")
            except Exception:
                pass

            # 局部变量
            if self.include_locals:
                try:
                    local_vars = frame.f_locals
                    if local_vars:
                        lines.append(f"      局部变量:")
                        for var_name, var_value in list(local_vars.items())[:20]:  # 最多20个
                            if var_name.startswith("__"):
                                continue
                            try:
                                var_str = repr(var_value)
                                if len(var_str) > 200:
                                    var_str = var_str[:200] + "...(截断)"
                                lines.append(f"        {var_name} = {var_str}")
                            except Exception:
                                lines.append(f"        {var_name} = <无法显示>")
                        if len(local_vars) > 20:
                            lines.append(f"        ... 还有 {len(local_vars) - 20} 个变量")
                except Exception:
                    pass

            tb = tb.tb_next
            level += 1

        lines.append("")

        # 运行中的线程信息
        lines.append("-" * 70)
        lines.append("  当前运行线程")
        lines.append("-" * 70)
        try:
            for thread in threading.enumerate():
                status = "运行中" if thread.is_alive() else "已结束"
                daemon = "守护线程" if thread.daemon else "普通线程"
                lines.append(f"  - {thread.name} (ID: {thread.ident}, {status}, {daemon})")
        except Exception:
            lines.append("  <无法获取线程信息>")
        lines.append("")

        # 尾部
        lines.append("=" * 70)
        lines.append(f"  日志结束 — 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)

        return lines

    def _cleanup_old_logs(self):
        """清理旧的崩溃日志文件，保留最近的max_log_files个。"""
        try:
            if not os.path.exists(self.log_dir):
                return
            log_files = [
                os.path.join(self.log_dir, f)
                for f in os.listdir(self.log_dir)
                if f.startswith("crash_") and f.endswith(".txt")
            ]
            if len(log_files) <= self.max_log_files:
                return
            # 按修改时间排序，删除最旧的
            log_files.sort(key=lambda x: os.path.getmtime(x))
            for old_file in log_files[:-self.max_log_files]:
                try:
                    os.remove(old_file)
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 安装全局异常处理器
    # ------------------------------------------------------------------

    def install(self) -> bool:
        """
        安装全局异常处理器。

        捕获：
        - 主线程未处理异常（sys.excepthook）
        - 子线程未处理异常（threading.excepthook）

        Returns:
            bool: 是否安装成功。
        """
        if self._installed:
            return True

        try:
            # 保存原始处理器
            self._original_excepthook = sys.excepthook
            self._original_threading_excepthook = getattr(threading, "excepthook", None)

            # 主线程异常处理器
            def handle_exception(exc_type, exc_value, exc_traceback):
                # 键盘中断不处理
                if issubclass(exc_type, KeyboardInterrupt):
                    if self._original_excepthook:
                        self._original_excepthook(exc_type, exc_value, exc_traceback)
                    return
                # 记录崩溃日志
                self.log_crash(exc_type, exc_value, exc_traceback, thread_name="MainThread")
                # 继续原始处理（通常会退出程序）
                if self._original_excepthook:
                    try:
                        self._original_excepthook(exc_type, exc_value, exc_traceback)
                    except Exception:
                        pass

            sys.excepthook = handle_exception

            # 子线程异常处理器（Python 3.8+）
            if hasattr(threading, "excepthook"):
                def handle_thread_exception(args):
                    self.log_crash(
                        args.exc_type,
                        args.exc_value,
                        args.exc_traceback,
                        thread_name=args.thread.name if args.thread else "UnknownThread",
                    )

                threading.excepthook = handle_thread_exception

            self._installed = True
            print(f"[CrashLogger] 全局崩溃日志已安装")
            print(f"[CrashLogger] 日志目录: {os.path.abspath(self.log_dir)}")
            print(f"[CrashLogger] 应用版本: {self.app_version}")
            return True

        except Exception as e:
            print(f"[CrashLogger] 安装失败: {e}", file=sys.stderr)
            return False

    def uninstall(self):
        """卸载全局异常处理器，恢复原始处理器。"""
        if not self._installed:
            return
        try:
            if hasattr(self, "_original_excepthook") and self._original_excepthook:
                sys.excepthook = self._original_excepthook
            if hasattr(self, "_original_threading_excepthook") and self._original_threading_excepthook:
                threading.excepthook = self._original_threading_excepthook
            self._installed = False
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def get_log_files(self) -> List[str]:
        """获取所有崩溃日志文件列表（按时间倒序）。"""
        try:
            if not os.path.exists(self.log_dir):
                return []
            log_files = [
                os.path.join(self.log_dir, f)
                for f in os.listdir(self.log_dir)
                if f.startswith("crash_") and f.endswith(".txt")
            ]
            log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return log_files
        except Exception:
            return []

    def get_latest_log(self) -> Optional[str]:
        """获取最新的崩溃日志文件路径。"""
        files = self.get_log_files()
        return files[0] if files else None

    def clear_all_logs(self) -> int:
        """清除所有崩溃日志，返回删除的文件数。"""
        count = 0
        try:
            for f in self.get_log_files():
                try:
                    os.remove(f)
                    count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return count

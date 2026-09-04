# client/ui/compat.py
"""Flet 版本兼容层：集中处理新旧版 Flet API 差异，防御式编程兜底。

覆盖范围：
- ft.padding / ft.margin：module -> 类
- ft.alignment.center 等：补全模块级常量
- ft.icons：module -> Icons 类
- Page.show_dialog / pop_dialog：猴子补丁，用 page.dialog 兜底
- Page.clean / add：猴子补丁，用 page.controls 兜底
- Page.update：猴子补丁，异常捕获 + 日志
- _ui 调度异常：回调打印
- 启动时打印版本与兼容状态
"""
import traceback

import flet as ft


def _log(msg: str) -> None:
    """统一兼容层日志前缀。"""
    print(f"[FletCompat] {msg}")


def apply_compat() -> None:
    """应用所有 Flet 版本兼容补丁（幂等，可安全重复调用）。"""
    # 打印版本信息
    try:
        _log(f"Flet version: {getattr(ft, '__version__', 'unknown')}")
    except Exception:
        pass

    # ===== padding: module -> Padding 类 =====
    try:
        ft.padding.symmetric
        _log("padding: OK (native)")
    except AttributeError:
        ft.padding = ft.Padding
        _log("padding: patched (ft.padding = ft.Padding)")

    # ===== margin: module -> Margin 类 =====
    try:
        ft.margin.symmetric
        _log("margin: OK (native)")
    except AttributeError:
        ft.margin = ft.Margin
        _log("margin: patched (ft.margin = ft.Margin)")

    # ===== alignment: 补全模块级常量 =====
    try:
        ft.alignment.center
        _log("alignment: OK (native)")
    except AttributeError:

        class _Alignment:
            center = ft.Alignment(0, 0)
            top_left = ft.Alignment(-1, -1)
            top_center = ft.Alignment(0, -1)
            top_right = ft.Alignment(1, -1)
            center_left = ft.Alignment(-1, 0)
            center_right = ft.Alignment(1, 0)
            bottom_left = ft.Alignment(-1, 1)
            bottom_center = ft.Alignment(0, 1)
            bottom_right = ft.Alignment(1, 1)

        ft.alignment = _Alignment
        _log("alignment: patched (added center/top_left/... constants)")

    # ===== icons: module -> Icons 类 =====
    try:
        ft.icons.DARK_MODE
        _log("icons: OK (native)")
    except AttributeError:
        if hasattr(ft, "Icons"):
            ft.icons = ft.Icons
            _log("icons: patched (ft.icons = ft.Icons)")
        else:
            _log("icons: WARNING - ft.Icons not found")

    # ===== transform.Offset / Scale：新版扁平化到 ft 顶层，补回 ft.transform 子模块 =====
    try:
        ft.transform.Offset
        _log("transform: OK (native)")
    except AttributeError:
        import types as _types
        _ns = {}
        for _name in ("Offset", "Scale", "Rotation"):
            if hasattr(ft, _name):
                _ns[_name] = getattr(ft, _name)
        if _ns:
            ft.transform = _types.SimpleNamespace(**_ns)
            _log(f"transform: patched (ft.transform.{', '.join(_ns)})")
        else:
            _log("transform: WARNING - no Offset class found")

    # ===== Page.show_dialog / pop_dialog 猴子补丁 =====
    if not hasattr(ft.Page, "show_dialog"):
        def _show_dialog(self, dialog):
            """旧版兼容：用 page.dialog 属性显示弹窗。"""
            try:
                self.dialog = dialog
                dialog.open = True
                self.update()
            except Exception as e:
                _log(f"show_dialog error: {e}")

        ft.Page.show_dialog = _show_dialog
        _log("Page.show_dialog: patched")
    else:
        _log("Page.show_dialog: OK (native)")

    if not hasattr(ft.Page, "pop_dialog"):
        def _pop_dialog(self):
            """旧版兼容：关闭 page.dialog。"""
            try:
                if hasattr(self, "dialog") and self.dialog:
                    self.dialog.open = False
                    self.update()
            except Exception as e:
                _log(f"pop_dialog error: {e}")

        ft.Page.pop_dialog = _pop_dialog
        _log("Page.pop_dialog: patched")
    else:
        _log("Page.pop_dialog: OK (native)")

    # ===== Page.clean / add 猴子补丁 =====
    if not hasattr(ft.Page, "clean"):
        def _clean(self):
            """旧版兼容：清空 page.controls。"""
            try:
                self.controls.clear()
            except Exception as e:
                _log(f"clean error: {e}")

        ft.Page.clean = _clean
        _log("Page.clean: patched")
    else:
        _log("Page.clean: OK (native)")

    if not hasattr(ft.Page, "add"):
        def _add(self, *controls):
            """旧版兼容：追加到 page.controls。"""
            try:
                self.controls.extend(controls)
            except Exception as e:
                _log(f"add error: {e}")

        ft.Page.add = _add
        _log("Page.add: patched")
    else:
        _log("Page.add: OK (native)")

    # ===== Page.update 异常捕获猴子补丁 =====
    if not getattr(ft.Page.update, "_compat_wrapped", False):
        _orig_update = ft.Page.update

        def _safe_update(self, *args, **kwargs):
            """安全 update：异常不崩溃，打印日志。"""
            try:
                return _orig_update(self, *args, **kwargs)
            except Exception as e:
                _log(f"page.update() error: {e}")
                traceback.print_exc()

        _safe_update._compat_wrapped = True
        ft.Page.update = _safe_update
        _log("Page.update: wrapped with try/except")

    _log("=== compat layer ready ===")


def safe_update(page) -> None:
    """安全调用 page.update()，任何异常都不崩溃。"""
    try:
        page.update()
    except Exception as e:
        _log(f"safe_update error: {e}")

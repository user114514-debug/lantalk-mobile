# client/ui/flet_compat.py - Flet 版本兼容垫片
#
# 不同 Flet 版本里位移类的位置不一样：
#   旧版: ft.transform.Offset(x, y)
#   新版(0.8x): ft.Offset(x, y)（ft.transform 子模块不再自动挂载）
# 统一通过这里获取，避免 "module 'flet' has no attribute 'transform'" 报错，
# 也避免动画整体失败退化成生硬的瞬间切换。
import flet as ft

_OFFSET_CLS = None
_OFFSET_TRIED = False


def make_offset(x, y):
    """返回一个 Offset 位移对象；当前版本实在不支持时返回 None（调用方应允许 offset=None）。"""
    global _OFFSET_CLS, _OFFSET_TRIED
    if not _OFFSET_TRIED:
        _OFFSET_TRIED = True
        if hasattr(ft, "Offset"):
            _OFFSET_CLS = getattr(ft, "Offset")
        else:
            try:
                from flet import transform as _t
                if hasattr(_t, "Offset"):
                    _OFFSET_CLS = _t.Offset
            except Exception:
                _OFFSET_CLS = None
    if _OFFSET_CLS is None:
        return None
    try:
        return _OFFSET_CLS(x, y)
    except Exception:
        return None

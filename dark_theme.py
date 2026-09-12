# -*- coding: utf-8 -*-
"""
dark_theme.py — LanTalk 移动端深色主题“控件树换肤层”（新增独立模块，不改原有UI）

【为什么需要它】
原界面是 iOS 浅色风格，颜色全部硬编码：主背景 #F2F2F7、卡片 white 半透明、
输入框 grey100、次要文字 grey800/grey900/black87……这些写死的颜色不会响应
page.theme_mode = DARK，所以“点了深色依旧是白色”。

本模块在【不修改任何原有控件构建代码】的前提下，递归遍历已经构建好的控件树，
把“明确在映射表中的浅色”替换为深色；品牌蓝、蓝紫渐变、红/绿按钮、彩色按钮上
的白色图标与文字都不在映射表中，保持不变。切回亮色时，依据首次记录的原始颜色
原样还原（不是反向硬凑，可反复切换不累积误差）。

关键设计：
1) 同一个 white：出现在“背景属性(bgcolor/fill_color)”要变深；出现在“前景
   属性(color)”往往是彩色按钮上的白字/白图标，必须保持 -> 按属性分别建映射。
2) Flet 0.86 颜色三种形态：Colors 枚举(value='white')、with_opacity 结果
   'white,0.85'、hex '#F2F2F7'；统一解析成 (基础色, 透明度)，映射后保留透明度。
3) Card / AlertDialog 默认就是白底(颜色属性为 None)，深色下主动填深色表面，
   切回亮色还原成 None；普通 Container 的 bgcolor=None(透明) 不处理。
4) 全程异常防御：换肤只是美化，任何错误都不得影响聊天主流程。
"""

import flet as ft

# ---- 深色配色（参照 iOS Dark Mode）----
DARK_BG = "#1C1C1E"          # 主背景
DARK_SURFACE = "#2C2C2E"     # 卡片 / 对方气泡 / 对话框表面
DARK_SURFACE_2 = "#3A3A3C"   # 输入框填充 / 未选中chip
DARK_BLUE_CARD = "#1F2B3A"   # 浅蓝信息卡 blue50 对应深色
DARK_TEXT = "#F2F2F7"        # 主文字
DARK_TEXT_2 = "#AEAEB2"      # 次要文字
DARK_TEXT_3 = "#C7C7CC"      # 更弱文字

# 背景/表面属性：浅色 -> 深色
SURFACE_MAP = {
    "#f2f2f7": DARK_BG,
    "white": DARK_SURFACE,
    "grey100": DARK_SURFACE_2,
    "grey200": DARK_SURFACE_2,
    "blue50": DARK_BLUE_CARD,
}
# 前景(文字/图标)属性：深色文字 -> 浅色文字；white 不在表中 => 保持白色
FORECOLOR_MAP = {
    "grey800": DARK_TEXT_2,
    "grey900": DARK_TEXT,
    "black87": DARK_TEXT,
    "black54": DARK_TEXT_3,
    "black45": DARK_TEXT_3,
    "black38": DARK_TEXT_3,
    "black26": DARK_TEXT_3,
    "black12": DARK_TEXT_3,
    "black": DARK_TEXT,
}
# 边框属性
BORDER_MAP = {
    "grey100": DARK_SURFACE_2,
    "grey200": "#48484A",
}

# 这些控件默认白底，颜色属性为 None 时深色下也要填深色表面
_DEFAULT_SURFACE_TYPES = ()
try:
    _DEFAULT_SURFACE_TYPES = (ft.Card, ft.AlertDialog)
except Exception:
    _DEFAULT_SURFACE_TYPES = ()

# 可能持有子控件的属性名
_CHILD_ATTRS = (
    "content", "controls", "actions", "leading", "trailing",
    "title", "subtitle", "tabs", "header", "footer",
)


# ---------------------------------------------------------------------- #
# 颜色解析 / 重建
# ---------------------------------------------------------------------- #
def _parse_color(color):
    """把任意颜色值解析为 (基础色小写, 透明度或None)；无法解析返回 None。"""
    if color is None:
        return None
    val = getattr(color, "value", None)
    if isinstance(val, str):
        s = val
    elif isinstance(color, str):
        s = color
    else:
        return None
    s = s.strip()
    if not s:
        return None
    alpha = None
    if "," in s:  # 'white,0.85' / '#2c2c2e,0.85'
        base, ap = s.split(",", 1)
        s = base.strip()
        try:
            alpha = float(ap.strip())
        except Exception:
            alpha = None
    return s.lower(), alpha


def _rebuild(base, alpha):
    if alpha is not None:
        try:
            return ft.Colors.with_opacity(alpha, base)
        except Exception:
            return base
    return base


def _remap(color, table):
    """按映射表转换单个颜色，返回 (新颜色, 是否改变)。"""
    parsed = _parse_color(color)
    if not parsed:
        return color, False
    base, alpha = parsed
    if base in table:
        return _rebuild(table[base], alpha), True
    return color, False


# ---------------------------------------------------------------------- #
# 控件树递归换肤
# ---------------------------------------------------------------------- #
def _is_control(obj):
    try:
        return isinstance(obj, ft.Control)
    except Exception:
        return False


def _iter_children(ctrl):
    for attr in _CHILD_ATTRS:
        try:
            val = getattr(ctrl, attr, None)
        except Exception:
            val = None
        if val is None:
            continue
        if _is_control(val):
            yield val
        elif isinstance(val, (list, tuple)):
            for item in val:
                if _is_control(item):
                    yield item


def _surface_attrs(ctrl):
    # Flet 0.86 中 Card / Container / AlertDialog 的表面色统一是 bgcolor，
    # 输入类控件填充色是 fill_color；不再额外处理 Card.color（该版本无此属性）。
    return ["bgcolor", "fill_color"]


def _apply_attr(ctrl, attr, table, is_dark, fill_none_surface=False):
    """处理单个颜色属性：dark 改色并记录原值；light 还原原值。"""
    try:
        cur = getattr(ctrl, attr, None)
    except Exception:
        return
    orig_key = "_lt_orig_" + attr
    if is_dark:
        target = None
        changed = False
        if cur is not None:
            target, changed = _remap(cur, table)
        # Card/Dialog 默认白底(None)：深色下补深色表面（仅 bgcolor）
        if cur is None and fill_none_surface and attr == "bgcolor":
            target, changed = DARK_SURFACE, True
        if changed:
            if not hasattr(ctrl, orig_key):
                try:
                    setattr(ctrl, orig_key, cur)
                except Exception:
                    pass
            try:
                setattr(ctrl, attr, target)
            except Exception:
                pass
    else:
        if hasattr(ctrl, orig_key):
            try:
                setattr(ctrl, attr, getattr(ctrl, orig_key))
            except Exception:
                pass
            try:
                delattr(ctrl, orig_key)
            except Exception:
                pass


def skin_control(ctrl, is_dark, visited=None, depth=0):
    """递归对一棵控件子树换肤(深色)或还原(亮色)。"""
    if not _is_control(ctrl) or depth > 60:
        return
    if visited is None:
        visited = set()
    cid = id(ctrl)
    if cid in visited:
        return
    visited.add(cid)
    try:
        is_default_surface = isinstance(ctrl, _DEFAULT_SURFACE_TYPES)
    except Exception:
        is_default_surface = False
    try:
        # 背景/表面
        for attr in _surface_attrs(ctrl):
            _apply_attr(ctrl, attr, SURFACE_MAP, is_dark,
                        fill_none_surface=is_default_surface)
        # 边框
        for attr in ("border_color", "focused_border_color"):
            _apply_attr(ctrl, attr, BORDER_MAP, is_dark, False)
        # 前景文字/图标
        _apply_attr(ctrl, "color", FORECOLOR_MAP, is_dark, False)
    except Exception:
        pass
    try:
        for child in _iter_children(ctrl):
            skin_control(child, is_dark, visited, depth + 1)
    except Exception:
        pass


def _roots_of_page(page):
    roots = []
    try:
        roots.extend(list(page.controls))
    except Exception:
        pass
    try:
        roots.extend(list(page.overlay))
    except Exception:
        pass
    return roots


def skin_app(app, update=False):
    """
    根据 app._theme_mode 对当前整个界面换肤/还原。
    dark -> 深色；light/system(检测不到系统暗色时) -> 还原亮色。
    update=True 时换肤后刷新页面。
    """
    try:
        mode = getattr(app, "_theme_mode", "system")
        is_dark = mode == "dark"
        if mode == "system":
            is_dark = is_system_dark()
        page = getattr(app, "page", None)
        if page is not None:
            visited = set()
            for root in _roots_of_page(page):
                skin_control(root, is_dark, visited)
            # 当前打开的对话框
            dlg = getattr(app, "_active_dialog", None)
            if dlg is not None:
                skin_control(dlg, is_dark, visited)
            if update:
                try:
                    page.update()
                except Exception:
                    pass
        return is_dark
    except Exception:
        return False


def is_system_dark():
    """best-effort 检测系统是否处于深色模式；检测不到一律返回 False(按亮色)。"""
    try:
        import sys
        if "android" in sys.modules or hasattr(sys, "getandroidapilevel"):
            try:
                from jnius import autoclass
                host = __import__("os").environ.get(
                    "MAIN_ACTIVITY_HOST_CLASS_NAME",
                    "com.flet.serious_python_android.PythonActivity")
                act = autoclass(host).mActivity
                res = act.getResources().getConfiguration()
                ui_mode = int(res.uiMode) & 0x30  # Configuration.UI_MODE_NIGHT_MASK
                return ui_mode == 0x20          # UI_MODE_NIGHT_YES
            except Exception:
                return False
        # 桌面：Windows 读注册表
        if sys.platform.startswith("win"):
            try:
                import subprocess
                out = subprocess.run(
                    ["reg", "query",
                     r"HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
                     "/v", "AppsUseLightTheme"],
                    capture_output=True, text=True, timeout=2)
                return "0x0" in (out.stdout or "")
            except Exception:
                return False
    except Exception:
        return False
    return False

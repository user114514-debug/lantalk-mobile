# -*- coding: utf-8 -*-
"""UI 冒烟测试（无需服务器/屏幕）：用 Mock Page 直接构建聊天页、抽屉、设置及
全部子页面，断言：
  1) 每个按钮 on_click 均已绑定（拒绝空壳）；
  2) Stack 结构正确：遮罩/抽屉 Positioned 精确，把手为 22x50 非定位控件（不全高遮挡）；
  3) 抽屉纯手动：打开后把手移到右缘、箭头朝左；等待 >3.5s 不再自动收回；
  4) 关闭后遮罩/抽屉 visible=False，把手归位、箭头朝右；
  5) 颜色审计：无蓝/青色值，主色为绿。
运行：python test_ui_smoke.py
"""
import asyncio
import sys
from unittest.mock import MagicMock

import flet as ft
import mobile_main
from mobile_main import MobileChatApp

# 允许的按钮类名片段
BUTTON_MARK = "Button"
# 蓝/青色黑名单（小写 hex）
BLUE_BLACKLIST = {
    "#0a84ff", "#5e5ce6", "#bf5af2", "#007aff", "#1e90ff", "#2196f3",
    "#4285f4", "#3b82f6", "#2563eb", "#06b6d4", "#0891b2", "#2563eb",
}
GREEN_TOKENS = ("#34c759", "#30d158")


class MockPage:
    def __init__(self, loop):
        self._loop = loop
        self.controls = []
        self.overlay = []
        self.width = 400.0
        self.height = 850.0
        self.theme_mode = "light"
        self.dialog = None
        self.padding = 0
        self.spacing = 0
        self.window = MagicMock()
        self.clipboard = MagicMock()

    def update(self):
        pass

    def clean(self):
        self.controls.clear()

    def add(self, *c):
        self.controls.extend(c)

    def show_dialog(self, d):
        self.dialog = d

    def pop_dialog(self):
        self.dialog = None

    def run_task(self, coro):
        return self._loop.create_task(coro)


def children(control):
    """统一枚举控件的直接子控件。"""
    out = []
    # Container / Positioned / Card / AlertDialog 等带 content
    content = getattr(control, "content", None)
    if content is not None and isinstance(content, ft.Control):
        out.append(content)
    # 容器类 controls
    ctrls = getattr(control, "controls", None)
    if ctrls:
        out.extend([c for c in ctrls if isinstance(c, ft.Control)])
    # AlertDialog actions
    actions = getattr(control, "actions", None)
    if actions:
        out.extend([c for c in actions if isinstance(c, ft.Control)])
    return out


def walk(root):
    """深度优先遍历整棵控件树。"""
    stack = [root]
    while stack:
        c = stack.pop()
        yield c
        stack.extend(children(c))


def current_controls(page):
    return list(page.controls)


def icon_value(icon_control):
    """跨版本取图标标识：1.0 用 .icon(code point)，0.86 用 .name(字符串)。"""
    return getattr(icon_control, "icon", None) or getattr(icon_control, "name", None)


def _container_has_icon(c):
    inner = getattr(c, "content", None)
    if isinstance(inner, ft.Icon):
        return True
    if isinstance(inner, (ft.Row, ft.Column)):
        return any(isinstance(x, ft.Icon) for x in inner.controls)
    return False


def is_interactive(c):
    """识别视觉上可点击的控件。"""
    cn = type(c).__name__
    if "Button" in cn and "Style" not in cn and "Theme" not in cn:
        return True
    if cn in ("IconButton", "GestureDetector", "InkWell", "CupertinoActionSheetAction"):
        return True
    if isinstance(c, ft.Container):
        # ink 涟漪容器，或内含图标的容器（把手/设置/回形针/麦克风/发送/设置行）
        return bool(getattr(c, "ink", False)) or _container_has_icon(c)
    return False


def audit_buttons(page, where, min_bound=1):
    """审计当前页面：
      - 明确按钮类（Button/IconButton/GestureDetector/InkWell）必须绑定点击；
      - 全页真实绑定 on_click/on_tap 的控件数必须 >= min_bound（防止 Text 型
        Container 按钮被漏判，也防止空壳）。
    """
    bad = []
    bound = 0
    for root in current_controls(page):
        for c in walk(root):
            cn = type(c).__name__
            h = (getattr(c, "on_click", None) or getattr(c, "on_tap", None)
                 or getattr(c, "on_tap_down", None) or getattr(c, "on_tap_up", None)
                 or getattr(c, "on_pan_start", None) or getattr(c, "on_pan_update", None)
                 or getattr(c, "on_pan_end", None))
            is_explicit = (
                ("Button" in cn and "Style" not in cn and "Theme" not in cn)
                or cn in ("IconButton", "GestureDetector", "InkWell"))
            if is_explicit and h is None:
                bad.append(cn)
            if h is not None:
                bound += 1
    assert bound >= min_bound, \
        f"[{where}] 绑定点击的控件仅 {bound}，期望 >={min_bound}（疑似空壳/未绑定）"
    assert not bad, f"[{where}] 明确按钮未绑定点击: {bad}"
    return bound


def collect_colors(page):
    colors = set()
    def scan(c):
        for attr in ("bgcolor", "color", "icon_color", "focused_border_color",
                     "border_color", "selected_color", "active_color"):
            v = getattr(c, attr, None)
            if isinstance(v, str):
                colors.add(v.lower())
        g = getattr(c, "gradient", None)
        if g is not None:
            for v in getattr(g, "colors", []) or []:
                if isinstance(v, str):
                    colors.add(v.lower())
    for root in current_controls(page):
        for c in walk(root):
            scan(c)
    return colors


async def run_test():
    app = MobileChatApp()
    loop = asyncio.get_running_loop()
    page = MockPage(loop)
    app.page = page
    app._loop = loop

    # mock client：已登录、有一个好友
    app.client = MagicMock()
    app.client.username = "admin"
    app.client.server_host = "127.0.0.1"
    app.client.server_port = 9999
    app.client.sock = object()
    app.client.ip_mode = "auto"
    app.client.get_friends = MagicMock(
        return_value=[{"username": "ArthurMorgan", "online": True}])

    # ---------- 聊天页 ----------
    await app.show_chat()
    await asyncio.sleep(0.2)
    n = audit_buttons(page, "聊天页", min_bound=6)
    print(f"聊天页可点击控件数: {n}")

    # Stack 结构（跨 flet 0.86 与 1.0）
    stack = app._chat_root
    assert isinstance(stack, ft.Stack), "聊天根不是 Stack"
    NATIVE_POS = isinstance(ft.Positioned, type)  # 旧版是类，新版是工厂函数
    assert app._drawer_handle in stack.controls, "把手不在 Stack 直接子级"
    assert app._drawer_handle.width == 22 and app._drawer_handle.height == 50, \
        "把手尺寸不是 22x50"
    if NATIVE_POS:
        positioned = [c for c in stack.controls if isinstance(c, ft.Positioned)]
        assert not any(p.content is app._drawer_handle for p in positioned), \
            "把手仍被 Positioned 包裹（可能全高遮挡）"
        drawer_pos = next((p for p in positioned if getattr(p, "width", None) == 280), None)
        assert drawer_pos is not None, "缺少抽屉 Positioned(width=280)"
        assert drawer_pos.top == 0 and drawer_pos.bottom == 0 and drawer_pos.left == 0, \
            "抽屉 Positioned 未全高贴左"
        mask_pos = next((p for p in positioned if p.content is app._drawer_mask), None)
        assert mask_pos is not None, "缺少遮罩 Positioned"
        assert all(getattr(mask_pos, a) == 0 for a in ("left", "top", "right", "bottom")), \
            "遮罩未四边归零"
    else:
        drawer = app._drawer_content
        assert drawer.width == 280 and drawer.top == 0 and drawer.bottom == 0 and drawer.left == 0, \
            "抽屉自身定位属性不正确"
        mask = app._drawer_mask
        assert all(getattr(mask, a) == 0 for a in ("left", "top", "right", "bottom")), \
            "遮罩自身定位未四边归零"
        assert all(getattr(app._drawer_handle, a) is None for a in ("left", "top", "right", "bottom")), \
            "把手不应带定位属性（否则脱离 Stack 居中）"
    print("Stack 结构正确：遮罩/抽屉精确定位，把手 22x50 非全高")

    # ---------- 抽屉：纯手动打开 ----------
    await app._open_drawer()
    assert app._drawer_open is True
    assert app._drawer_content.visible is True
    assert app._drawer_mask.visible is True
    ox, oy = app._drawer_handle.offset.x, app._drawer_handle.offset.y
    assert abs(ox - 11.73) < 0.05 and oy == 0, f"把手未移到抽屉右缘: ({ox},{oy})"
    assert icon_value(app._drawer_handle.content) == ft.icons.CHEVRON_LEFT, \
        "打开后箭头未朝左"
    print("抽屉打开：把手移至右缘、箭头朝左")

    # 等待超过旧的 3 秒自动关闭窗口，确认不再自动收回
    await asyncio.sleep(3.6)
    assert app._drawer_open is True, "抽屉仍被自动关闭（手动化失败）"
    assert app._drawer_mask.visible is True, "遮罩被自动隐藏"
    print("等待 3.6s 后抽屉保持打开（无自动超时）")

    # ---------- 抽屉：手动关闭 ----------
    app._close_drawer()
    assert app._drawer_open is False
    assert app._drawer_handle.offset.x == 0, "关闭后把手 offset 未归零"
    assert icon_value(app._drawer_handle.content) == ft.icons.CHEVRON_RIGHT, \
        "关闭后箭头未朝右"
    await asyncio.sleep(0.45)  # 等动画后的隐藏
    assert app._drawer_mask.visible is False, "遮罩关闭后未隐藏"
    assert app._drawer_content.visible is False, "抽屉关闭后未隐藏"
    print("抽屉关闭：把手归位箭头朝右，遮罩/抽屉 visible=False")

    # ---------- 录音抽屉（mock 音频链路，不依赖麦克风设备）----------
    import os as _os
    import android_voice_message as vmsg_mod
    assert app._rec_sheet.visible is False, "录音抽屉初始应隐藏"
    o_start = vmsg_mod.start_recording
    o_stop = vmsg_mod.stop_recording
    o_cancel = vmsg_mod.cancel_recording
    # 真实临时文件，让松开发送走完整 send_file 分支
    _os.makedirs("voice_tmp", exist_ok=True)
    fake = _os.path.abspath(_os.path.join("voice_tmp", "fake.m4a"))
    with open(fake, "wb") as _f:
        _f.write(b"x" * 200)
    vmsg_mod.start_recording = MagicMock(return_value=fake)
    vmsg_mod.stop_recording = MagicMock(return_value=(1.5, fake))
    vmsg_mod.cancel_recording = MagicMock()
    try:
        # 打开抽屉（待命态）
        await app._open_rec_panel()
        assert app._rec_panel_open is True, "打开后抽屉未标记为开"
        assert app._rec_sheet.visible is True, "打开后抽屉应可见"
        assert app._rec_time.value == "--:--", "待命态时间应为 --:--"
        assert len(app._rec_bars) == 23, "声纹条数应为23"
        # 大麦克风是 GestureDetector，按住/移动/松开手势已绑定
        assert isinstance(app._rec_big_mic, ft.GestureDetector), "大麦克风应为手势控件"
        assert app._rec_big_mic.on_pan_start is not None, "大麦克风未绑按下(pan_start)"
        assert app._rec_big_mic.on_pan_update is not None, "大麦克风未绑移动(pan_update)"
        assert app._rec_big_mic.on_pan_end is not None, "大麦克风未绑松开(pan_end)"
        # 待命态取消按钮隐藏
        assert app._rec_cancel_btn.visible is False, "待命态取消按钮应隐藏"

        from types import SimpleNamespace as _NS
        app.send_file = MagicMock(return_value=None)
        app._ui = MagicMock()

        # --- 场景1：上滑到取消区，松开=丢弃 ---
        app._rec_big_press(_NS(global_position=_NS(y=1000)))
        assert app._recording_path == fake, "按下后未保存录音路径"
        assert app._rec_cancel_btn.visible is True, "录音开始后取消按钮应显示"
        assert len(app._rec_bars) == 23, "声纹条数应为23"
        app._rec_big_move(_NS(global_position=_NS(y=850)))  # 上滑150px > 90
        assert app._cancel_mode is True, "上滑超过阈值应进入取消模式"
        assert "RED" in type(app._rec_cancel_btn.bgcolor).__name__ or app._rec_cancel_btn.bgcolor == ft.Colors.RED_400, "取消按钮应变红"
        app._rec_big_release()
        await asyncio.sleep(0.15)
        assert app.send_file.called is False, "取消模式松开不应发送"
        assert vmsg_mod.cancel_recording.called, "取消模式松开应丢弃"
        assert app._rec_sheet.visible is True, "取消后抽屉仍应打开回待命"
        assert app._rec_time.value == "--:--", "取消后时间应重置 --:--"

        # --- 场景2：不上滑，松开=发送 ---
        with open(fake, "wb") as _f:
            _f.write(b"x" * 200)  # 场景1取消时删掉了，重建
        vmsg_mod.cancel_recording.reset_mock()
        app._rec_big_press(_NS(global_position=_NS(y=1000)))
        app._rec_big_move(_NS(global_position=_NS(y=990)))  # 上滑10px，不触发
        assert app._cancel_mode is False, "未达上滑阈值不应取消"
        app._rec_big_release()
        await asyncio.sleep(0.15)
        assert vmsg_mod.stop_recording.called, "正常松开未停止录音"
        assert app.send_file.called, "正常松开未发送语音文件"
        # 发送后回到待命态：抽屉仍开、时间重置 --:--
        assert app._rec_sheet.visible is True, "发送后抽屉应保持打开回待命"
        assert app._rec_time.value == "--:--", "发送后时间应重置为 --:--"
        # 再点红X/取消才关闭
        await app._close_rec_panel_anim()
        assert app._rec_sheet.visible is False, "关闭后抽屉应隐藏"
        print("录音抽屉：平时隐藏取消钮，按住才显示，上滑变红松开取消，不滑松开发送，发完回待命")

        # ---------- 文件选择抽屉（临时目录模拟真实文件系统）----------
        import tempfile as _tf
        import shutil as _sh
        assert app._file_drawer_sheet.visible is False, "文件抽屉初始应隐藏"
        tmpd = _tf.mkdtemp()
        try:
            _os.makedirs(_os.path.join(tmpd, "subfolder"))
            f1 = _os.path.join(tmpd, "a.txt")
            with open(f1, "w") as _x:
                _x.write("hi")
            app._file_browser_start_dir = lambda: tmpd
            await app._open_file_drawer()
            assert app._file_drawer_sheet.visible is True, "打开后抽屉应可见"
            assert app._file_drawer_path.value == tmpd, "路径栏未显示当前目录"
            # .. + subfolder + a.txt = 3 行
            assert len(app._file_drawer_list.controls) == 3, "目录条目数不对"
            # 进子文件夹：只剩 ..
            app._refresh_file_drawer_dir(_os.path.join(tmpd, "subfolder"))
            assert len(app._file_drawer_list.controls) == 1, "子文件夹应只有 .."
            # 绿色返回：回到 tmpd
            app._file_drawer_go_up()
            assert app._file_drawer_path.value == tmpd, "返回未回到上级"
            # 点文件发送
            app.send_file = MagicMock()
            app._file_drawer_send(f1)
            await asyncio.sleep(0.35)
            assert app.send_file.called, "点文件未发送"
            assert app._file_drawer_sheet.visible is False, "发送后抽屉应关闭"
            # 再打开，测抓手下拉关闭
            await app._open_file_drawer()
            app._file_grab_start()
            app._file_grab_update(_NS(local_delta=_NS(y=150)))  # 下拉150>120
            assert app._file_drag_dy == 150, "抓手未跟手累计"
            app._file_grab_end()
            await asyncio.sleep(0.35)
            assert app._file_drawer_sheet.visible is False, "抓手下拉超阈值应关闭"
            print("文件抽屉：滑入浏览，进文件夹/绿色返回，点文件发送，抓手下拉关闭")
        finally:
            _sh.rmtree(tmpd, ignore_errors=True)
    finally:
        vmsg_mod.start_recording = o_start
        vmsg_mod.stop_recording = o_stop
        vmsg_mod.cancel_recording = o_cancel
        try:
            _os.remove(fake)
        except Exception:
            pass

    # ---------- 设置主页 ----------
    await app.show_settings()
    n = audit_buttons(page, "设置主页", min_bound=6)
    print(f"设置主页可点击控件数: {n}")

    # ---------- 各子页面 ----------
    # 下限：改密码/昵称 = 返回+保存(2)；语言/主题/IP模式 = 返回+3选项(4)
    subpages = [
        ("修改密码", app.show_change_password, 2),
        ("修改用户名", app.show_change_nickname, 2),
        ("语言", app.show_language_settings, 3),
        ("主题", app.show_theme_settings, 4),
        ("IP模式", app.show_ip_mode_settings, 4),
    ]
    for name, fn, mn in subpages:
        await fn()
        c = audit_buttons(page, name, min_bound=mn)
        print(f"子页面[{name}]可点击控件数: {c}")

    # ---------- 颜色审计（聊天页 + 设置 + 子页面综合）----------
    await app.show_chat()
    await asyncio.sleep(0.1)
    colors_chat = collect_colors(page)
    await app.show_settings()
    colors_set = collect_colors(page)
    all_colors = colors_chat | colors_set
    blues = [c for c in all_colors if c in BLUE_BLACKLIST]
    assert not blues, f"发现蓝/青色值: {blues}"
    has_green = any(tok in all_colors for tok in GREEN_TOKENS)
    assert has_green, "未发现绿色主色"
    print(f"颜色审计通过：无蓝/青色，含绿色主色（共{len(all_colors)}种颜色）")

    print("\n============================================================")
    print("  UI 冒烟测试全部通过！")
    print("============================================================")


if __name__ == "__main__":
    try:
        asyncio.run(run_test())
    except AssertionError as e:
        print("\n[TEST FAILED]", e)
        sys.exit(1)

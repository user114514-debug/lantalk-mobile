# mobile_main.py - LanTalk 移动端（安卓）入口  v1.0.0 Release
#
# ============================================================
#  重要约束声明：本文件为【新增】文件，原有项目【零改动】。
#  - TCP通信 / 登录 / 好友 / 数据包协议 / 文件收发 / UDP中转语音
#    全部沿用现有底层逻辑（client/ 目录下的模块），本文件只负责
#    移动端 UI 适配 + 界面编排。
#  - 语音按钮仅做【界面触发 + 通话状态展示】，不重新编写任何
#    UDP 语音底层代码，VoiceCall 类原样复用。
#  - 聊天记录仅存内存（dict），不使用数据库。
# ============================================================
#  功能清单（与PC版对齐，一个不漏）：
#  登录/注册 | 公共+私聊 | 加好友 | 好友列表(在线/未读) | 文件收发(进度条)
#  语音通话(一对一+公共房间) | 来电弹窗 | 专门语音通话界面(时长/丢包/静音/麦设置/返回/挂断)
#  网络状态栏(延迟+丢包率0.5s刷新) | 心跳 | 昵称设置 | 改密码 | 主题切换
#  退出登录 | 断线提示 | 提示音 | 自动滚动 | 语音房间加入按钮 | 设置对话框
# ============================================================
#  运行：Windows调试  flet run mobile_main.py
#        Linux打包   flet build apk
# ============================================================
import asyncio
import json
import os
import sys
import time
import threading
from datetime import datetime

# ---------- Flet 版本兼容层（复用现有 compat.py，自动打补丁） ----------
from client.ui.compat import apply_compat
from client.lang import t, set_lang
apply_compat()

import flet as ft

def _is_android():
    """可靠检测安卓环境：flet打包的APK里 sys.platform 返回 'linux' 而非 'android'，
    用 getandroidapilevel 或 jnius 可用性来判断。"""
    try:
        if hasattr(sys, "getandroidapilevel"):
            return True
    except Exception:
        pass
    try:
        import jnius
        return True
    except Exception:
        pass
    return sys.platform == "android"

# Flet 0.86 起音频控件从核心拆到独立包 flet_audio：必须 import flet_audio 并用 fta.Audio，
# ft.Audio 在新版可能不存在（会导致音效控件创建失败、点击无声）。做兼容兜底。
try:
    import flet_audio as fta
except Exception:
    fta = None


def _make_audio(src, volume=0.5, loop=False):
    """构造音频控件：优先 flet_audio.Audio，回退 ft.Audio；循环用 release_mode（若支持）。"""
    kwargs = dict(src=src, volume=volume)
    if loop:
        rm = getattr(getattr(ft, "ReleaseMode", None), "LOOP", None)
        if rm is not None:
            kwargs["release_mode"] = rm
    if fta is not None:
        try:
            return fta.Audio(**kwargs)
        except Exception:
            pass
    return ft.Audio(**kwargs)


def _mount_service(page, ctrl):
    """非视觉控件在 0.86 挂 page.services，旧版挂 page.overlay，做兼容。"""
    try:
        if hasattr(page, "services"):
            page.services.append(ctrl)
            return
    except Exception:
        pass
    try:
        page.overlay.append(ctrl)
    except Exception:
        pass

# ---------- 底层业务模块复用（原样 import，零修改） ----------
from client.client import ChatClient
from client.message import recv_action
from client.config import load_server_config, save_server_config, load_ip_mode, save_ip_mode, load_language, save_language
from utils.network import get_local_ipv4, get_local_ipv6, get_all_ipv6
from client.ui.models import ChatMessage, FileMessage
from client.ui.file_transfer import FileTransferClient
from client.ui.voice_udp import (
    VoiceCall, parse_voice_signal,
    build_voice_start, build_voice_accept, build_voice_reject, build_voice_end,
    build_room_start, build_room_join, build_room_leave, build_room_end,
)

VERSION = "v3.7.2"


class MobileChatApp:
    """LanTalk 移动端主应用。UI 全部移动端优先自适应，业务底层全部复用 client/。"""

    # ==================== 初始化 ====================
    def __init__(self) -> None:
        self.client = ChatClient()
        self.file_transfer = None
        # 会话（内存存储，无数据库）
        self.current_conv = "public"
        self.conversations = {}          # {conv_id: [ChatMessage/FileMessage]}
        self.friends = []                # [{"username","online"}]
        self._unread = {}                # {conv_id: 未读数}
        self._file_progress = {}         # {file_id: percent}
        # 语音
        self.voice_call = None
        self.voice_incoming = None
        self.voice_target = ""
        self._current_voice_room = ""
        self.public_voice_room = None
        self._voice_timer_running = False
        self._voice_start_time = 0
        # 网络状态
        self._rtt_samples = []
        self._ping_sent = 0
        self._ping_received = 0
        # UI / 线程
        self.page = None
        self._loop = None
        self._heartbeat_running = False
        self._heartbeat_timer = None
        self._net_ui_running = False
        self._net_ui_thread = None
        self._friend_timer = None
        self._file_picker = None
        self._file_picker_ok = False
        self._save_dir = self._get_save_dir()
        self._theme_dark = False
        self._password = ""  # 保存登录密码供重连使用
        # UI 引用防御初始化
        self._login_err = None
        self._login_status = None
        self._chat_root = None
        self._voice_overlay = None
        self._net_text = None
        self._nick_text = None
        self._device_row = None
        self._message_list = None
        self._input_field = None
        self._voice_duration_text = None
        self._voice_status_text = None
        self._voice_loss_text = None
        self._voice_mute_btn = None
        self._voice_target_text = None
        # ===== UI改造新增变量 =====
        self._theme_mode = "system"
        self.speaker_on = True
        self._reconnect_attempts = 0  # 自动重连计数（退避用）
        self._reconnect_timer = None
        # ===== 音频系统（安卓用ft.Audio，Windows用winsound）=====
        self._audio_click = None      # 按钮点击音：Windows Navigation Start.wav
        self._audio_notify = None     # 消息通知音：notify.wav
        self._audio_dial = None       # 拨号音：Alarm08.wav（播2次）
        self._audio_ring = None       # 来电循环音：Alarm03.wav
        self._audio_hangup = None     # 挂断音：Speech Misrecognition.wav
        self._active_dialog = None
        self._voice_mic_icon = None
        self._voice_speaker_btn = None
        self._pending_file_dialog = None
        self._pending_path_display = None
        self._pending_send_btn = None
        self._typewriter_active = False
        self._typewriter_cancel = False

    def _log(self, msg): print(f"[Mobile] {msg}")

    # ==================== 主题配置持久化（UI改造新增） ====================
    def _get_theme_config_path(self):
        if _is_android():
            app_dir = os.path.join(os.path.expanduser("~"), ".lantalk")
        else:
            app_dir = os.path.dirname(os.path.abspath(__file__))
        os.makedirs(app_dir, exist_ok=True)
        return os.path.join(app_dir, "theme_config.json")

    def _load_theme_config(self):
        try:
            path = self._get_theme_config_path()
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    self._theme_mode = cfg.get("theme_mode", "system")
        except Exception as e:
            self._log(f"load theme config failed: {e}")
            self._theme_mode = "system"

    def _save_theme_config(self, mode):
        try:
            with open(self._get_theme_config_path(), "w", encoding="utf-8") as f:
                json.dump({"theme_mode": mode}, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"save theme config failed: {e}")

    def _apply_theme_mode(self, mode):
        self._theme_mode = mode
        try:
            if mode == "system":
                self.page.theme_mode = ft.ThemeMode.SYSTEM
            elif mode == "dark":
                self.page.theme_mode = ft.ThemeMode.DARK
            else:
                self.page.theme_mode = ft.ThemeMode.LIGHT
        except Exception:
            try:
                self.page.theme_mode = mode
            except Exception:
                pass
        try:
            self.page.update()
        except Exception:
            pass

    def _close_dialog_with_fade(self, dlg):
        try:
            if dlg.content is not None:
                dlg.content.opacity = 0
                self.page.update()
        except Exception:
            pass
        def _do_close():
            try:
                self.page.pop_dialog()
            except Exception:
                pass
            self._active_dialog = None
        threading.Timer(0.28, _do_close).start()

    def _android_play(self, audio):
        """Android 异步播放 ft.Audio。支持单个Audio或Audio列表（多文件名候选，逐个尝试）。
        Flet 0.86 的 play()/seek() 是协程，必须丢进事件循环 await 才真正发声；
        每次播放前 seek(0) 回到开头，规避 Android“只响一次、之后 play 无效”的已知问题。"""
        if audio is None or self._loop is None:
            return
        audios = audio if isinstance(audio, (list, tuple)) else [audio]
        async def _do():
            last = None
            for a in audios:
                if a is None:
                    continue
                # play(position=0) 直接从头播放，规避 Android“只响一次”问题，也免去 seek 的 Duration 参数
                for attempt in ("zero", "plain"):
                    try:
                        if attempt == "zero":
                            r = a.play(0)
                        else:
                            r = a.play()
                        if asyncio.iscoroutine(r):
                            await r
                        return
                    except Exception as e:
                        last = e
            if last:
                self._log(f"android audio play failed: {last}")
        try:
            asyncio.run_coroutine_threadsafe(_do(), self._loop)
        except Exception as e:
            self._log(f"android audio schedule failed: {e}")

    def _android_pause(self, audio):
        """Android 异步暂停 ft.Audio（pause 也是协程）。支持单个或列表。"""
        if audio is None or self._loop is None:
            return
        audios = audio if isinstance(audio, (list, tuple)) else [audio]
        async def _do():
            for a in audios:
                if a is None:
                    continue
                try:
                    r = a.pause()
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
        try:
            asyncio.run_coroutine_threadsafe(_do(), self._loop)
        except Exception:
            pass

    def _btn_sound(self):
        """按钮点击音效：安卓用ft.Audio，Windows用winsound。"""
        try:
            if _is_android():
                # 重连后页面重建可能导致音频控件从 page.services 丢失，先兜底重新挂载
                self._remount_audio_services()
                self._android_play(self._audio_click)
            else:
                self._play_sound("Windows Navigation Start.wav")
        except Exception:
            pass

    def _remount_audio_services(self):
        """重新挂载所有音频控件到页面（修复重连后点击音效失效的问题）。

        根因：flet 0.86 在页面重大更新（controls.clear + 重建）后，
        page.services 中的非视觉控件（ft.Audio）会被重置或丢失，
        导致底层音频播放器被释放，后续 play() 调用无声。
        每次播放前检查并重新挂载即可修复。
        """
        if not hasattr(self, "page") or self.page is None:
            return
        audio_controls = []
        for attr in ("_audio_click", "_audio_notify", "_audio_dial",
                     "_audio_ring", "_audio_hangup"):
            val = getattr(self, attr, None)
            if isinstance(val, (list, tuple)):
                audio_controls.extend([a for a in val if a is not None])
            elif val is not None:
                audio_controls.append(val)
        if not audio_controls:
            return

        # 获取当前已挂载的控件列表（兼容 page.services 和 page.overlay）
        try:
            if hasattr(self.page, "services"):
                mounted = list(self.page.services)
                for a in audio_controls:
                    if a not in mounted:
                        self.page.services.append(a)
                return
        except Exception:
            pass
        try:
            mounted = list(self.page.overlay)
            for a in audio_controls:
                if a not in mounted:
                    self.page.overlay.append(a)
        except Exception:
            pass

    # ==================== 页面入口 ====================
    async def setup(self, page):
        self.page = page
        self._loop = asyncio.get_running_loop()
        set_lang(load_language())  # 启动时应用上次选择的界面语言
        page.title = "LanTalk Mobile"
        page.padding = 0
        page.spacing = 0
        try:
            page.window.width = 400
            page.window.height = 800
        except Exception:
            pass
        # 全局主题：BLUE_700主色，SYSTEM跟随系统
        try:
            page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_700)
            self._load_theme_config()
            self._apply_theme_mode(self._theme_mode)
        except Exception:
            pass
        # 文件选择：Android用FilePicker，桌面端用tkinter（Flet0.86.5桌面端Unknown control）
        self._file_picker = None
        self._file_picker_ok = False
        if _is_android():
            try:
                # visible=False：避免安卓端FilePicker默认显示遮挡左侧界面
                self._file_picker = ft.FilePicker(visible=False)
                try:
                    self._file_picker.on_result = self._on_file_picked
                except Exception:
                    pass
                page.overlay.append(self._file_picker)
                try:
                    page.update()
                except Exception:
                    pass
                self._file_picker_ok = True
            except Exception as e:
                self._log(f"FilePicker init failed: {e}")
        # ===== 安卓端音效初始化（ft.Audio）=====
        # 注意：
        # 1. flet build apk 必须加 --include-packages flet_audio，否则原生音频插件不会打包进 APK。
        # 2. asset 路径以 "/" 开头（相对于 assets 目录），不要用文件系统绝对路径——
        #    APK 内 assets 打包在 Flutter asset bundle 中，os.path 路径无法访问。
        # 3. Android 端统一使用 MP3 格式（兼容性最好），文件放在 assets/ 目录。
        if _is_android():
            try:
                # 每套音效创建两个候选文件名（短名 + 桌面WAV风格名），播放时逐个尝试，
                # 兼容 assets 目录中实际使用的任意一套命名。
                self._audio_click = [_make_audio("/click.mp3", volume=0.4),
                                      _make_audio("/Windows Navigation Start.mp3", volume=0.4)]
                self._audio_notify = [_make_audio("/notify.mp3", volume=0.6),
                                       _make_audio("/notify.mp3", volume=0.6)]
                self._audio_dial = [_make_audio("/dial.mp3", volume=0.6),
                                     _make_audio("/Alarm08.mp3", volume=0.6)]
                self._audio_ring = [_make_audio("/ring.mp3", volume=0.6, loop=True),
                                     _make_audio("/Alarm03.mp3", volume=0.6, loop=True)]
                self._audio_hangup = [_make_audio("/hangup.mp3", volume=0.6),
                                       _make_audio("/Speech Misrecognition.mp3", volume=0.6)]
                # 展平列表后全部挂载到 page.services
                all_audios = []
                for _alist in [self._audio_click, self._audio_notify, self._audio_dial,
                                self._audio_ring, self._audio_hangup]:
                    all_audios.extend([a for a in _alist if a is not None])
                for _a in all_audios:
                    _mount_service(page, _a)
                self._log(f"Android audio effects initialized (fta={fta is not None}, {len(all_audios)} objects)")
            except Exception as e:
                self._log(f"Android audio init failed: {e}")
        await self.show_splash()

    # ==================== 本机IP获取 ====================
    def _get_local_ipv4(self):
        """获取本机IPv4地址。"""
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return t("未检测到")

    def _get_local_ipv6(self):
        """获取本机IPv6地址（用于界面显示），公网和内网都显示。"""
        from utils.network import get_local_ipv6, get_local_ipv6_private
        pub = get_local_ipv6()
        priv = get_local_ipv6_private()
        parts = []
        if pub:
            parts.append(f"{pub} ({t('公网')})")
        if priv:
            parts.append(f"{priv} ({t('内网')})")
        if not parts:
            return t("未检测到")
        return " | ".join(parts)

    def _get_all_ipv6(self):
        """获取全部原始IPv6地址（用于调试显示）。"""
        return get_all_ipv6()

    def _build_ip_mode_row(self):
        """构建IP版本切换按钮行（自动/仅IPv4/仅IPv6）。"""
        current_mode = load_ip_mode()
        # 初始化按钮引用列表
        if not hasattr(self, '_ip_mode_buttons'):
            self._ip_mode_buttons = []
        def _make_btn(label, mode):
            is_active = (current_mode == mode)
            btn = ft.ElevatedButton(
                label, expand=True, height=44,
                bgcolor=ft.Colors.BLUE_600 if is_active else None,
                color=ft.Colors.WHITE if is_active else None,
                on_click=lambda e, m=mode: self._on_ip_mode_change(m),
            )
            btn.data = f"ip_mode_{mode}"
            self._ip_mode_buttons.append(btn)
            return btn
        return ft.Row(controls=[
            _make_btn(t("自动"), "auto"),
            _make_btn(t("仅IPv4"), "ipv4"),
            _make_btn(t("仅IPv6"), "ipv6"),
        ], spacing=6)

    def _on_ip_mode_change(self, mode):
        """IP版本切换回调，保存到配置并更新客户端，同时更新所有按钮视觉状态。"""
        if mode in ("auto", "ipv4", "ipv6"):
            save_ip_mode(mode)
            self.client.set_ip_mode(mode)
            mode_name = {'auto': t('自动(优先IPv4)'), 'ipv4': t('仅IPv4'), 'ipv6': t('仅IPv6')}[mode]
            self._append_system(t("IP版本已切换为: {mode_name}").format(mode_name=mode_name))
            # 更新所有IP模式按钮的视觉状态（登录页+设置页的按钮都会更新）
            if hasattr(self, '_ip_mode_buttons'):
                for btn in self._ip_mode_buttons:
                    try:
                        btn_mode = btn.data.replace("ip_mode_", "")
                        is_active = (btn_mode == mode)
                        btn.bgcolor = ft.Colors.BLUE_600 if is_active else None
                        btn.color = ft.Colors.WHITE if is_active else None
                    except Exception:
                        pass
                try:
                    self.page.update()
                except Exception:
                    pass

    # ==================== 登录页 ====================
    async def show_splash(self):
        """入场启动画面（iOS 风格静态启动页）：渐变背景+Logo+淡入，2秒后自动进入登录页，点击跳过。

        flet 0.86.5 既没有 ft.Video 也没有 ft.WebView，无法播放 mp4 视频，
        改用静态启动画面 + 淡入动画实现入场效果。
        """
        self._splash_skipped = False

        async def _skip_to_login(e=None):
            if not getattr(self, "_splash_skipped", False):
                self._splash_skipped = True
                await self.show_login()

        # iOS 风格蓝紫渐变
        _splash_gradient = ft.LinearGradient(
            begin=ft.alignment.top_center, end=ft.alignment.bottom_center,
            colors=["#0A84FF", "#5E5CE6", "#BF5AF2"])

        # Logo：白色聊天气泡图标 + 蓝色渐变圆背景
        logo_icon = ft.Container(
            content=ft.Icon(ft.icons.CHAT_BUBBLE, size=48, color=ft.Colors.WHITE),
            width=96, height=96, border_radius=28,
            gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                                        colors=["#FFFFFF", "#E8E8ED"]),
            alignment=ft.alignment.center,
        )
        # 用蓝色图标代替白色（在白底上更清晰）
        logo_icon.content = ft.Icon(ft.icons.CHAT_BUBBLE, size=48, color="#0A84FF")

        title = ft.Text("LanTalk", size=36, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE)
        subtitle = ft.Text(t("局域网聊天"), size=15, color=ft.Colors.with_opacity(0.8, ft.Colors.WHITE))
        skip_hint = ft.Text(t("点击任意位置进入"), size=13, color=ft.Colors.with_opacity(0.6, ft.Colors.WHITE))

        splash_content = ft.Container(
            content=ft.Column(controls=[
                ft.Container(expand=True),
                logo_icon,
                ft.Container(height=20),
                title,
                ft.Container(height=6),
                subtitle,
                ft.Container(expand=True),
                skip_hint,
                ft.Container(height=30),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0, alignment=ft.MainAxisAlignment.CENTER),
            expand=True, gradient=_splash_gradient, opacity=0,
            animate_opacity=ft.Animation(500, ft.AnimationCurve.EASE_OUT),
            on_click=_skip_to_login,
        )

        # 先渲染再淡入
        self.page.controls.clear()
        self.page.controls.append(splash_content)
        self.page.update()
        await asyncio.sleep(0.05)
        splash_content.opacity = 1
        self.page.update()

        # 2秒后自动进入登录页
        async def _auto_skip():
            await asyncio.sleep(2.0)
            await _skip_to_login()
        self._ui(_auto_skip())

    async def show_login(self):
        host, port = load_server_config()
        # iOS 风格：蓝紫渐变背景
        _ios_gradient = ft.LinearGradient(
            begin=ft.alignment.top_center,
            end=ft.alignment.bottom_center,
            colors=["#0A84FF", "#5E5CE6", "#BF5AF2"],
        )

        # 输入框统一 iOS 风格：圆角、浮动标签、无下划线
        def _ios_field(**kwargs):
            defaults = dict(
                expand=True, height=50, border_radius=14,
                border_color=ft.Colors.with_opacity(0.2, ft.Colors.GREY_400),
                focused_border_color="#0A84FF",
                filled=True, fill_color=ft.Colors.with_opacity(0.6, ft.Colors.WHITE),
                text_size=15, content_padding=ft.padding.symmetric(horizontal=14, vertical=8),
            )
            defaults.update(kwargs)
            return ft.TextField(**defaults)

        host_field = _ios_field(value=str(host), label=t("服务器地址"), hint_text=t("IPv4/域名"))
        port_field = _ios_field(value=str(port), label=t("端口"), width=110)
        ipv6_field = _ios_field(label=t("IPv6地址（可选）"), hint_text=t("填了优先用IPv6"))
        user_field = _ios_field(label=t("昵称"), hint_text=t("请输入昵称"))
        pwd_field = _ios_field(label=t("密码"), hint_text=t("可留空"), password=True)
        err_text = ft.Text("", size=14, color=ft.Colors.RED_400, text_align=ft.TextAlign.CENTER)
        status_text = ft.Text("", size=14, color=ft.Colors.with_opacity(0.7, ft.Colors.WHITE), text_align=ft.TextAlign.CENTER)

        # iOS 风格按钮：蓝色渐变填充 + 大圆角
        login_btn = ft.Container(
            content=ft.Text(t("登 录"), size=16, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE),
            alignment=ft.alignment.center, height=52, border_radius=16, expand=True,
            gradient=ft.LinearGradient(begin=ft.alignment.center_left, end=ft.alignment.center_right,
                                        colors=["#0A84FF", "#5E5CE6"]),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        reg_btn = ft.Container(
            content=ft.Text(t("注 册"), size=16, weight=ft.FontWeight.W_600, color="#0A84FF"),
            alignment=ft.alignment.center, height=52, border_radius=16, expand=True,
            bgcolor=ft.Colors.with_opacity(0.85, ft.Colors.WHITE),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        self._login_err = err_text
        self._login_status = status_text

        def _set_busy(busy: bool):
            # Container 没有 disabled 属性，通过保存/置空 on_click 真正阻止点击
            if busy:
                login_btn._saved_on_click = login_btn.on_click
                reg_btn._saved_on_click = reg_btn.on_click
                login_btn.on_click = None
                reg_btn.on_click = None
            else:
                login_btn.on_click = getattr(login_btn, '_saved_on_click', None)
                reg_btn.on_click = getattr(reg_btn, '_saved_on_click', None)
            login_btn.opacity = 0.5 if busy else 1.0
            reg_btn.opacity = 0.5 if busy else 1.0
            try:
                self.page.update()
            except Exception:
                pass

        def _do_login(e):
            self._btn_sound()
            username = (user_field.value or "").strip()
            password = pwd_field.value or ""
            ipv6_addr = (ipv6_field.value or "").strip()
            host_val = (host_field.value or "").strip() or "127.0.0.1"
            connect_host = ipv6_addr if ipv6_addr else host_val
            try:
                port = int(str(port_field.value or "9999").strip() or 9999)
            except (TypeError, ValueError):
                err_text.value = t("端口必须是数字")
                self.page.update()
                return
            if not username:
                err_text.value = t("请输入昵称")
                self.page.update()
                return
            err_text.value = ""
            status_text.value = t("正在连接服务器...")
            _set_busy(True)
            self._login_buttons = (login_btn, reg_btn, _set_busy)
            self.do_login(username, password, connect_host, port)

        def _after_register(ok, msg):
            _set_busy(False)
            status_text.value = msg
            uname = (user_field.value or "").strip()
            pwd = pwd_field.value or ""
            self.page.update()
            if ok:
                status_text.value = t("注册成功，正在自动登录...")
                _set_busy(True)
                ipv6_addr = (ipv6_field.value or "").strip()
                host_val = (host_field.value or "").strip() or "127.0.0.1"
                connect_host = ipv6_addr if ipv6_addr else host_val
                try:
                    port = int(str(port_field.value or "9999").strip() or 9999)
                except (TypeError, ValueError):
                    port = 9999
                self.do_login(uname, pwd, connect_host, port)

        def _do_register(e):
            self._btn_sound()
            username = (user_field.value or "").strip()
            password = pwd_field.value or ""
            if not username:
                err_text.value = t("请输入昵称")
                self.page.update()
                return
            if len(password) < 4:
                err_text.value = t("密码至少 4 位（也可设置后牢记）")
                self.page.update()
                return
            err_text.value = ""
            status_text.value = t("正在注册...")
            _set_busy(True)
            self.do_register(username, password, _after_register)

        # 按钮点击缩放动画
        def _btn_tap(btn, handler):
            def _wrap(e):
                btn.scale = 0.94
                self.page.update()
                import asyncio
                async def _rebound():
                    await asyncio.sleep(0.1)
                    btn.scale = 1.0
                    self.page.update()
                    handler(e)
                self._ui(_rebound())
            return _wrap

        login_btn.on_click = _btn_tap(login_btn, _do_login)
        reg_btn.on_click = _btn_tap(reg_btn, _do_register)

        # 毛玻璃卡片
        glass_card = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[host_field, port_field], spacing=8),
                ipv6_field,
                user_field,
                pwd_field,
                ft.Container(height=4),
                self._build_ip_mode_row(),
                ft.Container(height=2),
                ft.Text(t("本机IPv4: {v4}  |  IPv6: {v6}").format(v4=self._get_local_ipv4(), v6=self._get_local_ipv6()),
                        size=11, color=ft.Colors.with_opacity(0.6, ft.Colors.GREY_600), text_align=ft.TextAlign.CENTER),
                err_text, status_text,
                ft.Container(height=4),
                ft.Row(controls=[login_btn, reg_btn], spacing=10),
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            padding=ft.padding.all(18), border_radius=24,
            bgcolor=ft.Colors.with_opacity(0.82, ft.Colors.WHITE),
            margin=ft.margin.symmetric(horizontal=16),
        )

        root = ft.Container(
            content=ft.Column(controls=[
                ft.Container(height=20),
                ft.Icon(ft.icons.CHAT_BUBBLE, size=56, color=ft.Colors.WHITE),
                ft.Text("LanTalk", size=30, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                ft.Text(t("移动端 {VERSION}").format(VERSION=VERSION), size=13,
                        color=ft.Colors.with_opacity(0.7, ft.Colors.WHITE)),
                ft.Container(height=16),
                glass_card,
                ft.Container(height=20),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4,
               alignment=ft.MainAxisAlignment.START, scroll=ft.ScrollMode.AUTO),
            padding=ft.padding.only(top=10, bottom=10), expand=True, gradient=_ios_gradient,
        )
        await self._mount_with_fade(root)

    def do_login(self, username, password, host, port):
        self._password = password
        self._auto_reconnect = True
        def worker():
            try:
                save_server_config(host, port)
                self.client.set_server_addr(host, port)
                ip_mode = load_ip_mode()
                # IPv6地址与IP模式联动：host是IPv6格式就强制用ipv6模式，避免协议不匹配连不上
                if ":" in host and "." not in host.split("%")[0] and ip_mode != "ipv6":
                    ip_mode = "ipv6"
                    save_ip_mode("ipv6")
                self.client.set_ip_mode(ip_mode)
                self.client.connect(host, port, ip_mode=ip_mode)
                payload = self.client.login(username, password)
                if payload.get("ok") or payload.get("type") == "login_ok":
                    self._reconnect_attempts = 0  # 登录成功，重置重连计数
                    self.client.start_receive(
                        self._on_message, self._on_connection_close,
                        on_kicked=self._on_kicked, on_pong=self._on_pong)
                    self._ui(self.show_chat())
                else:
                    self._show_login_error(payload.get("message", t("登录失败")))
                    try: self.client.close()
                    except Exception: pass
            except Exception as e:
                self._show_login_error(t("连接失败：{e}").format(e=e))
        threading.Thread(target=worker, daemon=True).start()

    def do_register(self, username, password, on_result):
        def worker():
            try:
                host, port = load_server_config()
                self.client.set_server_addr(host, port)
                self.client.connect(host, port, ip_mode=load_ip_mode())
                payload = self.client.register(username, password)
                try: self.client.close()
                except Exception: pass
                on_result(payload.get("ok", False), payload.get("message", t("注册完成")))
            except Exception as e:
                on_result(False, t("注册失败：{e}").format(e=e))
        threading.Thread(target=worker, daemon=True).start()

    def _show_login_error(self, msg):
        def _set():
            if self._login_err is not None:
                self._login_err.value = msg
                self.page.update()
            else:
                # 重连场景：已在聊天页，_login_err 为 None，用系统消息显示
                self._append_system(f"⚠️ {msg}")
                # 自动重连场景：本次失败后继续调度下一次重试（形成完整重试链）
                if getattr(self, "_auto_reconnect", False) and self.client.username:
                    self._do_auto_reconnect()
            # 登录失败：恢复登录/注册按钮可点击（仅在登录页时操作，重连场景按钮已销毁）
            if self._login_err is not None:
                btns = getattr(self, "_login_buttons", None)
                if btns:
                    try:
                        _, _, _set_busy = btns
                        _set_busy(False)
                    except Exception:
                        pass
        self._ui(_set)

    # ==================== 主界面（聊天） ====================
    async def show_chat(self):
        # iOS 风格颜色
        _ios_bg = "#F2F2F7"
        _ios_glass = ft.Colors.with_opacity(0.85, ft.Colors.WHITE)
        _ios_gradient = ft.LinearGradient(
            begin=ft.alignment.center_left, end=ft.alignment.center_right,
            colors=["#0A84FF", "#5E5CE6"])

        # 按钮点击缩放包装
        def _tap_scale(btn, handler):
            def _wrap(e):
                self._btn_sound()
                btn.scale = 0.88
                self.page.update()
                import asyncio
                async def _rebound():
                    await asyncio.sleep(0.1)
                    btn.scale = 1.0
                    self.page.update()
                    handler(e)
                self._ui(_rebound())
            return _wrap

        # 顶部状态栏（毛玻璃）
        self._net_text = ft.Text(t("延迟 --ms  丢包 --%"), size=13, color=ft.Colors.with_opacity(0.6, ft.Colors.GREY_800))
        self._nick_text = ft.Text(self.client.username or "", size=15, weight=ft.FontWeight.W_600)
        settings_btn = ft.Container(
            content=ft.Icon(ft.icons.SETTINGS, size=20, color="#0A84FF"),
            width=36, height=36, border_radius=18, alignment=ft.alignment.center,
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        settings_btn.on_click = _tap_scale(settings_btn, self._open_settings)
        top_card = ft.Container(
            content=ft.Row(controls=[self._net_text, ft.Container(expand=True), self._nick_text, settings_btn],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            border_radius=16, bgcolor=_ios_glass,
            margin=ft.margin.symmetric(horizontal=10, vertical=6),
        )

        # 在线设备列表（毛玻璃卡片）
        self._device_row = ft.Row(controls=[], wrap=True, spacing=6, run_spacing=6)
        add_friend_btn = ft.Container(
            content=ft.Icon(ft.icons.PERSON_ADD, size=20, color="#0A84FF"),
            width=36, height=36, border_radius=18, alignment=ft.alignment.center,
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        add_friend_btn.on_click = _tap_scale(add_friend_btn, self._open_add_friend)
        device_card = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[ft.Text(t("在线设备"), size=13, color=ft.Colors.with_opacity(0.5, ft.Colors.GREY_800)),
                                  ft.Container(expand=True), add_friend_btn]),
                self._device_row,
            ], spacing=6),
            padding=ft.padding.symmetric(horizontal=14, vertical=10),
            border_radius=16, bgcolor=_ios_glass,
            margin=ft.margin.symmetric(horizontal=10, vertical=4),
        )

        # 消息区（浅灰背景，无卡片边框）
        self._message_list = ft.ListView(expand=True, spacing=6, padding=ft.padding.symmetric(horizontal=10, vertical=8), auto_scroll=True)
        message_area = ft.Container(content=self._message_list, expand=True, bgcolor=_ios_bg)

        # 底部输入栏（毛玻璃）
        self._input_field = ft.TextField(
            expand=True, height=40, hint_text=t("输入消息..."),
            border_radius=20, filled=True,
            fill_color=ft.Colors.with_opacity(0.5, ft.Colors.GREY_100),
            border_color=ft.Colors.TRANSPARENT, focused_border_color="#0A84FF",
            content_padding=ft.padding.symmetric(horizontal=14, vertical=6), text_size=15,
            on_submit=lambda e: self._ui(self.send_message(self._input_field.value or "")))
        file_btn = ft.Container(content=ft.Icon(ft.icons.ATTACH_FILE, size=22, color="#0A84FF"),
                                 width=38, height=38, border_radius=19, alignment=ft.alignment.center,
                                 animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT))
        file_btn.on_click = _tap_scale(file_btn, self._pick_file)
        voice_btn = ft.Container(content=ft.Icon(ft.icons.MIC, size=22, color="#0A84FF"),
                                  width=38, height=38, border_radius=19, alignment=ft.alignment.center,
                                  animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT))
        voice_btn.on_click = _tap_scale(voice_btn, self._start_voice)
        send_btn = ft.Container(content=ft.Icon(ft.icons.SEND, size=20, color=ft.Colors.WHITE),
                                 width=40, height=40, border_radius=20, alignment=ft.alignment.center,
                                 gradient=_ios_gradient,
                                 animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT))
        send_btn.on_click = _tap_scale(send_btn, lambda e: self._ui(self.send_message(self._input_field.value or "")))
        self._normal_input = ft.Row(controls=[self._input_field, file_btn, voice_btn, send_btn], spacing=6,
                                     vertical_alignment=ft.CrossAxisAlignment.CENTER)
        input_card = ft.Container(
            content=self._normal_input,
            padding=ft.padding.symmetric(horizontal=10, vertical=8),
            border_radius=20, bgcolor=_ios_glass,
            margin=ft.margin.symmetric(horizontal=10, vertical=6),
        )

        self._chat_root = ft.Container(
            content=ft.Column(controls=[top_card, device_card, message_area, input_card], spacing=0, expand=True),
            bgcolor=_ios_bg, expand=True, padding=ft.padding.only(top=4, bottom=4),
        )
        await self._mount_with_fade(self._chat_root)

        # 初始化
        self.file_transfer = FileTransferClient(self.client)
        # 注意：不要清空 conversations，重连后需要保留历史聊天记录
        await self.refresh_friend_list()
        self._start_heartbeat()
        self._start_net_ui_refresh()
        self._start_friend_refresh()
        # 重连后恢复历史消息到新的 message_list
        self._refresh_messages(self.current_conv)
        self._append_system(t("已连接到聊天室"))
        # 重连后页面重建，重新挂载音频控件（修复点击音效失效）
        self._remount_audio_services()
        # 重连后恢复语音房间注册（TCP断开时服务器清理了语音注册，需重新voice_join）
        if self.voice_call and getattr(self.voice_call, "_running", False):
            self._ui(self._voice_rejoin_room())
        self._log(f"show_chat done, version={VERSION}")

    # ==================== 消息渲染 ====================
    def _refresh_messages(self, conv_id):
        if conv_id != self.current_conv:
            return
        if self._message_list is None:
            return
        msgs = self.conversations.get(conv_id, [])
        controls = []
        last_incoming_wrap = None
        last_incoming_text = None
        for i, m in enumerate(msgs):
            is_last = (i == len(msgs) - 1)
            if isinstance(m, FileMessage):
                controls.append(self._build_file_bubble(m))
            elif getattr(m, "is_voice_invite", False):
                controls.append(self._build_voice_invite(m))
            elif m.is_system:
                controls.append(ft.Container(content=ft.Text(m.text, size=14, opacity=0.5),
                                             alignment=ft.alignment.center, padding=ft.padding.symmetric(vertical=2)))
            else:
                need_animate = (is_last and not m.is_self and m.text)
                if need_animate:
                    wrap = self._build_msg_bubble(m, animate_in=True)
                    last_incoming_wrap = wrap
                    last_incoming_text = m.text
                else:
                    wrap = self._build_msg_bubble(m, fade_in=(is_last and m.is_self))
                controls.append(wrap)
        self._message_list.controls = controls
        self._ui(self.page.update)
        # 启动最后一条别人消息的入场动画（展开+打字机）
        if last_incoming_wrap is not None and last_incoming_text:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._animate_incoming_message(last_incoming_wrap, last_incoming_text), self._loop)
            except Exception as ex:
                self._log(f"start animate incoming failed: {ex}, fallback to immediate display")
                # fallback：动画启动失败时立即显示完整消息
                try:
                    tc = getattr(last_incoming_wrap, "_msg_text_ctrl", None)
                    cc = getattr(last_incoming_wrap, "_msg_card", None)
                    if tc: tc.value = last_incoming_text
                    if cc: cc.scale = ft.Scale(scale_x=1.0, scale_y=1.0)
                    last_incoming_wrap.opacity = 1.0
                    self.page.update()
                except Exception:
                    pass

    async def _animate_incoming_message(self, wrap_ctrl, full_text):
        """收到别人消息的入场动画：展开(220ms) → 打字机(0.05s/字)。"""
        try:
            text_ctrl = getattr(wrap_ctrl, "_msg_text_ctrl", None)
            card_ctrl = getattr(wrap_ctrl, "_msg_card", None)
            if text_ctrl is None or card_ctrl is None:
                return
            if self._message_list is None or wrap_ctrl not in self._message_list.controls:
                return
            card_ctrl.scale = ft.Scale(scale_x=1.0, scale_y=1.0)
            wrap_ctrl.opacity = 1.0
            self.page.update()
            await asyncio.sleep(0.22)
            if self._message_list is None or wrap_ctrl not in self._message_list.controls:
                return
            for i in range(1, len(full_text) + 1):
                if self._message_list is None or wrap_ctrl not in self._message_list.controls:
                    return
                text_ctrl.value = full_text[:i]
                self.page.update()
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._log(f"animate_incoming_message error: {e}")

    def _build_msg_bubble(self, m, fade_in=False, animate_in=False):
        """消息气泡：[时间|用户名]行 + 气泡Card。animate_in:别人消息入场动画。"""
        # iOS 风格消息气泡
        msg_text = ft.Text("" if animate_in else m.text, size=16, selectable=True,
                            color=ft.Colors.WHITE if m.is_self else ft.Colors.GREY_900,
                            width=260)
        if m.is_self:
            bubble_card = ft.Container(
                content=ft.Container(content=msg_text, padding=ft.padding.symmetric(horizontal=14, vertical=9)),
                border_radius=ft.BorderRadius(top_left=18, top_right=18, bottom_left=18, bottom_right=4),
                gradient=ft.LinearGradient(begin=ft.alignment.center_left, end=ft.alignment.center_right,
                                            colors=["#0A84FF", "#5E5CE6"]),
            )
        else:
            bubble_card = ft.Container(
                content=ft.Container(content=msg_text, padding=ft.padding.symmetric(horizontal=14, vertical=9)),
                border_radius=ft.BorderRadius(top_left=18, top_right=18, bottom_left=4, bottom_right=18),
                bgcolor=ft.Colors.with_opacity(0.9, ft.Colors.WHITE),
            )
        # 时间（HH:MM），主题自适应用opacity，调大字号更明显
        time_str = m.timestamp.strftime("%H:%M") if hasattr(m, "timestamp") and m.timestamp else ""
        time_text = ft.Text(time_str, size=13, opacity=0.5) if time_str else ft.Container(width=0, height=0)
        # 用户名：别人的消息显示，调大字号+提高对比度，自己的不显示
        sender_text = ft.Text(m.sender, size=14, opacity=0.7, weight=ft.FontWeight.W_500) if (not m.is_self and m.sender) else ft.Container(width=0, height=0)
        # 用SPACE_BETWEEN确保时间左、用户名右，占满整行宽度
        meta_row = ft.Row(controls=[time_text, sender_text], spacing=4,
                          alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                          vertical_alignment=ft.CrossAxisAlignment.CENTER)
        if animate_in:
            bubble_card.scale = ft.Scale(scale_x=1.0, scale_y=0.0)
            bubble_card.animate_scale = ft.Animation(220, ft.AnimationCurve.EASE_OUT)
        row = ft.Row(controls=[ft.Container(expand=True), bubble_card] if m.is_self else [bubble_card, ft.Container(expand=True)],
                      alignment=ft.MainAxisAlignment.END if m.is_self else ft.MainAxisAlignment.START)
        inner_col = ft.Column(controls=[meta_row, row], spacing=1,
                              horizontal_alignment=ft.CrossAxisAlignment.END if m.is_self else ft.CrossAxisAlignment.START)
        animated_wrap = ft.Container(content=inner_col, opacity=0 if (fade_in or animate_in) else 1,
                                      animate_opacity=ft.Animation(220 if animate_in else 250, ft.AnimationCurve.EASE_OUT),
                                      margin=ft.margin.symmetric(vertical=1))
        if fade_in and not animate_in:
            def _do_fade():
                try:
                    animated_wrap.opacity = 1
                    self.page.update()
                except Exception:
                    pass
            threading.Timer(0.08, _do_fade).start()
        animated_wrap._msg_text_ctrl = msg_text
        animated_wrap._msg_card = bubble_card
        return animated_wrap

    def _build_file_bubble(self, m):
        status_map = {"uploading": t("发送中..."), "downloading": t("下载中..."), "completed": t("完成"), "failed": t("失败"), "pending": t("待处理")}
        size_txt = f"{m.file_size/1024:.1f} KB" if m.file_size else ""
        progress = self._file_progress.get(m.file_id, 0)
        lines = [f"📁 {m.filename}", size_txt, status_map.get(m.status, m.status)]
        if m.download_path:
            lines.append(t("已保存: {path}").format(path=m.download_path))
        bubble = ft.Container(
            content=ft.Column(controls=[
                ft.Text(x, size=15, color=ft.Colors.BLUE_800 if i == 0 else ft.Colors.GREY_600)
                for i, x in enumerate(lines)
            ] + ([ft.ProgressBar(value=progress/100, width=200)] if m.status in ("uploading","downloading") and progress > 0 else []),
            spacing=2),
            bgcolor=ft.Colors.BLUE_50, border_radius=10, padding=ft.padding.all(10),
        )
        return ft.Row(controls=[bubble, ft.Container(expand=True)] if m.is_self else [ft.Container(expand=True), bubble],
                      alignment=ft.MainAxisAlignment.END if m.is_self else ft.MainAxisAlignment.START)

    def _build_voice_invite(self, m):
        """公共语音房间邀请卡片（含加入按钮）。"""
        def _join(e):
            self._ui(self.join_public_voice(m.room_id, m.host))
        return ft.Container(
            content=ft.Row(controls=[
                ft.Icon(ft.icons.MIC, size=20, color=ft.Colors.BLUE_700),
                ft.Text(t("🔊 {host} 发起了公共语音通话").format(host=m.host), size=16, color=ft.Colors.BLUE_700, expand=True),
                ft.ElevatedButton(t("加入"), height=36, bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE, on_click=_join),
            ], spacing=8),
            bgcolor=ft.Colors.BLUE_50, border_radius=10, padding=ft.padding.all(10),
        )

    def _append_system(self, text):
        self.conversations.setdefault("public", []).append(ChatMessage(text=text, is_system=True))
        self._refresh_messages("public")

    # ==================== 消息接收/发送 ====================
    def _localize_system(self, text):
        """服务端下发的中文系统消息（加入/离开聊天室）按当前语言本地化；逻辑判断仍用原文。"""
        for suffix, key in (("加入了聊天室", "{who} 加入了聊天室"),
                            ("离开了聊天室", "{who} 离开了聊天室")):
            if text.endswith(suffix):
                return t(key).format(who=text[:-len(suffix)])
        return text

    def _on_message(self, payload):
        self._ui(self._handle_message(payload))

    async def _handle_message(self, payload):
        try:
            if payload.get("msg_type") == "private":
                sender = payload.get("sender", "")
                target = payload.get("target", "")
                text = payload.get("text", "")
                is_self = (sender == self.client.username)
                conv_id = f"friend:{target}" if is_self else f"friend:{sender}"
                self.conversations.setdefault(conv_id, [])
                # 文件通知
                is_file, file_id, file_name = FileTransferClient.parse_file_notify(text)
                if is_file and not is_self:
                    await self._handle_incoming_file(file_id, file_name, sender, conv_id)
                    return
                # 语音信令
                voice_sig = parse_voice_signal(text)
                if voice_sig:
                    sig_type, sig_caller, sig_callee, sig_extra = voice_sig
                    await self._handle_voice_signal(sig_type, sig_caller, sig_callee, sig_extra, is_self)
                    return
                # 自己发的私聊消息已在 send_message 中本地添加，跳过服务器回传避免重复
                if not is_self:
                    self.conversations[conv_id].append(ChatMessage(text=text, is_self=is_self, sender=sender))
                    if conv_id != self.current_conv:
                        self._unread[conv_id] = self._unread.get(conv_id, 0) + 1
                        self._refresh_friend_chips()
                    else:
                        self._refresh_messages(conv_id)
                    self._play_notify_sound()
            else:
                text = payload.get("text", "")
                sender, actual_text = "", text
                if ": " in text:
                    parts = text.split(": ", 1)
                    sender, actual_text = parts[0], parts[1]
                is_self = bool(sender) and sender == self.client.username
                is_system = (sender == "")
                # 文件通知
                is_file, file_id, file_name = FileTransferClient.parse_file_notify(actual_text)
                if is_file and not is_self:
                    await self._handle_incoming_file(file_id, file_name, sender, "public")
                    return
                # 语音信令
                voice_sig = parse_voice_signal(actual_text)
                if voice_sig:
                    sig_type, sig_caller, sig_callee, sig_extra = voice_sig
                    await self._handle_voice_signal(sig_type, sig_caller, sig_callee, sig_extra, is_self)
                    return
                # 自己发的公共消息已经在 send_message 中本地添加了，跳过避免重复
                if not is_self:
                    show_text = self._localize_system(actual_text) if is_system else actual_text
                    self.conversations.setdefault("public", []).append(
                        ChatMessage(text=show_text, is_self=is_self, sender=sender, is_system=is_system))
                if is_system and ("加入了聊天室" in actual_text or "离开了聊天室" in actual_text):
                    await self.refresh_friend_list()
                if self.current_conv == "public":
                    self._refresh_messages("public")
                if not is_self and not is_system:
                    self._play_notify_sound()
        except Exception as e:
            self._log(f"_handle_message EXCEPTION: {e}")

    def _on_connection_close(self):
        self._stop_heartbeat()
        self._stop_net_ui_refresh()
        if self._friend_timer:
            self._friend_timer.cancel(); self._friend_timer = None
        self._ui(self._append_system, t("⚠️ 连接已断开，正在尝试重连..."))
        if getattr(self, "_auto_reconnect", False) and self.client.username:
            self._ui(self._do_auto_reconnect)

    def _do_auto_reconnect(self, e=None):
        """自动重连（带退避和次数上限，避免断线时无限快速重连刷屏）。"""
        if not getattr(self, "_auto_reconnect", False) or not self.client.username:
            return
        self._reconnect_attempts += 1
        attempt = self._reconnect_attempts
        if attempt > 10:
            self._append_system(t("⚠️ 多次重连失败，请检查网络后手动重连"))
            self._reconnect_attempts = 0
            return
        # 退避：2,4,6...秒，上限20秒
        delay = min(2 * attempt, 20)
        self._append_system(t("第 {attempt}/10 次重连，{delay} 秒后尝试...").format(attempt=attempt, delay=delay))
        def _do():
            host, port = load_server_config()
            self.do_login(self.client.username or "", self._password, host, port)
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        self._reconnect_timer = threading.Timer(delay, _do)
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()

    async def send_message(self, text):
        self._btn_sound()
        text = (text or "").strip()
        if not text:
            return
        if self._input_field is not None:
            self._input_field.value = ""
        if not self.client.sock:
            self._append_system(t("连接已断开，请重新连接"))
            self._ui(self.page.update)
            return
        try:
            if self.current_conv == "public":
                self.client.send_text(text)
            else:
                target = self.current_conv[7:]
                await self._run_in_thread(lambda: self.client._temp_request("private_chat", {
                    "username": self.client.username, "target": target, "text": text}))
            # 发送后立即本地显示自己的消息，不需要等服务器广播
            self.conversations.setdefault(self.current_conv, [])
            self.conversations[self.current_conv].append(
                ChatMessage(text=text, is_self=True, sender=self.client.username or ""))
            self._refresh_messages(self.current_conv)
        except Exception as e:
            self._append_system(t("发送失败：{e}").format(e=e))
        self._ui(self.page.update)

    # ==================== 好友 / 设备列表 ====================
    async def get_friend_list(self):
        def worker():
            try:
                payload = self.client._temp_request("get_friends", {"username": self.client.username})
                return payload.get("friends", [])
            except Exception:
                return []
        return await self._run_in_thread(worker)

    async def refresh_friend_list(self):
        self.friends = await self.get_friend_list()
        self._refresh_friend_chips()

    def _refresh_friend_chips(self):
        row = self._device_row
        if row is None:
            return
        chips = [self._make_device_chip(t("公共聊天室"), "public", True, self._unread.get("public", 0))]
        for f in self.friends:
            # 兼容 dict（{username,online}）和纯用户名字符串两种格式
            if isinstance(f, dict):
                fname = f.get("username", "?")
                fonline = f.get("online", False)
            else:
                fname, fonline = str(f), False
            chips.append(self._make_device_chip(
                fname, f"friend:{fname}",
                fonline, self._unread.get(f"friend:{fname}", 0)))
        row.controls = chips
        self._ui(self.page.update)

    def _make_device_chip(self, name, conv_id, online, unread=0):
        color = ft.Colors.GREEN_700 if online else ft.Colors.GREY_600
        if conv_id == "public":
            color = ft.Colors.BLUE_700
        is_current = (conv_id == self.current_conv)
        badge = ft.Text(f" {unread}", size=13, color=ft.Colors.WHITE) if unread > 0 else ft.Text("")
        return ft.Container(
            content=ft.Row(controls=[
                ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                ft.Text(name, size=16, color=ft.Colors.WHITE if is_current else ft.Colors.BLACK87),
                badge,
            ], spacing=5),
            bgcolor=ft.Colors.BLUE_500 if is_current else ft.Colors.GREY_200,
            border_radius=16, padding=ft.padding.symmetric(horizontal=12, vertical=8),
            on_click=lambda e, cid=conv_id: self._switch_conv(cid),
        )

    def _switch_conv(self, conv_id):
        self.current_conv = conv_id
        self._unread[conv_id] = 0
        self.conversations.setdefault(conv_id, [])
        self._refresh_messages(conv_id)
        self._refresh_friend_chips()

    # ---------- 加好友 ----------
    def _open_add_friend(self, e):
        self._btn_sound()
        field = ft.TextField(expand=True, dense=True, hint_text=t("输入对方昵称"))
        msg = ft.Text("", size=14, color=ft.Colors.GREY_600)
        def _submit(ev):
            name = (field.value or "").strip()
            if not name:
                msg.value = t("昵称不能为空")
                self.page.update()
                return
            self.page.pop_dialog()
            self._do_add_friend(name)
        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("加好友")),
            content=ft.Column(controls=[field, msg], width=280, tight=True, spacing=8),
            actions=[ft.TextButton(t("取消"), on_click=lambda x: self.page.pop_dialog()),
                     ft.ElevatedButton(t("发送"), on_click=_submit)])
        self.page.show_dialog(dlg)

    def _do_add_friend(self, username):
        def worker():
            try:
                payload = self.client._temp_request("add_friend", {
                    "username": self.client.username, "target": username})
                ok = payload.get("ok", False)
                self._append_system(t("加好友{result}：{msg}").format(result=(t("成功") if ok else t("失败")), msg=payload.get("message","")))
                if ok:
                    self._ui(self.refresh_friend_list())
            except Exception as e:
                self._append_system(t("加好友失败：{e}").format(e=e))
        threading.Thread(target=worker, daemon=True).start()

    # ==================== 文件收发 ====================
    def _get_save_dir(self):
        if _is_android() or os.path.exists("/storage/emulated/0"):
            return "/storage/emulated/0/Download/LanTalkFiles"
        return os.path.join(os.path.expanduser("~"), "Downloads", "LanTalkFiles")

    def _pick_file(self, e):
        self._btn_sound()
        self._show_file_send_dialog()

    def _resolve_android_content_uri(self, uri_str):
        """Android content URI 复制到临时文件，返回文件系统路径；非 content URI 直接返回原路径。"""
        if not uri_str or not uri_str.startswith("content://"):
            return uri_str
        try:
            from jnius import autoclass
            PythonActivity = autoclass("org.kivy.android.PythonActivity")
            Uri = autoclass("android.net.Uri")
            uri = Uri.parse(uri_str)
            activity = PythonActivity.mActivity
            resolver = activity.getContentResolver()
            # 尝试获取原始文件名
            display_name = "picked_file"
            try:
                cursor = resolver.query(uri, None, None, None, None)
                if cursor and cursor.moveToFirst():
                    name_idx = cursor.getColumnIndex("_display_name")
                    if name_idx >= 0:
                        val = cursor.getString(name_idx)
                        if val:
                            display_name = val
                    cursor.close()
            except Exception:
                pass
            # 复制到临时文件
            import tempfile
            ext = os.path.splitext(display_name)[1]
            fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(fd)
            input_stream = resolver.openInputStream(uri)
            try:
                with open(tmp_path, "wb") as out:
                    buf = bytearray(8192)
                    while True:
                        n = input_stream.read(buf)
                        if n <= 0:
                            break
                        out.write(bytes(buf[:n]))
            finally:
                input_stream.close()
            self._log(f"content URI resolved to: {tmp_path}")
            return tmp_path
        except Exception as e:
            self._log(f"content URI resolve failed: {e}")
            return uri_str

    def _pick_file_with_fallback(self, path_display, send_btn, dlg):
        """选择文件：优先FilePicker，失败用tkinter兜底（桌面端，独立线程避免阻塞UI）。"""
        self._pending_file_dialog = dlg
        self._pending_path_display = path_display
        self._pending_send_btn = send_btn
        if self._file_picker_ok and self._file_picker:
            try:
                self._file_picker.pick_files(dialog_title=t("选择要发送的文件"), allow_multiple=False)
                return
            except Exception as ex:
                self._log(f"FilePicker failed: {ex}, fallback to tkinter")
        # tkinter 兜底：必须放独立线程，否则 askopenfilename 阻塞会冻结整个 Flet 窗口
        def _tk_worker():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                file_path = filedialog.askopenfilename(title=t("选择要发送的文件"))
                root.destroy()
                if file_path:
                    self._ui(self._on_file_picked_path, file_path)
            except Exception as ex:
                self._log(f"tkinter pick failed: {ex}")
                def _err():
                    if path_display:
                        path_display.value = t("文件选择失败，请重试")
                        self.page.update()
                self._ui(_err)
        threading.Thread(target=_tk_worker, daemon=True).start()

    def _on_file_picked_path(self, filepath):
        """处理文件路径（FilePicker和tkinter共用）。Android content URI 先解析为临时文件。"""
        try:
            filepath = self._resolve_android_content_uri(filepath)
            if filepath and os.path.exists(filepath):
                if self._pending_path_display is not None:
                    self._pending_path_display.value = filepath
                    if self._pending_send_btn is not None:
                        # iOS 风格按钮：用 opacity 控制启用/禁用（Container 没有 disabled 属性）
                        try:
                            self._pending_send_btn.opacity = 1.0
                        except Exception:
                            self._pending_send_btn.disabled = False
                    self.page.update()
                else:
                    self._ui(self.send_file(filepath))
        except Exception as ex:
            self._log(f"on_file_picked_path error: {ex}")

    def _on_file_picked(self, e):
        try:
            if e.files and len(e.files) > 0:
                self._on_file_picked_path(e.files[0].path)
        except Exception as ex:
            self._log(f"on_file_picked error: {ex}")

    def _show_file_send_dialog(self):
        """发送文件弹窗（iOS 风格）：选择文件按钮 + 只读路径展示 + 发送按钮。"""
        _ios_gradient = ft.LinearGradient(
            begin=ft.alignment.center_left, end=ft.alignment.center_right,
            colors=["#0A84FF", "#5E5CE6"])

        path_display = ft.TextField(
            read_only=True, height=44, hint_text=t("未选择文件"),
            border_radius=12, filled=True,
            fill_color=ft.Colors.with_opacity(0.5, ft.Colors.GREY_100),
            border_color=ft.Colors.TRANSPARENT, text_size=14,
            content_padding=ft.padding.symmetric(horizontal=12, vertical=8))

        # 发送按钮（iOS 风格渐变）
        send_file_btn = ft.Container(
            content=ft.Text(t("发送"), size=16, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE),
            alignment=ft.alignment.center, height=48, border_radius=14,
            gradient=_ios_gradient, opacity=0.4, expand=True,
            animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT))

        def _pick(ev):
            self._btn_sound()
            self._animate_btn(pick_btn)
            pick_photo_native(self, path_display, send_file_btn, dlg)

        # 选择文件按钮（iOS 风格渐变）
        pick_btn = ft.Container(
            content=ft.Row(controls=[
                ft.Icon(ft.icons.FOLDER_OPEN, size=20, color=ft.Colors.WHITE),
                ft.Text(t("选择文件"), size=16, weight=ft.FontWeight.W_600, color=ft.Colors.WHITE),
            ], spacing=8, alignment=ft.MainAxisAlignment.CENTER),
            alignment=ft.alignment.center, height=48, border_radius=14,
            gradient=_ios_gradient, expand=True,
            animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT))
        pick_btn.on_click = _pick

        # 发送按钮点击
        def _send(ev):
            if send_file_btn.opacity < 0.5:
                return
            self._btn_sound()
            self._animate_btn(send_file_btn)
            self._confirm_send_file(path_display.value, dlg)
        send_file_btn.on_click = _send

        content = ft.Container(
            content=ft.Column(controls=[
                ft.Text(t("选择要发送的文件"), size=16, weight=ft.FontWeight.W_600),
                ft.Container(height=12),
                pick_btn,
                ft.Container(height=10),
                path_display,
                ft.Container(height=10),
                send_file_btn,
            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            padding=ft.padding.all(4))

        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("发送文件"), weight=ft.FontWeight.W_700),
            content=ft.Container(content=content, width=320, opacity=0,
                                  animate_opacity=ft.Animation(250, ft.AnimationCurve.EASE_OUT)),
            actions=[ft.TextButton(t("取消"), on_click=lambda x: self._close_dialog_with_fade(dlg))])
        self._active_dialog = dlg
        self.page.show_dialog(dlg)
        def _fade_in():
            try:
                dlg.content.opacity = 1
                self.page.update()
            except Exception:
                pass
        threading.Timer(0.05, _fade_in).start()

    def _confirm_send_file(self, filepath, dlg):
        self._btn_sound()
        self._close_dialog_with_fade(dlg)
        self._ui(self.send_file(filepath))

    async def send_file(self, filepath):
        if not self.file_transfer or self.file_transfer.is_busy():
            self._append_system(t("已有文件正在传输，请稍候"))
            return
        filepath = (filepath or "").strip()
        if not filepath or not os.path.exists(filepath):
            self._append_system(t("文件不存在"))
            return
        filename = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        is_private = self.current_conv.startswith("friend:")
        target_user = self.current_conv[7:] if is_private else ""
        fm = FileMessage(filename=filename, file_size=file_size, is_self=True,
                         sender=self.client.username or "", status="uploading")
        self.conversations.setdefault(self.current_conv, []).append(fm)
        self._refresh_messages(self.current_conv)

        def on_progress(file_id, percent):
            if file_id and not fm.file_id:
                fm.file_id = file_id
            self._file_progress[file_id or filename] = percent
            self._refresh_messages(self.current_conv)

        def worker():
            ok, file_id, fname, err = self.file_transfer.upload_file(filepath, target_user, on_progress=on_progress)
            if ok:
                fm.file_id = file_id
                fm.status = "completed"
                notify_msg = f"__FILE__:{file_id}:{fname}"
                try:
                    if is_private:
                        self.client._temp_request("private_chat", {
                            "username": self.client.username, "target": target_user, "text": notify_msg})
                    else:
                        self.client.send_text(notify_msg)
                except Exception as ex:
                    self._log(f"notify failed: {ex}")
            else:
                fm.status = "failed"
                self._append_system(t("文件发送失败: {err}").format(err=err))
            self._refresh_messages(self.current_conv)
        threading.Thread(target=worker, daemon=True).start()

    async def _handle_incoming_file(self, file_id, filename, sender, conv_id):
        if not self.file_transfer:
            return
        fm = FileMessage(filename=filename, file_id=file_id, is_self=False, sender=sender, status="downloading")
        self.conversations.setdefault(conv_id, []).append(fm)
        self._refresh_messages(conv_id)
        save_dir = self._save_dir
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception:
            save_dir = os.path.join(os.path.expanduser("~"), "LanTalkFiles")
            os.makedirs(save_dir, exist_ok=True)

        def on_progress(file_id, percent):
            self._file_progress[file_id] = percent
            self._refresh_messages(conv_id)

        def worker():
            ok, filepath, err = self.file_transfer.download_file(file_id, save_dir, on_progress=on_progress)
            if ok:
                fm.status = "completed"
                fm.download_path = filepath
                try: fm.file_size = os.path.getsize(filepath)
                except Exception: pass
            else:
                fm.status = "failed"
            self._refresh_messages(conv_id)
        threading.Thread(target=worker, daemon=True).start()

    # ==================== 语音通话（复用 VoiceCall + 信令） ====================
    def _get_private_room_id(self, u1, u2):
        return "private:" + ":".join(sorted([u1, u2]))

    async def _voice_join_room(self, room_id):
        """加入语音房间（异步，阻塞网络调用放线程）。"""
        if not self.voice_call:
            return False
        try:
            local_port = self.voice_call.bind_socket()
            result = await self._run_in_thread(lambda: self.client._temp_request("voice_join", {
                "username": self.client.username, "room_id": room_id, "udp_port": local_port}))
            if not result.get("ok"):
                self._append_system(t("语音连接失败: {e}").format(e=result.get("message", t("未知错误"))))
                return False
            self.voice_call.server_port = result.get("relay_port", 5005)
            ok = self.voice_call.start()
            if not ok:
                err = getattr(self.voice_call, "last_error", t("音频设备启动失败"))
                self._append_system(t("语音启动失败：{err}").format(err=err))
                if self._voice_status_text:
                    self._voice_status_text.value = t("启动失败：{err}").format(err=err)
                    try:
                        self.page.update()
                    except Exception:
                        pass
                # 启动失败：自动停止并清理VoiceCall，释放UDP socket和音频设备
                try:
                    self.voice_call.stop()
                except Exception:
                    pass
                self.voice_call = None
                self._current_voice_room = ""
            return ok
        except Exception as e:
            self._log(f"voice_join_room failed: {e}")
            self._append_system(t("语音连接失败: {e}").format(e=e))
            return False

    async def _voice_leave_room(self, room_id):
        try:
            await self._run_in_thread(lambda: self.client._temp_request("voice_leave", {
                "username": self.client.username, "room_id": room_id}))
        except Exception as e:
            self._log(f"voice_leave failed: {e}")

    async def _voice_rejoin_room(self):
        """重连后重新注册到语音房间（复用已有UDP socket和音频流，不重建、不重启）。"""
        if not self.voice_call or not getattr(self.voice_call, "_running", False):
            return
        room_id = getattr(self, "_current_voice_room", "")
        if not room_id:
            return
        try:
            local_port = self.voice_call.local_udp_port
            result = await self._run_in_thread(lambda: self.client._temp_request("voice_join", {
                "username": self.client.username, "room_id": room_id, "udp_port": local_port}))
            if result.get("ok"):
                self.voice_call.server_port = result.get("relay_port", 5005)
                self._append_system(t("语音通话已恢复"))
            else:
                self._append_system(t("语音重连失败，请重新发起通话"))
        except Exception as e:
            self._log(f"voice rejoin failed: {e}")

    def _start_voice(self, e):
        self._btn_sound()
        if self.voice_call:
            self._append_system(t("已有通话进行中"))
            return
        if self.current_conv == "public":
            self._ui(self.start_public_voice())
        else:
            self._ui(self.start_voice_call(self.current_conv[7:]))

    async def start_voice_call(self, target_user):
        if not target_user:
            return
        try:
            friends = await self.get_friend_list()
            online = any(f.get("username") == target_user and f.get("online") for f in friends)
            if not online:
                self._append_system(t("{target_user} 当前离线，无法拨通").format(target_user=target_user))
                return
        except Exception:
            pass
        self.voice_target = target_user
        room_id = self._get_private_room_id(self.client.username or "", target_user)
        self._current_voice_room = room_id
        self.voice_call = VoiceCall(server_ip=self.client.server_host, room_id=room_id,
                                     on_status_change=self._on_voice_status, on_packet_loss=self._on_voice_loss,
                                     )
        self._play_dial_sound()  # 拨号音效：Alarm08.wav 播放两次
        signal = build_voice_start(self.client.username or "", target_user)
        try:
            await self._run_in_thread(lambda: self.client._temp_request("private_chat", {
                "username": self.client.username, "target": target_user, "text": signal}))
            self._append_system(t("正在呼叫 {target_user}...").format(target_user=target_user))
        except Exception as e:
            self.voice_call = None
            self._append_system(t("呼叫失败：{e}").format(e=e))

    async def accept_voice_call(self):
        self._stop_sound()  # 停止来电铃声
        if not self.voice_incoming:
            return
        caller = self.voice_incoming["caller"]
        callee = self.voice_incoming["callee"]
        room_id = self._get_private_room_id(caller, callee)
        self._current_voice_room = room_id
        self.voice_call = VoiceCall(server_ip=self.client.server_host, room_id=room_id,
                                     on_status_change=self._on_voice_status, on_packet_loss=self._on_voice_loss,
                                     )
        signal = build_voice_accept(caller, callee, 0)
        try:
            await self._run_in_thread(lambda: self.client._temp_request("private_chat", {
                "username": self.client.username, "target": caller, "text": signal}))
        except Exception as e:
            self._log(f"accept signal failed: {e}")
        self.voice_incoming = None
        self.voice_target = caller
        # 先显示通话浮层，再后台注册房间并启动音频，避免状态被覆盖、避免阻塞UI
        self._show_voice_overlay(caller, is_room=False)
        self._ui(self._voice_join_room(room_id))

    async def reject_voice_call(self):
        self._stop_sound()  # 停止来电铃声
        if not self.voice_incoming:
            return
        caller = self.voice_incoming["caller"]
        callee = self.voice_incoming["callee"]
        signal = build_voice_reject(caller, callee)
        try:
            await self._run_in_thread(lambda: self.client._temp_request("private_chat", {
                "username": self.client.username, "target": caller, "text": signal}))
        except Exception:
            pass
        self.voice_incoming = None
        self._append_system(t("已拒绝通话"))
        self._hide_incoming_call()

    async def end_voice_call(self):
        target = self.voice_target
        room_id = getattr(self, "_current_voice_room", "")
        if self.voice_call:
            self.voice_call.stop()
            self.voice_call = None
        if room_id:
            await self._voice_leave_room(room_id)
            self._current_voice_room = ""
        if target:
            signal = build_voice_end(self.client.username or "", target)
            try:
                await self._run_in_thread(lambda: self.client._temp_request("private_chat", {
                    "username": self.client.username, "target": target, "text": signal}))
            except Exception:
                pass
        self.voice_target = ""
        self._hide_voice_overlay()
        self._append_system(t("通话已结束"))

    async def start_public_voice(self):
        room_id = f"room_{int(time.time())}_{self.client.username}"
        host = self.client.username or ""
        self.public_voice_room = {"room_id": room_id, "host": host, "participants": {}}
        self._current_voice_room = room_id
        self.voice_call = VoiceCall(server_ip=self.client.server_host, room_id=room_id,
                                     on_status_change=self._on_voice_status, on_packet_loss=self._on_voice_loss,
                                     )
        self._play_dial_sound()  # 拨号音效：Alarm08.wav 播放两次
        self._show_voice_overlay(t("公共语音房间"), is_room=True)
        async def _pub_start(rid=room_id, h=host):
            await self._voice_join_room(rid)
            signal = build_room_start(rid, h)
            if self.client.sock:
                try:
                    self.client.send_text(signal)
                except Exception as e:
                    self._log(f"send room_start failed: {e}")
            else:
                self._append_system(t("连接已断开，请重新连接"))
        self._ui(_pub_start())

    async def join_public_voice(self, room_id, host):
        if self.voice_call:
            self._append_system(t("已有通话进行中"))
            return
        self.public_voice_room = {"room_id": room_id, "host": host, "participants": {}}
        self._current_voice_room = room_id
        self.voice_call = VoiceCall(server_ip=self.client.server_host, room_id=room_id,
                                     on_status_change=self._on_voice_status, on_packet_loss=self._on_voice_loss,
                                     )
        self._show_voice_overlay(t("公共语音房间（{host}）").format(host=host), is_room=True)
        async def _pub_join(rid=room_id):
            await self._voice_join_room(rid)
            signal = build_room_join(rid, self.client.username or "", 0)
            if self.client.sock:
                try:
                    self.client.send_text(signal)
                except Exception as e:
                    self._log(f"send room_join failed: {e}")
        self._ui(_pub_join())

    async def leave_public_voice(self):
        if not self.public_voice_room:
            return
        room_id = self.public_voice_room["room_id"]
        is_host = self.public_voice_room.get("host") == (self.client.username or "")
        signal = build_room_leave(room_id, self.client.username or "")
        try:
            self.client.send_text(signal)
        except Exception as e:
            self._log(f"room leave signal failed: {e}")
        # 房主离开时额外发送 room_end，通知房间内其他用户房间已结束
        if is_host:
            try:
                end_signal = build_room_end(room_id, self.client.username or "")
                self.client.send_text(end_signal)
            except Exception as e:
                self._log(f"room end signal failed: {e}")
        if self.voice_call:
            self.voice_call.stop()
            self.voice_call = None
        await self._voice_leave_room(room_id)
        self._current_voice_room = ""
        self.public_voice_room = None
        self._hide_voice_overlay()
        self._append_system(t("已离开语音房间"))

    # ---------- 语音信令分发 ----------
    async def _handle_voice_signal(self, sig_type, caller, callee, extra, is_self):
        self._log(f"voice signal: {sig_type}, caller={caller}, callee={callee}, is_self={is_self}")
        if sig_type == "start" and not is_self:
            self.voice_incoming = {"caller": caller, "callee": callee}
            self._show_incoming_call(caller)
        elif sig_type == "accept" and caller == self.client.username:
            self._show_voice_overlay(self.voice_target, is_room=False)
            if self.voice_target and self.voice_call and not self.voice_call._running:
                room_id = self._get_private_room_id(self.client.username or "", self.voice_target)
                self._current_voice_room = room_id
                # 直接await而非self._ui()，避免异步调度延迟导致主叫方丢前几秒音频
                await self._voice_join_room(room_id)
        elif sig_type == "reject" and caller == self.client.username:
            # 主叫收到被叫的拒绝（信令中 caller 为主叫自己）
            if self.voice_call:
                self.voice_call.stop()
                self.voice_call = None
            self._current_voice_room = ""
            self.voice_target = ""
            self._hide_voice_overlay()
            self._append_system(t("对方拒绝了通话"))
        elif sig_type == "end" and not is_self:
            if self.voice_call:
                self.voice_call.stop()
                self.voice_call = None
            self.voice_target = ""
            self._hide_voice_overlay()
            self._append_system(t("对方已挂断"))
        elif sig_type == "room_start" and not is_self:
            room_id, host = caller, callee
            # 公共聊天室显示语音房间邀请卡片（含加入按钮）
            msg = ChatMessage(text=t("[语音房间] {host} 发起了公共语音通话").format(host=host), is_system=True)
            msg.is_voice_invite = True
            msg.room_id = room_id
            msg.host = host
            self.conversations.setdefault("public", []).append(msg)
            self._refresh_messages("public")
        elif sig_type == "room_join":
            room_id, user = caller, callee
            if self.public_voice_room and self.public_voice_room["room_id"] == room_id and user != self.client.username:
                self._append_system(t("{user} 加入了语音房间").format(user=user))
        elif sig_type == "room_leave" and not is_self:
            self._append_system(t("{callee} 离开了语音房间").format(callee=callee))
        elif sig_type == "room_end" and not is_self:
            room_id = caller
            if self.voice_call:
                self.voice_call.stop()
                self.voice_call = None
            await self._voice_leave_room(room_id)
            self._current_voice_room = ""
            self.public_voice_room = None
            self._hide_voice_overlay()
            self._append_system(t("语音房间已结束"))

    def _on_voice_status(self, status):
        self._log(f"voice status: {status}")
        if self._voice_status_text:
            self._voice_status_text.value = status
            self._voice_status_text.color = ft.Colors.GREEN_600 if t("通话中") in status else ft.Colors.ORANGE_700
            self._ui(self.page.update)

    def _on_voice_loss(self, loss):
        if self._voice_loss_text:
            self._voice_loss_text.value = t("丢包率: {pct:.1f}%").format(pct=loss*100)
            self._voice_loss_text.color = (ft.Colors.GREEN_500 if loss < 0.03
                                            else ft.Colors.ORANGE_500 if loss < 0.08 else ft.Colors.RED_500)
            self._ui(self.page.update)

    # ---------- 专门语音通话界面（全屏覆盖） ----------
    def _build_voice_overlay(self):
        # iOS 风格颜色
        _ios_bg = "#F2F2F7"
        _ios_glass = ft.Colors.with_opacity(0.85, ft.Colors.WHITE)
        _ios_gradient = ft.LinearGradient(
            begin=ft.alignment.center_left, end=ft.alignment.center_right,
            colors=["#0A84FF", "#5E5CE6"])
        _red_gradient = ft.LinearGradient(
            begin=ft.alignment.top_center, end=ft.alignment.bottom_center,
            colors=["#FF453A", "#FF3B30"])

        self._voice_target_text = ft.Text("", size=26, weight=ft.FontWeight.W_700, text_align=ft.TextAlign.CENTER)
        self._voice_status_text = ft.Text(t("正在连接..."), size=15, color=ft.Colors.ORANGE_500, text_align=ft.TextAlign.CENTER)
        self._voice_duration_text = ft.Text("00:00", size=48, weight=ft.FontWeight.W_200, text_align=ft.TextAlign.CENTER)
        self._voice_loss_text = ft.Text(t("丢包率: 0.0%"), size=13, color=ft.Colors.with_opacity(0.5, ft.Colors.GREY_800), text_align=ft.TextAlign.CENTER)

        # iOS 风格按钮：图标+文字，毛玻璃背景
        def _ios_voice_btn(icon_name, label, on_click, width=140):
            btn = ft.Container(
                content=ft.Column(controls=[
                    ft.Icon(icon_name, size=24, color="#0A84FF"),
                    ft.Text(label, size=13, color="#0A84FF", weight=ft.FontWeight.W_500),
                ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER, alignment=ft.MainAxisAlignment.CENTER),
                width=width, height=72, border_radius=20, bgcolor=_ios_glass,
                alignment=ft.alignment.center, animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
            )
            btn.on_click = lambda e: (self._btn_sound(), self._animate_btn(btn), on_click(e))
            return btn

        self._voice_mute_btn = _ios_voice_btn(ft.icons.MIC_OFF, t("静音"), self._toggle_mute)
        self._voice_speaker_btn = _ios_voice_btn(ft.icons.VOLUME_UP, t("扬声器"), self._toggle_speaker)

        # 挂断按钮：红色渐变大圆
        hangup_btn = ft.Container(
            content=ft.Icon(ft.icons.CALL_END, size=32, color=ft.Colors.WHITE),
            width=72, height=72, border_radius=36, gradient=_red_gradient,
            alignment=ft.alignment.center, animate_scale=ft.Animation(150, ft.AnimationCurve.EASE_OUT),
        )
        hangup_btn.on_click = lambda e: (self._btn_sound(), self._animate_btn(hangup_btn), self._hangup(e))

        # 麦克风图标：蓝色渐变圆
        mic_icon = ft.Container(
            content=ft.Icon(ft.icons.MIC, size=40, color=ft.Colors.WHITE),
            width=96, height=96, border_radius=48, gradient=_ios_gradient,
            alignment=ft.alignment.center,
        )

        content = ft.Container(
            content=ft.Column(controls=[
                ft.Container(height=50),
                mic_icon,
                ft.Container(height=16),
                self._voice_target_text,
                ft.Container(height=4),
                self._voice_status_text,
                ft.Container(height=30),
                self._voice_duration_text,
                ft.Container(height=4),
                self._voice_loss_text,
                ft.Container(expand=True),
                ft.Row(controls=[self._voice_mute_btn, self._voice_speaker_btn],
                       spacing=16, alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(height=20),
                hangup_btn,
                ft.Container(height=30),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=0, alignment=ft.MainAxisAlignment.START),
            padding=ft.padding.all(16), expand=True, bgcolor=_ios_bg,
        )
        overlay = ft.Container(content=content, expand=True, opacity=0,
                               animate_opacity=ft.Animation(250, ft.AnimationCurve.EASE_OUT))
        return overlay

    def _animate_btn(self, btn):
        """按钮点击缩放回弹动画。"""
        try:
            btn.scale = 0.9
            self.page.update()
            import asyncio
            async def _rebound():
                await asyncio.sleep(0.1)
                btn.scale = 1.0
                self.page.update()
            self._ui(_rebound())
        except Exception:
            pass

    def _update_voice_btn(self, btn, icon_name, label_text):
        """更新 iOS 风格语音按钮的图标和文字（Container 结构：content=Column([Icon, Text])）。"""
        try:
            if btn is None or btn.content is None:
                return
            col = btn.content
            if hasattr(col, "controls") and len(col.controls) >= 2:
                col.controls[0].name = icon_name
                col.controls[1].value = label_text
        except Exception:
            pass

    def _show_voice_overlay(self, target_name, is_room=False):
        if self._voice_overlay is None:
            self._voice_overlay = self._build_voice_overlay()
        self._voice_target_text.value = target_name
        # 修复：VoiceCall.start() 可能在 overlay 显示前已调用，此时状态回调被跳过
        # 检查 VoiceCall 是否已在运行，是则直接显示"通话中"
        if self.voice_call and getattr(self.voice_call, "_running", False):
            self._voice_status_text.value = t("通话中")
            self._voice_status_text.color = ft.Colors.GREEN_600
        else:
            self._voice_status_text.value = t("正在连接...")
            self._voice_status_text.color = ft.Colors.ORANGE_700
        self._voice_duration_text.value = "00:00"
        self._voice_loss_text.value = t("丢包率: 0.0%")
        # iOS 风格按钮：更新图标和文字（Container 结构：content=Column([Icon, Text])）
        self._update_voice_btn(self._voice_mute_btn, ft.icons.MIC_OFF, t("静音"))
        if self._voice_speaker_btn:
            self._update_voice_btn(self._voice_speaker_btn,
                                   ft.icons.VOLUME_UP if self.speaker_on else ft.icons.VOLUME_OFF,
                                   t("扬声器开") if self.speaker_on else t("扬声器关"))
        self._voice_overlay.opacity = 0
        self.page.controls.clear()
        self.page.controls.append(self._voice_overlay)
        self.page.update()
        self._voice_overlay.opacity = 1
        self.page.update()
        self._start_voice_timer()
        self._log(f"voice overlay shown: {target_name}, running={getattr(self.voice_call, '_running', False) if self.voice_call else False}")

    def _hide_voice_overlay(self):
        self._stop_voice_timer()
        if self._voice_overlay is not None:
            try:
                self._voice_overlay.opacity = 0
                self.page.update()
            except Exception:
                pass
        def _after_fade():
            if self._chat_root is not None:
                self.page.controls.clear()
                self.page.controls.append(self._chat_root)
                self.page.update()
        threading.Timer(0.28, _after_fade).start()

    def _start_voice_timer(self):
        self._voice_timer_running = True
        self._voice_start_time = time.time()
        def tick():
            while self._voice_timer_running:
                elapsed = int(time.time() - self._voice_start_time)
                mins, secs = divmod(elapsed, 60)
                if self._voice_duration_text:
                    self._voice_duration_text.value = f"{mins:02d}:{secs:02d}"
                    self._ui(self.page.update)
                time.sleep(1)
        threading.Thread(target=tick, daemon=True).start()

    def _stop_voice_timer(self):
        self._voice_timer_running = False

    def _toggle_mute(self, e):
        self._btn_sound()
        try:
            if not self.voice_call:
                return
            muted = self.voice_call.is_muted()
            self.voice_call.set_muted(not muted)
            now_muted = not muted
            if self._voice_mute_btn:
                self._update_voice_btn(self._voice_mute_btn,
                                       ft.icons.MIC if now_muted else ft.icons.MIC_OFF,
                                       t("已静音") if now_muted else t("静音"))
            self.page.update()
        except Exception as ex:
            self._log(f"_toggle_mute error: {ex}")

    def _toggle_speaker(self, e):
        """扬声器切换（真正控制接收音频是否播放）。"""
        self._btn_sound()
        try:
            self.speaker_on = not self.speaker_on
            if self.voice_call:
                self.voice_call.set_speaker_on(self.speaker_on)
            if self._voice_speaker_btn:
                self._update_voice_btn(self._voice_speaker_btn,
                                       ft.icons.VOLUME_UP if self.speaker_on else ft.icons.VOLUME_OFF,
                                       t("扬声器开") if self.speaker_on else t("扬声器关"))
            self.page.update()
        except Exception as ex:
            self._log(f"_toggle_speaker error: {ex}")

    def _hangup(self, e):
        self._btn_sound()
        self._play_sound('Speech Misrecognition.wav')
        if self.public_voice_room:
            self._ui(self.leave_public_voice())
        else:
            self._ui(self.end_voice_call())

    # ---------- 来电弹窗 ----------
    def _show_incoming_call(self, caller):
        self._play_sound("Alarm03.wav", loop=True)  # 来电循环音效
        def _accept(e):
            self._stop_sound()  # 停止来电铃声
            self.page.pop_dialog()
            self._ui(self.accept_voice_call())
        def _reject(e):
            self._stop_sound()  # 停止来电铃声
            self.page.pop_dialog()
            self._ui(self.reject_voice_call())
        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("📞 来电")),
            content=ft.Text(t("{caller} 邀请您语音通话").format(caller=caller), size=17),
            actions=[
                ft.ElevatedButton(t("拒绝"), bgcolor=ft.Colors.RED_500, color=ft.Colors.WHITE, on_click=_reject),
                ft.ElevatedButton(t("同意"), bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE, on_click=_accept),
            ])
        self.page.show_dialog(dlg)

    def _hide_incoming_call(self):
        try: self.page.pop_dialog()
        except Exception: pass

    # ==================== 设置对话框（聚合：昵称/改密码/主题/重连/退出） ====================
    def _open_settings(self, e):
        self._btn_sound()
        nick_btn = ft.ElevatedButton(t("修改昵称"), expand=True, height=44, on_click=self._open_nickname_dialog)
        pwd_btn = ft.ElevatedButton(t("修改密码"), expand=True, height=44, on_click=self._open_password_dialog)
        # 三主题切换按钮
        def _make_theme_btn(label, mode, icon):
            is_active = (self._theme_mode == mode)
            return ft.ElevatedButton(label, expand=True, height=40, icon=icon,
                                      bgcolor=ft.Colors.BLUE_700 if is_active else None,
                                      color=ft.Colors.WHITE if is_active else None,
                                      on_click=lambda ev: self._switch_theme(mode))
        theme_row = ft.Row(controls=[
            _make_theme_btn(t("亮色"), "light", ft.icons.LIGHT_MODE),
            _make_theme_btn(t("暗色"), "dark", ft.icons.DARK_MODE),
            _make_theme_btn(t("跟随"), "system", ft.icons.SETTINGS),
        ], spacing=6)
        reconn_btn = ft.ElevatedButton(t("重新连接"), expand=True, height=44, on_click=self._reconnect)
        clear_btn = ft.ElevatedButton(t("清理聊天记录"), expand=True, height=44,
                                        bgcolor=ft.Colors.RED_500, color=ft.Colors.WHITE,
                                        on_click=self._clear_chat_history)
        logout_btn = ft.ElevatedButton(t("退出登录"), expand=True, height=44,
                                        bgcolor=ft.Colors.RED_500, color=ft.Colors.WHITE, on_click=self._logout)
        info = ft.Text(t("版本: {VERSION}\n用户: {user}").format(VERSION=VERSION, user=self.client.username), size=14, opacity=0.5)
        # IP版本切换（三个按钮）
        ip_mode_row = self._build_ip_mode_row()
        # 本机IP只读显示（含调试信息）
        all_ipv6 = self._get_all_ipv6()
        debug_text = t("\n调试-全部IPv6: {ips}").format(ips=(", ".join(all_ipv6) if all_ipv6 else t("(无)")))
        ip_info = ft.Text(t("本机IPv4: {v4}\n本机IPv6: {v6}{debug}").format(v4=self._get_local_ipv4(), v6=self._get_local_ipv6(), debug=debug_text),
                          size=13, opacity=0.5, selectable=True)
        def _lang_btn(label, code):
            return ft.ElevatedButton(label, expand=True, height=38,
                                     on_click=lambda ev: self._switch_lang(code))
        lang_row = ft.Row(controls=[_lang_btn(t("中文"), "zh"), _lang_btn("English", "en")], spacing=6)
        content_card = ft.Card(content=ft.Container(
            content=ft.Column(controls=[nick_btn, pwd_btn, ft.Text(t("主题"), size=14, opacity=0.6), theme_row,
                                         ft.Text(t("语言"), size=14, opacity=0.6), lang_row,
                                         ft.Text(t("IP版本"), size=14, opacity=0.6), ip_mode_row,
                                         reconn_btn, clear_btn, logout_btn, ip_info, info],
                                width=280, tight=True, spacing=8,
                                scroll=ft.ScrollMode.AUTO, height=420),
            padding=ft.padding.all(12)), elevation=2)
        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("设置")),
            content=ft.Container(content=content_card, opacity=0,
                                  animate_opacity=ft.Animation(250, ft.AnimationCurve.EASE_OUT)),
            actions=[ft.TextButton(t("关闭"), on_click=lambda x: self._close_dialog_with_fade(dlg))])
        self._active_dialog = dlg
        self.page.show_dialog(dlg)
        def _fade_in():
            try:
                dlg.content.opacity = 1
                self.page.update()
            except Exception:
                pass
        threading.Timer(0.05, _fade_in).start()

    def _switch_lang(self, code):
        """切换界面语言、持久化，并用新语言重建当前页面。"""
        self._btn_sound()
        set_lang(code)
        save_language(code)
        if self._active_dialog:
            try: self._close_dialog_with_fade(self._active_dialog)
            except Exception: pass
        async def _rebuild():
            try:
                if getattr(self.client, "username", None):
                    await self.show_chat()
                else:
                    await self.show_login()
            except Exception as e:
                print(f"[lang] rebuild failed: {e}")
        self._ui(_rebuild())

    def _switch_theme(self, mode):
        self._btn_sound()
        self._apply_theme_mode(mode)
        self._save_theme_config(mode)
        self._append_system(t("已切换到{mode}主题").format(mode=(t("亮色") if mode=="light" else t("暗色") if mode=="dark" else t("跟随系统"))))
        # 关闭设置弹窗
        if self._active_dialog:
            self._close_dialog_with_fade(self._active_dialog)

    def _open_nickname_dialog(self, e):
        self._btn_sound()
        field = ft.TextField(value=self.client.username or "", expand=True, dense=True, hint_text=t("新昵称"))
        pwd = ft.TextField(password=True, expand=True, dense=True, hint_text=t("密码"))
        msg = ft.Text("", size=14, color=ft.Colors.GREY_600)
        def _save(ev):
            new_name = (field.value or "").strip()
            password = pwd.value or ""
            if not new_name:
                msg.value = t("昵称不能为空"); self.page.update(); return
            self.page.pop_dialog()
            self._change_nickname(new_name, password)
        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("修改昵称")),
            content=ft.Column(controls=[field, pwd, msg], width=280, tight=True, spacing=8),
            actions=[ft.TextButton(t("取消"), on_click=lambda x: self.page.pop_dialog()),
                     ft.ElevatedButton(t("保存"), on_click=_save)])
        self.page.show_dialog(dlg)

    def _change_nickname(self, new_name, password):
        def worker():
            try:
                payload = self.client._temp_request("change_username", {
                    "old_username": self.client.username, "new_username": new_name, "password": password})
                ok = payload.get("ok", False)
                msg = payload.get("message", t("修改完成") if ok else t("修改失败"))
                if ok:
                    self.client.username = new_name
                    if self._nick_text:
                        self._nick_text.value = new_name
                self._append_system(msg)
                self._ui(self.page.update)
            except Exception as ex:
                self._append_system(t("昵称修改失败：{ex}").format(ex=ex))
        threading.Thread(target=worker, daemon=True).start()

    def _open_password_dialog(self, e):
        self._btn_sound()
        old = ft.TextField(password=True, expand=True, dense=True, hint_text=t("原密码"))
        new = ft.TextField(password=True, expand=True, dense=True, hint_text=t("新密码"))
        msg = ft.Text("", size=14, color=ft.Colors.GREY_600)
        def _save(ev):
            if not (old.value and new.value):
                msg.value = t("密码不能为空"); self.page.update(); return
            self.page.pop_dialog()
            self._change_password(old.value, new.value)
        dlg = ft.AlertDialog(modal=True, title=ft.Text(t("修改密码")),
            content=ft.Column(controls=[old, new, msg], width=280, tight=True, spacing=8),
            actions=[ft.TextButton(t("取消"), on_click=lambda x: self.page.pop_dialog()),
                     ft.ElevatedButton(t("保存"), on_click=_save)])
        self.page.show_dialog(dlg)

    def _change_password(self, old_pwd, new_pwd):
        def worker():
            try:
                payload = self.client._temp_request("change_password", {
                    "username": self.client.username, "old_password": old_pwd, "new_password": new_pwd})
                self._append_system(payload.get("message", t("密码修改完成")))
            except Exception as ex:
                self._append_system(t("密码修改失败：{ex}").format(ex=ex))
        threading.Thread(target=worker, daemon=True).start()

    def _toggle_theme(self, e):
        """兼容旧调用，转发到三主题切换（默认亮色/暗色交替）。"""
        self._switch_theme("dark" if self._theme_mode != "dark" else "light")

    def _quick_reconnect(self, e):
        """顶部栏快速重连（不弹窗，直接重连）。"""
        self._btn_sound()
        self._append_system(t("正在重新连接..."))
        self._reconnect_attempts = 0
        self._stop_heartbeat()
        self._stop_net_ui_refresh()
        try: self.client.close()
        except Exception: pass
        host, port = load_server_config()
        self.do_login(self.client.username or "", self._password, host, port)

    def _reconnect(self, e):
        self._btn_sound()
        self.page.pop_dialog()
        self._append_system(t("正在重新连接..."))
        self._reconnect_attempts = 0
        self._stop_heartbeat()
        self._stop_net_ui_refresh()
        try: self.client.close()
        except Exception: pass
        host, port = load_server_config()
        self.do_login(self.client.username or "", self._password, host, port)

    def _clear_chat_history(self, e):
        """清空所有聊天记录（公共聊天+私聊），关闭设置弹窗并提示。"""
        self._btn_sound()
        self.conversations.clear()
        self._refresh_messages(self.current_conv)
        self.page.pop_dialog()
        self._append_system(t("聊天记录已清理"))

    def _logout(self, e):
        self._auto_reconnect = False
        self._btn_sound()
        self.page.pop_dialog()
        self._stop_heartbeat()
        self._stop_net_ui_refresh()
        if self._friend_timer:
            self._friend_timer.cancel(); self._friend_timer = None
        try: self.client.close()
        except Exception: pass
        self.conversations = {}
        self.friends = []
        self._unread = {}
        self.voice_call = None
        self.voice_incoming = None
        self._ui(self.show_login())

    # ==================== 心跳 / 网络状态栏 ====================
    def _start_heartbeat(self):
        self._stop_heartbeat()  # 先停旧的，防止重连后心跳重复
        self._heartbeat_running = True
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        """单线程心跳循环：每10秒发一次ping，_stop_heartbeat置False后最多10秒退出。"""
        while self._heartbeat_running:
            try:
                if self.client.running and self.client.sock:
                    self._ping_sent += 1
                    try:
                        self._ping_send_time = time.time()
                        self.client.send_ping()
                    except Exception:
                        pass
            except Exception:
                pass
            # 分段sleep，使_stop_heartbeat能更快响应（最多1秒延迟）
            for _ in range(10):
                if not self._heartbeat_running:
                    break
                time.sleep(1)

    def _on_pong(self):
        """收到服务端pong，计算真实RTT。"""
        if getattr(self, "_ping_send_time", None) is not None:
            rtt = round((time.time() - self._ping_send_time) * 1000, 1)
            self._ping_received += 1
            self._rtt_samples.append(rtt)
            if len(self._rtt_samples) > 20:
                self._rtt_samples.pop(0)
            self._ping_send_time = None

    def _on_kicked(self, reason):
        """被管理员踢出。"""
        self._auto_reconnect = False
        self._stop_heartbeat()
        self._append_system(f"⚠️ {reason}")
        self._logout(None)

    def _stop_heartbeat(self):
        self._heartbeat_running = False
        if self._heartbeat_timer:
            self._heartbeat_timer.cancel(); self._heartbeat_timer = None
        if getattr(self, "_heartbeat_thread", None):
            self._heartbeat_thread = None

    def _start_net_ui_refresh(self):
        """网络状态栏 0.5 秒刷新一次显示（仅刷新显示，不重新发 ping；先停旧的防重复）。"""
        self._stop_net_ui_refresh()
        self._net_ui_running = True
        self._net_ui_thread = threading.Thread(target=self._net_ui_refresh_loop, daemon=True)
        self._net_ui_thread.start()

    def _net_ui_refresh_loop(self):
        while self._net_ui_running:
            try:
                txt = self._net_text
                if txt and self._rtt_samples:
                    avg = sum(self._rtt_samples) / len(self._rtt_samples)
                    loss = (self._ping_sent - self._ping_received) / self._ping_sent if self._ping_sent > 0 else 0
                    color = ft.Colors.GREEN_700 if avg < 30 else (ft.Colors.ORANGE_700 if avg <= 120 else ft.Colors.RED_600)
                    txt.value = t("延迟 {avg:.0f}ms 丢包 {pct:.0f}%").format(avg=avg, pct=loss*100)
                    txt.color = color
                    self._ui(self.page.update)
            except Exception:
                pass
            time.sleep(0.5)

    def _stop_net_ui_refresh(self):
        self._net_ui_running = False

    def _start_friend_refresh(self):
        self._friend_timer = threading.Timer(30, self._do_friend_refresh)
        self._friend_timer.daemon = True
        self._friend_timer.start()

    def _do_friend_refresh(self):
        self._ui(self.refresh_friend_list())
        self._start_friend_refresh()

    # ==================== 提示音 ====================
    def _play_notify_sound(self):
        """消息提示音：安卓用ft.Audio，Windows用winsound。"""
        try:
            if _is_android():
                if self._audio_notify:
                    self._android_play(self._audio_notify)
            else:
                import winsound
                wav = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notify.wav")
                if os.path.exists(wav):
                    winsound.PlaySound(wav, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

    def _play_sound(self, wav_name, loop=False):
        """通用音效播放：安卓用ft.Audio，Windows用winsound。"""
        try:
            if _is_android():
                # 重连后页面重建可能导致音频控件丢失，先兜底重新挂载
                self._remount_audio_services()
                _audio_map = {
                    "Alarm03.wav": self._audio_ring,
                    "Alarm08.wav": self._audio_dial,
                    "Speech Misrecognition.wav": self._audio_hangup,
                    "notify.wav": self._audio_notify,
                    "Windows Navigation Start.wav": self._audio_click,
                }
                _a = _audio_map.get(wav_name)
                if _a:
                    self._android_play(_a)
            else:
                import winsound
                wav_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), wav_name)
                if not os.path.exists(wav_path):
                    return
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC
                if loop:
                    flags |= winsound.SND_LOOP
                winsound.PlaySound(wav_path, flags)
        except Exception:
            pass

    def _stop_sound(self):
        """停止所有正在播放的音效。"""
        try:
            if _is_android():
                for _a in [getattr(self, "_audio_ring", None), getattr(self, "_audio_dial", None),
                           getattr(self, "_audio_hangup", None), getattr(self, "_audio_notify", None),
                           getattr(self, "_audio_click", None)]:
                    if _a:
                        self._android_pause(_a)
            else:
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def _play_dial_sound(self):
        """拨打电话音效：Android 用 ft.Audio，Windows 用 winsound 播放两次。"""
        if _is_android():
            try:
                if self._audio_dial:
                    self._android_play(self._audio_dial)
            except Exception:
                pass
            return

        def worker():
            try:
                import winsound
                import os
                import time
                wav = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Alarm08.wav")
                if not os.path.exists(wav):
                    return
                for _ in range(2):
                    winsound.PlaySound(wav, winsound.SND_FILENAME)  # 同步播放
                    time.sleep(0.15)
            except Exception:
                pass
        threading.Thread(target=worker, daemon=True).start()



    # ==================== 工具方法 ====================
    async def _run_in_thread(self, func):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func)

    async def _mount_with_fade(self, content):
        """页面切换淡入+轻微上移过渡（先渲染初始帧再触发动画，避免被合并跳过）。"""
        try:
            wrapper = ft.Container(
                content=content, opacity=0, offset=ft.transform.Offset(0, 0.06),
                animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
                animate_offset=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
                expand=True)
            self.page.controls.clear()
            self.page.controls.append(wrapper)
            self.page.update()
            await asyncio.sleep(0.03)
            wrapper.opacity = 1
            wrapper.offset = ft.transform.Offset(0, 0)
            self.page.update()
        except Exception as e:
            self._log(f"page fade failed: {e}")
            self.page.controls.clear()
            self.page.controls.append(content)
            self.page.update()

    def _ui(self, coro_or_func, *args):
        if coro_or_func is None or self.page is None or self._loop is None:
            return
        try:
            if asyncio.iscoroutine(coro_or_func):
                async def _wrapper():
                    await coro_or_func
                if hasattr(self.page, "run_task"):
                    self.page.run_task(_wrapper)
                else:
                    asyncio.run_coroutine_threadsafe(_wrapper(), self._loop)
            elif callable(coro_or_func):
                if args:
                    self._loop.call_soon_threadsafe(coro_or_func, *args)
                else:
                    self._loop.call_soon_threadsafe(coro_or_func)
        except Exception as e:
            self._log(f"_ui error: {e}")


# ==================== 新增：Android原生Photo Picker接入（增量开发，不修改原有代码）====================
# 注意：本函数必须定义在 `if __name__ == "__main__": ft.run(main)` 阻塞点之前，
# 否则安卓上 ft.run 进入事件循环后，后面的定义永远不会执行，点击时会报
# NameError: name 'pick_photo_native' is not defined。
def pick_photo_native(app, path_display, send_btn, dlg):
    """
    使用Android原生Photo Picker选择图片，不可用时自动降级到原有Flet FilePicker/tkinter。

    这是新增的独立函数，不修改原有 _pick_file_with_fallback 的任何一行代码。
    集成方式：在 _show_file_send_dialog 的 _pick 函数中，将
        self._pick_file_with_fallback(path_display, send_file_btn, dlg)
    替换为
        pick_photo_native(self, path_display, send_file_btn, dlg)

    参数:
        app: MobileChatApp 实例（即 self）
        path_display: 路径显示控件
        send_btn: 发送按钮
        dlg: 文件发送弹窗
    """
    def _ft(msg):
        try:
            import os, time
            d = "crash_logs"
            try: os.makedirs(d, exist_ok=True)
            except Exception: pass
            with open(os.path.join(d, "debug_trace.txt"), "a", encoding="utf-8") as _f:
                _f.write(f"[{time.strftime('%H:%M:%S')}] [PICK] {msg}\n")
        except Exception: pass
    _ft("pick_photo_native called")
    # Flet/serious_python 环境没有 kivy 的 android.activity 模块，原生
    # Photo Picker 的 onActivityResult 回调无法绑定，直接走 Flet FilePicker
    # （底层 Flutter file_picker，Android 稳定且自动把 content URI 复制为缓存文件）
    try:
        import sys as _sys
        _is_droid = "android" in _sys.modules or hasattr(_sys, "getandroidapilevel")
    except Exception:
        _is_droid = False
    if _is_droid:
        _ft("android: directly use Flet FilePicker")
        app._pick_file_with_fallback(path_display, send_btn, dlg)
        return
    try:
        from android_photo_picker import AndroidPhotoPicker
        _ft("android_photo_picker import OK (desktop test)")
    except ImportError as ie:
        _ft(f"android_photo_picker import FAIL: {ie!r}, fallback to flet")
        app._pick_file_with_fallback(path_display, send_btn, dlg)
        return

    if not hasattr(app, '_photo_picker') or app._photo_picker is None:
        app._photo_picker = AndroidPhotoPicker(page=app.page)

    def on_photos(paths):
        _ft(f"on_photos callback: paths={paths!r}")
        if paths:
            try:
                app._on_file_picked_path(paths[0])
                _ft(f"_on_file_picked_path OK for {paths[0]!r}")
            except Exception as ex:
                _ft(f"_on_file_picked_path FAIL: {ex!r}")
                raise
        else:
            _ft("on_photos: paths empty (user cancelled?)")

    try:
        ok = app._photo_picker.pick(on_photos, multi=False)
        _ft(f"picker.pick returned {ok}")
    except Exception as ex:
        _ft(f"picker.pick EXCEPTION: {ex!r}")
        ok = False
    if not ok:
        _ft("fallback to flet FilePicker")
        app._pick_file_with_fallback(path_display, send_btn, dlg)


# ======================================================================
# 崩溃日志模块初始化（追加代码，不修改原有代码）
# 全局捕获未处理异常，崩溃时自动生成本地日志文件
# 同样必须放在 ft.run(main) 阻塞点之前，否则安卓上不会执行。
# ======================================================================
try:
    from crash_logger import CrashLogger
    _crash_logger = CrashLogger(
        log_dir="crash_logs",
        app_version=VERSION,
        app_name="LanTalk",
        include_locals=True,
        max_log_files=50,
    )
    _crash_logger.install()
except Exception as _crash_init_error:
    print(f"[CrashLogger] 初始化失败: {_crash_init_error}")



# ======================================================================
# 连接看门狗（新增，不修改原有代码）：修复 WiFi/移动网络切换后 TCP 僵死、
# 无法自动重连的问题。通过包装 show_chat，在首次进入聊天页及每次重连成功
# 重建页面后幂等启动看门狗；原有 show_chat 逻辑一行未改，只是前后加钩子。
# ======================================================================
try:
    from connection_watchdog import ConnectionWatchdog

    _orig_show_chat = MobileChatApp.show_chat

    async def _show_chat_with_watchdog(self, *args, **kwargs):
        _result = await _orig_show_chat(self, *args, **kwargs)
        try:
            # 30秒收不到任何 pong 判定僵死，每5秒检查一次；幂等，重复调用安全
            ConnectionWatchdog.start_for_app(self, timeout=30.0, check_interval=5.0)
        except Exception as _wd_start_err:
            print(f"[ConnWatchdog] start failed(ignored): {_wd_start_err}")
        return _result

    MobileChatApp.show_chat = _show_chat_with_watchdog
except Exception as _watchdog_hook_error:
    print(f"[ConnWatchdog] hook failed(ignored): {_watchdog_hook_error}")


# ======================================================================
# 语音录音权限（新增，不修改原有代码）：修复安卓上“一点语音就闪退”。
# 根因：原 audio_backend 申请运行时权限用的 Activity 类名在 serious_python
# 打包下不存在，RECORD_AUDIO 从未真正申请；无权限时 AudioRecord.startRecording()
# 在 native 层直接崩溃（Python try 拦不住）。这里在“主动发起语音”和“接听来电”
# 两个入口前，用正确的 Activity 类名申请录音权限，授权后再走原逻辑。
# ======================================================================
try:
    import android_perms as _android_perms

    _orig_start_voice = MobileChatApp._start_voice

    def _start_voice_with_perm(self, e):
        try:
            _need = _android_perms.is_android() and not _android_perms.has_record_permission()
        except Exception:
            _need = False
        if not _need:
            return _orig_start_voice(self, e)

        def _perm_worker():
            _ok = False
            try:
                _ok = _android_perms.request_record_permission()
            except Exception:
                _ok = False

            def _cont():
                if _ok:
                    try:
                        _orig_start_voice(self, e)
                    except Exception as _ex:
                        self._log(f"start voice after perm failed: {_ex}")
                else:
                    try:
                        self._append_system(t("需要麦克风权限才能语音通话，请在系统设置中允许"))
                    except Exception:
                        pass
            try:
                self._ui(_cont)
            except Exception:
                pass

        threading.Thread(target=_perm_worker, daemon=True).start()

    MobileChatApp._start_voice = _start_voice_with_perm

    _orig_accept_voice = MobileChatApp.accept_voice_call

    async def _accept_voice_with_perm(self, *a, **k):
        _need = False
        try:
            _need = _android_perms.is_android() and not _android_perms.has_record_permission()
        except Exception:
            _need = False
        if _need:
            try:
                _ok = await self._run_in_thread(lambda: _android_perms.request_record_permission())
            except Exception:
                _ok = False
            if not _ok:
                try:
                    self._append_system(t("需要麦克风权限才能接听语音通话"))
                except Exception:
                    pass
                return
        return await _orig_accept_voice(self, *a, **k)

    MobileChatApp.accept_voice_call = _accept_voice_with_perm
except Exception as _voice_perm_hook_err:
    print(f"[VoicePerm] hook failed(ignored): {_voice_perm_hook_err}")


# ======================================================================
# 深色主题换肤挂载（新增，不修改原有UI代码）：
# 原界面颜色全部硬编码为浅色，theme_mode=DARK 对它们无效。这里通过包装在
# “进入聊天页 / 切换主题 / 构建每条消息气泡 / 打开语音浮层与设置框”几个时机，
# 调用 dark_theme 控件树换肤层；切回亮色时由换肤层按记录的原值还原。
# ======================================================================
try:
    import dark_theme as _dt

    def _is_dark_now(app):
        _mode = getattr(app, "_theme_mode", "system")
        return _mode == "dark" or (_mode == "system" and _dt.is_system_dark())

    # 1) 进入/重建聊天页后整体换肤（此时 show_chat 已被看门狗包装，再包一层）
    _prev_show_chat = MobileChatApp.show_chat

    async def _show_chat_themed(self, *a, **k):
        _r = await _prev_show_chat(self, *a, **k)
        try:
            _dt.skin_app(self, update=True)
        except Exception as _te:
            self._log(f"dark theme on show_chat failed: {_te}")
        return _r
    MobileChatApp.show_chat = _show_chat_themed

    # 2) 每条消息/文件/语音邀请气泡构建后按当前主题换肤（保证深色下新消息也深色）
    def _wrap_bubble(_orig_name):
        _orig = getattr(MobileChatApp, _orig_name)

        def _w(self, *a, **k):
            _ctrl = _orig(self, *a, **k)
            try:
                if _ctrl is not None and _is_dark_now(self):
                    _dt.skin_control(_ctrl, True)
            except Exception:
                pass
            return _ctrl
        return _w

    for _bn in ("_build_msg_bubble", "_build_file_bubble", "_build_voice_invite",
                "_build_voice_overlay"):
        if hasattr(MobileChatApp, _bn):
            setattr(MobileChatApp, _bn, _wrap_bubble(_bn))

    # 3) 切换主题后立即整体换肤/还原
    _orig_switch_theme = MobileChatApp._switch_theme

    def _switch_theme_themed(self, mode):
        _r = _orig_switch_theme(self, mode)
        try:
            _dt.skin_app(self, update=True)
        except Exception:
            pass
        return _r
    MobileChatApp._switch_theme = _switch_theme_themed

    # 4) 打开设置对话框后对其换肤
    _orig_open_settings = MobileChatApp._open_settings

    def _open_settings_themed(self, e):
        _r = _orig_open_settings(self, e)
        try:
            _dt.skin_app(self, update=True)
        except Exception:
            pass
        return _r
    MobileChatApp._open_settings = _open_settings_themed
except Exception as _theme_hook_err:
    print(f"[DarkTheme] hook failed(ignored): {_theme_hook_err}")


# ======================================================================
# 崩溃提示 + 一键导出日志弹窗（新增，不修改原有代码）：
# 1) 包装已安装的 CrashLogger.log_crash，运行时崩溃best-effort弹窗；
# 2) 每次进入聊天页检测“上次未读的崩溃日志”，真闪退后下次启动也能补弹。
# ======================================================================
try:
    from crash_dialog import crash_dialog_manager as _cdm

    _CURRENT_APP = None
    try:
        _cdm.attach(_crash_logger, lambda: globals().get("_CURRENT_APP"))
    except Exception as _cdm_attach_err:
        print(f"[CrashDialog] attach failed(ignored): {_cdm_attach_err}")

    _show_chat_before_cdm = MobileChatApp.show_chat

    async def _show_chat_crash_dialog(self, *a, **k):
        globals()["_CURRENT_APP"] = self
        _r = await _show_chat_before_cdm(self, *a, **k)
        try:
            _cdm.check_previous_crash()
        except Exception:
            pass
        return _r
    MobileChatApp.show_chat = _show_chat_crash_dialog
except Exception as _crash_dialog_hook_err:
    print(f"[CrashDialog] hook failed(ignored): {_crash_dialog_hook_err}")
# ======================================================================
# SAF 文件选择修复（新增，不修改原有代码）：
# 原 _resolve_android_content_uri 和 android_photo_picker 里写死了
# org.kivy.android.PythonActivity，在 serious_python 下不存在，
# 导致 content:// URI 解析失败、文件选择"选了发不出去"。
# ======================================================================
try:
    import android_saf_picker as _saf

    def _patched_resolve_uri(self, uri_str):
        return _saf.resolve_content_uri(uri_str, log_fn=getattr(self, "_log", None))

    MobileChatApp._resolve_android_content_uri = _patched_resolve_uri
    _saf.patch_photo_picker_activity()
    print("[SAF] content URI resolver patched OK")
except Exception as _saf_hook_err:
    print(f"[SAF] patch failed(ignored): {_saf_hook_err}")


# ======================================================================
# 语音闪退修复（新增，不修改原有代码）：
# 替换 audio_backend._create_android()，用 MIC 代替 VOICE_COMMUNICATION，
# 正确检查权限，startRecording/play 包安全检查，加细粒度日志。
# ======================================================================
try:
    from android_audio_fix import patch_android_audio
    _audio_patched = patch_android_audio()
    try:
        import os as _os
        _os.makedirs("crash_logs", exist_ok=True)
        with open(_os.path.join("crash_logs","debug_trace.txt"),"a",encoding="utf-8") as _tf:
            _tf.write(f"[BOOT] android_audio_fix patch result={_audio_patched}\n")
    except Exception:
        pass
except Exception as _audio_patch_err:
    try:
        import os as _os
        _os.makedirs("crash_logs", exist_ok=True)
        with open(_os.path.join("crash_logs","debug_trace.txt"),"a",encoding="utf-8") as _tf:
            _tf.write(f"[BOOT] android_audio_fix IMPORT/PATCH FAILED: {_audio_patch_err!r}\n")
    except Exception:
        pass
except Exception as _audio_fix_err:
    print(f"[AudioFix] init failed(ignored): {_audio_fix_err}")


# ======================================================================
# 崩溃日志导出按钮（新增，不修改原有代码）：
# 在设置对话框的列表里加【导出崩溃日志】按钮，把私有目录的 crash_logs/
# 复制到公共 Downloads/LanTalk_crash_logs_时间戳/，荣耀 MagicOS 也能看到。
# ======================================================================
try:
    from crash_export import build_export_button as _build_crash_btn

    _orig_open_settings_for_export = MobileChatApp._open_settings

    def _find_column(node, depth=0):
        """递归找第一个有 controls 的容器。"""
        if depth > 6 or node is None:
            return None
        try:
            if hasattr(node, "controls") and isinstance(node.controls, list):
                return node
        except Exception:
            pass
        for attr in ("content", "controls", "rows", "columns"):
            child = getattr(node, attr, None)
            if child is None:
                continue
            if isinstance(child, list):
                for item in child:
                    hit = _find_column(item, depth + 1)
                    if hit is not None:
                        return hit
            else:
                hit = _find_column(child, depth + 1)
                if hit is not None:
                    return hit
        return None

    def _open_settings_with_export(self, e):
        _r = _orig_open_settings_for_export(self, e)
        try:
            btn = _build_crash_btn(self)
            dlg = getattr(self, "_active_dialog", None)
            if dlg is None:
                return _r
            col = _find_column(dlg)
            if col is not None:
                col.controls.append(btn)
                try:
                    if hasattr(dlg, "update"):
                        dlg.update()
                except Exception:
                    pass
        except Exception as _exp_err:
            print(f"[CrashExport] inject failed: {_exp_err}")
        return _r

    MobileChatApp._open_settings = _open_settings_with_export
except Exception as _crash_export_hook_err:
    print(f"[CrashExport] hook failed(ignored): {_crash_export_hook_err}")




# ========== UI 动画 + 语音通话模式 patch（增量，不改原有代码）==========
try:
    from ui_animations import patch_ui_animations
    patch_ui_animations()
except Exception as _anim_err:
    print(f"[UIAnim] load failed(ignored): {_anim_err}")

# 语音通话模式设置（P2P优先 / 仅P2P / 仅中转）
_VOICE_MODE_KEY = "voice_relay_mode"
_DEFAULT_VOICE_MODE = "p2p_first"  # p2p_first / p2p_only / relay_only

def _get_voice_mode():
    try:
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_mode.json")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r") as f:
                return json.load(f).get("mode", _DEFAULT_VOICE_MODE)
    except Exception:
        pass
    return _DEFAULT_VOICE_MODE

def _set_voice_mode(mode):
    try:
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_mode.json")
        with open(cfg_path, "w") as f:
            json.dump({"mode": mode}, f)
        return True
    except Exception:
        return False

# 导出给其他模块用
VOICE_MODES = {
    "p2p_first": "P2P优先(中转兜底)",
    "p2p_only": "仅P2P直连",
    "relay_only": "仅中转模式",
}
print(f"[VoiceMode] 当前语音模式: {VOICE_MODES.get(_get_voice_mode(), '未知')}")

async def main(page):
    app = MobileChatApp()
    await app.setup(page)


if __name__ == "__main__":
    try:
        ft.run(main)
    except AttributeError:
        ft.app(target=main)


# ======================================================================
# 主界面右下角悬浮【导出崩溃日志】FAB（新增，不修改原有代码）：
# 进聊天页后即在右下角显示一个🐛按钮，点击导出 crash_logs/ 到公共 Download。
# ======================================================================
try:
    from crash_export import build_export_button as _build_crash_btn

    _orig_show_chat_fab = MobileChatApp.show_chat

    async def _show_chat_with_fab(self, *args, **kwargs):
        _r = await _orig_show_chat_fab(self, *args, **kwargs)
        try:
            if self.page is not None:
                btn = _build_crash_btn(self)
                # FAB 悬浮在右下角，最显眼；若原页已有 FAB 则追加为 secondary
                try:
                    self.page.floating_action_button = btn
                except Exception:
                    pass
                self.page.update()
        except Exception as _fab_err:
            print(f"[CrashFAB] add failed(ignored): {_fab_err}")
        return _r

    MobileChatApp.show_chat = _show_chat_with_fab
except Exception as _fab_hook_err:
    print(f"[CrashFAB] hook failed(ignored): {_fab_hook_err}")


# ======================================================================
# 关键步骤埋点（新增，不修改原有代码）：在语音/文件等易崩溃操作前
# 写一行到 crash_logs/debug_trace.txt。native 崩溃后重启 APP，导出日志
# 就能看到最后停在哪一步，定位 native 闪退点。
# ======================================================================
def _trace_event(msg):
    try:
        import os, time
        d = "crash_logs"
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        with open(os.path.join(d, "debug_trace.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# 包装语音启动
try:
    _orig_start_trace = MobileChatApp._start_voice
    def _start_voice_traced(self, e):
        _trace_event("=== TAP: start voice button ===")
        try:
            r = _orig_start_trace(self, e)
            _trace_event("start voice returned OK")
            return r
        except Exception as ex:
            _trace_event(f"start voice Python exception: {ex!r}")
            raise
    MobileChatApp._start_voice = _start_voice_traced
except Exception:
    pass

# 包装文件选择
try:
    _orig_pick_trace = MobileChatApp._pick_file
    def _pick_file_traced(self, e):
        _trace_event("=== TAP: pick file button ===")
        try:
            r = _orig_pick_trace(self, e)
            _trace_event("pick file returned OK")
            return r
        except Exception as ex:
            _trace_event(f"pick file Python exception: {ex!r}")
            raise
    MobileChatApp._pick_file = _pick_file_traced
except Exception:
    pass

# APP 启动时写一行标记
_trace_event("=== APP process started ===")

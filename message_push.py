# -*- coding: utf-8 -*-
"""
LanTalk 安卓本地消息推送模块（独立增量开发，不修改原有业务代码）

基于 flet-android-notifications 插件实现。
全部代码封装在此文件，不和原有 Socket、UI、数据包解析代码混写。

业务规则：
1. 在线判定：进程存活 + TCP连接正常 = 在线；进程被杀 / TCP断开 = 离线。
   APP进程被杀后不会自动唤醒，离线期间不产生消息推送。
2. 推送逻辑：
   - APP前台活跃：收到消息，不弹出系统通知，直接调用原有UI渲染消息气泡。
   - APP后台运行（前台服务保持进程存活）：收到消息，弹出安卓本地通知，
     标题"LanTalk新消息"，展示消息预览；点击通知唤起APP，进入聊天页面。
3. 离线消息：客户端离线时，中转服务端缓存文字消息和文件通知（UDP语音帧不缓存）；
   用户手动打开APP、重连登录上线之后，拉取全部离线消息，在聊天界面加载，
   离线消息不产生系统推送。
4. 桌面端运行：flet_android_notifications 仅安卓生效，桌面端实例化不报错但不弹通知，
   本模块自动检测平台，桌面端所有推送方法为安全空操作。

使用方式（仅新增调用，不修改原有代码）：
    from message_push import MessagePushManager

    # 在APP启动时（main函数或登录成功后）初始化一次：
    push_mgr = MessagePushManager(page=page, on_notification_tap=my_tap_handler)
    await push_mgr.init()

    # 在APP生命周期回调中设置前后台状态：
    page.on_resume = lambda e: push_mgr.set_foreground(True)
    page.on_disconnect = lambda e: push_mgr.set_foreground(False)

    # 在收到新消息时（原有消息接收回调中，仅新增这一行调用）：
    await push_mgr.on_message_received(
        sender="张三",
        content="你好",
        conversation_id="user_zhangsan",
        msg_type="text",
    )

    # 进入聊天页面时清除该会话的通知：
    await push_mgr.cancel_conversation_notifications("user_zhangsan")
"""

import json
import hashlib
import asyncio
import logging
from typing import Optional, Callable, Any

logger = logging.getLogger("LanTalk.MessagePush")

# 通知渠道常量
CHANNEL_ID = "lantalk_message_channel"
CHANNEL_NAME = "LanTalk 消息通知"
CHANNEL_DESCRIPTION = "LanTalk 新消息提醒"
NOTIFICATION_TITLE = "LanTalk新消息"

# 通知ID范围（Android要求正整数，0保留给前台服务）
NOTIF_ID_MIN = 1
NOTIF_ID_MAX = 2_000_000_000


def _is_android() -> bool:
    """检测当前是否运行在安卓平台。"""
    try:
        import sys
        if hasattr(sys, "getandroidapilevel"):
            return True
    except Exception:
        pass
    try:
        from jnius import autoclass  # noqa: F401
        return True
    except Exception:
        pass
    return False


def _conversation_to_notification_id(conversation_id: str) -> int:
    """
    将会话ID稳定映射为通知ID。
    同一会话的多条消息复用同一个通知ID，实现通知更新（不堆叠）。
    """
    h = hashlib.md5(conversation_id.encode("utf-8")).hexdigest()
    raw = int(h[:8], 16)
    return NOTIF_ID_MIN + (raw % (NOTIF_ID_MAX - NOTIF_ID_MIN))


def _build_payload(conversation_id: str, sender: str, msg_type: str) -> str:
    """构建通知点击时携带的payload（JSON字符串）。"""
    return json.dumps({
        "conversation_id": conversation_id,
        "sender": sender,
        "type": msg_type,
    }, ensure_ascii=False)


def _parse_payload(payload: str) -> dict:
    """解析通知点击时携带的payload。"""
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return {}


def _truncate_preview(content: str, max_len: int = 60) -> str:
    """截断消息预览，超长加省略号。"""
    if len(content) <= max_len:
        return content
    return content[:max_len] + "..."


class MessagePushManager:
    """
    LanTalk 安卓本地消息推送管理器。

    职责：
    - 管理通知权限请求、通知渠道创建
    - 跟踪APP前后台状态
    - 根据前后台状态决定是否弹出系统通知
    - 处理通知点击回调，唤起APP并进入对应聊天页面
    - 提供前台服务保活（可选，用于后台保持TCP连接）
    """

    def __init__(
        self,
        page: Any = None,
        on_notification_tap: Optional[Callable[[dict], None]] = None,
    ):
        """
        初始化推送管理器。

        Args:
            page: Flet Page 对象（用于绑定生命周期和UI线程回调）。
                  桌面端或测试环境可传None。
            on_notification_tap: 通知点击回调，接收一个dict参数，
                  包含 conversation_id, sender, type 等字段。
                  回调中应导航到对应聊天页面。
        """
        self._page = page
        self._on_tap_callback = on_notification_tap
        self._is_foreground = True  # 默认前台，APP启动时通常在前台
        self._is_online = False     # TCP连接状态，由外部设置
        self._notifications = None   # FletAndroidNotifications 实例
        self._initialized = False
        self._permission_granted = False
        self._is_android = _is_android()

    # ------------------------------------------------------------------
    # 初始化与权限
    # ------------------------------------------------------------------

    async def init(self) -> bool:
        """
        初始化推送服务：请求通知权限、创建通知渠道。
        桌面端自动跳过，返回True。

        Returns:
            bool: 初始化是否成功（权限是否授予）。
        """
        if not self._is_android:
            logger.info("非安卓平台，消息推送模块初始化为空操作模式")
            self._initialized = True
            self._permission_granted = True
            return True

        try:
            from flet_android_notifications import (
                FletAndroidNotifications,
                NotificationError,
            )
        except ImportError:
            logger.warning("flet_android_notifications 未安装，消息推送不可用")
            self._initialized = True
            return False

        # 实例化通知服务（ft.Service，不要添加到page）
        self._notifications = FletAndroidNotifications(
            on_notification_tap=self._handle_notification_tap
        )

        # 请求通知权限（Android 13+）
        try:
            self._permission_granted = await self._notifications.request_permissions()
        except NotificationError as e:
            logger.error(f"请求通知权限失败: {e}")
            self._permission_granted = False

        # 创建通知渠道（声音/振动/重要性在创建后不可变）
        try:
            await self._notifications.create_notification_channel(
                channel_id=CHANNEL_ID,
                channel_name=CHANNEL_NAME,
                channel_description=CHANNEL_DESCRIPTION,
                importance="high",
                play_sound=True,
                enable_vibration=True,
                show_badge=True,
            )
        except NotificationError as e:
            logger.warning(f"创建通知渠道失败（可能已存在）: {e}")

        self._initialized = True
        logger.info(f"消息推送模块初始化完成，权限授予: {self._permission_granted}")
        return self._permission_granted

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------

    def set_foreground(self, is_foreground: bool):
        """
        设置APP前后台状态。由APP生命周期回调调用。

        Args:
            is_foreground: True=前台活跃，False=后台运行。
        """
        old = self._is_foreground
        self._is_foreground = is_foreground
        if old != is_foreground:
            logger.info(f"APP状态变更: {'前台' if is_foreground else '后台'}")

    def is_foreground(self) -> bool:
        """返回APP是否在前台活跃。"""
        return self._is_foreground

    def set_online(self, is_online: bool):
        """
        设置TCP在线状态。由TCP连接/断开回调调用。

        Args:
            is_online: True=TCP连接正常，False=TCP断开。
        """
        self._is_online = is_online
        logger.info(f"TCP在线状态: {'在线' if is_online else '离线'}")

    def is_online(self) -> bool:
        """返回TCP是否在线。"""
        return self._is_online

    def is_available(self) -> bool:
        """
        推送是否可用：安卓平台 + 已初始化 + 权限授予 + 进程存活。
        离线（TCP断开）时推送不可用（离线消息不推送）。
        """
        return (
            self._is_android
            and self._initialized
            and self._permission_granted
            and self._notifications is not None
        )

    # ------------------------------------------------------------------
    # 核心：收到消息时的推送决策
    # ------------------------------------------------------------------

    async def on_message_received(
        self,
        sender: str,
        content: str,
        conversation_id: str,
        msg_type: str = "text",
    ) -> bool:
        """
        收到新消息时调用。根据APP前后台状态决定是否弹出系统通知。

        业务规则：
        - 前台活跃：不弹通知，返回False（调用方应直接渲染UI气泡）
        - 后台运行：弹出系统通知，返回True
        - 离线/不可用：不弹通知，返回False

        Args:
            sender: 发送者昵称（用于通知标题/预览）
            content: 消息内容（文字消息传文字，文件消息传文件名等描述）
            conversation_id: 会话ID（用于通知去重和点击跳转）
            msg_type: 消息类型 "text" | "file" | "system"

        Returns:
            bool: 是否弹出了系统通知。
        """
        # 前台活跃：不弹通知，直接由调用方渲染UI
        if self._is_foreground:
            logger.debug(f"前台收到消息 from={sender}, 不弹通知")
            return False

        # 推送不可用（非安卓/无权限/未初始化）：不弹通知
        if not self.is_available():
            logger.debug("推送不可用，跳过通知")
            return False

        # 离线状态：不弹通知（离线消息由服务端缓存，上线后拉取，不产生推送）
        if not self._is_online:
            logger.debug("TCP离线，跳过通知（离线消息不推送）")
            return False

        # 后台运行：弹出系统通知
        notif_id = _conversation_to_notification_id(conversation_id)
        payload = _build_payload(conversation_id, sender, msg_type)

        # 根据消息类型构建预览文本
        if msg_type == "file":
            preview = f"[{sender}] 发来文件: {_truncate_preview(content)}"
        elif msg_type == "system":
            preview = content
        else:
            preview = f"[{sender}] {_truncate_preview(content)}"

        try:
            await self._notifications.show_notification(
                notification_id=notif_id,
                title=NOTIFICATION_TITLE,
                body=preview,
                payload=payload,
                channel_id=CHANNEL_ID,
                channel_name=CHANNEL_NAME,
                channel_description=CHANNEL_DESCRIPTION,
                importance="high",
                play_sound=True,
                enable_vibration=True,
                auto_cancel=True,
                category="message",
            )
            logger.info(f"后台推送通知: conv={conversation_id}, notif_id={notif_id}")
            return True
        except Exception as e:
            logger.error(f"弹出通知失败: {e}")
            return False

    # ------------------------------------------------------------------
    # 通知取消
    # ------------------------------------------------------------------

    async def cancel_conversation_notifications(self, conversation_id: str):
        """
        清除指定会话的通知。进入聊天页面时调用。

        Args:
            conversation_id: 会话ID
        """
        if not self.is_available():
            return
        notif_id = _conversation_to_notification_id(conversation_id)
        try:
            await self._notifications.cancel(notif_id)
            logger.debug(f"清除会话通知: conv={conversation_id}, notif_id={notif_id}")
        except Exception as e:
            logger.error(f"清除通知失败: {e}")

    async def cancel_all_notifications(self):
        """清除所有通知。APP退出或登出时调用。"""
        if not self.is_available():
            return
        try:
            await self._notifications.cancel_all()
            logger.info("清除所有通知")
        except Exception as e:
            logger.error(f"清除所有通知失败: {e}")

    # ------------------------------------------------------------------
    # 通知点击处理
    # ------------------------------------------------------------------

    def _handle_notification_tap(self, event):
        """
        通知点击事件内部处理。解析payload，调用用户回调。

        FletAndroidNotifications 的 on_notification_tap 回调中，
        event.data 是JSON字符串: {"payload": "...", "action_id": "..."}
        action_id 为空字符串表示点击了通知正文（而非动作按钮）。
        """
        try:
            data = json.loads(event.data) if isinstance(event.data, str) else {}
        except (json.JSONDecodeError, TypeError):
            data = {}

        payload_str = data.get("payload", "")
        action_id = data.get("action_id", "")
        payload = _parse_payload(payload_str)

        logger.info(f"通知被点击: action_id={action_id!r}, payload={payload}")

        # 调用用户回调（导航到聊天页面）
        if self._on_tap_callback and payload:
            try:
                self._on_tap_callback(payload)
            except Exception as e:
                logger.error(f"通知点击回调异常: {e}")

    async def get_launch_details(self) -> dict:
        """
        检查APP是否由点击通知启动。APP启动时调用一次。

        Returns:
            dict: {
                "launched_from_notification": bool,
                "conversation_id": str|None,
                "sender": str|None,
                "type": str|None,
            }
        """
        result = {
            "launched_from_notification": False,
            "conversation_id": None,
            "sender": None,
            "type": None,
        }

        if not self.is_available():
            return result

        try:
            details = await self._notifications.get_notification_app_launch_details()
            if details.get("did_notification_launch_app"):
                response = details.get("notification_response") or {}
                payload = _parse_payload(response.get("payload", ""))
                result["launched_from_notification"] = True
                result["conversation_id"] = payload.get("conversation_id")
                result["sender"] = payload.get("sender")
                result["type"] = payload.get("type")
                logger.info(f"APP由通知启动: {result}")
        except Exception as e:
            logger.error(f"获取启动详情失败: {e}")

        return result

    # ------------------------------------------------------------------
    # 前台服务保活（可选）
    # ------------------------------------------------------------------

    async def start_keep_alive(
        self,
        title: str = "LanTalk 运行中",
        body: str = "后台保持连接，接收消息",
    ) -> bool:
        """
        启动安卓前台服务，在后台保持进程存活和TCP连接。

        注意：需要在 pyproject.toml 中声明 FOREGROUND_SERVICE 和
        FOREGROUND_SERVICE_SPECIAL_USE 权限，并运行 flet-android-notifications-patch。

        Args:
            title: 前台服务通知标题
            body: 前台服务通知内容

        Returns:
            bool: 是否成功启动
        """
        if not self.is_available():
            logger.info("非安卓或推送不可用，跳过前台服务")
            return False

        try:
            await self._notifications.start_foreground_service(
                notification_id=0,  # 0被Android禁止，这里用一个固定的大ID
                title=title,
                body=body,
                channel_id=CHANNEL_ID,
                channel_name=CHANNEL_NAME,
                importance="low",
                play_sound=False,
                enable_vibration=False,
                ongoing=True,
                foreground_service_types=["special_use"],
                start_type="start_sticky",
            )
            logger.info("前台保活服务已启动")
            return True
        except Exception as e:
            logger.error(f"启动前台服务失败: {e}")
            return False

    async def stop_keep_alive(self):
        """停止前台保活服务。"""
        if not self.is_available():
            return
        try:
            await self._notifications.stop_foreground_service()
            logger.info("前台保活服务已停止")
        except Exception as e:
            logger.error(f"停止前台服务失败: {e}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    async def are_notifications_enabled(self) -> bool:
        """检查系统通知是否已启用（用户可能在设置中关闭）。"""
        if not self.is_available():
            return False
        try:
            return await self._notifications.are_notifications_enabled()
        except Exception:
            return False

    def get_notification_id(self, conversation_id: str) -> int:
        """公开：获取会话对应的通知ID（用于调试）。"""
        return _conversation_to_notification_id(conversation_id)

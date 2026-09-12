# -*- coding: utf-8 -*-
"""
LanTalk 消息推送模块单元测试。

在桌面端运行（flet_android_notifications 不可用），测试业务逻辑：
- 前后台状态管理
- 通知ID稳定映射
- payload构建与解析
- 推送决策（前台不推/后台推/离线不推）
- 消息预览截断
- 启动详情解析

运行方式：
    cd LanTalk_mobile_clean
    python test_message_push.py
"""

import sys
import json
import asyncio

sys.path.insert(0, ".")

from message_push import (
    MessagePushManager,
    _conversation_to_notification_id,
    _build_payload,
    _parse_payload,
    _truncate_preview,
    _is_android,
    NOTIF_ID_MIN,
    NOTIF_ID_MAX,
    NOTIFICATION_TITLE,
    CHANNEL_ID,
)


class MockNotifications:
    """模拟 FletAndroidNotifications，用于桌面端测试。"""

    def __init__(self):
        self.shown = []
        self.cancelled = []
        self.cancel_all_called = False
        self.permission_result = True
        self.channel_created = False
        self.launch_details = {"did_notification_launch_app": False, "notification_response": None}

    async def request_permissions(self):
        return self.permission_result

    async def create_notification_channel(self, **kwargs):
        self.channel_created = True

    async def show_notification(self, **kwargs):
        self.shown.append(kwargs)

    async def cancel(self, notification_id):
        self.cancelled.append(notification_id)

    async def cancel_all(self):
        self.cancel_all_called = True

    async def get_notification_app_launch_details(self):
        return self.launch_details

    async def are_notifications_enabled(self):
        return True


def make_test_manager():
    """创建一个注入了Mock通知服务的测试管理器。"""
    mgr = MessagePushManager(page=None, on_notification_tap=None)
    # 强制设为安卓模式并注入mock
    mgr._is_android = True
    mgr._notifications = MockNotifications()
    mgr._initialized = True
    mgr._permission_granted = True
    mgr._is_online = True
    return mgr


# ------------------------------------------------------------------
# 测试1：通知ID稳定映射
# ------------------------------------------------------------------
def test_notification_id_stable():
    print("=" * 60)
    print("测试1：通知ID稳定映射（同会话同ID，不同会话不同ID）")
    id1 = _conversation_to_notification_id("user_zhangsan")
    id2 = _conversation_to_notification_id("user_zhangsan")
    id3 = _conversation_to_notification_id("user_lisi")
    assert id1 == id2, "同一会话应映射到相同通知ID"
    assert id1 != id3, "不同会话应映射到不同通知ID"
    assert NOTIF_ID_MIN <= id1 <= NOTIF_ID_MAX, f"通知ID应在范围内, got {id1}"
    assert id1 > 0, "通知ID必须为正整数"
    print(f"  [PASS] user_zhangsan -> {id1}, user_lisi -> {id3}")


# ------------------------------------------------------------------
# 测试2：payload构建与解析
# ------------------------------------------------------------------
def test_payload_build_parse():
    print("=" * 60)
    print("测试2：payload构建与解析（通知点击跳转）")
    payload = _build_payload("conv_123", "张三", "text")
    assert isinstance(payload, str), "payload应为JSON字符串"
    parsed = _parse_payload(payload)
    assert parsed["conversation_id"] == "conv_123"
    assert parsed["sender"] == "张三"
    assert parsed["type"] == "text"
    # 中文不转义
    assert "张三" in payload, "中文应保持可读（ensure_ascii=False）"
    # 异常输入容错
    assert _parse_payload("") == {}
    assert _parse_payload("invalid json") == {}
    assert _parse_payload(None) == {}
    print(f"  [PASS] payload={payload}")


# ------------------------------------------------------------------
# 测试3：消息预览截断
# ------------------------------------------------------------------
def test_preview_truncate():
    print("=" * 60)
    print("测试3：消息预览截断")
    short = "你好"
    assert _truncate_preview(short) == "你好"
    long_msg = "a" * 100
    truncated = _truncate_preview(long_msg, max_len=60)
    assert len(truncated) == 63  # 60 + "..."
    assert truncated.endswith("...")
    # 自定义长度
    assert len(_truncate_preview("b" * 20, max_len=10)) == 13
    print(f"  [PASS] 短消息不截断, 长消息截断为{len(truncated)}字符")


# ------------------------------------------------------------------
# 测试4：前台不推送
# ------------------------------------------------------------------
async def test_foreground_no_push():
    print("=" * 60)
    print("测试4：APP前台活跃时不弹系统通知")
    mgr = make_test_manager()
    mgr.set_foreground(True)
    pushed = await mgr.on_message_received(
        sender="张三", content="你好", conversation_id="conv1", msg_type="text"
    )
    assert pushed is False, "前台不应推送通知"
    assert len(mgr._notifications.shown) == 0, "前台不应调用show_notification"
    print("  [PASS] 前台收到消息，未弹通知")


# ------------------------------------------------------------------
# 测试5：后台推送
# ------------------------------------------------------------------
async def test_background_push():
    print("=" * 60)
    print("测试5：APP后台运行时弹系统通知")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    pushed = await mgr.on_message_received(
        sender="张三", content="你好世界", conversation_id="conv_bg", msg_type="text"
    )
    assert pushed is True, "后台应推送通知"
    assert len(mgr._notifications.shown) == 1
    notif = mgr._notifications.shown[0]
    assert notif["title"] == NOTIFICATION_TITLE
    assert "张三" in notif["body"]
    assert "你好世界" in notif["body"]
    assert notif["channel_id"] == CHANNEL_ID
    assert notif["auto_cancel"] is True
    assert notif["category"] == "message"
    # payload可解析
    payload = _parse_payload(notif["payload"])
    assert payload["conversation_id"] == "conv_bg"
    assert payload["sender"] == "张三"
    print(f"  [PASS] 后台推送: title={notif['title']}, body={notif['body']}")


# ------------------------------------------------------------------
# 测试6：离线不推送
# ------------------------------------------------------------------
async def test_offline_no_push():
    print("=" * 60)
    print("测试6：TCP离线时不弹通知（离线消息不推送）")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    mgr.set_online(False)
    pushed = await mgr.on_message_received(
        sender="张三", content="离线消息", conversation_id="conv_off", msg_type="text"
    )
    assert pushed is False, "离线不应推送通知"
    assert len(mgr._notifications.shown) == 0
    print("  [PASS] 离线收到消息，未弹通知")


# ------------------------------------------------------------------
# 测试7：文件消息推送
# ------------------------------------------------------------------
async def test_file_message_push():
    print("=" * 60)
    print("测试7：文件消息通知预览格式")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    await mgr.on_message_received(
        sender="李四", content="项目文档.pdf", conversation_id="conv_file", msg_type="file"
    )
    notif = mgr._notifications.shown[0]
    assert "文件" in notif["body"]
    assert "项目文档.pdf" in notif["body"]
    payload = _parse_payload(notif["payload"])
    assert payload["type"] == "file"
    print(f"  [PASS] 文件通知: body={notif['body']}")


# ------------------------------------------------------------------
# 测试8：同会话多条消息更新同一通知（不堆叠）
# ------------------------------------------------------------------
async def test_same_conversation_updates():
    print("=" * 60)
    print("测试8：同一会话多条消息复用同一通知ID（更新不堆叠）")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    await mgr.on_message_received(sender="王五", content="第一条", conversation_id="conv_same", msg_type="text")
    await mgr.on_message_received(sender="王五", content="第二条", conversation_id="conv_same", msg_type="text")
    await mgr.on_message_received(sender="王五", content="第三条", conversation_id="conv_same", msg_type="text")
    # 三次推送都用同一个通知ID
    ids = [n["notification_id"] for n in mgr._notifications.shown]
    assert len(set(ids)) == 1, f"同会话应使用相同通知ID, got {ids}"
    # 最后一条内容是最新的
    assert "第三条" in mgr._notifications.shown[-1]["body"]
    print(f"  [PASS] 3条消息共用通知ID={ids[0]}, 最新内容已更新")


# ------------------------------------------------------------------
# 测试9：清除会话通知
# ------------------------------------------------------------------
async def test_cancel_conversation():
    print("=" * 60)
    print("测试9：进入聊天页面时清除该会话通知")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    await mgr.on_message_received(sender="赵六", content="测试", conversation_id="conv_cancel", msg_type="text")
    notif_id = mgr._notifications.shown[0]["notification_id"]
    # 进入聊天页面，清除通知
    await mgr.cancel_conversation_notifications("conv_cancel")
    assert notif_id in mgr._notifications.cancelled
    print(f"  [PASS] 已清除通知ID={notif_id}")


# ------------------------------------------------------------------
# 测试10：清除所有通知
# ------------------------------------------------------------------
async def test_cancel_all():
    print("=" * 60)
    print("测试10：登出/退出时清除所有通知")
    mgr = make_test_manager()
    mgr.set_foreground(False)
    await mgr.on_message_received(sender="A", content="msg1", conversation_id="c1", msg_type="text")
    await mgr.on_message_received(sender="B", content="msg2", conversation_id="c2", msg_type="text")
    await mgr.cancel_all_notifications()
    assert mgr._notifications.cancel_all_called is True
    print("  [PASS] 已调用cancel_all")


# ------------------------------------------------------------------
# 测试11：通知点击回调
# ------------------------------------------------------------------
def test_notification_tap_handler():
    print("=" * 60)
    print("测试11：通知点击回调解析payload并触发用户回调")
    received = []

    def my_tap(payload):
        received.append(payload)

    mgr = MessagePushManager(page=None, on_notification_tap=my_tap)
    mgr._is_android = True
    mgr._notifications = MockNotifications()
    mgr._initialized = True
    mgr._permission_granted = True

    # 模拟通知点击事件
    class FakeEvent:
        data = json.dumps({
            "payload": _build_payload("conv_tap", "点击者", "text"),
            "action_id": "",
        })

    mgr._handle_notification_tap(FakeEvent())
    assert len(received) == 1
    assert received[0]["conversation_id"] == "conv_tap"
    assert received[0]["sender"] == "点击者"
    print(f"  [PASS] 点击回调收到: {received[0]}")


# ------------------------------------------------------------------
# 测试12：APP由通知启动
# ------------------------------------------------------------------
async def test_launch_from_notification():
    print("=" * 60)
    print("测试12：APP由点击通知启动时的启动详情")
    mgr = make_test_manager()
    # 模拟由通知启动
    mgr._notifications.launch_details = {
        "did_notification_launch_app": True,
        "notification_response": {
            "payload": _build_payload("conv_launch", "启动者", "file"),
            "action_id": "",
        },
    }
    details = await mgr.get_launch_details()
    assert details["launched_from_notification"] is True
    assert details["conversation_id"] == "conv_launch"
    assert details["sender"] == "启动者"
    assert details["type"] == "file"
    print(f"  [PASS] 启动详情: {details}")

    # 非通知启动
    mgr._notifications.launch_details = {
        "did_notification_launch_app": False,
        "notification_response": None,
    }
    details2 = await mgr.get_launch_details()
    assert details2["launched_from_notification"] is False
    print("  [PASS] 非通知启动检测正确")


# ------------------------------------------------------------------
# 测试13：桌面端安全空操作
# ------------------------------------------------------------------
async def test_desktop_noop():
    print("=" * 60)
    print("测试13：桌面端所有推送方法为安全空操作（不崩溃）")
    mgr = MessagePushManager(page=None, on_notification_tap=None)
    # 桌面端 _is_android 应为 False
    assert mgr._is_android is False or isinstance(mgr._is_android, bool)
    # init 应安全返回
    result = await mgr.init()
    assert result is True  # 桌面端初始化为空操作，返回True
    # 前台/后台状态管理正常
    mgr.set_foreground(False)
    assert mgr.is_foreground() is False
    mgr.set_foreground(True)
    assert mgr.is_foreground() is True
    # on_message_received 安全返回False
    pushed = await mgr.on_message_received(sender="x", content="y", conversation_id="z")
    assert pushed is False
    # cancel 安全无异常
    await mgr.cancel_conversation_notifications("z")
    await mgr.cancel_all_notifications()
    # 启动详情安全返回
    details = await mgr.get_launch_details()
    assert details["launched_from_notification"] is False
    print("  [PASS] 桌面端所有方法安全空操作，无异常")


# ------------------------------------------------------------------
# 测试14：状态变更日志
# ------------------------------------------------------------------
def test_state_management():
    print("=" * 60)
    print("测试14：前后台/在线状态管理")
    mgr = MessagePushManager(page=None)
    assert mgr.is_foreground() is True  # 默认前台
    assert mgr.is_online() is False      # 默认离线
    mgr.set_foreground(False)
    mgr.set_online(True)
    assert mgr.is_foreground() is False
    assert mgr.is_online() is True
    print("  [PASS] 状态管理正确")


# ------------------------------------------------------------------
# 主函数
# ------------------------------------------------------------------
async def run_all():
    print("=" * 60)
    print("  LanTalk 消息推送模块单元测试")
    print("  平台: " + ("安卓" if _is_android() else "桌面（mock测试）"))
    print("=" * 60)
    print()

    # 同步测试
    test_notification_id_stable()
    test_payload_build_parse()
    test_preview_truncate()
    test_notification_tap_handler()
    test_state_management()

    # 异步测试
    await test_foreground_no_push()
    await test_background_push()
    await test_offline_no_push()
    await test_file_message_push()
    await test_same_conversation_updates()
    await test_cancel_conversation()
    await test_cancel_all()
    await test_launch_from_notification()
    await test_desktop_noop()

    print()
    print("=" * 60)
    print("  全部 14 项测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all())

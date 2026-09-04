# client/ui/models.py
"""UI 层数据模型：消息、会话、用户等前端状态。

与网络层/数据层解耦，仅用于 UI 组件之间传递展示数据。
"""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ChatMessage:
    """一条聊天消息。"""

    text: str                       # 消息文本
    is_self: bool = False           # 是否为自己发送
    sender: str = ""                # 发送者昵称
    timestamp: datetime = field(default_factory=datetime.now)
    is_system: bool = False         # 是否为系统提示消息


@dataclass
class FileMessage:
    """一条文件消息（在对话框中显示文件卡片）。"""

    filename: str                   # 文件名
    file_size: int = 0              # 文件大小（字节）
    file_id: str = ""               # 服务端文件ID
    is_self: bool = False           # 是否为自己发送
    sender: str = ""                # 发送者昵称
    timestamp: datetime = field(default_factory=datetime.now)
    download_path: str = ""         # 下载保存路径（接收方下载后填充）
    status: str = "pending"         # pending/downloading/completed/failed


@dataclass
class Conversation:
    """一个会话（对应一个设备/用户或公共频道）。"""

    conv_id: str                    # 会话唯一标识
    title: str                      # 显示名称
    subtitle: str = ""              # 副标题（IP / 最后消息等）
    is_online: bool = True          # 在线状态
    unread: int = 0                 # 未读数
    is_self: bool = False           # 是否为自己（置顶不可点）

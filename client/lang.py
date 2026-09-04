# client/lang.py - 多语言支持（中文原文做 key，英文查表；兼容旧英文 key）
#
# 用法：
#   t("设置")                 -> 中文环境"设置" / 英文环境"Settings"
#   t("第 {n} 次重连").format(n=i)   -> 动态句子（英文表用同样的 {n} 占位）
#   t("settings")             -> 旧英文 key，仍兼容（等价 t("设置")）
#
# 设计原则：查不到翻译时原样返回中文，绝不抛异常、绝不显示成空白或 key。
_current_lang = "zh"

# 旧英文 key -> 中文原文（保持历史 t("settings") 调用不破坏）
LEGACY = {
    "settings": "设置",
    "server_set": "服务器设置",
    "save_server": "保存服务器设置",
    "change_pwd": "修改密码",
    "change_name": "修改用户名",
    "test_latency": "测试延迟",
    "reconnect": "重新连接",
    "language": "语言",
    "latency": "延迟",
    "close": "关闭",
    "old_pwd": "旧密码",
    "new_pwd": "新密码",
    "confirm_pwd": "确认新密码",
    "new_name": "新用户名",
    "verify_pwd": "验证密码",
    "confirm_change_pwd": "确认修改密码",
    "confirm_change_name": "确认修改用户名",
    "reconnect_ok": "已断开，请重新登录",
    "lang_restart": "语言已切换",
    "ip": "IP",
    "port": "端口",
    "logout": "退出登录",
    "send": "发 送",
    "current_user": "当前用户",
    "login": "登 录",
    "register": "注 册",
    "username": "用户名",
    "password": "密码",
    "connecting": "正在登录...",
    "registering": "注册中...",
    "register_now": "立即注册",
    "new_register": "注册新账号",
    "confirm_password": "确认密码",
    "chat_room": "聊天室",
    "online": "在线",
    "public": "公共聊天室",
    "ok": "确定",
    "type": "类型",
    "msg_type": "消息类型",
}

# 中文原文 -> English（静态 + 带占位符的动态模板，占位符保持一致，用 .format 填充）
EN = {
    # —— 通用按钮 / 动作 ——
    "设置": "Settings", "关闭": "Close", "保存": "Save", "取消": "Cancel",
    "应用": "Apply", "完成": "Done", "确定": "OK", "失败": "Failed", "成功": "Success",
    "拒绝": "Decline", "同意": "Accept", "接受": "Accept", "加入": "Join",
    "发送": "Send", "发 送": "Send", "提交中...": "Submitting...", "发送中...": "Sending...",
    "挂断": "Hang Up", "静音": "Mute", "取消静音": "Unmute", "跟随": "Follow",
    "刷新好友列表": "Refresh Friends", "添加好友": "Add Friend", "加好友": "Add Friend",
    "返回聊天": "Back to Chat", "选择文件": "Choose File", "立即注册": "Register Now",
    "登 录": "Login", "注 册": "Register", "登录": "Login", "注册": "Register",
    "退出登录": "Logout", "重新连接": "Reconnect", "测试延迟": "Test Latency",
    "中文": "Chinese",
    # —— 主题 / 语言 ——
    "主题": "Theme", "主题切换": "Toggle Theme", "亮色": "Light", "暗色": "Dark",
    "语言": "Language", "语言已切换": "Language changed",
    # —— 表单标签 ——
    "用户名": "Username", "昵称": "Nickname", "密码": "Password",
    "密码（可留空）": "Password (optional)", "端口": "Port", "服务器": "Server",
    "旧密码": "Old Password", "新密码": "New Password", "原密码": "Old Password",
    "确认密码": "Confirm Password", "确认新密码": "Confirm New Password",
    "新用户名": "New Username", "新昵称": "New Nickname", "验证密码": "Verify Password",
    "对方用户名": "Friend's Username", "输入对方昵称": "Enter friend's nickname",
    "输入消息...": "Type a message...", "当前用户": "Current User",
    # —— 列表 / 状态 ——
    "在线": "Online", "离线": "Offline", "我": "Me", "我自己": "(Me)", "所有人": "Everyone",
    "参与者": "Participants", "在线设备": "Online Devices", "好友": "Friends",
    "会话": "Conversations", "待处理": "Pending", "公共聊天室": "Public Chat Room",
    "聊天室": "Chat Room", "公共语音房间": "Public Voice Room", "语音通话": "Voice Call",
    "通话中": "In Call", "通话中...": "In Call...", "已结束": "Ended",
    "已连接到聊天室": "Connected to chat room", "已断开，请重新登录": "Disconnected, please log in again",
    "暂无好友，点击下方添加": "No friends yet — tap below to add",
    "加入了聊天室": "joined the chat room", "离开了聊天室": "left the chat room",
    # —— 连接 / 登录流程 ——
    "正在连接...": "Connecting...", "正在登录...": "Logging in...",
    "正在注册...": "Registering...", "注册中...": "Registering...",
    "正在连接服务器...": "Connecting to server...", "正在重新连接...": "Reconnecting...",
    "正在呼叫 {target_user}...": "Calling {target_user}...",
    "注册新账号": "New Account", "注册完成": "Registration complete",
    "注册成功，正在自动登录...": "Registered, signing you in...",
    "局域网聊天": "LAN Chat",
    "重新连接成功！": "Reconnected!",
    "连接已断开，正在尝试重连...": "Connection lost, trying to reconnect...",
    "连接已断开，正在尝试重连……": "Connection lost, trying to reconnect...",
    "连接已断开，请重新连接": "Disconnected, please reconnect", "连接断开": "Disconnected",
    "⚠️ 连接已断开，正在尝试重连...": "⚠️ Connection lost, trying to reconnect...",
    "⚠️ 多次重连失败，请检查网络后手动重连": "⚠️ Reconnect failed repeatedly; check your network and reconnect manually",
    "您已被踢出": "You have been kicked",
    # —— 校验 / 错误 ——
    "请输入用户名": "Please enter a username", "请输入昵称": "Please enter a nickname",
    "昵称不能为空": "Nickname cannot be empty", "密码不能为空": "Password cannot be empty",
    "密码至少4位": "Password must be at least 4 characters",
    "密码至少 4 位（也可设置后牢记）": "Password must be at least 4 characters",
    "用户名至少3位": "Username must be at least 3 characters",
    "两次密码不一致": "The two passwords do not match",
    "两次新密码不一致": "The two new passwords do not match",
    "新密码至少4位": "New password must be at least 4 characters",
    "请填写完整": "Please fill in all fields", "端口号无效": "Invalid port number",
    "端口必须是数字": "Port must be a number", "IP 不能为空": "IP cannot be empty",
    "登录失败": "Login failed", "修改失败": "Change failed", "修改完成": "Changed successfully",
    "修改密码": "Change Password", "修改用户名": "Change Username", "修改昵称": "Change Nickname",
    "确认修改密码": "Confirm Password Change", "确认修改用户名": "Confirm Username Change",
    "密码修改完成": "Password changed", "好友请求已发送": "Friend request sent",
    "服务器设置": "Server Settings", "保存服务器设置": "Save Server Settings",
    "服务器设置已保存，下次连接生效": "Server settings saved; takes effect on next connection",
    # —— IP 模式 ——
    "服务器地址(IPv4/域名)": "Server address (IPv4/domain)",
    "服务器IPv6地址(可选)": "Server IPv6 address (optional)",
    "填了就优先用IPv6连接，不填用上面的地址": "If filled, IPv6 is preferred; otherwise use the address above",
    "填了优先用IPv6，不填用上面的地址": "IPv6 preferred if filled; otherwise use the address above",
    "自动": "Auto", "仅IPv4": "IPv4 Only", "仅IPv6": "IPv6 Only",
    "自动(优先IPv4)": "Auto (prefer IPv4)", "IP版本": "IP Version",
    "IP版本已切换为: {mode_name}": "IP mode switched to: {mode_name}",
    "本机IPv4: 检测中...": "Local IPv4: detecting...", "本机IPv6: 检测中...": "Local IPv6: detecting...",
    "未检测到": "Not detected", "无公网IPv6": "No public IPv6",
    "本机没有IPv6网络，无法使用IPv6模式连接": "No IPv6 network available; cannot connect in IPv6 mode",
    "调试-全部IPv6: ": "Debug - all IPv6: ", "调试-全部IPv6: (无)": "Debug - all IPv6: (none)",
    # —— 语音通话 ——
    "静音麦克风": "Mute Microphone", "关闭扬声器": "Turn Speaker Off",
    "打开扬声器": "Turn Speaker On", "麦克风选择": "Microphone",
    "选择麦克风": "Select Microphone", "系统默认": "System Default",
    "系统默认麦克风": "System Default Microphone",
    "系统默认麦克风（推荐）": "System Default (Recommended)",
    "手机内置麦克风": "Phone Built-in Microphone",
    "麦克风: 系统默认": "Mic: System Default",
    "丢包率: 0.0%": "Packet loss: 0.0%", "丢包率: 0%": "Packet loss: 0%",
    "对方已挂断": "The other side hung up", "对方拒绝了通话": "Call declined",
    "已拒绝通话": "Call declined", "通话已结束": "Call ended",
    "语音房间已结束": "Voice room ended", "已离开语音房间": "Left the voice room",
    "你发起了公共语音房间，等待他人加入...": "You started a public voice room, waiting for others to join...",
    "（语音通话进行中，可在语音界面挂断）": "(Voice call in progress; hang up on the voice screen)",
    "来电提醒": "Incoming Call", "📞 来电": "📞 Incoming Call",
    "已有通话进行中": "A call is already in progress",
    "已有语音房间进行中": "A voice room is already in progress",
    "房间注册失败，请检查服务器语音中继": "Failed to register room; check the server voice relay",
    "音频启动失败": "Audio failed to start", "音频设备启动失败": "Audio device failed to start",
    "注意：切换麦克风后需要重新开始通话": "Note: restart the call after switching microphones",
    # —— 文件传输 ——
    "发送文件": "Send File", "选择要发送的文件": "Select a file to send",
    "上传失败": "Upload failed", "上传成功": "Uploaded", "下载中...": "Downloading...",
    "等待下载...": "Waiting to download...", "已下载": "Downloaded",
    "下载失败": "Download failed", "下载成功": "Downloaded successfully",
    "发送失败": "Send failed", "文件不存在": "File not found", "文件为空": "File is empty",
    "文件过大（上限200MB）": "File too large (limit 200MB)",
    "文件选择失败，请重试": "File selection failed, please try again",
    "未选择文件": "No file selected",
    "已有文件正在传输，请稍候": "A file is already transferring, please wait",
    "上传完成确认失败": "Failed to confirm upload completion",
    "MD5校验失败，文件可能已损坏": "MD5 checksum failed; the file may be corrupted",
    "第 {i} 块上传失败": "Chunk {i} upload failed",
    "第 {i} 块下载失败": "Chunk {i} download failed",
    # —— 动态模板（用 .format 填充，占位符保持一致）——
    "{user} (我)": "{user} (me)",
    "{user} 加入了语音房间": "{user} joined the voice room",
    "{user} 离开了语音房间": "{user} left the voice room",
    "{callee} 离开了语音房间": "{callee} left the voice room",
    "{caller} 发起语音通话": "{caller} started a voice call",
    "{caller} 邀请您语音通话": "{caller} invites you to a voice call",
    "{host} 正在进行语音通话": "{host} is in a voice call",
    "公共语音房间({host})": "Public Voice Room ({host})",
    "公共语音房间（{host}）": "Public Voice Room ({host})",
    "[语音房间] {host} 发起了公共语音通话": "[Voice Room] {host} started a public voice call",
    "{target_user} 当前离线，无法拨通": "{target_user} is offline and cannot be reached",
    "丢包率: {pct:.1f}%": "Packet loss: {pct:.1f}%",
    "丢包: {pct:.1f}%": "Loss: {pct:.1f}%",
    "丢包率: {pct:.1f}%": "Packet loss: {pct:.1f}%",
    "延迟 --ms  丢包 --%": "Latency --ms  Loss --%",
    "延迟 {avg:.0f}ms 丢包 {pct:.0f}%": "Latency {avg:.0f}ms  Loss {pct:.0f}%",
    "{dot} 延迟: {ms:.0f}ms": "{dot} Latency: {ms:.0f}ms",
    "启动失败：{err}": "Start failed: {err}",
    "启动失败: {reason}": "Start failed: {reason}",
    "语音启动失败：{err}": "Voice start failed: {err}",
    "语音连接失败: {e}": "Voice connection failed: {e}",
    "呼叫失败: {e}": "Call failed: {e}", "呼叫失败：{e}": "Call failed: {e}",
    "注册失败：{e}": "Registration failed: {e}", "连接失败：{e}": "Connection failed: {e}",
    "请求失败：{e}": "Request failed: {e}", "下载失败: {e}": "Download failed: {e}",
    "发送失败：{e}": "Send failed: {e}", "加好友失败：{e}": "Add friend failed: {e}",
    "文件发送失败: {err}": "File send failed: {err}",
    "聊天页加载失败：{e}": "Failed to load chat page: {e}",
    "无法打开麦克风：{e}": "Cannot open microphone: {e}",
    "无法打开扬声器：{e}": "Cannot open speaker: {e}",
    "麦克风初始化失败（可能未授予录音权限）": "Microphone init failed (recording permission may be missing)",
    "Android pyjnius 不可用：{e}": "Android pyjnius unavailable: {e}",
    "桌面端缺少音频库（sounddevice/pyaudio 均无）：{e}": "No desktop audio backend (need sounddevice/pyaudio): {e}",
    "缺少 pyaudio：{e}": "Missing pyaudio: {e}",
    "{e_pa}；建议改用 sounddevice：python -m pip install sounddevice":
        "{e_pa}; install sounddevice instead: python -m pip install sounddevice",
    "当前: {current_name}": "Current: {current_name}",
    "麦克风: {name}": "Mic: {name}",
    "系统默认 + {n} 个可用麦克风，不选即跟随系统": "System default + {n} mics; leave as-is to follow system",
    "设备{i}": "Device {i}",
    "已保存: {m.download_path}": "Saved: {m.download_path}",
    "保存位置: {path_text}": "Save to: {path_text}",
    "私聊: {conv_id}": "Private: {conv_id}",
    "服务器响应格式异常: {resp!r}": "Malformed server response: {resp!r}",
    "消息长度异常: {length}": "Bad message length: {length}",
    "无法解析地址 {host}: {e}": "Cannot resolve address {host}: {e}",
    "地址 {host}:{port} 在{mode_name}模式下没有可用的IP记录":
        "No IP record for {host}:{port} in {mode_name} mode",
    "连接 {host}:{port} 失败（{mode_name}模式）: {last_error}":
        "Connect to {host}:{port} failed ({mode_name}): {last_error}",
    "第 {attempt}/10 次尝试连接...": "Attempt {attempt}/10...",
    "第 {attempt}/10 次重连，{delay} 秒后尝试...": "Reconnect {attempt}/10 in {delay}s...",
    "第 {attempt} 次失败: {e}": "Attempt {attempt} failed: {e}",
    "版本: {VERSION}\n用户: {self.client.username}":
        "Version: {VERSION}\nUser: {self.client.username}",
    "移动端 {VERSION}": "Mobile {VERSION}",
    "语音通话 - {target}": "Voice Call - {target}",
    "登录失败: {e}": "Login failed: {e}",
    "本机IPv4: {ipv4}": "Local IPv4: {ipv4}",
    "本机IPv6: {ipv6}": "Local IPv6: {ipv6}",
    "[网络] 消息处理异常（已忽略，不断开）: {e}": "[Net] message error (ignored, kept alive): {e}",
    "[语音] 发送异常: {e}": "[Voice] send error: {e}",
    "[语音] 接收异常: {e}": "[Voice] receive error: {e}",
    "[语音] 音频初始化失败: {e}": "[Voice] audio init failed: {e}",
    "✅ {msg}，请重新登录": "✅ {msg}, please log in again",
    "切换后界面立即刷新": "Interface refreshes immediately",
    "发送请求": "Send Request",
    "失败: {err}": "Failed: {err}",
    "失败：{e}": "Failed: {e}",
    "未知错误": "Unknown error",
    "缺少 sounddevice，请运行: python -m pip install sounddevice。原始错误: {e}": "sounddevice missing; run: python -m pip install sounddevice. Original: {e}",
    "连接不存在": "Connection does not exist",
    "重新连接10次均失败，请检查服务器后重试": "Failed to reconnect after 10 attempts; check the server and retry",
    "{who} 加入了聊天室": "{who} joined the chat room",
    "{who} 离开了聊天室": "{who} left the chat room",
    "本机IPv4: {v4}  |  IPv6: {v6}": "Local IPv4: {v4}  |  IPv6: {v6}",
    "本机IPv4: {v4}\n本机IPv6: {v6}{debug}": "Local IPv4: {v4}\nLocal IPv6: {v6}{debug}",
    "\n调试-全部IPv6: {ips}": "\nDebug - all IPv6: {ips}",
    "(无)": "(none)",
    "🔊 {host} 发起了公共语音通话": "🔊 {host} started a public voice call",
    "加好友{result}：{msg}": "Add friend {result}: {msg}",
    "版本: {VERSION}\n用户: {user}": "Version: {VERSION}\nUser: {user}",
    "已切换到{mode}主题": "Switched to {mode} theme",
    "跟随系统": "Follow System",
    "昵称修改失败：{ex}": "Nickname change failed: {ex}",
    "密码修改失败：{ex}": "Password change failed: {ex}",
    "已保存: {path}": "Saved: {path}",
}


def set_lang(lang):
    global _current_lang
    _current_lang = "en" if str(lang).lower().startswith("en") else "zh"


def get_lang():
    return _current_lang


def t(text, **kwargs):
    """翻译文本。text 可为中文原文或旧英文 key；查不到则原样返回，绝不抛异常。"""
    if text is None:
        return ""
    s = str(text)
    if s in LEGACY:                      # 旧英文 key -> 中文原文
        s = LEGACY[s]
    if _current_lang == "en":
        s = EN.get(s, s)
    if kwargs:
        try:
            s = s.format(**kwargs)
        except Exception:
            pass
    return s

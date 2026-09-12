# -*- coding: utf-8 -*-
"""
connection_watchdog.py — LanTalk 移动端连接看门狗（新增独立模块，不修改任何原有代码）

【要解决的问题：WiFi/移动网络切换后的“无线重连”】
原有心跳循环 `_heartbeat_loop` 每 10 秒只负责发送 ping：
  1) send_ping 失败被 except 静默吞掉；
  2) 从不检测“多久没收到 pong”。
当手机切换 WiFi、信号中断、在蜂窝与 WiFi 间切换时，TCP 连接会变成
**半开/僵死连接**：服务端没有发 FIN，客户端阻塞在 recv() 上永远不报错，
于是 `_on_connection_close` 不会被触发，自动重连链永远不启动，表现为
APP“假在线”、消息收发都无响应，只能手动点重新连接。

【本模块原理（复用原有状态与重连链，不重写协议）】
原有代码已经维护了一个时间戳 `app._ping_send_time`：
  - 心跳线程每次发送 ping 前置为当前时间；
  - 收到服务端 pong（`_on_pong`）后置为 None。
看门狗周期性读取它：
  - None            → 最近一次 ping 已收到 pong，连接健康；
  - 距今 < timeout  → pong 还在路上，正常等待；
  - 距今 > timeout  → 连续多个心跳周期收不到 pong，判定连接僵死。
判定僵死后，**只 shutdown/close 底层 socket，绝不动 client.running 标志**：
这样阻塞在 recv() 的接收线程会立刻抛异常，而接收循环的判断是
`if self.running and self.on_close` —— running 仍为 True，于是会正常回调
原有 `_on_connection_close` → `_do_auto_reconnect`，完整复用原有退避重连、
页面重建、语音重新注册等全部逻辑，本模块不重复实现任何一部分。

【线程安全 / 幂等】
- 一个 app 实例只运行一个看门狗线程（start_for_app 幂等，重连后重复调用安全）；
- 所有读取均做防御，任何异常都不会影响主程序；
- 退出登录 / 被踢（_auto_reconnect=False、username 为空）时看门狗空转不干预。
"""

import socket
import threading
import time


class ConnectionWatchdog:
    """检测僵死 TCP 连接并主动触发原有重连流程的后台看门狗。"""

    def __init__(self, app, timeout=30.0, check_interval=5.0):
        """
        参数:
            app: MobileChatApp 实例
            timeout: 一次 ping 发出后多少秒没收到 pong 即判定僵死（默认30秒，
                     约等于连续 3 个 10 秒心跳周期无响应，避免弱网误判）
            check_interval: 看门狗检查周期（默认5秒）
        """
        self.app = app
        self.timeout = float(timeout)
        self.check_interval = float(check_interval)
        self._stop_event = threading.Event()
        self._thread = None
        self._last_action_time = 0.0  # 限流，避免对同一条僵死连接反复close

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self):
        """启动看门狗线程（幂等：已在运行则直接返回）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="ConnWatchdog", daemon=True)
        self._thread.start()

    def stop(self):
        """停止看门狗。"""
        self._stop_event.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception:
                # 看门狗自身任何异常都不允许影响主程序
                pass
            # 用 Event.wait 代替 sleep，stop 时能立即退出
            self._stop_event.wait(self.check_interval)

    def _is_session_active(self, client):
        """判断当前是否处于“应当保持在线”的会话状态。"""
        # 未开启自动重连（退出登录/被踢后为 False）→ 不干预
        if not getattr(self.app, "_auto_reconnect", False):
            return False
        # 未登录 → 不干预
        if not getattr(client, "username", ""):
            return False
        # 接收循环未运行或没有 socket → 原有逻辑会自行处理，不干预
        if not getattr(client, "running", False):
            return False
        if not getattr(client, "sock", None):
            return False
        return True

    def _check_once(self):
        app = self.app
        client = getattr(app, "client", None)
        if client is None:
            return
        if not self._is_session_active(client):
            return

        send_time = getattr(app, "_ping_send_time", None)
        if send_time is None:
            return  # 最近一次 ping 已收到 pong，连接健康

        elapsed = time.time() - float(send_time)
        if elapsed <= self.timeout:
            return  # 还在容忍窗口内

        # 限流：同一次僵死至少间隔 timeout 才再次动作，防止重复触发
        now = time.time()
        if now - self._last_action_time < self.timeout:
            return
        self._last_action_time = now

        self._on_dead_connection(elapsed)

    def _on_dead_connection(self, elapsed):
        """判定连接僵死：提示并只关闭底层 socket，交由原有链路自动重连。"""
        app = self.app
        try:
            msg = ("⚠️ 网络连接无响应（{:.0f}秒未收到心跳应答），正在自动重连..."
                   ).format(elapsed)
            # 复用原有 UI 线程调度与系统消息显示（若存在）
            if hasattr(app, "_ui") and hasattr(app, "_append_system"):
                try:
                    app._ui(app._append_system, msg)
                except Exception:
                    try:
                        app._append_system(msg)
                    except Exception:
                        pass
        except Exception:
            pass

        client = getattr(app, "client", None)
        sock = getattr(client, "sock", None) if client is not None else None
        if sock is None:
            return
        # 关键：只关闭底层 socket，不调用 client.close()（后者会把 running
        # 置 False，导致接收循环 `if self.running and self.on_close` 不成立，
        # 反而无法触发重连）。关闭后阻塞的 recv 抛异常，running 仍为 True，
        # 原有 on_close → _do_auto_reconnect 重连链被正常拉起。
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 便捷入口
    # ------------------------------------------------------------------ #
    @staticmethod
    def start_for_app(app, timeout=30.0, check_interval=5.0):
        """
        为一个 app 实例幂等地启动看门狗。重复调用不会创建多个线程。
        重连成功后原有 show_chat 会再次调用本方法，已运行则直接复用。
        """
        if app is None:
            return None
        wd = getattr(app, "_conn_watchdog", None)
        if wd is None:
            wd = ConnectionWatchdog(app, timeout=timeout,
                                    check_interval=check_interval)
            try:
                app._conn_watchdog = wd
            except Exception:
                pass
        wd.start()
        return wd

    @staticmethod
    def stop_for_app(app):
        """停止 app 实例上的看门狗（退出登录时可选调用）。"""
        wd = getattr(app, "_conn_watchdog", None) if app is not None else None
        if wd is not None:
            wd.stop()

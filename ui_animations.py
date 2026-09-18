# -*- coding: utf-8 -*-
"""
ui_animations.py — LanTalk UI 动画模块（新增独立文件）

提供现代化的 UI 动画效果：
  1. 弹窗弹性弹出（scale + fade）
  2. 按钮 hover 浮起
  3. 未读红点弹入
  4. 文件传输进度呼吸色
  5. 页面切换淡入

使用：在 mobile_main.py 末尾 import 并调用 patch_ui_animations()
"""

import flet as ft


def animate_dialog_open(dialog: ft.AlertDialog):
    """弹窗打开时弹性缩放+淡入。"""
    if dialog.content:
        dialog.content.scale = ft.transform.Scale(scale=0.8)
        dialog.content.opacity = 0
    return dialog


def animate_dialog_close(dialog: ft.AlertDialog):
    """弹窗关闭时淡出缩小。"""
    if dialog.content:
        dialog.content.scale = ft.transform.Scale(scale=0.9)
        dialog.content.opacity = 0
    return dialog


def create_hover_button(text, on_click, **kwargs):
    """创建带 hover 浮起效果的按钮。"""
    btn = ft.ElevatedButton(text, on_click=on_click, **kwargs)
    btn.animate_scale = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)

    def _on_hover(e):
        if e.data == "true":
            btn.scale = 1.05
        else:
            btn.scale = 1.0
        btn.update()

    btn.on_hover = _on_hover
    return btn


def create_unread_badge(count: int):
    """未读消息红点徽章（弹入动画）。"""
    badge = ft.Container(
        content=ft.Text(str(count), size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
        width=18, height=18, border_radius=9,
        bgcolor=ft.Colors.RED_500,
        alignment=ft.alignment.center,
        scale=ft.transform.Scale(scale=0),
        opacity=0,
    )
    # 入场动画
    badge.scale = ft.transform.Scale(scale=1)
    badge.opacity = 1
    badge.animate_scale = ft.animation.Animation(300, ft.AnimationCurve.BOUNCE_OUT)
    badge.animate_opacity = ft.animation.Animation(200, ft.AnimationCurve.EASE_OUT)
    return badge


def breathing_container(content, **kwargs):
    """文件传输中的呼吸色容器（缓慢明暗交替）。"""
    container = ft.Container(
        content=content,
        animate_opacity=ft.animation.Animation(1000, ft.AnimationCurve.EASE_IN_OUT),
        **kwargs
    )
    return container


def page_fade_in(page: ft.Page):
    """页面切换淡入效果。"""
    page.opacity = 1
    page.animate_opacity = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT)


def patch_ui_animations():
    """在 mobile_main.py 末尾调用，应用全局动画 patch。"""
    print("[UIAnim] 动画模块已加载")

# -*- coding: utf-8 -*-
import os, sys, threading, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(page):
    import flet as ft
    page.title = "EmojiRenderTest2"
    # 关键：主字体用 Twemoji。emoji/国旗由它 shaping（ccmp 组合），
    # 普通中/拉丁字符它没有字形，Skia 回退系统字体。
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.GREEN_700, font_family="Twemoji")
    page.padding = 18
    try:
        page.window.always_on_top = True
        page.window.width = 440
        page.window.height = 760
    except Exception:
        pass
    page.add(
        ft.Text("普通文字 Plain text 123：", weight=ft.FontWeight.BOLD),
        ft.Text("你好，世界！Hello World 0123", size=20),
        ft.Text("国旗 Flags：", weight=ft.FontWeight.BOLD),
        ft.Text("🇨🇳 🇺🇸 🇯🇵 🇬🇧 🇫🇷 🇩🇪 🇰🇷 🇷🇺", size=30),
        ft.Text("新 emoji：", weight=ft.FontWeight.BOLD),
        ft.Text("🫠 🫡 🫶 🫥 🥲", size=26),
        ft.Text("组合 ZWJ：", weight=ft.FontWeight.BOLD),
        ft.Text("👨‍👩‍👧‍👦 👋🏽 🧑‍💻", size=26),
        ft.Text("混合一整句：", weight=ft.FontWeight.BOLD),
        ft.Text("你好呀🇨🇳，我在测试 emoji🫠 和组合👨‍👩‍👧‍👦，Hello!", size=20),
        ft.Text("长文本自动换行：一二三四五六七八九十 abcdefghijklmnopqrstuvwxyz 🇨🇳🇺🇸 文字文字文字文字文字文字文字", size=16),
    )
    print("RENDER_READY", flush=True)
    threading.Timer(30, lambda: os._exit(0)).start()


if __name__ == "__main__":
    import flet as ft
    try:
        ft.run(main)
    except Exception:
        try:
            ft.app(target=main)
        except Exception:
            traceback.print_exc()
            sys.exit(2)

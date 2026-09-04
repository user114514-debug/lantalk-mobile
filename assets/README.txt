=== 安卓音效 ===
本目录存放 Android 端使用的 MP3 音效（ft.Audio 播放）：
  click.mp3   按钮点击音
  notify.mp3  消息通知音
  dial.mp3    拨号音
  ring.mp3    来电铃声（循环播放）
  hangup.mp3  挂断音

说明：
- 必须使用 MP3 格式（Android 兼容性最好）。
- flet build apk 必须加 --include-packages flet_audio，否则无声。
- 代码中通过 asset 路径引用，如 src="/click.mp3"（以 / 开头，相对于 assets 目录）。
- Windows 端仍使用根目录下的 WAV 文件（winsound 播放），与本目录无关。

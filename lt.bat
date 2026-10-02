@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   LanTalk 移动端 - 一键打包 APK 并推送到 GitHub Actions
echo ============================================================
echo.
echo 当前目录: %cd%
echo.

REM ========== 1. 检查 Git ==========
echo [1/7] 检查 Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Git，请先安装：
    echo https://git-scm.com/download/win
    echo 安装后重新运行本脚本。
    pause
    exit /b 1
)
echo   Git 已安装

REM ========== 2. 配置 Git 用户信息 ==========
echo [2/7] 配置 Git 用户信息...
git config --global user.name >nul 2>&1
if errorlevel 1 (
    echo   未设置用户名，使用默认...
    git config --global user.name "user114514-debug"
)
git config --global user.email >nul 2>&1
if errorlevel 1 (
    echo   未设置邮箱，使用默认...
    git config --global user.email "user114514@example.com"
)
echo   用户信息已就绪

REM ========== 3. 确认项目文件 ==========
echo [3/7] 检查项目文件...
if not exist mobile_main.py (
    echo [错误] 当前目录没有 mobile_main.py，请确认脚本放在项目根目录
    pause
    exit /b 1
)
echo   mobile_main.py: 存在

if not exist requirements.txt (
    echo flet==0.86.5 > requirements.txt
    echo flet-audio>=0.86.0 >> requirements.txt
    echo pyjnius>=1.6.1 >> requirements.txt
    echo   已生成 requirements.txt
) else (
    echo   requirements.txt: 存在
)

REM ========== 4. 生成 .gitignore ==========
echo [4/7] 生成 .gitignore...
(
echo __pycache__/
echo *.pyc
echo *.pyo
echo build/
echo .gradle/
echo *.log
echo venv/
echo .flet/
echo crash_logs/
echo theme_config.json
echo test_*.py
echo _emoji_*.py
echo generate_flag_png.py
echo err_*.txt
echo out_*.txt
echo *_trace.txt
) > .gitignore
echo   .gitignore 已更新（排除崩溃日志、测试脚本、临时文件）

REM ========== 5. 生成 GitHub Actions workflow ==========
echo [5/7] 生成 GitHub Actions 配置...
if not exist .github\workflows mkdir .github\workflows
del .github\workflows\build-apk.yml >nul 2>&1
(
echo name: Build Android APK
echo.
echo on:
echo   push:
echo     branches: [ main ]
echo   workflow_dispatch:
echo.
echo jobs:
echo   build:
echo     runs-on: ubuntu-latest
echo     steps:
echo       - name: Checkout code
echo         uses: actions/checkout@v4
echo.
echo       - name: Setup Java JDK 17
echo         uses: actions/setup-java@v4
echo         with:
echo           distribution: 'temurin'
echo           java-version: '17'
echo.
echo       - name: Setup Flutter
echo         uses: subosito/flutter-action@v2
echo         with:
echo           flutter-version: '3.44.8'
echo           channel: 'stable'
echo.
echo       - name: Accept Android licenses
echo         run: ^|
echo           yes ^| flutter doctor --android-licenses
echo.
echo       - name: Setup Python
echo         uses: actions/setup-python@v5
echo         with:
echo           python-version: '3.12'
echo.
echo       - name: Install Flet and dependencies
echo         run: ^|
echo           python -m pip install --upgrade pip
echo           pip install "flet==0.86.5" "flet-audio>=0.86.0" "flet-android-notifications>=0.11.0" "cryptography>=42.0.0" "pyjnius>=1.6.1" "psutil>=7.0.0"
echo.
echo       - name: Build APK with icon
echo         run: ^|
echo           flet build apk --module-name mobile_main --project "LanTalk" --org "com.lantalk" --product "lantalk" --build-number "5" --build-version "3.7.3" --icon "assets/icon.png"
echo.
echo       - name: Upload APK
echo         uses: actions/upload-artifact@v4
echo         with:
echo           name: lantalk-apk
echo           path: build/flutter/build/app/outputs/flutter-apk/app-release.apk
) > .github\workflows\build-apk.yml
echo   build-apk.yml 已生成（v3.7.3 / build 5 / 带图标）

REM ========== 6. 初始化 Git 并提交 ==========
echo [6/7] 初始化 Git 并提交...
if not exist .git (
    git init
    git branch -M main
    echo   已初始化 Git 仓库
) else (
    echo   Git 仓库已存在
)
git add .
git commit -m "LanTalk mobile v3.7.3 - white splash + zh_TW + icon" >nul 2>&1
if errorlevel 1 (
    echo   没有新的改动需要提交
) else (
    echo   已提交改动
)

REM ========== 7. 推送到远程仓库 ==========
echo [7/7] 推送到 GitHub...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/user114514-debug/lantalk-mobile.git
git push -f -u origin main

if errorlevel 1 (
    echo.
    echo [错误] 推送失败！
    echo 可能原因：
    echo   1. 需要登录 GitHub（首次推送会弹出登录窗口）
    echo   2. 网络问题
    echo.
    pause
    exit /b 1
)

REM ========== 完成 ==========
echo.
echo ============================================================
echo   推送成功！GitHub Actions 正在构建 APK
echo ============================================================
echo.
echo   预计需要 8-10 分钟。
echo.
echo   查看构建状态：
echo   https://github.com/user114514-debug/lantalk-mobile/actions
echo.
echo   构建完成后：
echo   1. 点击绿色 ✓ 的任务
echo   2. 拉到底部 Artifacts
echo   3. 下载 lantalk-apk
echo   4. 解压得到 app-release.apk
echo   5. 传到手机安装即可
echo.
echo   正在打开 Actions 页面...
pause >nul
start https://github.com/user114514-debug/lantalk-mobile/actions

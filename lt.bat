@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   LanTalk 移动端 - 一键打包 APK（推送到 GitHub Actions）
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
    echo 安装后重启命令行再运行本脚本
    pause
    exit /b 1
)
echo   Git 已安装

REM ========== 2. 配置 Git 用户身份 ==========
echo [2/7] 检查 Git 用户身份...
git config --global user.name >nul 2>&1
if errorlevel 1 (
    echo   未配置用户名，正在设置...
    git config --global user.name "user114514-debug"
)
git config --global user.email >nul 2>&1
if errorlevel 1 (
    echo   未配置邮箱，正在设置...
    git config --global user.email "user114514@example.com"
)
echo   用户身份已配置

REM ========== 3. 确认必要文件 ==========
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
    echo   已创建 requirements.txt（含 flet-audio、pyjnius）
) else (
    echo   requirements.txt: 存在
)

if not exist .gitignore (
    (
echo __pycache__/
echo *.pyc
echo *.pyo
echo build/
echo .gradle/
echo *.log
echo venv/
echo .flet/
    ) > .gitignore
    echo   已创建 .gitignore
) else (
    echo   .gitignore: 存在
)

REM ========== 4. 创建 GitHub Actions workflow ==========
echo [4/7] 检查 GitHub Actions 配置...
if not exist .github\workflows mkdir .github\workflows
if not exist .github\workflows\build-apk.yml (
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
echo           pip install "flet==0.86.5" "flet-audio>=0.86.0" "pyjnius>=1.6.1"
echo.
echo       - name: Build APK
echo         run: ^|
echo           flet build apk --module-name mobile_main --project "LanTalk" --org "com.lantalk" --product "lantalk" --build-number "1" --build-version "2.8.5" --clear-cache
echo.
echo       - name: Upload APK
echo         uses: actions/upload-artifact@v4
echo         with:
echo           name: lantalk-apk
echo           path: build/apk/*.apk
    ) > .github\workflows\build-apk.yml
    echo   已创建 build-apk.yml（含 flet-audio、pyjnius）
) else (
    echo   build-apk.yml: 存在
)

REM ========== 5. 初始化 Git 并提交 ==========
echo [5/7] 初始化 Git 并提交...
if not exist .git (
    git init
    git branch -M main
    echo   已初始化 Git 仓库
) else (
    echo   Git 仓库已存在
)
git add .
git commit -m "LanTalk mobile v2.8.5 - build APK" >nul 2>&1
if errorlevel 1 (
    echo   没有新的更改需要提交
) else (
    echo   已提交更改
)

REM ========== 6. 设置远程仓库并推送 ==========
echo [6/7] 推送到 GitHub...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/user114514-debug/lantalk-mobile.git
git push -f -u origin main

if errorlevel 1 (
    echo.
    echo [错误] 推送失败！
    echo 可能原因：
    echo   1. 需要登录 GitHub（浏览器登录或输入 token）
    echo   2. 网络问题
    echo.
    echo 请手动执行 git push 查看具体错误
    pause
    exit /b 1
)

REM ========== 7. 完成 ==========
echo.
echo ============================================================
echo   推送成功！GitHub Actions 正在自动编译 APK
echo ============================================================
echo.
echo   编译约需 8-10 分钟
echo.
echo   查看编译状态：
echo   https://github.com/user114514-debug/lantalk-mobile/actions
echo.
echo   编译完成后：
echo   1. 点进绿色 ? 的记录
echo   2. 拉到最底部 Artifacts
echo   3. 下载 lantalk-apk
echo   4. 解压得到 app-release.apk
echo   5. 传到手机安装
echo.
echo   按任意键打开 Actions 页面...
pause >nul
start https://github.com/user114514-debug/lantalk-mobile/actions

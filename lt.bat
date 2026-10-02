@echo off
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo   LanTalk 移动端 - 一键打包 APK 并推送到 GitHub Actions
echo ============================================================
echo.
echo 当前目录: %cd%
echo.

REM ===== 从 pyproject.toml 读取版本号（唯一真相源，避免不同步）=====
for /f "tokens=2 delims== " %%v in ('findstr /b /c:"version" pyproject.toml') do set APP_VER=%%v
set APP_VER=%APP_VER:"=%
if "%APP_VER%"=="" set APP_VER=4.0.0
REM build-number 用日期 YYYYMMDD，每次推送单调递增
set BNUM=%date:~0,4%%date:~5,2%%date:~8,2%
echo 应用版本: %APP_VER%   /   build-number: %BNUM%
echo.

REM ========== 1. 检查 Git ==========
echo [1/9] 检查 Git...
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
echo [2/9] 配置 Git 用户信息...
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

REM ========== 3. 确认项目文件与 assets（缺了手机端会崩）==========
echo [3/9] 检查项目文件与 assets...
set MISSING=0
if not exist mobile_main.py (
    echo   [缺失] mobile_main.py
    set MISSING=1
)
if not exist pyproject.toml (
    echo   [缺失] pyproject.toml（flet build 靠它决定 APK 依赖）
    set MISSING=1
)
if not exist assets\icon.png (
    echo   [缺失] assets\icon.png（应用图标）
    set MISSING=1
)
if not exist assets\fonts\Twemoji.ttf (
    echo   [缺失] assets\fonts\Twemoji.ttf（emoji/国旗回退字体）
    set MISSING=1
)
if not exist assets\flags (
    echo   [缺失] assets\flags\（国旗 PNG 目录）
    set MISSING=1
)
if "%MISSING%"=="1" (
    echo.
    echo [错误] 关键文件缺失，手机端运行会崩溃，请补齐后再推送。
    pause
    exit /b 1
)
echo   mobile_main.py / pyproject.toml / icon.png / Twemoji.ttf / flags/ 全部存在

REM ========== 4. 生成/补全 requirements.txt（完整手机端依赖）==========
echo [4/9] 检查 requirements.txt...
if not exist requirements.txt (
    echo flet==0.86.5 > requirements.txt
    echo flet-audio>=0.86.0 >> requirements.txt
    echo flet-android-notifications>=0.11.0 >> requirements.txt
    echo cryptography>=42.0.0 >> requirements.txt
    echo pyjnius>=1.6.1 >> requirements.txt
    echo psutil>=7.0.0 >> requirements.txt
    echo # 桌面调试专用（不打进 APK，安卓走原生 MediaRecorder/pyjnius）：>> requirements.txt
    echo sounddevice>=0.4.6 >> requirements.txt
    echo numpy>=1.26.0 >> requirements.txt
    echo   已生成 requirements.txt（完整依赖）
) else (
    echo   requirements.txt: 存在
)

REM ========== 5. 生成 .gitignore ==========
echo [5/9] 生成 .gitignore...
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
echo _debug_*.py
echo _*_test.py
echo _theme_*.py
echo _bubble_*.py
echo generate_flag_png.py
echo err_*.txt
echo out_*.txt
echo *_trace.txt
echo voice_tmp/
) > .gitignore
echo   .gitignore 已更新（排除崩溃日志、测试、临时调试脚本、voice_tmp）

REM ========== 6. 构建前自检（语法 + 国旗数量）==========
echo [6/9] 构建前自检...
python -m py_compile mobile_main.py
if errorlevel 1 (
    echo   [错误] mobile_main.py 语法检查失败，修复后再推送。
    pause
    exit /b 1
)
echo   mobile_main.py 语法检查通过
for /f %%a in ('dir /b assets\flags\*.png 2^>nul ^| find /c /v ""') do set FLAG_COUNT=%%a
echo   国旗 PNG 数量: %FLAG_COUNT%（应为 259）

REM ========== 7. 生成 GitHub Actions workflow ==========
echo [7/9] 生成 GitHub Actions 配置...
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
echo       - name: Install Flet CLI ^(retry on network flake^)
echo         uses: nick-fields/retry@v3
echo         with:
echo           timeout_minutes: 5
echo           max_attempts: 3
echo           retry_on: error
echo           command: ^|
echo             export PATH="$HOME/.local/bin:$PATH"
echo             python -m pip install --upgrade pip
echo             pip install flet==0.86.5
echo             flet --version
echo.
echo       - name: Pre-build self-check
echo         run: ^|
echo           python -m py_compile mobile_main.py
echo           test -f assets/icon.png
echo           test -f assets/fonts/Twemoji.ttf
echo           test -d assets/flags
echo.
echo       - name: Build APK with icon ^(retry + clean cache on failure^)
echo         uses: nick-fields/retry@v3
echo         with:
echo           timeout_minutes: 40
echo           max_attempts: 3
echo           retry_on: error
echo           command: ^|
echo             export PATH="$HOME/.local/bin:$PATH"
echo             rm -rf build/ .flet/
echo             echo "=== Step 1: flet build to generate Flutter project ==="
echo             flet build apk --module-name mobile_main --project "LanTalk" --org "com.lantalk" --product "lantalk" --build-number "%BNUM%" --build-version "%APP_VER%" || echo "flet gradle phase failed, patching and rebuilding manually"
echo             test -d build/flutter/android/app || exit 1
echo             echo "=== Step 2: patch build.gradle for core library desugaring ==="
echo             python3 scripts/patch_desugaring.py
echo             echo "=== Step 3: flutter build apk ==="
echo             cd build/flutter
echo             flutter build apk --release
echo             cd ../..
echo             test -f build/flutter/build/app/outputs/flutter-apk/app-release.apk || exit 1
echo.
echo       - name: Upload APK
echo         uses: actions/upload-artifact@v4
echo         with:
echo           name: lantalk-apk
echo           path: build/flutter/build/app/outputs/flutter-apk/app-release.apk
) > .github\workflows\build-apk.yml
echo   build-apk.yml 已生成（v%APP_VER% / build %BNUM% / 带图标 / 依赖读 requirements.txt）

REM ========== 8. 初始化 Git 并提交 ==========
echo [8/9] 初始化 Git 并提交...
if not exist .git (
    git init
    git branch -M main
    echo   已初始化 Git 仓库
) else (
    echo   Git 仓库已存在
)
git add .
git commit -m "LanTalk mobile v%APP_VER% - emoji flags + dark theme fix + green UI + file drawer" >nul 2>&1
if errorlevel 1 (
    echo   没有新的改动需要提交
) else (
    echo   已提交改动
)

REM ========== 9. 推送到远程仓库 ==========
echo [9/9] 推送到 GitHub...
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
echo   版本: %APP_VER% (build %BNUM%)
echo   预计需要 8-10 分钟。
echo.
echo   查看构建状态：
echo   https://github.com/user114514-debug/lantalk-mobile/actions
echo.
echo   构建完成后：
echo   1. 点击绿色 √ 的任务
echo   2. 拉到底部 Artifacts
echo   3. 下载 lantalk-apk
echo   4. 解压得到 app-release.apk
echo   5. 传到手机安装即可
echo.
echo   正在打开 Actions 页面...
pause >nul
start https://github.com/user114514-debug/lantalk-mobile/actions

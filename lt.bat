@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   LanTalk Mobile - One-click APK build via GitHub Actions
echo ============================================================
echo.
echo Current dir: %cd%
echo.

REM ===== Get version from pyproject.toml =====
for /f "tokens=2 delims== " %%v in ('findstr /b /c:"version" pyproject.toml') do set APP_VER=%%v
set APP_VER=%APP_VER:"=%
if "%APP_VER%"=="" set APP_VER=4.0.0
REM build-number = YYYYMMDD
set BNUM=%date:~0,4%%date:~5,2%%date:~8,2%
echo App version: %APP_VER%   /   build-number: %BNUM%
echo.

REM ========== 1. Check Git ==========
echo [1/9] Checking Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git not found. Please install:
    echo https://git-scm.com/download/win
    pause
    exit /b 1
)
echo   Git installed

REM ========== 2. Check Git user config ==========
echo [2/9] Checking Git user config...
git config --global user.name >nul 2>&1
if errorlevel 1 (
    echo   No user.name, using default...
    git config --global user.name "user114514-debug"
)
git config --global user.email >nul 2>&1
if errorlevel 1 (
    echo   No user.email, using default...
    git config --global user.email "user114514@example.com"
)
echo   User config OK

REM ========== 3. Check project files ==========
echo [3/9] Checking project files...
set MISSING=0
if not exist mobile_main.py (
    echo   [MISSING] mobile_main.py
    set MISSING=1
)
if not exist pyproject.toml (
    echo   [MISSING] pyproject.toml
    set MISSING=1
)
if not exist assets\icon.png (
    echo   [MISSING] assets\icon.png (app icon)
    set MISSING=1
)
if not exist assets\fonts\Twemoji.ttf (
    echo   [MISSING] assets\fonts\Twemoji.ttf (emoji font)
    set MISSING=1
)
if not exist assets\flags\ (
    echo   [MISSING] assets\flags\ (flag PNG dir)
    set MISSING=1
)
if not exist scripts\desugaring-init.gradle (
    echo   [MISSING] scripts\desugaring-init.gradle
    set MISSING=1
)
if not exist android_perms.py (
    echo   [MISSING] android_perms.py
    set MISSING=1
)
if not exist client\ (
    echo   [MISSING] client\ (client core)
    set MISSING=1
)
if not exist wallpapers\ (
    echo   [MISSING] wallpapers\ (wallpaper dir)
    set MISSING=1
)
if not exist assets\Pixel-Unlock.ogg (
    echo   [MISSING] assets\Pixel-Unlock.ogg (voice start sound)
    set MISSING=1
)
if not exist assets\Pixel-Lock.ogg (
    echo   [MISSING] assets\Pixel-Lock.ogg (voice end sound)
    set MISSING=1
)
if "%MISSING%"=="1" (
    echo.
    echo [ERROR] Critical files missing. Check list above.
    pause
    exit /b 1
)
echo   All files present

REM ========== 4. Check/create requirements.txt ==========
echo [4/9] Checking requirements.txt...
if not exist requirements.txt (
    echo flet==0.86.5 > requirements.txt
    echo flet-audio>=0.86.0 >> requirements.txt
    echo flet-android-notifications>=0.11.0 >> requirements.txt
    echo cryptography>=42.0.0 >> requirements.txt
    echo pyjnius>=1.6.1 >> requirements.txt
    echo psutil>=7.0.0 >> requirements.txt
    echo sounddevice>=0.4.6 >> requirements.txt
    echo numpy>=1.26.0 >> requirements.txt
    echo   Created requirements.txt
) else (
    echo   requirements.txt exists
)

REM ========== 5. Generate .gitignore ==========
echo [5/9] Generating .gitignore...
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
echo   .gitignore updated

REM ========== 6. Pre-build self-check ==========
echo [6/9] Running self-check...
python -m py_compile mobile_main.py
if errorlevel 1 (
    echo   [ERROR] mobile_main.py syntax check FAILED
    pause
    exit /b 1
)
echo   mobile_main.py syntax OK
for /f %%a in ('dir /b assets\flags\*.png 2^>nul ^| find /c /v ""') do set FLAG_COUNT=%%a
echo   Flag PNG count: %FLAG_COUNT%

REM ========== 7. Generate GitHub Actions workflow ==========
echo [7/9] Generating GitHub Actions workflow...
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
echo       - name: Install Flet CLI ^(retry^)
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
echo       - name: Setup Gradle init script for desugaring
echo         run: ^|
echo           GRADLE_HOME="${GRADLE_USER_HOME:-$HOME/.gradle}"
echo           mkdir -p "$GRADLE_HOME/init.d"
echo           cp scripts/desugaring-init.gradle "$GRADLE_HOME/init.d/"
echo.
echo       - name: Build APK ^(retry + jni fix^)
echo         uses: nick-fields/retry@v3
echo         with:
echo           timeout_minutes: 40
echo           max_attempts: 3
echo           retry_on: error
echo           command: ^|
echo             export PATH="$HOME/.local/bin:$PATH"
echo             rm -rf build/ .flet/
echo             flet build apk --module-name mobile_main --project "LanTalk" --org "com.lantalk" --product "lantalk" --build-number "%BNUM%" --build-version "%APP_VER%" || true
echo             FLUTTER_DIR=$(find build -name "pubspec.yaml" -not -path "*/.dart_tool/*" | head -1 | xargs dirname 2^>/dev/null || echo "")
echo             echo "Flutter project dir: $FLUTTER_DIR"
echo             if [ -z "$FLUTTER_DIR" ]; then echo "ERROR: No Flutter project found!"; exit 1; fi
echo             cd "$FLUTTER_DIR"
echo             sed -i 's/jni: 1\.0\.0/jni: ^1.1.0/g' pubspec.yaml
echo             sed -i 's/jni: "1\.0\.0"/jni: ^1.1.0/g' pubspec.yaml
echo             sed -i 's/^version: .*/version: %APP_VER%+%BNUM%/' pubspec.yaml
echo             SP_DIR=$(find . -type d -name "site-packages" 2^>/dev/null | head -1)
echo             if [ -z "$SP_DIR" ]; then SP_DIR=$(find .dart_tool -type d -name "site-packages" 2^>/dev/null | head -1); fi
echo             echo "site-packages dir: $SP_DIR"
echo             if [ -n "$SP_DIR" ]; then export SERIOUS_PYTHON_SITE_PACKAGES="$(pwd)/$SP_DIR"; echo "Set SERIOUS_PYTHON_SITE_PACKAGES=$SERIOUS_PYTHON_SITE_PACKAGES"; fi
echo             flutter pub get
echo             flutter build apk --release --build-number %BNUM% --build-name %APP_VER%
echo             APK_PATH=$(find . -name "app-release.apk" -type f 2^>/dev/null | head -1)
echo             echo "APK found at: $APK_PATH"
echo             if [ -n "$APK_PATH" ]; then cp "$APK_PATH" "$GITHUB_WORKSPACE/app-release.apk"; ls -la "$GITHUB_WORKSPACE/app-release.apk"; else echo "ERROR: APK not found!"; find . -name "*.apk" -type f 2^>/dev/null; exit 1; fi
echo.
echo       - name: Upload APK
echo         uses: actions/upload-artifact@v4
echo         with:
echo           name: lantalk-apk
echo           path: app-release.apk
) > .github\workflows\build-apk.yml
echo   build-apk.yml generated: v%APP_VER% / build %BNUM%

REM ========== 8. Git commit ==========
echo [8/9] Committing to Git...
if not exist .git (
    git init
    git branch -M main
    echo   Git repo initialized
) else (
    echo   Git repo exists
)
git add .
git commit -m "LanTalk mobile v%APP_VER% - file receive confirm + local history + file bubble UI + wallpaper picker + voice msg + offline mode" >nul 2>&1
if errorlevel 1 (
    echo   No new changes to commit
) else (
    echo   Committed
)

REM ========== 9. Push to GitHub ==========
echo [9/9] Pushing to GitHub...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/user114514-debug/lantalk-mobile.git
git push -f -u origin main

if errorlevel 1 (
    echo.
    echo [ERROR] Push failed!
    echo Possible causes:
    echo   1. Need to login GitHub (first push will prompt)
    echo   2. Network issue
    echo.
    pause
    exit /b 1
)

REM ========== Done ==========
echo.
echo ============================================================
echo   Push successful! GitHub Actions is building APK now
echo ============================================================
echo.
echo   Version: %APP_VER% (build %BNUM%)
echo   Estimated time: 8-10 minutes
echo.
echo   Check build status:
echo   https://github.com/user114514-debug/lantalk-mobile/actions
echo.
echo   After build completes:
echo   1. Click the green checkmark
echo   2. Scroll to Artifacts
echo   3. Download lantalk-apk
echo   4. Unzip to get app-release.apk
echo   5. Install on phone
echo.
pause >nul
start https://github.com/user114514-debug/lantalk-mobile/actions

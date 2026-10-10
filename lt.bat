@echo off
chcp 936 >nul
cd /d "%~dp0"

echo ============================================================
echo   LanTalk �ƶ��� - һ����� APK �����͵� GitHub Actions
echo ============================================================
echo.
echo ��ǰĿ¼: %cd%
echo.

REM ===== �� pyproject.toml ��ȡ�汾�ţ�Ψһ����Դ�����ⲻͬ����=====
for /f "tokens=2 delims== " %%v in ('findstr /b /c:"version" pyproject.toml') do set APP_VER=%%v
set APP_VER=%APP_VER:"=%
if "%APP_VER%"=="" set APP_VER=4.0.0
REM build-number ������ YYYYMMDD��ÿ�����͵�������
set BNUM=%date:~0,4%%date:~5,2%%date:~8,2%
echo Ӧ�ð汾: %APP_VER%   /   build-number: %BNUM%
echo.

REM ========== 1. ��� Git ==========
echo [1/9] ��� Git...
git --version >nul 2>&1
if errorlevel 1 (
    echo [����] δ��⵽ Git�����Ȱ�װ��
    echo https://git-scm.com/download/win
    echo ��װ���������б��ű���
    pause
    exit /b 1
)
echo   Git �Ѱ�װ

REM ========== 2. ���� Git �û���Ϣ ==========
echo [2/9] ���� Git �û���Ϣ...
git config --global user.name >nul 2>&1
if errorlevel 1 (
    echo   δ�����û�����ʹ��Ĭ��...
    git config --global user.name "user114514-debug"
)
git config --global user.email >nul 2>&1
if errorlevel 1 (
    echo   δ�������䣬ʹ��Ĭ��...
    git config --global user.email "user114514@example.com"
)
echo   �û���Ϣ�Ѿ���

REM ========== 3. ȷ����Ŀ�ļ��� assets��ȱ���ֻ��˻����==========
echo [3/9] �����Ŀ�ļ��� assets...
set MISSING=0
if not exist mobile_main.py (
    echo   [ȱʧ] mobile_main.py
    set MISSING=1
)
if not exist pyproject.toml (
    echo   [ȱʧ] pyproject.toml��flet build �������� APK ������
    set MISSING=1
)
if not exist assets\icon.png (
    echo   [ȱʧ] assets\icon.png��Ӧ��ͼ�꣩
    set MISSING=1
)
if not exist assets\fonts\Twemoji.ttf (
    echo   [ȱʧ] assets\fonts\Twemoji.ttf��emoji/����������壩
    set MISSING=1
)
if not exist assets\flags\ (
    echo   [ȱʧ] assets\flags\������ PNG Ŀ¼��
    set MISSING=1
)
if not exist scripts\desugaring-init.gradle (
    echo   [ȱʧ] scripts\desugaring-init.gradle (Gradle desugaring, ȱʧ�ṹ��ʧ��)
    set MISSING=1
)
if not exist android_perms.py (
    echo   [ȱʧ] android_perms.py (��׿�ԴʱȨ��ģ��)
    set MISSING=1
)
if not exist client\ (
    echo   [ȱʧ] client\ (�ͻ��˺��Ĵ�����)
    set MISSING=1
)
if not exist wallpapers\ (
    echo   [ȱʧ] wallpapers\ (����Ŀ¼)
    set MISSING=1
)
if "%MISSING%"=="1" (
    echo.
    echo [����] �ؼ��ļ�ȱʧ���鿴������б��ɾ�����ٿ�͡�
    pause
    exit /b 1
)
echo   mobile_main.py / pyproject.toml / icon.png / Twemoji.ttf / flags/ / desugaring-init.gradle / android_perms.py / client/ / wallpapers/ ȫ������

REM ========== 4. ����/��ȫ requirements.txt�������ֻ���������==========
echo [4/9] ��� requirements.txt...
if not exist requirements.txt (
    echo flet==0.86.5 > requirements.txt
    echo flet-audio>=0.86.0 >> requirements.txt
    echo flet-android-notifications>=0.11.0 >> requirements.txt
    echo cryptography>=42.0.0 >> requirements.txt
    echo pyjnius>=1.6.1 >> requirements.txt
    echo psutil>=7.0.0 >> requirements.txt
    echo # �������ר�ã������ APK����׿��ԭ�� MediaRecorder/pyjnius����>> requirements.txt
    echo sounddevice>=0.4.6 >> requirements.txt
    echo numpy>=1.26.0 >> requirements.txt
    echo   ������ requirements.txt������������
) else (
    echo   requirements.txt: ����
)

REM ========== 5. ���� .gitignore ==========
echo [5/9] ���� .gitignore...
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
echo   .gitignore �Ѹ��£��ų�������־�����ԡ���ʱ���Խű���voice_tmp��

REM ========== 6. ����ǰ�Լ죨�﷨ + ����������==========
echo [6/9] ����ǰ�Լ�...
python -m py_compile mobile_main.py
if errorlevel 1 (
    echo   [����] mobile_main.py �﷨���ʧ�ܣ��޸��������͡�
    pause
    exit /b 1
)
echo   mobile_main.py �﷨���ͨ��
for /f %%a in ('dir /b assets\flags\*.png 2^>nul ^| find /c /v ""') do set FLAG_COUNT=%%a
echo   ���� PNG ����: %FLAG_COUNT%��ӦΪ 259��

REM ========== 7. ���� GitHub Actions workflow ==========
echo [7/9] ���� GitHub Actions ����...
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
echo       - name: Setup Gradle init script for desugaring
echo         run: ^|
echo           GRADLE_HOME="${GRADLE_USER_HOME:-$HOME/.gradle}"
echo           mkdir -p "$GRADLE_HOME/init.d"
echo           cp scripts/desugaring-init.gradle "$GRADLE_HOME/init.d/"
echo           echo "Installed init scripts:"
echo           ls -la "$GRADLE_HOME/init.d/"
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
echo             flet build apk --module-name mobile_main --project "LanTalk" --org "com.lantalk" --product "lantalk" --build-number "%BNUM%" --build-version "%APP_VER%" || true
echo             # Fix jni version mismatch: jni_flutter 1.0.4+1 needs jni ^1.1.0
echo             cd build/flutter
echo             printf '\ndependency_overrides:\n  jni: ^1.1.0\n' >> pubspec.yaml
echo             flutter pub get
echo             flutter build apk --release
echo             cd ../..
echo.
echo       - name: Upload APK
echo         uses: actions/upload-artifact@v4
echo         with:
echo           name: lantalk-apk
echo           path: build/flutter/build/app/outputs/flutter-apk/app-release.apk
) > .github\workflows\build-apk.yml
echo   build-apk.yml �����ɣ�v%APP_VER% / build %BNUM% / ��ͼ�� / ������ requirements.txt��

REM ========== 8. ��ʼ�� Git ���ύ ==========
echo [8/9] ��ʼ�� Git ���ύ...
if not exist .git (
    git init
    git branch -M main
    echo   �ѳ�ʼ�� Git �ֿ�
) else (
    echo   Git �ֿ��Ѵ���
)
git add .
git commit -m "LanTalk mobile v%APP_VER% - wallpaper picker + voice msg + offline mode + i18n + safe area" >nul 2>&1
if errorlevel 1 (
    echo   û���µĸĶ���Ҫ�ύ
) else (
    echo   ���ύ�Ķ�
)

REM ========== 9. ���͵�Զ�ֿ̲� ==========
echo [9/9] ���͵� GitHub...
git remote remove origin >nul 2>&1
git remote add origin https://github.com/user114514-debug/lantalk-mobile.git
git push -f -u origin main

if errorlevel 1 (
    echo.
    echo [����] ����ʧ�ܣ�
    echo ����ԭ��
    echo   1. ��Ҫ��¼ GitHub���״����ͻᵯ����¼���ڣ�
    echo   2. ��������
    echo.
    pause
    exit /b 1
)

REM ========== ��� ==========
echo.
echo ============================================================
echo   ���ͳɹ���GitHub Actions ���ڹ��� APK
echo ============================================================
echo.
echo   �汾: %APP_VER% (build %BNUM%)
echo   Ԥ����Ҫ 8-10 ���ӡ�
echo.
echo   �鿴����״̬��
echo   https://github.com/user114514-debug/lantalk-mobile/actions
echo.
echo   ������ɺ�
echo   1. �����ɫ �� ������
echo   2. �����ײ� Artifacts
echo   3. ���� lantalk-apk
echo   4. ��ѹ�õ� app-release.apk
echo   5. �����ֻ���װ����
echo.
echo   ���ڴ� Actions ҳ��...
pause >nul
start https://github.com/user114514-debug/lantalk-mobile/actions

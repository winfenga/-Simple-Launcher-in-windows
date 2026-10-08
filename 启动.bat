@echo off
chcp 936 >nul 2>&1
title 我的启动器
cd /d "%~dp0"

echo ================================
echo   我的启动器 - 一键启动
echo ================================
echo.

rem ---------- 1. 检查 Python ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [x] 没有检测到 Python，请先安装 Python 3.8 及以上版本
    echo     下载地址: https://www.python.org/downloads/
    echo     安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

rem ---------- 2. 检查依赖 ----------
echo [1/2] 检查运行库（首次运行会自动安装，约十几秒）...
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q --disable-pip-version-check
if errorlevel 1 (
    echo       [提示] 有可选库没装上，程序会自动降级，不影响启动
)

rem 重新声明码页：python 跑过之后批处理的中文才不会被读乱
chcp 936 >nul 2>&1
echo.

rem ---------- 3. 无窗口启动，本窗口随即关闭 ----------
echo [2/2] 正在启动，本窗口会自动关闭...
where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" pythonw "launcher.py"
    exit /b 0
)

if exist "启动-无窗口.vbs" (
    start "" wscript.exe //nologo "启动-无窗口.vbs"
    exit /b 0
)

start "" python "launcher.py"
exit /b 0

@echo off
chcp 936 >nul 2>&1
title 打包为独立 exe
cd /d "%~dp0"

rem 有些环境会残留 TK_LIBRARY / TCL_LIBRARY 指向已删除的临时目录，
rem 会让 PyInstaller 找不到 Tk 数据目录而打包失败，这里先清掉
set "TK_LIBRARY="
set "TCL_LIBRARY="

echo ================================
echo   把启动器打包成独立 exe
echo ================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [x] 没有检测到 Python，请先安装 Python 3.8 及以上版本
    echo     安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

echo [1/4] 安装运行库和打包工具（首次会慢一点）...
python -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple -q --disable-pip-version-check
if errorlevel 1 (
    echo [x] 依赖安装失败，请检查网络后重试
    echo.
    pause
    exit /b 1
)

rem 重新声明码页：python 跑过之后批处理的中文才不会被读乱
chcp 936 >nul 2>&1

echo [2/4] 检测拖拽支持库 ...
set "DNDDIR="
for /f "delims=" %%i in ('python -c "import tkinterdnd2,os;print(os.path.dirname(tkinterdnd2.__file__))" 2^>nul') do set "DNDDIR=%%i"

set "ICONARG="
if exist "icons\_app.ico" set "ICONARG=--icon icons\_app.ico"

echo [3/4] 开始打包，需要一两分钟，请稍等 ...
rem 内部名刻意用纯英文 Launcher：命令行走 ASCII，
rem 就不会因为 cmd 码页问题把中文文件名写坏；打完包再由 Python 改成中文名
if defined DNDDIR (
    echo       已启用拖拽支持
    python -m PyInstaller --noconfirm --clean --onefile --windowed %ICONARG% --name Launcher --add-data "%DNDDIR%;tkinterdnd2" launcher.py
) else (
    echo       未检测到 tkinterdnd2，将不带拖拽功能
    python -m PyInstaller --noconfirm --clean --onefile --windowed %ICONARG% --name Launcher launcher.py
)

chcp 936 >nul 2>&1
echo [4/4] 整理输出目录 ...
python -c "import os,shutil;d='dist';a=os.path.join(d,'Launcher.exe');b=os.path.join(d,'\u542f\u52a8\u5668.exe');os.path.exists(a) and shutil.move(a,b);m='\u4f7f\u7528\u8bf4\u660e.md';os.path.exists(m) and shutil.copyfile(m,os.path.join(d,m));print('OK' if os.path.exists(b) else 'MISSING')"

echo.
if exist "dist\*.exe" (
    echo      [完成] dist 目录里就是打包好的启动器
    echo      双击即可使用，不需要装 Python，配置和图标保存在 exe 同目录
    echo.
    pause
    explorer "%~dp0dist"
) else (
    echo [失败] 打包未成功，请查看上方错误信息
    echo.
    pause
    exit /b 1
)
exit /b 0

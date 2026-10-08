#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
=========================================================
  Launcher - 本地可视化启动器  v1.2.1
=========================================================
把 BAT / EXE / Python / 文件夹 / 网址 拖进来，
起个中文名、配个图标，点一下就能启动。

v1.2.1 改了些什么
---------------------------------------------------------
1. 【移除】全局快捷键功能整个拆掉了。
   原因：打包成 exe 之后它不可靠 —— 热键确实注册上了（别的进程再注册同一个
   组合会被系统拒绝，错误码 1409），但真按下去窗口没反应；而用同样方式注入
   的按键，一个对照小程序却能正常收到 WM_HOTKEY。与其留一个「按了不一定有
   反应」的功能，不如拿掉：现在靠「托盘图标」或「再双击一次 exe」把窗口唤出来，
   这两条路都验证过是稳的。
   （每个启动项的「专属快捷键」用的是同一套机制，也一并移除。
     想找回这段代码的话，见 git 历史 / backup_v1.2_* 备份。）
2. 【修复】添加 / 编辑窗口根本打不开：那一行把 self.item 写成了 item，
   直接 NameError —— 等于加不了也改不了任何启动项。
3. 【修复】对话框建到一半崩了会留下一个攥着输入焦点、关不掉的空窗口，
   整个界面看着像卡死。现在失败会自动清掉并松开焦点。
4. 【修复】唤出窗口时用 AttachThreadInput（抢不到再补一次 ALT 轻敲）真正
   抢到最前面；以前后台进程直接调 SetForegroundWindow 会被系统拦掉。
5. 顺手记一笔踩过的坑：tkinter 的 self._w 是窗口路径名，拿它当「宽度」存
   会报 TclError: invalid command name "460"。

v1.2 新增
---------------------------------------------------------
1. 添加向导：添加 / 编辑启动项时不再是一片空表单
   - 分四步走的字段说明，每一项都告诉你「填什么、能不能留空」
   - 根据文件类型自动给出针对性提示（bat 要工作目录、py 要不要黑窗…）
   - 「✨ 推荐设置」一键填好，「▶ 试运行」先试试再保存
   - 首次运行弹出使用引导，菜单里随时能再看
3. 界面重做：圆角卡片 / 圆角按钮 / 主题色板 / 分组徽章 / 悬浮反馈
   - 卡片和列表全部用 Canvas 自绘，深色浅色两套配色都调过
   - 搜索框、空状态、状态栏、滚动条细节统一

v1.1 主要修复
---------------------------------------------------------
1. 命令行窗口乱码：启动脚本改为 GBK 编码，不再中途切码页
2. 命令行窗口不关闭：启动脚本用 pythonw / VBS 无窗口拉起，自己立即退出
3. 命令行窗口与程序绑定：启动项全部脱离启动器的控制台
   - .py 默认用 pythonw.exe 启动（不弹黑窗）
   - .bat/.cmd/.ps1 用独立的新控制台，脚本跑完窗口自动关闭
   - 每个启动项可单独选择「自动 / 显示控制台 / 隐藏控制台」
   - 子进程不再继承启动器的标准输入输出（不会写回启动器控制台）
4. 新增「工作目录」「启动参数」两个字段（BAT 找不到文件的问题有解了）
5. UI 优化：高 DPI 适配、任务栏图标、卡片/列表美化、搜索框占位符修正、
   图标缓存、窗口位置越界修正、单实例运行、崩溃日志

依赖：
    pip install pillow pywin32         (必需，图标与系统功能)
    pip install tkinterdnd2            (可选，启用拖拽)
    pip install pystray                (可选，启用托盘)
=========================================================
"""

import os
import sys
import json
import time
import queue
import socket
import base64
import zlib
import shutil
import ctypes
import datetime
import webbrowser
import subprocess
import threading
import hashlib
import traceback
from ctypes import wintypes

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, simpledialog

# 有些程序（尤其是别的 PyInstaller 打包程序的子进程）会往环境里塞
# TCL_LIBRARY / TK_LIBRARY。一旦它们指向已经被删掉的临时目录，
# Tk 初始化就会失败并弹一个 "Error" 对话框，所以在导入 tkinter 前先清掉无效值。
for _env_name in ("TCL_LIBRARY", "TK_LIBRARY"):
    _env_val = os.environ.get(_env_name)
    if _env_val and not os.path.isdir(_env_val):
        os.environ.pop(_env_name, None)

from PIL import Image, ImageTk, ImageDraw

# ---------------------------------------------------------
# 可选依赖（缺失时自动降级，不影响主功能）
# ---------------------------------------------------------
try:
    import win32gui
    import win32ui
    import win32con
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False

try:
    import winreg
    HAS_WINREG = True
except Exception:
    HAS_WINREG = False

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:
    HAS_DND = False

try:
    import pystray
    from pystray import MenuItem as TrayItem
    HAS_PYSTRAY = True
except Exception:
    HAS_PYSTRAY = False

# PIL 版本兼容
try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:
    RESAMPLE = Image.LANCZOS

# ---------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ICON_DIR = os.path.join(BASE_DIR, "icons")
CONFIG_FILE = os.path.join(BASE_DIR, "launcher_config.json")
BACKUP_FILE = os.path.join(BASE_DIR, "launcher_config.bak.json")
ERROR_LOG = os.path.join(BASE_DIR, "launcher_error.log")

APP_NAME = "启动器"
APP_VERSION = "1.2.1"

os.makedirs(ICON_DIR, exist_ok=True)

# 主题色板：两套配色都按「深底浅卡 / 浅底白卡」的思路调过，
# 键名保持向后兼容（bg/card/hover/border/text/sub/accent/...）
THEMES = {
    "dark": {
        "bg": "#12121c", "card": "#1e1e2e", "hover": "#2b2b40",
        "card2": "#191926", "border": "#2e2e44", "border_hi": "#46466a",
        "text": "#e6e8f2", "sub": "#9aa1bb",
        "accent": "#7aa2f7", "accent_hi": "#93b4ff", "accent2": "#bb9af7",
        "accent_fg": "#0d0d16",
        "entry": "#191926", "sel": "#3b3b57",
        "ok": "#9ece6a", "err": "#f7768e", "warn": "#e0af68",
        "shadow": "#0b0b12",
    },
    "light": {
        "bg": "#f3f4fa", "card": "#ffffff", "hover": "#eaeefb",
        "card2": "#f8f9fe", "border": "#e3e6f2", "border_hi": "#c6cde4",
        "text": "#262b3a", "sub": "#6b7285",
        "accent": "#4263eb", "accent_hi": "#5c7cfa", "accent2": "#7048e8",
        "accent_fg": "#ffffff",
        "entry": "#f7f8fd", "sel": "#dbe2f8",
        "ok": "#2f9e44", "err": "#e03131", "warn": "#e8590c",
        "shadow": "#d7dbe8",
    },
}

FONT_UI = "Microsoft YaHei"
FONT_EMOJI = "Segoe UI Emoji"


EXT_ICON = {
    ".exe": "🗔", ".bat": "📜", ".cmd": "📜", ".py": "🐍",
    ".pyw": "🐍", ".lnk": "🔗", ".msc": "⚙️", ".ps1": "💠",
    ".jar": "☕", ".reg": "📋", ".vbs": "📄", ".js": "📄",
    ".iso": "💿", ".ahk": "⌨", ".com": "🗔", ".url": "🌐",
}

SUPPORTED_EXT = (
    ".exe", ".bat", ".cmd", ".py", ".pyw", ".lnk", ".msc", ".ps1",
    ".jar", ".reg", ".vbs", ".js", ".wsf", ".ahk", ".com",
)

# 控制台窗口策略
CONSOLE_AUTO = "auto"
CONSOLE_SHOW = "show"
CONSOLE_HIDE = "hide"
CONSOLE_CHOICES = [
    ("自动（推荐）", CONSOLE_AUTO),
    ("始终显示控制台窗口", CONSOLE_SHOW),
    ("始终隐藏控制台窗口", CONSOLE_HIDE),
]
CONSOLE_LABEL = {key: label for label, key in CONSOLE_CHOICES}
CONSOLE_KEY = {label: key for label, key in CONSOLE_CHOICES}

# Windows 进程创建标志
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
IS_WIN = os.name == "nt"

# 图片缓存：{(path, size, mtime, filesize): PhotoImage}
_IMAGE_CACHE = {}

# 分组分隔符：分组字段里写 "开发/前端" 就是两级分组
GROUP_SEP = "/"

# 新建文本文件时的模板
BAT_TEMPLATE = (
    "@echo off\r\n"
    "chcp 936 >nul\r\n"
    "\r\n"
    "rem ===== 在这里写你要执行的命令 =====\r\n"
    "echo Hello from %~n0\r\n"
    "\r\n"
    "rem 下面这行让窗口停住方便看结果，不需要可以删掉\r\n"
    "pause\r\n"
)

# 拼音首字母表（GB2312 区位顺序，zlib + base85）；构建期由 _gen_pinyin.py 生成
# GB2312 区位的汉字首字母表（zlib + base85，约 6763 字）
_PY_B85 = (
    "c-rMxYp>-f4*adb1duUY2uRre^;2ohO8a3(JKfXIZA<r_oRjdf%T<-@`ri@0dyKC#kp6A>=MP!*10w#SasOqc?-b*&e$1a6C"
    "4a9dzhdw=jmrNgQESch8^nK4D!*y8pELT`j*mZM{IHncBKY48&;Pl2zC70N67(g5_D>w=2Zer4Ed7~+{=qZ$o5q(P-2Yk7r~"
    "iNRboXC1=&MY^C-*fn<$h-yVI!+ity<pBz42^eWnW~x%+km;c(!PV;ev$Mse@-RK2LO6xji=Q9HQ#I8s^wN^KG@dZNhY9G6j"
    "#!G@s#1PT09}GUem4LnnQG+t#@Enr;+TOQsq|NeP^2Avcu~Oe7T1)w~&(^}#)t=+mlXe9hB~&RlQzV_;umwcGTHK3ZHD#6H<"
    "@a!zM*h#oWp1~g8oT4rr0;g#3oX7`H2iDgY=8zVJixg;uWu7a(AWY}h;6~YX6T5RQ<>~-LnAxjjZ-P>g`*0p*zT3N;XveCz0"
    "u||`Ut(lMxLw@4qGp8nGn&sx<j}lG%w1QV_c}QsdOiI!*YPL1^6Nq>5J=3GJXk#W8)y!=buVEd#G5^-IEUqkAW$NNS!=h~^q"
    "#aS($j5Sdr#Mt+)jNzWL)q8U?r;iA;3a0OMHLb}Aw~IdZV79aV`qi3A1o_nmegBDDWa((5jn56DUFNSO?cbYIR_pxb99?_X{"
    "fet;^iG{cySe{T!!MZ+l~t-tWg|EITqy(Ay4y~k>4J>t;(xJF%1}`HfrS7yyw<P^6};BoV+)2--VEc5JeW=?z>br3`R<%ew2"
    "BkARSNStCx6Pt85xVV_I0T4VFRtF@+-DzHE($N<ZACAT1MR_p!<IIn7i0WUc@>0!pzRjo6Ig)3GqH1jIjffK3u_Yq3iN+Gvm"
    "(KuyJ*=bH1%t(%VY06T$kK*M0^i&`P9)1BW!7b?pfc0nH_B*Ackrf)LG$wPHtXqt-&xYI~<{DV#<AQnd*(Yz-z+eTF@4mxYK"
    "{UVSK6|#DZ11ZyZ5=q-w>XcSDG6y8-DfgUZ&oT!#yeL^~v1I_}NM0hgwlvYPOJn?m`WI&{TN&_q){)~(L0fTP^6k!?kbYTe4"
    "K>xldVun}nhkk&37Yn3#Q?#AyaP>|KUpP`C6VMAp`i1DlM-5v&8W|LWTIdM!g9NrV;MoF?paU4G*hp<MTNC3;j+Ct-yorvt5"
    "QI}COa0`GjJ9iiFokXAQ=Z6J~s#KpZaC{<Tftcu!(=JNN-y?r?*JKS90r73ou{7+UF8r8mxrE<x_2d6_O{4wQ$xgGp!t#%;Q"
    "yeD=TDg><^lk!)VeL8Uzq4ZcGkbod7nL7)Rqf14GD5k!iD3Lm9b2Vf(eY7!f*>rUX9+TMm<&jAjoL?xgWbAx;NSnQxaIk<=>"
    "oxaYY+tR=JXr70&6gHVdoK?WJ@;j07N3@HG_1Sf0eX)lGUtnj#Ex3nEpH#aVk9|oyPAiElY)n!Bu_S{eus5CliIUeeVoJ0=K"
    "_f<Y-_74fc$H<b)&aR+-|5RiG@=GXL>(#d6v|&}KJx0jnn6RH{`JDy`I~lW$3P#NV6v43PGUkX%(V%*0@`#IU=PCSPm2Okwt"
    "8|#pr*n>0R~g<mxZ!lR1ifVH?c5N_EMbGZ(ADy(VF852PzPY4fYNP1d^#B8L<op0#1{R8&Etol0o?+TwFFh60O^y$1+u3OPW"
    "1-l?1>8Mvv=guyZM}L&)7h>7D4?v7M#LEN|%W5C_8D>>9OZC!Fv=rD)bxV60zqND#%J(jgUo<y8{l4E7*Z&)mGa;`ST8QCtq"
    "hLFfJMqZ44kVyaE&(Tthu<4l`9WDDYfI2-d*bOQGijsk>igg9KXj7$FaDBhkJdkzIkZ8#M-<Oul9hu~slYZ%5bPw918M!13O"
    "kaR|FXAHZV3XapV4h!!BU7kTivf0I;b((35}T6IMD62oz$Oj@@?dV^%secBbM+|&;4zqo~LOYV_DBbF<O=mjY59Ma_}A7od+"
    "s{jFV#wB1Epg(-X5hsc~JwT_by`!i<+<<3b83O|C06>L+rODxW#>*|xw^d|+2v{b@C_UF}BQt2xT5f37D$gi*Nhg?$G#)K4?"
    "C_<$-3Se`$sKI8D|}2_C=R+2lB0$i+1MbxMo&+*LiJxO(&p75K}vfp(Ee6+Uu_h8gGx4uGZ`q@Unc<513r(-LBuF5%$FLh*v"
    "jKQ==5jq@SfEx2qB?~VG1Nd%VT&onzVPQYyb)~Bv;pIi>S>GO$$Ih%$>FXLxSFR^Z@BIIfD<>6OUpIpthLMtnzwuUNO66+S;"
    "%Og7OULN7*nWP!WZWPbDCO)AY0i8%9!s!bS?RN7$5er1x(;eM~?n(aTzJ1+ghoz;L%2&5caT*|@#nNWcr|<Ci|0ZC{vdf;Nx"
    "0OOOq?!!9d<gBTLPuj>Icc3y=+TdBfIcjPa{dqRoU1@Z*hDGGpvV2k2}OMBpfc16d+Q9~9&Y;d)(UuqjRx3&lFQ{hgFG^i&#"
    "O8AN#NM*iwzFuL1;qyNLhazBf"
)

_PINYIN_MAP = None


def pinyin_map():
    """汉字 -> 拼音首字母（首次调用时解压，约 6763 字）"""
    global _PINYIN_MAP
    if _PINYIN_MAP is not None:
        return _PINYIN_MAP
    table = {}
    try:
        raw = zlib.decompress(base64.b85decode(_PY_B85)).decode("ascii")
        idx = 0
        for area in range(16, 88):
            for pos in range(1, 95):
                try:
                    ch = bytes([area + 0xA0, pos + 0xA0]).decode("gb2312")
                except Exception:
                    continue
                if idx < len(raw):
                    table[ch] = raw[idx]
                idx += 1
    except Exception:
        table = {}
    _PINYIN_MAP = table
    return table


def name_initials(text):
    """取名字的拼音首字母：'记事本' -> 'jsb'；ASCII 字符原样保留"""
    table = pinyin_map()
    out = []
    for ch in str(text):
        if "\u4e00" <= ch <= "\u9fff":
            out.append(table.get(ch, ""))
        elif ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


def match_query(item, query):
    """搜索匹配：名称 / 路径包含关键词，或者名称拼音首字母以关键词开头"""
    q = str(query or "").lower()
    if not q:
        return True
    name = str(item.get("name", "")).lower()
    path = str(item.get("path", "")).lower()
    if q in name or q in path:
        return True
    if q.isascii():
        ini = name_initials(name)
        if ini and (ini.startswith(q) or q in ini):
            return True
    return False


def split_group(path):
    return [p for p in str(path or "").replace("\\", GROUP_SEP).split(GROUP_SEP) if p]


def group_in(path, prefix):
    """按层级前缀匹配分组：筛选 '开发' 时 '开发/前端' 也算命中"""
    if not prefix:
        return True
    p = split_group(path)
    q = split_group(prefix)
    return p[:len(q)] == q


def all_groups(items):
    """所有分组（含上级），按层级排序"""
    seen = set()
    for it in items:
        parts = split_group(it.get("group", ""))
        for i in range(1, len(parts) + 1):
            seen.add(GROUP_SEP.join(parts[:i]))
    return sorted(seen)


def system_theme():
    """读 Windows 的「应用模式」，返回 'dark' / 'light'"""
    if not HAS_WINREG:
        return "dark"
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        try:
            val, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return "light" if int(val) else "dark"
    except Exception:
        return "dark"


# ---------------------------------------------------------
# 资源管理器右键菜单：添加到启动器
# ---------------------------------------------------------
SHELL_MENU_NAME = "MyLauncherAdd"
SHELL_MENU_LABEL = "添加到启动器"
SHELL_MENU_TARGETS = (("*", "%1"), ("Directory", "%1"),
                      ("Directory\\Background", "%V"))


def app_command_prefix():
    """重开自己需要的命令行前缀（打包成 exe 和跑源码不一样）"""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    pyw = find_python(console=False) or sys.executable
    return '"%s" "%s"' % (pyw, os.path.abspath(__file__))


def _delete_reg_tree(root, path):
    try:
        key = winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return
    except Exception:
        return
    try:
        while True:
            try:
                sub = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_reg_tree(root, path + "\\" + sub)
    finally:
        winreg.CloseKey(key)
    try:
        winreg.DeleteKey(root, path)
    except Exception:
        pass


def shell_menu_registered():
    if not HAS_WINREG:
        return False
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                       r"Software\Classes\*\shell\%s" % SHELL_MENU_NAME,
                       0, winreg.KEY_READ)
        return True
    except Exception:
        return False


def set_shell_menu(enable):
    """注册 / 注销资源管理器右键菜单项，返回 (是否成功, 信息)"""
    if not HAS_WINREG:
        return False, "当前环境不支持注册表操作"
    try:
        for target, placeholder in SHELL_MENU_TARGETS:
            base = r"Software\Classes\%s\shell\%s" % (target, SHELL_MENU_NAME)
            if enable:
                key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, base, 0,
                                         winreg.KEY_SET_VALUE)
                try:
                    winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ,
                                      SHELL_MENU_LABEL)
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ,
                                      os.path.join(ICON_DIR, "_app.ico"))
                finally:
                    winreg.CloseKey(key)
                cmd_key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                             base + r"\command", 0,
                                             winreg.KEY_SET_VALUE)
                try:
                    winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ,
                                      '%s --add "%s"' % (app_command_prefix(),
                                                         placeholder))
                finally:
                    winreg.CloseKey(cmd_key)
            else:
                _delete_reg_tree(winreg.HKEY_CURRENT_USER, base)
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------
# 单实例：第二个进程不再干瞪眼，而是把已有窗口叫出来
#
# 用的是本地回环 socket，而不是窗口消息：
# Tk 的主循环在 Tcl 里会释放 GIL，此时若有 ctypes 窗口回调进 Python，
# 解释器线程状态会被弄乱（Python 3.14 + Tk 9 下直接崩）。
# socket + 后台线程 + 一个 Event，由主线程轮询，最稳。
# ---------------------------------------------------------
IPC_PORT = 52117
IPC_BANNER = b"VISUALLAUNCHER1\n"
IPC_TOKEN = b"SHOW\n"


class WakeServer(object):
    """监听 127.0.0.1 上的一个端口，接收「唤出 / 顺便把某个文件加进来」的请求"""

    def __init__(self, sink):
        self.sink = sink
        self.sock = None
        self._thread = None
        self._closed = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 不要设 SO_REUSEADDR：Windows 上它会让两个实例同时绑上同一个端口
            s.bind(("127.0.0.1", IPC_PORT))
            s.listen(5)
            s.settimeout(0.5)
            self.sock = s
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()
        except Exception:
            # 端口被别的程序占了也不影响主功能，只是第二实例唤不出来
            self.sock = None

    def _serve(self):
        while not self._closed:
            try:
                conn, _addr = self.sock.accept()
            except socket.timeout:
                continue
            except Exception:
                break
            try:
                conn.sendall(IPC_BANNER)      # 先自报家门，客户端要核对
                conn.settimeout(0.6)
                try:
                    data = conn.recv(4096)
                except Exception:
                    data = b""
                self.sink.put(data.decode("utf-8", "ignore").strip() or "SHOW")
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self):
        self._closed = True
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


def send_to_running(command="SHOW", timeout=1.6):
    """叫醒已经在运行的启动器；返回是否成功。

    command: "SHOW" 或 "ADD\\n<路径>"
    """
    payload = str(command or "SHOW").encode("utf-8")
    deadline = time.time() + float(timeout)
    while True:
        try:
            conn = socket.create_connection(("127.0.0.1", IPC_PORT), 0.35)
        except Exception:
            conn = None
        if conn is not None:
            try:
                conn.settimeout(0.6)
                banner = conn.recv(32)
                if banner.strip() == IPC_BANNER.strip():
                    conn.sendall(payload)
                    return True
                return False        # 端口上是别的程序，别乱发
            except Exception:
                return False
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        if time.time() >= deadline:
            return False
        time.sleep(0.15)


def wake_running_instance(timeout=1.6):
    return send_to_running("SHOW", timeout)


# ---------------------------------------------------------
# 工具函数
# ---------------------------------------------------------
def human_key(text):
    """给项目生成稳定 id"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def icon_path_for(key):
    return os.path.join(ICON_DIR, "%s.png" % key)


def is_url(path):
    return str(path).lower().startswith(("http://", "https://"))


def _write_error_log(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write("\n===== %s =====\n%s\n"
                     % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def _msgbox(title, text, flags=0x10):
    """不依赖 tkinter 的消息框（用于启动早期崩溃）"""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), flags)
    except Exception:
        pass


def enable_dpi_awareness():
    """高 DPI 适配：让界面在缩放屏幕上不再模糊（必须在创建窗口前调用）"""
    if not IS_WIN:
        return
    try:
        # PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_MUTEX = None


# Windows: GetAncestor / ShowWindow / keybd_event 用的常量
GA_ROOT = 2
SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def _top_level_hwnd(widget):
    """拿到 Tk 窗口对应的真正顶层窗口句柄"""
    hwnd = int(widget.winfo_id())
    try:
        root = ctypes.windll.user32.GetAncestor(hwnd, GA_ROOT)
        if root:
            return int(root)
    except Exception:
        pass
    return hwnd


def _set_foreground(hwnd):
    """AttachThreadInput + SetForegroundWindow，返回是否真的抢到了前台"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(user32.AttachThreadInput(tid_fg, tid_me, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(tid_fg, tid_me, False)
    return int(user32.GetForegroundWindow() or 0) == int(hwnd)


def force_foreground(widget):
    """把窗口真正抢到最前面并拿到焦点。

    只靠 Tk 的 focus_force() / lift() 是不够的：Windows 只允许「当前前台进程」
    或「刚收到输入事件的进程」调用 SetForegroundWindow，别的进程调用只会让任务栏
    闪一下。于是再双击一次启动器图标时，窗口可能只是「显示出来了」却没到最前面。
    办法：先把本线程挂到前台线程上（AttachThreadInput）再抢；还被拦的话，
    就自己模拟一次 ALT 轻敲 —— 系统会把「最后一个输入事件」算到我们头上，
    这一步之后 SetForegroundWindow 就能成功（AutoHotkey / PowerToys 用的也是这招）。
    """
    if not IS_WIN:
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = _top_level_hwnd(widget)
        if not hwnd:
            return False
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)     # 最小化过就先还原
        except Exception:
            pass
        if _set_foreground(hwnd):
            return True
        try:
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        except Exception:
            pass
        return _set_foreground(hwnd)
    except Exception:
        return False


def acquire_single_instance():
    """单实例运行，避免两个启动器同时改配置文件"""
    global _MUTEX
    if not IS_WIN:
        return True
    try:
        # 必须用 use_last_error=True，否则 ctypes 之间的小动作会冲掉错误码
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _MUTEX = kernel32.CreateMutexW(None, False, "Local\\VisualLauncher_SingleInstance_v1")
        if not _MUTEX:
            return True
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def hicon_to_png(hicon, save_to, size=64):
    """把 Windows HICON 转成 PNG"""
    if not HAS_WIN32:
        return None
    mem_dc = None
    hdc = None
    screen_dc = None
    try:
        screen_dc = win32gui.GetDC(0)
        hdc = win32ui.CreateDCFromHandle(screen_dc)
        hbmp = win32ui.CreateBitmap()
        hbmp.CreateCompatibleBitmap(hdc, size, size)
        mem_dc = hdc.CreateCompatibleDC()
        mem_dc.SelectObject(hbmp)
        win32gui.DrawIconEx(mem_dc.GetSafeHdc(), 0, 0, hicon,
                            size, size, 0, None, win32con.DI_NORMAL)
        info = hbmp.GetInfo()
        bits = hbmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGBA", (info["bmWidth"], info["bmHeight"]),
            bits, "raw", "BGRA", 0, 1
        )
        img.save(save_to, "PNG")
        return save_to
    except Exception:
        return None
    finally:
        try:
            win32gui.DestroyIcon(hicon)
        except Exception:
            pass
        for dc in (mem_dc, hdc):
            try:
                if dc is not None:
                    dc.DeleteDC()
            except Exception:
                pass
        try:
            if screen_dc is not None:
                win32gui.ReleaseDC(0, screen_dc)
        except Exception:
            pass


def extract_system_icon(path, save_to, size=64):
    """提取文件 / 文件夹的系统关联图标"""
    if not HAS_WIN32 or not path or is_url(path):
        return None
    if not os.path.exists(path):
        return None
    try:
        if os.path.isdir(path):
            res = win32gui.SHGetFileInfo(
                path, win32con.FILE_ATTRIBUTE_DIRECTORY,
                win32con.SHGFI_ICON | win32con.SHGFI_LARGEICON
            )
        else:
            res = win32gui.SHGetFileInfo(
                path, 0, win32con.SHGFI_ICON | win32con.SHGFI_LARGEICON
            )
        hicon = res[0] if res else None
        if not hicon:
            return None
        return hicon_to_png(hicon, save_to, size)
    except Exception:
        return None


def import_custom_icon(src, save_to, size=64):
    """把用户选的图片 / ICO 统一转成 PNG"""
    try:
        img = Image.open(src)
        img = img.convert("RGBA")
        img.thumbnail((size, size), RESAMPLE)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
        canvas.save(save_to, "PNG")
        return save_to
    except Exception:
        return None


def load_image(path, size):
    """加载并缩放图片（带缓存），失败返回 None"""
    if not path:
        return None
    try:
        st = os.stat(path)
        key = (path, int(size), st.st_mtime, st.st_size)
    except OSError:
        return None
    photo = _IMAGE_CACHE.get(key)
    if photo is not None:
        return photo
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((size, size), RESAMPLE)
        photo = ImageTk.PhotoImage(img)
    except Exception:
        return None
    if len(_IMAGE_CACHE) > 400:
        _IMAGE_CACHE.clear()
    _IMAGE_CACHE[key] = photo
    return photo


def make_app_icon(png_path=None, ico_path=None):
    """生成程序图标（渐变小方块 + 播放三角）"""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / float(size - 1)
        color = (int(96 + (137 - 96) * t),
                 int(165 + (180 - 165) * t),
                 int(250 - (250 - 220) * t), 255)
        d.line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=58, fill=255)
    img.putalpha(mask)
    ImageDraw.Draw(img).polygon([(96, 74), (96, 182), (186, 128)],
                                fill=(17, 17, 27, 255))
    if png_path:
        try:
            img.save(png_path, "PNG")
        except Exception:
            pass
    if ico_path:
        try:
            img.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32),
                                      (48, 48), (64, 64), (128, 128), (256, 256)])
        except Exception:
            pass
    return png_path


# ---------------------------------------------------------
# 启动引擎（核心修复区）
#   · 子进程一律脱离启动器的控制台
#   · 控制台窗口按需显示 / 隐藏，脚本结束窗口自动关闭
# ---------------------------------------------------------
def _comspec():
    return os.environ.get("COMSPEC") or os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")


def _powershell():
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(exe):
        return exe
    return shutil.which("powershell.exe") or "powershell.exe"


def _sys_exe(name):
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                       "System32", name)
    return exe if os.path.isfile(exe) else (shutil.which(name) or name)


def find_python(console=False):
    """定位用来跑 .py 的解释器。

    console=False 时优先 pythonw.exe（无控制台窗口），
    console=True  时优先 python.exe（能看到脚本输出）。
    """
    def order(console_):
        return ("python.exe", "pythonw.exe") if console_ else ("pythonw.exe", "python.exe")

    primary, secondary = order(console)
    candidates = []

    def add(path, weight):
        if path and os.path.isfile(path):
            real = os.path.abspath(path)
            for old, _w in candidates:
                if old.lower() == real.lower():
                    return
            candidates.append((real, weight))

    if not getattr(sys, "frozen", False):
        # 当前解释器所在目录最可信
        here = os.path.dirname(os.path.abspath(sys.executable))
        add(os.path.join(here, primary), 0)
        add(os.path.join(here, secondary), 1)
        add(sys.executable, 0 if os.path.basename(sys.executable).lower() == primary else 2)

    for name, weight in ((primary, 0), (secondary, 1)):
        try:
            add(shutil.which(name), weight)
        except Exception:
            pass

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[1])
    return candidates[0][0]


def plan_launch(path, args="", console_mode=CONSOLE_AUTO):
    """决定如何启动目标。

    返回 dict：
        exe / params    : 要执行的程序与参数
        hidden          : 是否隐藏控制台窗口
        kind            : 'shell'（ShellExecute，走系统关联）或 'exec'（直接创建进程）
        desc            : 中文说明，用于状态栏
        error           : 出错时的原因
    """
    ext = os.path.splitext(path)[1].lower()
    arg = (args or "").strip()
    tail = (" " + arg) if arg else ""
    hide = (console_mode == CONSOLE_HIDE)
    show = (console_mode == CONSOLE_SHOW)

    if ext in (".bat", ".cmd"):
        # cmd /c ""脚本路径" 参数"  —— 独立控制台，脚本跑完窗口自动关闭
        return {
            "kind": "exec", "exe": _comspec(),
            "params": '/c ""%s"%s' % (path, tail),
            "hidden": hide and not show, "desc": "批处理脚本",
        }

    if ext == ".ps1":
        flags = "-NoProfile -ExecutionPolicy Bypass"
        if hide and not show:
            flags += " -WindowStyle Hidden"
        return {
            "kind": "exec", "exe": _powershell(),
            "params": '%s -File "%s"%s' % (flags, path, tail),
            "hidden": hide and not show, "desc": "PowerShell 脚本",
        }

    if ext in (".py", ".pyw"):
        py = find_python(console=show)
        if not py:
            return {"error": "没有找到 Python 解释器，无法运行 .py 脚本"}
        hidden = not show
        return {
            "kind": "exec", "exe": py,
            "params": '"%s"%s' % (path, tail),
            "hidden": hidden, "desc": "Python 脚本",
        }

    if ext in (".vbs", ".js", ".jse", ".wsf"):
        host = _sys_exe("cscript.exe") if show else _sys_exe("wscript.exe")
        return {
            "kind": "exec", "exe": host,
            "params": '//nologo "%s"%s' % (path, tail),
            "hidden": not show, "desc": "脚本",
        }

    if ext in (".exe", ".com"):
        if hide or show or arg:
            return {
                "kind": "exec", "exe": path, "params": arg,
                "hidden": hide and not show, "desc": "程序",
            }
        return {"kind": "shell", "exe": path, "params": None,
                "hidden": False, "desc": "程序"}

    # .lnk / .msc / .reg / .iso / .jar / .ahk / 其他 —— 交给系统关联
    return {"kind": "shell", "exe": path, "params": arg or None,
            "hidden": hide and not show, "desc": "项目"}


def shell_execute(verb, exe, params=None, cwd=None, show=1):
    """ShellExecuteW：走系统关联 / 可请求管理员权限"""
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, verb, str(exe), params or None,
            str(cwd) if cwd else None, int(show))
        return rc > 32, rc
    except Exception as exc:
        return False, str(exc)


def _spawn(cmdline, cwd=None, hidden=False):
    """直接创建进程。

    hidden=True  → CREATE_NO_WINDOW（无窗口，且不继承启动器的标准输入输出）
    hidden=False → CREATE_NEW_CONSOLE（独立的新控制台，与启动器彻底解绑）
    """
    flags = CREATE_NO_WINDOW if hidden else CREATE_NEW_CONSOLE
    kwargs = {
        "cwd": cwd or None,
        "shell": False,
        "creationflags": flags,
        "close_fds": True,
    }
    if hidden:
        kwargs.update(stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL)
    return subprocess.Popen(cmdline, **kwargs)


def launch_path(path, args="", console_mode=CONSOLE_AUTO, admin=False, cwd=None):
    """启动一个目标，返回 (成功, 说明)"""
    if not path:
        return False, "路径为空"
    if is_url(path):
        try:
            webbrowser.open(path)
            return True, "已用默认浏览器打开"
        except Exception as exc:
            return False, "打开网址失败：%s" % exc
    if not os.path.exists(path):
        return False, "路径不存在"
    if os.path.isdir(path):
        ok, _rc = shell_execute("open", path)
        return ok, "已打开文件夹" if ok else "系统拒绝打开该文件夹"

    work_dir = cwd or os.path.dirname(path) or None
    plan = plan_launch(path, args, console_mode)
    if plan.get("error"):
        return False, plan["error"]

    exe = plan["exe"]
    params = plan["params"] or ""
    hidden = plan["hidden"]
    desc = plan["desc"]

    if admin:
        ok, rc = shell_execute("runas", exe, params or None, work_dir, 0 if hidden else 1)
        if ok:
            return True, "已以管理员身份启动%s" % desc
        return False, "管理员启动失败（可能取消了 UAC 授权，错误码 %s）" % rc

    if plan["kind"] == "shell" and not hidden:
        ok, rc = shell_execute("open", exe, params or None, work_dir, 1)
        if ok:
            return True, "已启动%s" % desc
        return False, "系统拒绝打开（错误码 %s）" % rc

    cmdline = ('"%s" %s' % (exe, params)) if params else ('"%s"' % exe)
    try:
        _spawn(cmdline, work_dir, hidden)
    except Exception as exc:
        return False, "启动失败：%s" % exc
    return True, "已启动%s%s" % (desc, "（无窗口）" if hidden else "")


def set_autostart(enable):
    """开机自启（写注册表 Run 项）"""
    if not HAS_WINREG:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE
        )
        try:
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = '"%s"' % sys.executable
                else:
                    pyw = find_python(console=False)
                    cmd = '"%s" "%s"' % (pyw or sys.executable, os.path.abspath(__file__))
                winreg.SetValueEx(key, "MyLauncher", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "MyLauncher")
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except Exception:
        return False


def is_autostart():
    if not HAS_WINREG:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run"
        )
        try:
            winreg.QueryValueEx(key, "MyLauncher")
        finally:
            winreg.CloseKey(key)
        return True
    except Exception:
        return False


def make_default_tray_icon(path):
    """画一个默认托盘图标"""
    return make_app_icon(png_path=path)


# ---------------------------------------------------------
# 配置读写
# ---------------------------------------------------------
DEFAULT_CONFIG = {
    "items": [],
    "chains": [],
    "settings": {
        "theme": "dark",
        "theme_mode": "dark",
        "view": "grid",
        "always_on_top": False,
        "geometry": "1000x640",
        "tray_enabled": True,
        "guide_shown": False,
        "start_hidden": False,
        "hide_on_blur": False,
        "sort_mode": "name",
        "new_file_dir": "",
    }
}

ITEM_DEFAULTS = {
    "id": "", "name": "", "path": "", "icon": None, "group": "",
    "admin": False, "pinned": False, "run_count": 0,
    "cwd": "", "args": "", "console": CONSOLE_AUTO,
    "order": None,
}


def normalize_item(raw):
    item = dict(ITEM_DEFAULTS)
    if isinstance(raw, dict):
        item.update(raw)
    if item.get("console") not in (CONSOLE_AUTO, CONSOLE_SHOW, CONSOLE_HIDE):
        item["console"] = CONSOLE_AUTO
    if not item.get("id"):
        item["id"] = human_key(str(item.get("path", "")) + str(item.get("name", "")))
    item.pop("hotkey", None)   # v1.2 的专属快捷键已移除，老配置里残留的字段清掉
    try:
        item["order"] = float(item["order"]) if item.get("order") not in (None, "") else None
    except Exception:
        item["order"] = None
    return item


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    data = None
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if data is None and os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            data = None
    if isinstance(data, dict):
        items = data.get("items", [])
        cfg["items"] = [normalize_item(i) for i in items if isinstance(i, dict)]
        chains = data.get("chains", [])
        cfg["chains"] = [c for c in chains if isinstance(c, dict)] \
            if isinstance(chains, list) else []
        settings = data.get("settings", {})
        if isinstance(settings, dict):
            cfg["settings"].update(settings)
    return cfg


def save_config(cfg):
    try:
        if os.path.exists(CONFIG_FILE):
            shutil.copyfile(CONFIG_FILE, BACKUP_FILE)
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
        return True
    except Exception:
        return False


# ---------------------------------------------------------
# 圆角绘制 / 基础控件（v1.2 界面重做用）
# ---------------------------------------------------------
def _rr_points(x1, y1, x2, y2, r):
    """圆角矩形的控制点（配合 smooth=True 用，Tk 没有原生圆角）"""
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def draw_round(cv, x1, y1, x2, y2, r, fill, outline="", width=1, tags=None):
    """在画布上画一个圆角矩形（先铺描边色再铺填充色，得到干净的 1px 边）"""
    if x2 - x1 < 2 or y2 - y1 < 2:
        return
    if outline and width > 0:
        cv.create_polygon(_rr_points(x1, y1, x2, y2, r), smooth=True,
                          splinesteps=20, fill=outline, outline="", tags=tags)
        i = width
        cv.create_polygon(_rr_points(x1 + i, y1 + i, x2 - i, y2 - i, max(0, r - i)),
                          smooth=True, splinesteps=20, fill=fill, outline="", tags=tags)
    else:
        cv.create_polygon(_rr_points(x1, y1, x2, y2, r), smooth=True,
                          splinesteps=20, fill=fill, outline="", tags=tags)


def font_of(spec, default_size=10):
    """('Microsoft YaHei', 10, 'bold') / ('Microsoft YaHei', 10) -> tkfont.Font"""
    if isinstance(spec, tkfont.Font):
        return spec
    family, size, weight = FONT_UI, default_size, "normal"
    if isinstance(spec, (tuple, list)):
        if len(spec) > 0:
            family = spec[0]
        if len(spec) > 1:
            size = spec[1]
        if len(spec) > 2:
            weight = spec[2]
    return tkfont.Font(family=family, size=size, weight=weight)


def elide(font, text, max_px):
    """按像素宽度截断文字，超出用 … 结尾"""
    text = str(text)
    if max_px <= 0:
        return ""
    if font.measure(text) <= max_px:
        return text
    while text and font.measure(text + "…") > max_px:
        text = text[:-1]
    return text + "…"


class Tooltip(object):
    """鼠标停一会儿才弹的小提示"""

    def __init__(self, widget, text, th, delay=420, wraplength=300):
        self.widget = widget
        self.text = text
        self.th = th
        self.delay = delay
        self.wraplength = wraplength
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self._after = self.widget.after(self.delay, self._show)
        except Exception:
            self._after = None

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        try:
            th = self.th
            tip = tk.Toplevel(self.widget)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            box = tk.Frame(tip, bg=th["border_hi"])
            box.pack()
            tk.Label(box, text=self.text, bg=th["card"], fg=th["text"],
                     font=(FONT_UI, 9), justify="left", wraplength=self.wraplength,
                     padx=10, pady=7).pack(padx=1, pady=1)
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            tip.update_idletasks()
            sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
            w, h = tip.winfo_width(), tip.winfo_height()
            if x + w > sw - 8:
                x = sw - w - 8
            if y + h > sh - 8:
                y = self.widget.winfo_rooty() - h - 6
            tip.geometry("+%d+%d" % (max(0, x), max(0, y)))
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class RoundButton(tk.Canvas):
    """圆角扁平按钮（Canvas 自绘，带悬停 / 按下 / 选中 / 禁用状态）"""

    VARIANTS = ("primary", "secondary", "ghost", "danger")

    def __init__(self, master, text="", command=None, th=None, variant="ghost",
                 font=None, padx=14, pady=8, radius=9, bg=None, width=None,
                 height=None, tooltip=None):
        self.th = th or THEMES["dark"]
        self.variant = variant if variant in self.VARIANTS else "ghost"
        self._text = text
        self._command = command
        self._font = font_of(font, 10)
        self._radius = radius
        self._padx = padx
        self._pady = pady
        self._auto_size = width is None
        self._hover = False
        self._pressed = False
        self._enabled = True
        self._active = False
        self._base_bg = bg or self.th["bg"]
        tw = self._font.measure(text) if text else 0
        w = int(width) if width else int(tw + padx * 2)
        h = int(height) if height else int(self._font.metrics("linespace") + pady * 2)
        super().__init__(master, width=w, height=h, bg=self._base_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._size = (w, h)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()
        if tooltip:
            Tooltip(self, tooltip, self.th)

    # ---- 颜色 ----
    def _colors(self):
        th = self.th
        v = self.variant
        if not self._enabled:
            return th["card2"], th["sub"], th["border"]
        if v == "primary":
            fill = th["accent_hi"] if (self._hover or self._pressed) else th["accent"]
            return fill, th["accent_fg"], ""
        if v == "danger":
            fill = th["err"] if self._hover else th["card"]
            fg = th["accent_fg"] if self._hover else th["err"]
            return fill, fg, th["err"]
        if v == "secondary":
            if self._pressed:
                fill = th["sel"]
            elif self._hover or self._active:
                fill = th["hover"]
            else:
                fill = th["card"]
            edge = th["accent"] if self._active else th["border_hi"]
            return fill, th["text"], edge
        # ghost
        fill = th["hover"] if (self._hover or self._pressed or self._active) else self._base_bg
        fg = th["text"] if (self._hover or self._active) else th["sub"]
        return fill, fg, ""

    def _draw(self):
        self.delete("all")
        w, h = self._size
        fill, fg, edge = self._colors()
        draw_round(self, 1, 1, w - 1, h - 1, self._radius, fill, edge, 1)
        if self._text:
            self.create_text(w / 2.0, h / 2.0 + 1, text=self._text, fill=fg,
                             font=self._font)

    # ---- 事件 ----
    def _on_enter(self, _e=None):
        self._hover = True
        self._draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _e=None):
        if not self._enabled:
            return
        self._pressed = True
        self._draw()

    def _on_release(self, event=None):
        if not self._enabled:
            return
        was = self._pressed
        self._pressed = False
        self._draw()
        if was and self._command:
            try:
                self._command()
            except Exception:
                traceback.print_exc()

    # ---- 对外接口 ----
    def set_text(self, text):
        """改文字；自动宽度的按钮会跟着重新排版（避免文字被裁掉）"""
        self._text = text
        if self._auto_size:
            tw = self._font.measure(text) if text else 0
            w = int(tw + self._padx * 2)
            self._size = (w, self._size[1])
            try:
                self.configure(width=w)
            except Exception:
                pass
        self._draw()

    def set_enabled(self, flag):
        self._enabled = bool(flag)
        try:
            self.configure(cursor="hand2" if self._enabled else "arrow")
        except Exception:
            pass
        self._draw()

    def set_active(self, flag):
        self._active = bool(flag)
        self._draw()

    def set_theme(self, th, bg=None):
        self.th = th
        if bg:
            self._base_bg = bg
            self.configure(bg=bg)
        self._draw()


class RoundEntry(tk.Canvas):
    """圆角输入框（Canvas 画底 + 内嵌 Entry），带占位符与聚焦高亮。

    占位文字画在画布上，不写进输入框、也不会污染 textvariable。
    """

    def __init__(self, master, th, placeholder="", font=None, radius=10,
                 bg=None, height=None, textvariable=None, padx=12, width=200,
                 justify="left"):
        self.th = th
        self._placeholder = placeholder
        self._radius = radius
        self._padx = padx
        self._font = font_of(font, 10)
        self._focused = False
        self._ph = None
        if height is None:
            height = int(self._font.metrics("linespace") + 14)
        super().__init__(master, height=height, width=width, bg=bg or th["bg"],
                         highlightthickness=0, bd=0)
        self._h = int(height)
        self.entry = tk.Entry(self, textvariable=textvariable, bd=0,
                              relief="flat", bg=th["entry"], fg=th["text"],
                              insertbackground=th["text"], font=self._font,
                              highlightthickness=0, justify=justify)
        self.entry.place(x=padx, y=1, width=max(10, width - padx * 2),
                         height=self._h - 2)
        # 占位文字用一个贴在输入框上面的 Label（画布上的文字会被 Entry 挡住）
        self._ph_lbl = tk.Label(self, text=placeholder or "", bg=th["entry"],
                                fg=th["sub"], font=self._font, anchor="w",
                                justify=justify, bd=0, highlightthickness=0)
        self._ph_lbl.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        self.entry.bind("<KeyRelease>", lambda e: self._sync_placeholder(), add="+")
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        if textvariable is not None:
            try:
                textvariable.trace_add("write", lambda *a: self._sync_placeholder())
            except Exception:
                pass
        self._draw()

    # ---- 绘制 ----
    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 20)
        edge = self.th["accent"] if self._focused else self.th["border_hi"]
        draw_round(self, 1, 1, w - 1, self._h - 1, self._radius,
                   self.th["entry"], edge, 1)
        self._sync_placeholder()

    def _sync_placeholder(self):
        show = bool(self._placeholder) and not self._focused and not self.entry.get()
        try:
            if show:
                self._ph_lbl.place(x=self._padx + 2, y=1,
                                   width=max(10, self.winfo_width() - self._padx * 2 - 4),
                                   height=self._h - 2)
                self._ph_lbl.lift()
            else:
                self._ph_lbl.place_forget()
        except Exception:
            pass

    def _on_configure(self, event):
        self._h = event.height
        self.entry.place_configure(x=self._padx, y=1,
                                   width=max(10, event.width - self._padx * 2),
                                   height=max(10, event.height - 2))
        self._draw()

    def _on_focus_in(self, _e=None):
        self._focused = True
        self._draw()

    def _on_focus_out(self, _e=None):
        self._focused = False
        self._draw()

    # ---- 对外接口 ----
    def value(self):
        return self.entry.get().strip()

    def clear(self):
        self.entry.delete(0, "end")
        self._sync_placeholder()

    def set(self, text):
        self.entry.delete(0, "end")
        if text:
            self.entry.insert(0, text)
        self._sync_placeholder()

    def get(self):
        return self.entry.get()

    def focus_set(self):
        try:
            self.entry.focus_set()
        except Exception:
            pass

    def select_all(self):
        try:
            self.entry.select_range(0, "end")
            self.entry.icursor("end")
        except Exception:
            pass

    def bind_entry(self, sequence, func, add="+"):
        self.entry.bind(sequence, func, add=add)

    def set_theme(self, th, bg=None):
        self.th = th
        self.entry.configure(bg=th["entry"], fg=th["text"],
                             insertbackground=th["text"])
        try:
            self._ph_lbl.configure(bg=th["entry"], fg=th["sub"])
        except Exception:
            pass
        if bg:
            self.configure(bg=bg)
        self._draw()


# 兼容旧调用（内部其实已经是圆角按钮）
def make_button(parent, text, command, th, primary=False, pad=(14, 7), **kw):
    variant = kw.pop("variant", "primary" if primary else "secondary")
    font = kw.pop("font", None)
    tooltip = kw.pop("tooltip", None)
    radius = kw.pop("radius", 9)
    bg = kw.pop("bg", None)
    return RoundButton(parent, text=text, command=command, th=th, variant=variant,
                       font=font, padx=pad[0], pady=pad[1], radius=radius, bg=bg,
                       tooltip=tooltip)


class ScrollFrame(tk.Frame):
    """带自动隐藏滚动条的滚动容器，内容放到 .inner 里"""

    def __init__(self, master, bg, **kw):
        super().__init__(master, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_set)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_set(self, first, last):
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.vbar.pack_forget()
            elif not self.vbar.winfo_ismapped():
                self.vbar.pack(side="right", fill="y")
        except Exception:
            pass
        self.vbar.set(first, last)

    def _on_inner(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfig(self._win, width=event.width)

    def scroll(self, event):
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass


def walk_widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk_widgets(child)


# ---------------------------------------------------------
# 添加 / 编辑对话框
# ---------------------------------------------------------
class ItemDialog(tk.Toplevel):
    """添加或编辑一个启动项"""

    def __init__(self, master, app, path=None, item=None, groups=None):
        super().__init__(master)
        self.app = app
        self.item = item
        self.result = None
        self.icon_file = None
        self.groups = groups or []
        self.th = app.theme
        th = self.th

        self.configure(bg=th["bg"])
        self.title("编辑启动项" if item else "添加启动项")
        height = min(870, max(580, self.winfo_screenheight() - 90))
        self.geometry("600x%d" % height)
        self.resizable(False, True)
        self.minsize(600, 480)
        self.transient(master)

        self.path_var = tk.StringVar(value=(item or {}).get("path", path or ""))
        self.name_var = tk.StringVar()
        self.group_var = tk.StringVar(value=(item or {}).get("group", ""))
        self.cwd_var = tk.StringVar(value=(item or {}).get("cwd", ""))
        self.args_var = tk.StringVar(value=(item or {}).get("args", ""))
        self.admin_var = tk.BooleanVar(value=(item or {}).get("admin", False))
        self.pin_var = tk.BooleanVar(value=(item or {}).get("pinned", False))
        console_key = (item or {}).get("console", CONSOLE_AUTO)
        self.console_var = tk.StringVar(value=CONSOLE_LABEL.get(console_key, CONSOLE_LABEL[CONSOLE_AUTO]))

        base = (item or {}).get("name")
        if not base:
            src = self.path_var.get()
            if is_url(src):
                base = src.split("//")[-1].split("/")[0]
            else:
                base = os.path.splitext(os.path.basename(src.rstrip("\\/")))[0] or "未命名"
        self.name_var.set(base)

        self._build(th)
        # 构建成功之后再抢输入焦点：万一某个控件建失败了，也不会留下一个抢着焦点、
        # 又没人能关掉的空窗口（那会让整个界面看起来像卡死）
        self.grab_set()

        if item and item.get("icon") and os.path.exists(item["icon"]):
            self.icon_file = item["icon"]
        else:
            self._auto_icon()
        self._refresh_icon_preview()
        self._on_path_changed()
        self._center_on(master)
        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<MouseWheel>", lambda e: self.scroll.scroll(e))

    # ---------- 布局 ----------
    def _header(self, th):
        head = tk.Frame(self, bg=th["bg"])
        head.pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(head, text=("✏️  编辑启动项" if self.item else "➕  添加启动项"),
                 bg=th["bg"], fg=th["text"], font=(FONT_UI, 14, "bold")).pack(anchor="w")
        tk.Label(head,
                 text="跟着编号一步步填就行：① 选要打开的东西 → ② 起个中文名 → "
                      "③ 启动方式（不懂就用推荐）→ ④ 图标（可跳过）",
                 bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9),
                 justify="left", wraplength=540).pack(anchor="w", pady=(4, 0))

    def _section(self, num, title, hint=""):
        """一节 = 一个编号徽章 + 标题 +（可选）一句说明"""
        th = self.th
        wrap = tk.Frame(self.scroll.inner, bg=th["bg"])
        wrap.pack(fill="x", padx=20, pady=(16, 0))
        head = tk.Frame(wrap, bg=th["bg"])
        head.pack(fill="x")
        badge = tk.Canvas(head, width=22, height=22, bg=th["bg"],
                          highlightthickness=0, bd=0)
        badge.pack(side="left")
        draw_round(badge, 1, 1, 21, 21, 8, th["accent"], "")
        badge.create_text(11, 12, text=str(num), fill=th["accent_fg"],
                          font=(FONT_UI, 9, "bold"))
        tk.Label(head, text=title, bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 11, "bold")).pack(side="left", padx=(8, 0))
        if hint:
            tk.Label(head, text=hint, bg=th["bg"], fg=th["sub"],
                     font=(FONT_UI, 8)).pack(side="left", padx=(8, 0))
        return wrap

    def _field_hint(self, parent, text, color_key="sub", tip=None):
        th = self.th
        lbl = tk.Label(parent, text=text, bg=th["bg"], fg=th[color_key],
                       font=(FONT_UI, 8), justify="left", anchor="w",
                       wraplength=520)
        lbl.pack(fill="x", pady=(2, 0))
        if tip:
            Tooltip(lbl, tip, th)
        return lbl

    def _entry_row(self, parent, var, button_text=None, command=None,
                   placeholder="", tip=None):
        """圆角输入框 +（可选）右侧按钮"""
        th = self.th
        row = tk.Frame(parent, bg=th["bg"])
        row.pack(fill="x", pady=(4, 0))
        ent = RoundEntry(row, th, placeholder=placeholder, textvariable=var,
                         font=(FONT_UI, 10), padx=10, height=34)
        ent.pack(side="left", fill="x", expand=True)
        if tip:
            Tooltip(ent, tip, th)
        if button_text:
            make_button(row, button_text, command, th, variant="secondary",
                        font=(FONT_UI, 10), pad=(14, 8)).pack(side="left", padx=(8, 0))
        return ent

    def _build(self, th):
        self._header(th)

        self.scroll = ScrollFrame(self, bg=th["bg"])
        self.scroll.pack(fill="both", expand=True, padx=(0, 6), pady=(0, 0))
        body = self.scroll.inner

        # ---------- ① 要启动什么 ----------
        sec1 = self._section(1, "要启动什么", "必填")
        self.path_entry = self._entry_row(
            sec1, self.path_var, "浏览…", self._browse,
            placeholder=r"例如 D:\tools\backup.bat 或 https://example.com",
            tip="既是程序/脚本/文件夹的完整路径，也可以直接填一个网址")
        self.path_entry.bind_entry("<KeyRelease>", lambda e: self._on_path_changed())
        self.path_entry.bind_entry("<FocusOut>", lambda e: self._on_path_changed())

        tipbox = tk.Frame(sec1, bg=th["card2"], highlightthickness=1,
                          highlightbackground=th["border"])
        tipbox.pack(fill="x", pady=(8, 0))
        self.tip_bar = tk.Frame(tipbox, bg=th["accent"], width=3)
        self.tip_bar.pack(side="left", fill="y")
        tip_inner = tk.Frame(tipbox, bg=th["card2"])
        tip_inner.pack(side="left", fill="x", expand=True, padx=12, pady=8)
        self.tip_kind = tk.Label(tip_inner, text="", bg=th["card2"],
                                 fg=th["accent"], font=(FONT_UI, 9, "bold"),
                                 anchor="w")
        self.tip_kind.pack(fill="x")
        self.tip_label = tk.Label(
            tip_inner, text="", bg=th["card2"], fg=th["text"], font=(FONT_UI, 9),
            justify="left", anchor="w", wraplength=470)
        self.tip_label.pack(fill="x")

        # ---------- ② 叫什么 ----------
        sec2 = self._section(2, "显示名称", "必填，随便起中文名")
        self._entry_row(sec2, self.name_var,
                        placeholder="例如：每日备份、游戏启动器",
                        tip="只影响界面上显示的文字，不影响实际路径")
        self._field_hint(sec2, "名字只用于显示，可以随时改；留空会自动用文件名。")

        sub = tk.Frame(sec2, bg=th["bg"])
        sub.pack(fill="x", pady=(10, 0))
        tk.Label(sub, text="分组（可选）", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w")
        cb = ttk.Combobox(sub, textvariable=self.group_var,
                          values=[""] + sorted({g for g in self.groups if g}),
                          font=(FONT_UI, 10))
        cb.pack(fill="x", ipady=4, pady=(4, 0))
        self._field_hint(sub, "支持多级分组：写「开发/前端」就是两级，主界面按「开发」筛选会带出它下面所有子分组。")

        # ---------- ③ 启动方式 ----------
        sec3 = self._section(3, "启动方式", "不懂就点「推荐设置」")
        row_tools = tk.Frame(sec3, bg=th["bg"])
        row_tools.pack(fill="x", pady=(8, 0))
        make_button(row_tools, "✨  推荐设置", self._apply_recommended, th,
                    variant="primary", font=(FONT_UI, 9), pad=(14, 7),
                    tooltip="按文件类型自动填好工作目录、控制台、名称",
                    ).pack(side="left")
        self.console_hint = tk.Label(row_tools, text="", bg=th["bg"], fg=th["sub"],
                                     font=(FONT_UI, 9))
        self.console_hint.pack(side="left", padx=(10, 0))

        tk.Label(sec3, text="控制台窗口", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w", pady=(10, 0))
        combo = ttk.Combobox(sec3, textvariable=self.console_var, state="readonly",
                             values=[label for label, _key in CONSOLE_CHOICES],
                             font=(FONT_UI, 10))
        combo.pack(fill="x", ipady=4, pady=(4, 0))
        Tooltip(combo, "自动：.py 不弹黑窗、.bat 弹独立窗口跑完自动关闭；\n"
                       "想看到 print 输出就选「始终显示」。", th)
        self._field_hint(sec3, "批处理默认弹独立窗口、跑完自动关闭；Python 脚本默认不弹黑窗。")

        self._entry_row(sec3, self.cwd_var, "选择…", self._browse_cwd,
                        placeholder="留空 = 用文件所在目录",
                        tip="脚本里写相对路径（比如 copy a.txt）时，靠的就是这个目录")

        self._entry_row(sec3, self.args_var,
                        placeholder="留空即可，例如 --debug",
                        tip="传给程序的命令行参数，一般不用填")

        # ---------- ④ 图标与选项 ----------
        sec4 = self._section(4, "图标与选项", "可跳过")
        icon_row = tk.Frame(sec4, bg=th["bg"])
        icon_row.pack(fill="x", pady=(8, 0))
        self.icon_label = tk.Label(icon_row, bg=th["card"], width=5, height=2,
                                   highlightthickness=1,
                                   highlightbackground=th["border_hi"])
        self.icon_label.pack(side="left")
        btns = tk.Frame(icon_row, bg=th["bg"])
        btns.pack(side="left", padx=12)
        make_button(btns, "选一张图片", self._choose_icon, th, variant="secondary",
                    font=(FONT_UI, 9), pad=(14, 7)).pack(pady=(0, 4))
        make_button(btns, "自动提取", self._auto_icon_and_refresh, th,
                    variant="ghost", font=(FONT_UI, 9), pad=(14, 7)).pack()
        self._field_hint(sec4, "EXE 会自动提取原生图标；其他类型用 emoji 兜底，不选也没关系。")

        opt = tk.Frame(sec4, bg=th["bg"])
        opt.pack(fill="x", pady=(12, 0))
        for text, var, tip in (
                ("以管理员身份运行（会弹 UAC）", self.admin_var,
                 "需要管理员权限的程序才勾；普通程序别勾，否则每次都要点「是」"),
                ("置顶显示（排在列表最前）", self.pin_var,
                 "常用的项目勾上，它会一直排在最前面")):
            cbx = tk.Checkbutton(opt, text=text, variable=var, bg=th["bg"],
                                 fg=th["text"], selectcolor=th["card"],
                                 activebackground=th["bg"], activeforeground=th["text"],
                                 font=(FONT_UI, 10), bd=0, highlightthickness=0)
            cbx.pack(anchor="w")
            Tooltip(cbx, tip, th)

        tk.Frame(body, bg=th["bg"], height=14).pack()

        # ---------- 底部按钮 ----------
        tk.Frame(self, bg=th["border"], height=1).pack(fill="x", padx=20)
        foot = tk.Frame(self, bg=th["bg"])
        foot.pack(fill="x", padx=20, pady=12)
        # 先摆按钮再摆提示文字，避免长提示把按钮挤出去
        make_button(foot, "确定保存", self._ok, th, variant="primary",
                    font=(FONT_UI, 10, "bold"), pad=(22, 10)).pack(side="right")
        make_button(foot, "取消", self._cancel, th, variant="secondary",
                    font=(FONT_UI, 10), pad=(18, 10)).pack(side="right", padx=(0, 10))
        make_button(foot, "▶  试运行", self._test_run, th, variant="ghost",
                    font=(FONT_UI, 9), pad=(12, 10),
                    tooltip="先按当前设置启动一次看看效果，不会保存也不会关闭本窗口",
                    ).pack(side="right", padx=(0, 10))
        self.status_lbl = tk.Label(foot, text="", bg=th["bg"], fg=th["sub"],
                                   font=(FONT_UI, 9), anchor="w", justify="left",
                                   wraplength=210)
        self.status_lbl.pack(side="left", fill="x", expand=True)

    def _center_on(self, master):
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            x = master.winfo_rootx() + (master.winfo_width() - w) // 2
            y = master.winfo_rooty() + (master.winfo_height() - h) // 3
            x = max(0, min(x, self.winfo_screenwidth() - w))
            y = max(0, min(y, self.winfo_screenheight() - h))
            self.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    # ---------- 交互 ----------
    def _say(self, text, color_key="sub"):
        try:
            self.status_lbl.config(text=text, fg=self.th[color_key])
        except Exception:
            pass

    def _analyze(self, raw):
        """根据当前路径判断类型，给出引导文案与推荐设置"""
        path = (raw or "").strip()
        auto = {"kind": "还没选", "name": None, "cwd": None,
                "console": CONSOLE_AUTO, "advice": ""}
        if not path:
            auto["tip"] = ("点右边的「浏览…」挑一个文件，或者直接把文件拖到主窗口上。\n"
                           "支持 EXE / BAT / Python / 文件夹，也可以直接填一个网址。")
            return auto
        if is_url(path):
            auto.update(kind="网址",
                        name=path.split("//")[-1].split("/")[0] or "网址",
                        tip="网址会用系统默认浏览器打开；「工作目录」「启动参数」对它不起作用。")
            return auto
        name = os.path.splitext(os.path.basename(path.rstrip("\\/")))[0] or None
        auto["name"] = name
        if not os.path.exists(path):
            auto.update(kind="路径不存在",
                        tip="⚠ 这个路径在本机找不到。检查有没有写错，或点「浏览…」重新选一个。")
            return auto
        if os.path.isdir(path):
            auto.update(kind="文件夹",
                        tip="文件夹：点一下卡片就用资源管理器打开它，不需要别的设置。")
            return auto
        ext = os.path.splitext(path)[1].lower()
        parent = os.path.dirname(path)
        auto["cwd"] = parent
        if ext in (".bat", ".cmd"):
            auto.update(kind="批处理脚本", advice="建议：独立窗口（自动）",
                        tip=("脚本里如果用了相对路径（比如 copy a.txt），"
                             "「工作目录」必须是脚本所在文件夹，否则会报「找不到文件」。\n"
                             "运行时会弹一个独立窗口，脚本跑完会自己关闭。"))
        elif ext in (".py", ".pyw"):
            auto.update(kind="Python 脚本", advice="建议：不弹黑窗（自动）",
                        tip=("默认用 pythonw 启动，不会弹黑窗口。\n"
                             "想看到 print 输出？把下面的「控制台窗口」改成「始终显示控制台窗口」。"))
        elif ext == ".ps1":
            auto.update(kind="PowerShell 脚本", advice="建议：独立窗口（自动）",
                        tip=("会在独立窗口里运行。脚本里用到相对路径时，"
                             "记得把「工作目录」设成脚本所在文件夹。"))
        elif ext in (".vbs", ".js", ".wsf"):
            auto.update(kind="脚本", advice="建议：不弹黑窗（自动）",
                        tip="这类脚本默认静默运行；需要看输出就选「始终显示控制台窗口」。")
        elif ext in (".exe", ".com"):
            auto.update(kind="可执行程序", advice="建议：保持「自动」",
                        tip=("一般保持默认就好（GUI 程序不会有黑窗）。\n"
                             "如果是命令行小工具、想看它的输出，把「控制台窗口」改成「始终显示」。"))
        elif ext in (".lnk", ".url"):
            auto.update(kind="快捷方式", advice="建议：保持「自动」",
                        tip="快捷方式会交给 Windows 打开，效果等于双击它本身。")
        else:
            auto.update(kind="文件", advice="建议：保持「自动」",
                        tip="这类文件会交给 Windows 的系统关联程序打开。")
        return auto

    def _on_path_changed(self, _e=None):
        info = self._analyze(self.path_var.get())
        self._info = info
        try:
            self.tip_kind.config(text=info["kind"])
            self.tip_label.config(text=info.get("tip", ""))
            self.tip_bar.config(bg=self.th["warn"] if info["kind"] == "路径不存在"
                                else self.th["accent"])
            self.console_hint.config(text=info.get("advice", ""))
        except Exception:
            pass
        auto_before = getattr(self, "_auto_name", None)
        cur = self.name_var.get().strip()
        if info.get("name") and (not cur or cur == auto_before or cur == "未命名"):
            self.name_var.set(info["name"])
            self._auto_name = info["name"]

    def _apply_recommended(self):
        info = self._analyze(self.path_var.get())
        if not self.path_var.get().strip():
            self.path_entry.focus_set()
            self._say("⚠ 先在 ① 里选一个要启动的文件", "warn")
            return
        if info.get("cwd"):
            self.cwd_var.set(info["cwd"])
        self.console_var.set(CONSOLE_LABEL.get(info.get("console", CONSOLE_AUTO),
                                               CONSOLE_LABEL[CONSOLE_AUTO]))
        if info.get("name"):
            self.name_var.set(info["name"])
            self._auto_name = info["name"]
        self._auto_icon_and_refresh()
        self._on_path_changed()
        self._say("✨ 已按推荐填好", "ok")

    def _test_run(self):
        path = self.path_var.get().strip()
        if not path:
            self.path_entry.focus_set()
            self._say("⚠ 先填路径，才能试运行", "warn")
            return
        mode = CONSOLE_KEY.get(self.console_var.get(), CONSOLE_AUTO)
        cwd = self.cwd_var.get().strip().strip('"') or None
        self._say("正在试运行…")
        ok, msg = launch_path(path, self.args_var.get().strip(), mode, False, cwd)
        self._say(("✅ 试运行成功：" if ok else "❌ 试运行失败：") + msg,
                  "ok" if ok else "err")

    def _browse(self):
        p = filedialog.askopenfilename(
            title="选择程序 / 脚本 / 文件",
            filetypes=[("可执行文件", "*.exe"),
                       ("脚本", "*.bat;*.cmd;*.py;*.pyw;*.ps1;*.vbs;*.js;*.ahk"),
                       ("快捷方式", "*.lnk"), ("所有文件", "*.*")]
        )
        if p:
            self.path_var.set(p)
            self._on_path_changed()
            self._auto_icon_and_refresh()

    def _browse_cwd(self):
        d = filedialog.askdirectory(title="选择工作目录")
        if d:
            self.cwd_var.set(os.path.normpath(d))
            self._say("已选择工作目录：%s" % os.path.normpath(d), "ok")

    def _auto_icon(self):
        key = human_key(self.path_var.get() or self.name_var.get()
                        or ("tmp_%s" % id(self)))
        dst = icon_path_for(key)
        self.icon_file = extract_system_icon(self.path_var.get(), dst)

    def _auto_icon_and_refresh(self):
        self._auto_icon()
        self._refresh_icon_preview()

    def _choose_icon(self):
        p = filedialog.askopenfilename(
            title="选择图标文件",
            filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.ico"), ("所有文件", "*.*")]
        )
        if not p:
            return
        key = human_key(self.path_var.get() or self.name_var()
                        or ("tmp_%s" % id(self)))
        dst = icon_path_for(key)
        if import_custom_icon(p, dst):
            self.icon_file = dst
            self._refresh_icon_preview()
            self._say("图标已更换", "ok")
        else:
            self._say("❌ 无法读取这张图片，换一张试试", "err")

    def _refresh_icon_preview(self):
        photo = load_image(self.icon_file, 44)
        if photo:
            self.icon_label.config(image=photo, text="", width=52, height=52)
            self.icon_label.image = photo
        else:
            ext = os.path.splitext(self.path_var.get())[1].lower()
            self.icon_label.config(image="", width=4, height=2,
                                   text=EXT_ICON.get(ext, "📄"),
                                   font=(FONT_EMOJI, 20),
                                   fg=self.app.theme["text"])

    def _ok(self):
        path = self.path_var.get().strip()
        name = self.name_var.get().strip()
        # ① 路径是唯一硬性要求，出错就停在原地提示，不再弹系统小窗吓人
        if not path:
            self.path_entry.focus_set()
            self._say("⚠ 还没填「要启动什么」：点右边的「浏览…」选一个文件吧", "warn")
            return
        if not is_url(path) and not os.path.exists(path):
            self._say("⚠ 路径在本机不存在，确认后仍可保存", "warn")
            if not messagebox.askyesno(
                    "路径不存在",
                    "本机找不到这个路径：\n%s\n\n仍然保存吗？（以后路径变了会自动提示重新指定）"
                    % path, parent=self):
                return
        if not name:
            name = self._analyze(path).get("name") or "未命名"
        cwd = self.cwd_var.get().strip().strip('"')
        if cwd and not os.path.isdir(cwd):
            if not messagebox.askyesno("目录不存在",
                                       "工作目录不存在：\n%s\n\n仍然保存吗？" % cwd,
                                       parent=self):
                return
        # 批处理 + 空工作目录 = 最常见的「找不到文件」坑，这里主动兜一下
        ext = os.path.splitext(path)[1].lower()
        if ext in (".bat", ".cmd", ".ps1") and not cwd and not is_url(path):
            if not messagebox.askyesno(
                    "建议填写工作目录",
                    "这是脚本文件，但工作目录留空了。\n"
                    "如果脚本里用了相对路径，运行时可能报「找不到文件」。\n\n"
                    "要自动填成脚本所在目录吗？", parent=self):
                pass
            else:
                cwd = os.path.dirname(path)
                self.cwd_var.set(cwd)
        self.result = {
            "name": name,
            "path": path,
            "icon": self.icon_file,
            "group": self.group_var.get().strip(),
            "cwd": cwd,
            "args": self.args_var.get().strip(),
            "console": CONSOLE_KEY.get(self.console_var.get(), CONSOLE_AUTO),
            "admin": self.admin_var.get(),
            "pinned": self.pin_var.get(),
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()

    def show(self):
        self.wait_window()
        return self.result


# ---------------------------------------------------------
# 使用引导 / 输入弹窗
# ---------------------------------------------------------
GUIDE_STEPS = [
    ("添加项目",
     "点右上角「＋ 添加」挑一个 EXE / BAT / Python 脚本或文件夹。\n"
     "也可以直接把文件 / 文件夹拖进主窗口，最快。"),
    ("填三样东西",
     "① 要启动什么（必填）\n"
     "② 叫什么名字（随便起个中文名）\n"
     "③ 启动方式 —— 不懂就点「✨ 推荐设置」，它会按文件类型自动填好。"),
    ("点一下就启动",
     "左键单击卡片＝启动；右键＝无窗口启动 / 管理员运行 / 编辑 / 删除。\n"
     "唤出后可以直接打字搜索，↑↓ 选，Enter 启动。"),
    ("怎么把它叫回来",
     "点 × 是缩到右下角托盘（不是退出），左键点托盘图标就回来了。\n"
     "也可以再双击一次启动器 exe：不会开第二个，而是把已经开着的窗口叫到最前面。"),
]


class GuideDialog(tk.Toplevel):
    """使用引导（首次运行自动弹，菜单里也能随时打开）"""

    def __init__(self, master, app, th, first_run=False):
        super().__init__(master)
        self.app = app
        self.th = th
        self.configure(bg=th["bg"])
        self.title("使用引导")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        head = tk.Frame(self, bg=th["bg"])
        head.pack(fill="x", padx=24, pady=(20, 6))
        tk.Label(head, text="👋  %s"
                 % ("欢迎使用「启动器」" if first_run else "使用引导"),
                 bg=th["bg"], fg=th["text"], font=(FONT_UI, 15, "bold")).pack(anchor="w")
        tk.Label(head, text="四步就能用起来，看不懂的地方随时回来点这里。",
                 bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9)).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(self, bg=th["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        for idx, (title, desc) in enumerate(GUIDE_STEPS, 1):
            row = tk.Frame(body, bg=th["card"], highlightthickness=1,
                           highlightbackground=th["border"])
            row.pack(fill="x", pady=5)
            badge = tk.Canvas(row, width=26, height=26, bg=th["card"],
                              highlightthickness=0, bd=0)
            badge.pack(side="left", padx=(12, 10), pady=12)
            draw_round(badge, 1, 1, 25, 25, 9, th["accent"], "")
            badge.create_text(13, 14, text=str(idx), fill=th["accent_fg"],
                              font=(FONT_UI, 10, "bold"))
            txt = tk.Frame(row, bg=th["card"])
            txt.pack(side="left", fill="x", expand=True, pady=10, padx=(0, 12))
            tk.Label(txt, text=title, bg=th["card"], fg=th["text"],
                     font=(FONT_UI, 10, "bold"), anchor="w").pack(fill="x")
            tk.Label(txt, text=desc, bg=th["card"], fg=th["sub"], justify="left",
                     anchor="w", font=(FONT_UI, 9), wraplength=560).pack(fill="x")

        foot = tk.Frame(self, bg=th["bg"])
        foot.pack(fill="x", padx=24, pady=16)
        RoundButton(foot, "＋  添加第一个启动项", self._add_now, th, variant="primary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="left")
        RoundButton(foot, "知道了", self._close, th, variant="ghost",
                    font=(FONT_UI, 10), padx=16, pady=10).pack(side="right")

        self.bind("<Escape>", lambda e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._center(master)
        if first_run:
            tk.Label(self, text="以后可以在右上角「☰ → ❓ 使用引导」里再看",
                     bg=th["bg"], fg=th["sub"], font=(FONT_UI, 8)).pack(pady=(0, 12))

    def _center(self, master):
        try:
            self.update_idletasks()
            w, h = 660, self.winfo_height()
            self.geometry("%dx%d" % (w, h))
            self.update_idletasks()
            x = master.winfo_rootx() + (master.winfo_width() - w) // 2
            y = master.winfo_rooty() + (master.winfo_height() - h) // 3
            self.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        except Exception:
            pass

    def _dismiss(self):
        try:
            self.app.settings["guide_shown"] = True
            save_config(self.app.cfg)
        except Exception:
            pass

    def _add_now(self):
        self._dismiss()
        self._close()
        self.app.add_item()

    def _close(self):
        self._dismiss()
        try:
            self.destroy()
        except Exception:
            pass


def ask_text(master, app, title, prompt, initial=""):
    """统一风格的单行输入弹窗，返回字符串或 None"""
    th = app.theme
    dlg = tk.Toplevel(master)
    dlg.configure(bg=th["bg"])
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(master)
    dlg.grab_set()
    result = {"value": None}

    tk.Label(dlg, text=prompt, bg=th["bg"], fg=th["text"], font=(FONT_UI, 10),
             justify="left", wraplength=380).pack(anchor="w", padx=22, pady=(18, 8))
    ent = RoundEntry(dlg, th, font=(FONT_UI, 10), width=400, height=36)
    ent.pack(padx=22, fill="x")
    ent.set(initial)
    ent.focus_set()
    ent.select_all()

    def ok(_e=None):
        result["value"] = ent.value()
        dlg.destroy()

    def cancel(_e=None):
        result["value"] = None
        dlg.destroy()

    foot = tk.Frame(dlg, bg=th["bg"])
    foot.pack(fill="x", padx=22, pady=16)
    RoundButton(foot, "确定", ok, th, variant="primary", font=(FONT_UI, 10),
                padx=22, pady=9).pack(side="right")
    RoundButton(foot, "取消", cancel, th, variant="secondary", font=(FONT_UI, 10),
                padx=18, pady=9).pack(side="right", padx=(0, 10))

    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", cancel)
    dlg.update_idletasks()
    x = master.winfo_rootx() + (master.winfo_width() - dlg.winfo_width()) // 2
    y = master.winfo_rooty() + (master.winfo_height() - dlg.winfo_height()) // 3
    dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
    dlg.wait_window()
    return result["value"]


# ---------------------------------------------------------
# 快速新建：文件夹 / BAT / TXT
# ---------------------------------------------------------
class CreateFileDialog(tk.Toplevel):
    """在界面里右键就能新建，建完可以顺手加进启动器"""

    KINDS = {
        "folder": {"title": "新建文件夹", "ext": "", "default": "新建文件夹",
                   "label": "文件夹", "tip": "就是个普通文件夹，可以直接放脚本 / 快捷方式"},
        "bat": {"title": "新建 BAT 脚本", "ext": ".bat", "default": "新建脚本",
                "label": "批处理脚本",
                "tip": "会写入一个带 chcp 936 的 GBK 编码模板（中文不乱码，跑完停住等你看结果）"},
        "txt": {"title": "新建 TXT 文本", "ext": ".txt", "default": "新建文本",
                "label": "文本文件", "tip": "空文本文件，可以用它记备忘"},
    }

    def __init__(self, master, app, kind="folder"):
        super().__init__(master)
        self.app = app
        self.kind = kind if kind in self.KINDS else "folder"
        info = self.KINDS[self.kind]
        self.th = app.theme
        th = self.th

        self.configure(bg=th["bg"])
        self.title(info["title"])
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        head = tk.Frame(self, bg=th["bg"])
        head.pack(fill="x", padx=24, pady=(18, 4))
        tk.Label(head, text="➕  %s" % info["title"], bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 13, "bold")).pack(anchor="w")
        tk.Label(head, text=info["tip"], bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9),
                 justify="left", wraplength=460).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(self, bg=th["bg"])
        body.pack(fill="x", padx=24, pady=(10, 0))

        tk.Label(body, text="名称", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w")
        row1 = tk.Frame(body, bg=th["bg"])
        row1.pack(fill="x", pady=(4, 0))
        self.name_entry = RoundEntry(row1, th, font=(FONT_UI, 10), height=34,
                                     textvariable=tk.StringVar(value=info["default"]))
        self.name_entry.pack(side="left", fill="x", expand=True)
        if info["ext"]:
            tk.Label(row1, text=info["ext"], bg=th["bg"], fg=th["sub"],
                     font=(FONT_UI, 10)).pack(side="left", padx=(6, 0))

        tk.Label(body, text="保存位置", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w", pady=(12, 0))
        row2 = tk.Frame(body, bg=th["bg"])
        row2.pack(fill="x", pady=(4, 0))
        self.dir_entry = RoundEntry(row2, th, font=(FONT_UI, 10), height=34,
                                    textvariable=tk.StringVar(value=self._default_dir()))
        self.dir_entry.pack(side="left", fill="x", expand=True)
        make_button(row2, "选择…", self._pick_dir, th, variant="secondary",
                    font=(FONT_UI, 10), pad=(14, 8)).pack(side="left", padx=(8, 0))
        tk.Label(body, text="记住这个位置，下次新建直接用它", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 8)).pack(anchor="w", pady=(2, 0))

        self.add_var = tk.BooleanVar(value=True)
        self.open_var = tk.BooleanVar(value=bool(info["ext"]))
        opt = tk.Frame(body, bg=th["bg"])
        opt.pack(fill="x", pady=(14, 0))
        tk.Checkbutton(opt, text="创建后加入启动器", variable=self.add_var, bg=th["bg"],
                       fg=th["text"], selectcolor=th["card"], activebackground=th["bg"],
                       activeforeground=th["text"], font=(FONT_UI, 10), bd=0,
                       highlightthickness=0).pack(anchor="w")
        if info["ext"]:
            tk.Checkbutton(opt, text="创建后用默认程序打开编辑", variable=self.open_var,
                           bg=th["bg"], fg=th["text"], selectcolor=th["card"],
                           activebackground=th["bg"], activeforeground=th["text"],
                           font=(FONT_UI, 10), bd=0,
                           highlightthickness=0).pack(anchor="w")

        self.msg = tk.Label(self, text="", bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9),
                            anchor="w", justify="left", wraplength=460)
        self.msg.pack(fill="x", padx=24, pady=(8, 0))

        foot = tk.Frame(self, bg=th["bg"])
        foot.pack(fill="x", padx=24, pady=16)
        RoundButton(foot, "创建", self._create, th, variant="primary",
                    font=(FONT_UI, 10, "bold"), padx=24, pady=10).pack(side="right")
        RoundButton(foot, "取消", self._cancel, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="right", padx=(0, 10))

        self.bind("<Return>", lambda e: self._create())
        self.bind("<Escape>", lambda e: self._cancel())
        self.name_entry.focus_set()
        self.name_entry.select_all()
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def _default_dir(self):
        saved = self.app.settings.get("new_file_dir", "")
        if saved and os.path.isdir(saved):
            return saved
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        return desktop if os.path.isdir(desktop) else BASE_DIR

    def _pick_dir(self):
        d = self.app._native(filedialog.askdirectory, title="选择保存位置")
        if d:
            self.dir_entry.set(os.path.normpath(d))

    def _say(self, text, color_key="sub"):
        self.msg.config(text=text, fg=self.th[color_key])

    def _create(self):
        info = self.KINDS[self.kind]
        name = self.name_entry.value().strip()
        if not name:
            self._say("⚠ 起个名字吧", "warn")
            return
        if any(c in name for c in '\\/:*?"<>|'):
            self._say('⚠ 名字里不能有 \\ / : * ? " < > | 这些符号', "warn")
            return
        folder = self.dir_entry.value().strip().strip('"')
        if not folder or not os.path.isdir(folder):
            self._say("⚠ 保存位置不存在，点「选择…」换一个", "warn")
            return
        if info["ext"] and not name.lower().endswith(info["ext"]):
            name += info["ext"]
        target = os.path.join(folder, name)
        if os.path.exists(target):
            self._say("⚠ 已经存在同名文件：%s" % target, "warn")
            return
        try:
            if self.kind == "folder":
                os.makedirs(target)
            elif self.kind == "bat":
                # newline="" 很关键：不然 Windows 会把 \n 再转成 \r\n，变成 \r\r\n
                with open(target, "w", encoding="gbk", errors="ignore",
                          newline="") as fh:
                    fh.write(BAT_TEMPLATE)
            else:
                with open(target, "w", encoding="utf-8", newline="") as fh:
                    fh.write("")
        except Exception as exc:
            self._say("❌ 创建失败：%s" % exc, "err")
            return

        add_after = bool(self.add_var.get())
        open_after = bool(self.open_var.get()) and bool(info["ext"])
        self.app.settings["new_file_dir"] = folder
        save_config(self.app.cfg)
        self.destroy()
        if open_after:
            try:
                os.startfile(target)
            except Exception:
                pass
        if add_after:
            self.app.after(60, lambda: self.app.add_item(target))
        else:
            self.app.refresh()
            self.app.status.config(text="✅ 已创建：%s" % target,
                                   fg=self.app.theme["ok"])

    def _cancel(self):
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------
# 桌面 / 开始菜单批量导入
# ---------------------------------------------------------
def shortcut_roots():
    """要扫描的目录：(路径, 建议分组前缀)"""
    home = os.path.expanduser("~")
    public = os.environ.get("PUBLIC", r"C:\Users\Public")
    appdata = os.environ.get("APPDATA", "")
    programdata = os.environ.get("ProgramData", r"C:\ProgramData")
    cands = [
        (os.path.join(home, "Desktop"), "桌面"),
        (os.path.join(public, "Desktop"), "桌面"),
        (os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs"), "开始菜单"),
        (os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs"), "开始菜单"),
    ]
    return [(p, g) for p, g in cands if p and os.path.isdir(p)]


def scan_shortcuts():
    """扫出桌面 / 开始菜单里的快捷方式 -> [(名称, 路径, 建议分组)]"""
    found = []
    for root, prefix in shortcut_roots():
        for dirpath, _dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            sub = "" if rel in (".", "") else rel
            for fn in filenames:
                if not fn.lower().endswith((".lnk", ".url")):
                    continue
                name = os.path.splitext(fn)[0]
                if name.lower() in ("desktop", "internet explorer"):
                    continue
                group = prefix + ("/" + sub.replace("\\", "/") if sub else "")
                found.append((name, os.path.join(dirpath, fn), group))
    seen = set()
    uniq = []
    for name, path, group in found:
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((name, path, group))
    uniq.sort(key=lambda t: (t[2], t[0].lower()))
    return uniq


def resolve_shortcut(path):
    """解析 .lnk 的真实目标；带参数或 UWP 应用就返回 None（用 .lnk 本身更稳）"""
    if not path.lower().endswith(".lnk"):
        return None
    try:
        from win32com.client import Dispatch
        lnk = Dispatch("WScript.Shell").CreateShortcut(path)
        target = lnk.TargetPath
        args = (lnk.Arguments or "").strip()
        if args:
            return None
        if not target or not os.path.exists(target):
            return None
        if target.lower().endswith("explorer.exe"):
            return None
        return target
    except Exception:
        return None


class ImportDialog(tk.Toplevel):
    """勾选式批量导入"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.th = app.theme
        th = self.th
        self.rows = []
        self.vars = []

        self.configure(bg=th["bg"])
        self.title("从桌面 / 开始菜单导入")
        self.geometry("740x620")
        self.minsize(640, 480)
        self.transient(master)
        self.grab_set()

        head = tk.Frame(self, bg=th["bg"])
        head.pack(fill="x", padx=22, pady=(16, 4))
        tk.Label(head, text="📥  从桌面 / 开始菜单导入", bg=th["bg"],
                 fg=th["text"], font=(FONT_UI, 14, "bold")).pack(anchor="w")
        self.sub = tk.Label(head, text="正在扫描…", bg=th["bg"], fg=th["sub"],
                            font=(FONT_UI, 9), justify="left", wraplength=660)
        self.sub.pack(anchor="w", pady=(4, 0))

        bar = tk.Frame(self, bg=th["bg"])
        bar.pack(fill="x", padx=22, pady=(8, 6))
        self.search = RoundEntry(bar, th, placeholder="🔍  筛选名称…",
                                 font=(FONT_UI, 9), height=32, radius=10,
                                 width=260, padx=10)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind_entry("<KeyRelease>", lambda e: self._filter())
        self.only_new = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="只看没加过的", variable=self.only_new,
                       command=self._filter, bg=th["bg"], fg=th["text"],
                       selectcolor=th["card"], activebackground=th["bg"],
                       activeforeground=th["text"], font=(FONT_UI, 9), bd=0,
                       highlightthickness=0).pack(side="left", padx=(10, 0))
        make_button(bar, "全选", lambda: self._check_all(True), th,
                    variant="ghost", font=(FONT_UI, 9), pad=(10, 6)).pack(side="left",
                                                                         padx=(8, 0))
        make_button(bar, "全不选", lambda: self._check_all(False), th,
                    variant="ghost", font=(FONT_UI, 9), pad=(10, 6)).pack(side="left",
                                                                         padx=(2, 0))

        self.scroll = ScrollFrame(self, bg=th["bg"])
        self.scroll.pack(fill="both", expand=True, padx=(22, 16), pady=(0, 0))

        foot = tk.Frame(self, bg=th["bg"])
        foot.pack(fill="x", padx=22, pady=12)
        self.msg = tk.Label(foot, text="", bg=th["bg"], fg=th["sub"],
                            font=(FONT_UI, 9), anchor="w", justify="left",
                            wraplength=380)
        self.msg.pack(side="left", fill="x", expand=True)
        RoundButton(foot, "导入选中项", self._do_import, th, variant="primary",
                    font=(FONT_UI, 10, "bold"), padx=20, pady=10).pack(side="right")
        RoundButton(foot, "取消", self._cancel, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="right",
                                                                padx=(0, 10))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<MouseWheel>", lambda e: self.scroll.scroll(e))
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.after(60, self._scan)

    # ---------- 扫描 ----------
    def _scan(self):
        shortcuts = scan_shortcuts()
        existing = {os.path.normcase(i.get("path", "")) for i in self.app.cfg["items"]}
        self.entries = []
        for name, path, group in shortcuts:
            real = resolve_shortcut(path) or path
            already = os.path.normcase(real) in existing
            self.entries.append({"name": name, "path": real, "group": group,
                                 "already": already})
        new_count = len([e for e in self.entries if not e["already"]])
        self.sub.config(text="扫到 %d 个快捷方式，其中 %d 个还没加过"
                             "（分组按它们所在的文件夹自动分好，可导入后再改）"
                             % (len(self.entries), new_count))
        self._build_rows()
        self._filter()

    def _build_rows(self):
        for child in self.scroll.inner.winfo_children():
            child.destroy()
        self.rows = []
        self.vars = []
        th = self.th
        for idx, entry in enumerate(self.entries):
            row = tk.Frame(self.scroll.inner, bg=th["card"], highlightthickness=1,
                           highlightbackground=th["border"])
            row.pack(fill="x", pady=2, padx=2)
            var = tk.BooleanVar(value=not entry["already"])
            cb = tk.Checkbutton(row, variable=var, bg=th["card"],
                                activebackground=th["card"], bd=0,
                                highlightthickness=0, selectcolor=th["entry"])
            cb.pack(side="left", padx=(8, 4))
            txt = tk.Frame(row, bg=th["card"])
            txt.pack(side="left", fill="x", expand=True, pady=6)
            name = entry["name"]
            if entry["already"]:
                name += "   （已经在列表里）"
            tk.Label(txt, text=name, bg=th["card"],
                     fg=th["sub"] if entry["already"] else th["text"],
                     font=(FONT_UI, 10), anchor="w").pack(fill="x")
            tk.Label(txt, text="%s    ·    %s" % (entry["group"], entry["path"]),
                     bg=th["card"], fg=th["sub"], font=(FONT_UI, 8),
                     anchor="w").pack(fill="x")
            self.rows.append((row, entry))
            self.vars.append(var)

    def _filter(self):
        q = self.search.value().lower()
        only_new = bool(self.only_new.get())
        shown = 0
        for (row, entry), var in zip(self.rows, self.vars):
            ok = True
            if only_new and entry["already"]:
                ok = False
            if q and q not in entry["name"].lower() and q not in entry["path"].lower():
                ok = False
            if ok:
                row.pack(fill="x", pady=2, padx=2)
                shown += 1
            else:
                row.pack_forget()
        self.msg.config(text="当前显示 %d 项，勾选 %d 项"
                             % (shown, len(self._checked())))

    def _check_all(self, flag):
        for (row, entry), var in zip(self.rows, self.vars):
            if row.winfo_ismapped():
                var.set(flag)
        self._filter()

    def _checked(self):
        out = []
        for (row, entry), var in zip(self.rows, self.vars):
            if var.get():
                out.append(entry)
        return out

    # ---------- 导入 ----------
    def _do_import(self):
        picked = self._checked()
        if not picked:
            self.msg.config(text="⚠ 一个都没勾选", fg=self.th["warn"])
            return
        exist = {os.path.normcase(i.get("path", "")) for i in self.app.cfg["items"]}
        added = 0
        merged = 0
        for entry in picked:
            path = entry["path"]
            key = os.path.normcase(path)
            if key in exist:
                continue
            item_id = human_key(path + entry["name"])
            icon = extract_system_icon(path, icon_path_for(item_id))
            item = normalize_item({
                "id": item_id, "name": entry["name"], "path": path,
                "icon": icon, "group": entry["group"],
                "order": self.app._next_order(),
            })
            # 已经在列表里的同名项合并分组
            for old in self.app.cfg["items"]:
                if os.path.normcase(old.get("path", "")) == key:
                    old["group"] = entry["group"]
                    merged += 1
                    break
            else:
                self.app.cfg["items"].append(item)
                exist.add(key)
                added += 1
        save_config(self.app.cfg)
        self.app.refresh()
        self.app.status.config(text="✅ 导入了 %d 个项目" % added, fg=self.app.theme["ok"])
        try:
            self.destroy()
        except Exception:
            pass

    def _cancel(self):
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------
# 失效路径体检
# ---------------------------------------------------------
class HealthDialog(tk.Toplevel):
    """列出打不开的项目，可以就地在原地重新指定或删除"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.th = app.theme
        th = self.th
        self.rows = []
        self.vars = []
        self.broken = []

        self.configure(bg=th["bg"])
        self.title("体检：打不开的项目")
        self.geometry("700x520")
        self.transient(master)
        self.grab_set()

        head = tk.Frame(self, bg=th["bg"])
        head.pack(fill="x", padx=22, pady=(16, 4))
        tk.Label(head, text="🩺  体检结果", bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 14, "bold")).pack(anchor="w")
        self.sub = tk.Label(head, text="", bg=th["bg"], fg=th["sub"],
                            font=(FONT_UI, 9), justify="left", wraplength=640)
        self.sub.pack(anchor="w", pady=(4, 0))

        self.scroll = ScrollFrame(self, bg=th["bg"])
        self.scroll.pack(fill="both", expand=True, padx=(22, 16), pady=(10, 0))

        foot = tk.Frame(self, bg=th["bg"])
        foot.pack(fill="x", padx=22, pady=12)
        self.msg = tk.Label(foot, text="", bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9),
                            anchor="w", justify="left", wraplength=300)
        self.msg.pack(side="left", fill="x", expand=True)
        RoundButton(foot, "关闭", self._cancel, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="right")
        RoundButton(foot, "删除选中项", self._delete_selected, th, variant="ghost",
                    font=(FONT_UI, 9), padx=14, pady=10).pack(side="right",
                                                               padx=(0, 8))
        RoundButton(foot, "重新指定选中项", self._relocate_selected, th,
                    variant="primary", font=(FONT_UI, 10), padx=18, pady=10).pack(
            side="right", padx=(0, 8))

        self.bind("<Escape>", lambda e: self._cancel())
        self.bind("<MouseWheel>", lambda e: self.scroll.scroll(e))
        self.after(80, self._scan)
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def _scan(self):
        th = self.th
        self.broken = []
        for item in self.app.cfg["items"]:
            path = item.get("path", "")
            if not path or is_url(path):
                continue
            if path.startswith("shell:") or path.lower().endswith(".lnk"):
                if os.path.exists(path):
                    continue
            if not os.path.exists(path):
                self.broken.append(item)
        for child in self.scroll.inner.winfo_children():
            child.destroy()
        self.rows, self.vars = [], []
        for item in self.broken:
            row = tk.Frame(self.scroll.inner, bg=th["card"], highlightthickness=1,
                           highlightbackground=th["border"])
            row.pack(fill="x", pady=2, padx=2)
            var = tk.BooleanVar(value=True)
            tk.Checkbutton(row, variable=var, bg=th["card"], activebackground=th["card"],
                           bd=0, highlightthickness=0,
                           selectcolor=th["entry"]).pack(side="left", padx=(8, 4))
            txt = tk.Frame(row, bg=th["card"])
            txt.pack(side="left", fill="x", expand=True, pady=6)
            tk.Label(txt, text=item.get("name") or "(没名字)", bg=th["card"],
                     fg=th["text"], font=(FONT_UI, 10), anchor="w").pack(fill="x")
            tk.Label(txt, text="找不到：%s" % item.get("path", ""), bg=th["card"],
                     fg=th["err"], font=(FONT_UI, 8), anchor="w").pack(fill="x")
            self.rows.append(row)
            self.vars.append(var)
        if self.broken:
            self.sub.config(text="有 %d 个项目打不开了（文件被删 / 移动 / 改名，"
                                 "或者盘符变了）。勾选后可以重新指定位置，或直接删掉。"
                                 % len(self.broken))
        else:
            self.sub.config(text="全部正常 ✅ 没有打不开的项目。")
            self.msg.config(text="很干净", fg=th["ok"])

    def _selected(self):
        return [it for it, var in zip(self.broken, self.vars) if var.get()]

    def _relocate_selected(self):
        picked = self._selected()
        if not picked:
            self.msg.config(text="⚠ 先勾选要处理的项目", fg=self.th["warn"])
            return
        fixed = 0
        for item in picked:
            old = item.get("path", "")
            new = self.app._native(filedialog.askopenfilename,
                                   title="给「%s」重新指定文件" % item.get("name", ""),
                                   initialdir=os.path.dirname(old) or None)
            if not new:
                continue
            item["path"] = os.path.normpath(new)
            if not item.get("name"):
                item["name"] = os.path.splitext(os.path.basename(new))[0]
            item["icon"] = extract_system_icon(item["path"],
                                               icon_path_for(item["id"] or human_key(item["path"])))
            fixed += 1
        if fixed:
            save_config(self.app.cfg)
            self.app.refresh()
            self.msg.config(text="✅ 修好了 %d 个" % fixed, fg=self.th["ok"])
        self._scan()

    def _delete_selected(self):
        picked = self._selected()
        if not picked:
            self.msg.config(text="⚠ 先勾选要删除的项目", fg=self.th["warn"])
            return
        names = "、".join((i.get("name") or "?") for i in picked[:6])
        if not messagebox.askyesno("确认删除",
                                   "确定删除这 %d 个项目吗？\n%s" % (len(picked), names),
                                   parent=self):
            return
        self.app.cfg["items"] = [i for i in self.app.cfg["items"] if i not in picked]
        save_config(self.app.cfg)
        self.app.refresh()
        self.msg.config(text="已删除 %d 个" % len(picked), fg=self.th["ok"])
        self._scan()

    def _cancel(self):
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------
# 启动链 / 工作模式
# ---------------------------------------------------------
class ChainDialog(tk.Toplevel):
    """把几个项目串成一条链，一键按顺序启动（可设间隔）"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.th = app.theme
        th = self.th
        self.editing = None
        self.vars = []

        self.configure(bg=th["bg"])
        self.title("启动链 / 工作模式")
        self.geometry("680x560")
        self.minsize(620, 460)
        self.transient(master)
        self.grab_set()

        self.head = tk.Frame(self, bg=th["bg"])
        self.head.pack(fill="x", padx=22, pady=(16, 4))
        tk.Label(self.head, text="🔗  启动链 / 工作模式", bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 14, "bold")).pack(anchor="w")
        self.sub = tk.Label(self.head, text="", bg=th["bg"], fg=th["sub"],
                            font=(FONT_UI, 9), justify="left", wraplength=620)
        self.sub.pack(anchor="w", pady=(4, 0))

        self.body = tk.Frame(self, bg=th["bg"])
        self.body.pack(fill="both", expand=True, padx=22, pady=(8, 0))
        self.foot = tk.Frame(self, bg=th["bg"])
        self.foot.pack(fill="x", padx=22, pady=12)

        self.bind("<Escape>", lambda e: self._cancel())
        self._build_list()
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))

    def _clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    # ---------- 列表视图 ----------
    def _build_list(self):
        self.editing = None
        self._clear(self.body)
        self._clear(self.foot)
        th = self.th
        chains = self.app.cfg.setdefault("chains", [])
        self.sub.config(text="一条链 = 点一下依次启动好几个程序（比如「开工模式」："
                             "编辑器 → 终端 → 项目文件夹）。间隔可以自己设。")

        if not chains:
            tk.Label(self.body, text="还没有启动链，点下面「＋ 新建启动链」建一条吧",
                     bg=th["bg"], fg=th["sub"], font=(FONT_UI, 10)).pack(pady=40)
        else:
            scroll = ScrollFrame(self.body, bg=th["bg"])
            scroll.pack(fill="both", expand=True)
            self.bind("<MouseWheel>", lambda e: scroll.scroll(e))
            for idx, chain in enumerate(chains):
                row = tk.Frame(scroll.inner, bg=th["card"], highlightthickness=1,
                               highlightbackground=th["border"])
                row.pack(fill="x", pady=3, padx=2)
                txt = tk.Frame(row, bg=th["card"])
                txt.pack(side="left", fill="x", expand=True, padx=12, pady=8)
                tk.Label(txt, text=chain.get("name") or "(没名字)", bg=th["card"],
                         fg=th["text"], font=(FONT_UI, 11, "bold"),
                         anchor="w").pack(fill="x")
                tk.Label(txt, text="%d 个项目 · 间隔 %s 秒"
                                   % (len(chain.get("items", [])),
                                      chain.get("delay", 2)),
                         bg=th["card"], fg=th["sub"], font=(FONT_UI, 8),
                         anchor="w").pack(fill="x")
                btns = tk.Frame(row, bg=th["card"])
                btns.pack(side="right", padx=10)
                RoundButton(btns, "▶  运行", lambda c=chain: self.run_chain(c), th,
                            variant="primary", font=(FONT_UI, 9), padx=12,
                            pady=7).pack(side="left", padx=(0, 6))
                RoundButton(btns, "编辑", lambda c=chain: self._build_edit(c), th,
                            variant="secondary", font=(FONT_UI, 9), padx=10,
                            pady=7).pack(side="left", padx=(0, 6))
                RoundButton(btns, "删除", lambda c=chain: self._delete(c), th,
                            variant="ghost", font=(FONT_UI, 9), padx=10,
                            pady=7).pack(side="left")

        RoundButton(self.foot, "＋  新建启动链", lambda: self._build_edit(None), th,
                    variant="primary", font=(FONT_UI, 10), padx=18, pady=10).pack(
            side="left")
        RoundButton(self.foot, "关闭", self._cancel, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="right")

    # ---------- 编辑视图 ----------
    def _build_edit(self, chain):
        self.editing = chain
        self._clear(self.body)
        self._clear(self.foot)
        th = self.th
        self.sub.config(text="勾选要串起来的项目，设好间隔时间，保存后就能一键启动这一组。")

        tk.Label(self.body, text="名称", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w")
        self.name_entry = RoundEntry(self.body, th, font=(FONT_UI, 10), height=34,
                                     width=520,
                                     textvariable=tk.StringVar(
                                         value=(chain or {}).get("name", "开工模式")))
        self.name_entry.pack(fill="x", pady=(4, 0))

        tk.Label(self.body, text="每个之间等几秒（给上一个程序留出启动时间）",
                 bg=th["bg"], fg=th["sub"], font=(FONT_UI, 9)).pack(anchor="w",
                                                                   pady=(12, 0))
        self.delay_entry = RoundEntry(self.body, th, font=(FONT_UI, 10), height=34,
                                      width=120,
                                      textvariable=tk.StringVar(
                                          value=str((chain or {}).get("delay", 2))))
        self.delay_entry.pack(anchor="w", pady=(4, 0))

        tk.Label(self.body, text="包含哪些项目", bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(anchor="w", pady=(12, 0))
        scroll = ScrollFrame(self.body, bg=th["bg"])
        scroll.pack(fill="both", expand=True, pady=(4, 0))
        self.bind("<MouseWheel>", lambda e: scroll.scroll(e))

        chosen = set((chain or {}).get("items", []))
        self.vars = []
        if not self.app.cfg["items"]:
            tk.Label(scroll.inner, text="还没有任何启动项，先去主界面加几个吧",
                     bg=th["bg"], fg=th["sub"], font=(FONT_UI, 10)).pack(pady=20)
        for item in self.app.cfg["items"]:
            var = tk.BooleanVar(value=item.get("id") in chosen)
            self.vars.append((var, item))
            row = tk.Frame(scroll.inner, bg=th["card"])
            row.pack(fill="x", pady=1, padx=2)
            tk.Checkbutton(row, variable=var, bg=th["card"],
                           activebackground=th["card"], bd=0, highlightthickness=0,
                           selectcolor=th["entry"]).pack(side="left", padx=(8, 4))
            tk.Label(row, text=item.get("name") or "?", bg=th["card"], fg=th["text"],
                     font=(FONT_UI, 10), anchor="w").pack(side="left", pady=5)
            tk.Label(row, text=item.get("path", ""), bg=th["card"], fg=th["sub"],
                     font=(FONT_UI, 8), anchor="w").pack(side="left", padx=(10, 0))

        self.msg = tk.Label(self.foot, text="", bg=th["bg"], fg=th["sub"],
                            font=(FONT_UI, 9), anchor="w", justify="left",
                            wraplength=300)
        self.msg.pack(side="left", fill="x", expand=True)
        RoundButton(self.foot, "保存", self._save, th, variant="primary",
                    font=(FONT_UI, 10, "bold"), padx=20, pady=10).pack(side="right")
        RoundButton(self.foot, "取消", self._build_list, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="right",
                                                               padx=(0, 10))

    def _save(self):
        name = self.name_entry.value().strip() or "未命名启动链"
        try:
            delay = max(0.0, min(120.0, float(self.delay_entry.value() or 2)))
        except Exception:
            delay = 2.0
        ids = [item.get("id") for var, item in self.vars if var.get()]
        ids = [i for i in ids if i]
        if not ids:
            self.msg.config(text="⚠ 至少勾一个项目", fg=self.th["warn"])
            return
        chains = self.app.cfg.setdefault("chains", [])
        if self.editing is None:
            chains.append({"id": human_key(name + str(time.time())), "name": name,
                           "delay": delay, "items": ids})
        else:
            self.editing.update({"name": name, "delay": delay, "items": ids})
        save_config(self.app.cfg)
        self._build_list()
        self.app.status.config(text="✅ 启动链已保存：%s" % name, fg=self.app.theme["ok"])

    def _delete(self, chain):
        if not messagebox.askyesno("确认删除", "删除启动链「%s」？"
                                                 % chain.get("name", ""), parent=self):
            return
        self.app.cfg["chains"] = [c for c in self.app.cfg.get("chains", [])
                                  if c is not chain]
        save_config(self.app.cfg)
        self._build_list()

    # ---------- 运行 ----------
    def run_chain(self, chain):
        items = [self.app.item_by_id(i) for i in chain.get("items", [])]
        items = [i for i in items if i]
        if not items:
            messagebox.showwarning("启动链是空的",
                                   "这条链里的项目都不在了（可能被删了），编辑一下再试。",
                                   parent=self)
            return
        try:
            delay = max(0.0, float(chain.get("delay", 2) or 0))
        except Exception:
            delay = 2.0
        self.app.status.config(text="🚀 正在运行启动链「%s」（%d 项，间隔 %s 秒）"
                                    % (chain.get("name", ""), len(items), delay),
                               fg=self.app.theme["accent"])
        self._run_seq(items, delay, 0, chain)

    def _run_seq(self, items, delay, idx, chain):
        if not self.app.winfo_exists():
            return
        if idx >= len(items):
            self.app.status.config(text="✅ 启动链「%s」执行完毕" % chain.get("name", ""),
                                   fg=self.app.theme["ok"])
            return
        try:
            self.app.launch(items[idx])
        except Exception:
            pass
        self.app.after(max(200, int(delay * 1000)),
                       lambda: self._run_seq(items, delay, idx + 1, chain))

    def _cancel(self):
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------
# 主应用
# ---------------------------------------------------------
_Base = TkinterDnD.Tk if HAS_DND else tk.Tk

CARD_W = 158
CARD_H = 144
CARD_PAD = 8
ROW_H = 62


def wrap_lines(font, text, max_px, max_lines=2):
    """把文字按像素宽度折行，超出部分用 … 收尾"""
    text = str(text)
    lines = []
    cur = ""
    i = 0
    while i < len(text):
        if not cur or font.measure(cur + text[i]) <= max_px:
            cur += text[i]
            i += 1
        else:
            lines.append(cur)
            cur = ""
            if len(lines) >= max_lines:
                break
    if len(lines) < max_lines and cur:
        lines.append(cur)
        cur = ""
    if i < len(text):
        if lines:
            lines[-1] = elide(font, lines[-1] + text[i:], max_px)
        else:
            lines.append(elide(font, text, max_px))
    return lines[:max_lines] or [""]


def _item_glyph(item):
    path = item.get("path", "")
    if os.path.isdir(path):
        return "📁"
    if is_url(path):
        return "🌐"
    return EXT_ICON.get(os.path.splitext(path)[1].lower(), "📄")


class ItemCard(tk.Canvas):
    """网格视图里的圆角卡片（全部 Canvas 自绘，悬停时描边变主题色）"""

    def __init__(self, master, app, item, th):
        super().__init__(master, width=CARD_W, height=CARD_H, bg=th["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.item = item
        self.th = th
        self._hover = False
        self._photo = None
        self.selected = False
        self._name_font = tkfont.Font(family=FONT_UI, size=10, weight="bold")
        self._pill_font = tkfont.Font(family=FONT_UI, size=8)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", lambda e: app._drag_press(e, item, self))
        self.bind("<B1-Motion>", lambda e: app._drag_motion(e))
        self.bind("<ButtonRelease-1>", lambda e: app._drag_release(e, item))
        self.bind("<Button-3>", lambda e: app._popup(e, item))
        self.draw()

    def set_selected(self, flag):
        flag = bool(flag)
        if flag != self.selected:
            self.selected = flag
            self.draw()

    def _on_enter(self, _e=None):
        self._hover = True
        self.draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self.draw()

    def draw(self):
        th = self.th
        item = self.item
        self.delete("all")
        w, h = CARD_W, CARD_H
        fill = th["hover"] if (self._hover or self.selected) else th["card"]
        edge = th["accent"] if (self._hover or self.selected) else th["border"]
        draw_round(self, 1, 1, w - 1, h - 1, 14, fill, edge, 2 if self.selected else 1)

        photo = load_image(item.get("icon"), 46)
        if photo:
            self._photo = photo
            self.create_image(w / 2.0, 46, image=photo)
        else:
            self.create_text(w / 2.0, 46, text=_item_glyph(item),
                             font=(FONT_EMOJI, 22), fill=th["text"])

        lines = wrap_lines(self._name_font, item.get("name", "未命名") or "未命名",
                           w - 26, 2)
        self.create_text(w / 2.0, 86, text="\n".join(lines), fill=th["text"],
                         font=self._name_font, justify="center", anchor="n")

        group = item.get("group")
        if group:
            tw = self._pill_font.measure(group) + 18
            x1 = (w - tw) / 2.0
            draw_round(self, x1, 118, x1 + tw, 136, 9, th["card2"], th["border"])
            self.create_text(w / 2.0, 127, text=group, fill=th["accent"],
                             font=self._pill_font)
        if item.get("pinned"):
            self.create_text(w - 15, 15, text="📌", font=(FONT_EMOJI, 9))


class ItemRow(tk.Canvas):
    """列表视图里的一行"""

    def __init__(self, master, app, item, th):
        super().__init__(master, height=ROW_H, bg=th["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.app = app
        self.item = item
        self.th = th
        self._hover = False
        self._photo = None
        self.selected = False
        self._name_font = tkfont.Font(family=FONT_UI, size=11, weight="bold")
        self._sub_font = tkfont.Font(family=FONT_UI, size=8)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<ButtonPress-1>", lambda e: app._drag_press(e, item, self))
        self.bind("<B1-Motion>", lambda e: app._drag_motion(e))
        self.bind("<ButtonRelease-1>", lambda e: app._drag_release(e, item))
        self.bind("<Button-3>", lambda e: app._popup(e, item))
        self.draw()

    def set_selected(self, flag):
        flag = bool(flag)
        if flag != self.selected:
            self.selected = flag
            self.draw()

    def _on_enter(self, _e=None):
        self._hover = True
        self.draw()

    def _on_leave(self, _e=None):
        self._hover = False
        self.draw()

    def draw(self):
        th = self.th
        item = self.item
        self.delete("all")
        w = max(self.winfo_width(), 200)
        h = ROW_H
        fill = th["hover"] if (self._hover or self.selected) else th["card"]
        edge = th["accent"] if (self._hover or self.selected) else th["border"]
        draw_round(self, 1, 1, w - 1, h - 1, 12, fill, edge, 2 if self.selected else 1)

        photo = load_image(item.get("icon"), 30)
        if photo:
            self._photo = photo
            self.create_image(34, h / 2.0, image=photo)
        else:
            self.create_text(34, h / 2.0, text=_item_glyph(item),
                             font=(FONT_EMOJI, 14), fill=th["text"])

        pin = "📌 " if item.get("pinned") else ""
        name = elide(self._name_font, pin + (item.get("name") or "未命名"), w - 260)
        self.create_text(58, 22, text=name, fill=th["text"],
                         font=self._name_font, anchor="w")

        extra = []
        if item.get("group"):
            extra.append("#%s" % item["group"])
        if item.get("run_count", 0):
            extra.append("启动 %d 次" % item["run_count"])
        if item.get("console") == CONSOLE_HIDE:
            extra.append("无窗口")
        elif item.get("console") == CONSOLE_SHOW:
            extra.append("显示控制台")
        sub = item.get("path", "")
        if extra:
            sub = "%s    ·    %s" % (sub, "  ".join(extra))
        self.create_text(58, 41, text=elide(self._sub_font, sub, w - 130),
                         fill=th["sub"], font=self._sub_font, anchor="w")
        self.create_text(w - 22, h / 2.0, text="▶", fill=th["sub"],
                         font=(FONT_UI, 9))


class LauncherApp(_Base):

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.settings = self.cfg["settings"]
        if not os.path.exists(CONFIG_FILE):
            save_config(self.cfg)   # 首次运行就落盘，方便用户找到配置文件
        self.theme_name = self.settings.get("theme", "dark")
        self.theme = THEMES.get(self.theme_name, THEMES["dark"])
        self.view = self.settings.get("view", "grid")

        self.filter_text = ""
        self.filter_group = ""
        self.cols = 5
        self.photos = []
        self.tray_icon = None
        self._refresh_job = None
        self.sel_index = 0
        self._shown = []
        self._widgets = []
        self._drag = None
        self._wake_q = queue.Queue()
        self._group_labels = {}

        self.title(APP_NAME)
        self.configure(bg=self.theme["bg"])
        self._apply_window_geometry()
        self.minsize(760, 480)
        self._set_app_icon()

        self._apply_ttk_style()
        self._build_topbar()
        self._build_canvas()
        self._build_statusbar()
        self._build_menu()

        if HAS_DND:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass

        if self.settings.get("always_on_top", False):
            self.attributes("-topmost", True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda e: self._clear_filter())
        self.bind("<Control-f>", lambda e: self.search.focus_set())
        self.bind("<F5>", lambda e: self.refresh())
        self.bind("<Up>", lambda e: self._move_sel(-1))
        self.bind("<Down>", lambda e: self._move_sel(1))
        self.bind("<Return>", self._launch_selected)
        self.bind("<KP_Enter>", self._launch_selected)
        self.bind("<Home>", lambda e: self._select_first())
        self.bind("<End>", lambda e: self._select_last())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<FocusOut>", self._on_focus_out, add="+")
        self.report_callback_exception = self._on_tk_error

        self.after(60, self.refresh)
        self._setup_tray()
        self.ipc = WakeServer(self._wake_q)
        self.after(200, self._poll_events)
        self.after(4000, self._poll_system_theme)
        if not self.settings.get("guide_shown", False):
            self.after(450, lambda: self._show_guide(first_run=True))
        # 「启动时隐藏窗口」：配合开机自启，安安静静缩在托盘里
        if self.settings.get("start_hidden", False) and HAS_PYSTRAY \
                and self.settings.get("tray_enabled", True):
            self.after(300, self.withdraw)

    # ---------------- 窗口 / 图标 ----------------
    def _apply_window_geometry(self):
        """恢复上次窗口大小位置，并确保窗口不会跑到屏幕外"""
        geo = str(self.settings.get("geometry", "980x620"))
        size, pos = geo, None
        for i in range(1, len(geo)):
            if geo[i] in "+-" and "x" in geo[:i]:
                size, pos = geo[:i], geo[i:]
                break
        try:
            w, h = size.lower().split("x")
            w, h = int(w), int(h)
        except Exception:
            w, h = 980, 620
        w = max(720, min(w, self.winfo_screenwidth()))
        h = max(460, min(h, self.winfo_screenheight()))
        self.geometry("%dx%d" % (w, h))
        if pos:
            try:
                nums = pos.replace("+", " +").replace("-", " -").split()
                x = self.winfo_screenwidth() // 2 - w // 2
                y = self.winfo_screenheight() // 2 - h // 3
                if len(nums) >= 2:
                    x, y = int(nums[0]), int(nums[1])
                x = max(-20, min(x, self.winfo_screenwidth() - 120))
                y = max(0, min(y, self.winfo_screenheight() - 80))
                self.geometry("%dx%d+%d+%d" % (w, h, x, y))
            except Exception:
                pass

    def _set_app_icon(self):
        try:
            png = os.path.join(ICON_DIR, "_app.png")
            ico = os.path.join(ICON_DIR, "_app.ico")
            if not os.path.exists(png):
                make_app_icon(png_path=png, ico_path=ico if not os.path.exists(ico) else None)
            if os.path.exists(ico):
                try:
                    self.iconbitmap(ico)
                except Exception:
                    pass
            photo = load_image(png, 64)
            if photo:
                self.photos.append(photo)
                try:
                    self.iconphoto(True, photo)
                except Exception:
                    pass
        except Exception:
            pass

    def _on_tk_error(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        _write_error_log(text)
        # 兜底自救：万一某个窗口是「建到一半炸了」，它可能还攥着 grab，
        # 那会让整个界面点不动、看起来像卡死。先松开再说。
        try:
            self._release_stuck_grab()
        except Exception:
            pass
        try:
            messagebox.showerror("出错了", "%s\n\n详情已写入 launcher_error.log" % val)
        except Exception:
            pass

    def _release_stuck_grab(self, force=False):
        """松开 grab，并清掉没建完的对话框（force=True 时连显示出来的一起清）"""
        try:
            self.grab_release()
        except Exception:
            pass
        for w in list(self.winfo_children()):
            try:
                if not isinstance(w, tk.Toplevel) or not w.winfo_exists():
                    continue
                if not force and w.winfo_viewable():
                    continue
                w.grab_release()
                w.destroy()
            except Exception:
                pass

    def _dialog_failed(self, exc, val, tb):
        """某个对话框建到一半失败：把半成品窗口和 grab 清掉，否则界面会像卡死"""
        try:
            self._release_stuck_grab(force=True)
        except Exception:
            pass
        self._busy = 0
        self._on_tk_error(exc, val, tb)

    # ---------------- UI 构建 ----------------
    def _build_topbar(self):
        th = self.theme

        self.top = tk.Frame(self, bg=th["bg"])
        self.top.pack(fill="x", padx=20, pady=(16, 2))

        brand = tk.Frame(self.top, bg=th["bg"])
        brand.pack(side="left")
        tk.Label(brand, text="🚀  %s" % APP_NAME, bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 16, "bold")).pack(side="left")
        tk.Label(brand, text="v%s" % APP_VERSION, bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 8)).pack(side="left", padx=(6, 0), pady=(6, 0))

        self.add_btn = RoundButton(
            self.top, "＋  添加", self._add_menu, th, variant="primary",
            font=(FONT_UI, 10, "bold"), padx=18, pady=9,
            tooltip="添加程序 / 脚本 / 文件夹 / 网址（也可以直接把文件拖进来）")
        self.add_btn.pack(side="right")

        self.theme_btn = RoundButton(
            self.top, self._theme_glyph(),
            self._toggle_theme, th, variant="ghost", font=(FONT_UI, 11),
            padx=11, pady=9,
            tooltip="切换深色 / 浅色（菜单里还能选「跟随系统」）")
        self.theme_btn.pack(side="right", padx=(6, 0))

        self.view_btn = RoundButton(
            self.top, "▤" if self.view == "grid" else "▦", self._toggle_view, th,
            variant="ghost", font=(FONT_UI, 11), padx=11, pady=9,
            tooltip="切换 网格 / 列表 视图")
        self.view_btn.pack(side="right", padx=(6, 0))

        self.menu_btn = RoundButton(
            self.top, "☰", self._show_menu, th, variant="ghost",
            font=(FONT_UI, 11), padx=11, pady=9, tooltip="更多设置")
        self.menu_btn.pack(side="right", padx=(6, 0))

        # 第二行：搜索 + 分组
        bar2 = tk.Frame(self, bg=th["bg"])
        bar2.pack(fill="x", padx=20, pady=(12, 12))

        self.search = RoundEntry(bar2, th, placeholder="🔍  搜索名称或路径…",
                                 font=(FONT_UI, 10), height=36, radius=11,
                                 width=320, padx=12)
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind_entry("<KeyRelease>", self._on_search)
        # 在搜索框里也能用 ↑↓ 选、Enter 启动（返回 break 挡掉输入框自带的光标移动）
        self.search.bind_entry("<Up>", lambda e: self._move_sel(-1))
        self.search.bind_entry("<Down>", lambda e: self._move_sel(1))
        self.search.bind_entry("<Return>", self._launch_selected)
        self.search.bind_entry("<KP_Enter>", self._launch_selected)

        self.group_cb = ttk.Combobox(bar2, values=["全部分组"], width=12,
                                     state="readonly", font=(FONT_UI, 9))
        self.group_cb.set("全部分组")
        self.group_cb.pack(side="left", padx=(10, 0), ipady=5)
        self.group_cb.bind("<<ComboboxSelected>>", self._on_group)


        tk.Frame(self, bg=th["border"], height=1).pack(fill="x", padx=20)

    def _build_canvas(self):
        th = self.theme
        wrap = tk.Frame(self, bg=th["bg"])
        wrap.pack(fill="both", expand=True, padx=(12, 6), pady=(10, 0))

        self.canvas = tk.Canvas(wrap, bg=th["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)

        self.body = tk.Frame(self.canvas, bg=th["bg"])
        self.body_win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        # 空白处右键 = 快速新建 / 添加（卡片自己有右键菜单，不会冲突）
        self.canvas.bind("<Button-3>", self._blank_menu)
        self.body.bind("<Button-3>", self._blank_menu)

    def _on_scroll_set(self, first, last):
        """内容不多时自动藏起滚动条，界面更干净"""
        try:
            if float(first) <= 0.0 and float(last) >= 1.0:
                self.scroll.pack_forget()
            elif not self.scroll.winfo_ismapped():
                self.scroll.pack(side="right", fill="y", padx=(2, 0))
        except Exception:
            pass
        self.scroll.set(first, last)

    def _build_statusbar(self):
        th = self.theme
        bar = tk.Frame(self, bg=th["bg"])
        bar.pack(fill="x", padx=20, pady=(6, 12))
        self.status = tk.Label(bar, bg=th["bg"], fg=th["sub"],
                               font=(FONT_UI, 9), anchor="w")
        self.status.pack(side="left")
        self.hint = tk.Label(bar, bg=th["bg"], fg=th["sub"],
                             font=(FONT_UI, 9), anchor="e")
        self.hint.pack(side="right")
        self._update_hint()

    def _update_hint(self):
        try:
            self.hint.config(text="↑↓ 选择 · Enter 启动 · 右键更多 · Ctrl+F 搜索")
        except Exception:
            pass

    def _build_menu(self):
        th = self.theme
        self.menu = tk.Menu(self, tearoff=0, bg=th["card"], fg=th["text"],
                            activebackground=th["accent"], activeforeground=th["accent_fg"],
                            bd=0, activeborderwidth=0)
        self.autostart_var = tk.BooleanVar(value=is_autostart())
        self.topmost_var = tk.BooleanVar(value=self.settings.get("always_on_top", False))
        self.tray_var = tk.BooleanVar(value=self.settings.get("tray_enabled", True))
        self.menu.add_command(label="❓  使用引导 / 怎么看", command=self._show_guide)
        self.menu.add_separator()
        self.menu.add_checkbutton(label="开机自动启动", variable=self.autostart_var,
                                  command=self._toggle_autostart)
        self.menu.add_checkbutton(label="窗口始终置顶", variable=self.topmost_var,
                                  command=self._toggle_topmost)
        self.menu.add_checkbutton(label="启用托盘（关闭时最小化到右下角）",
                                  variable=self.tray_var, command=self._toggle_tray)
        self.start_hidden_var = tk.BooleanVar(value=self.settings.get("start_hidden", False))
        self.blur_var = tk.BooleanVar(value=self.settings.get("hide_on_blur", False))
        self.sort_var = tk.StringVar(value=self.settings.get("sort_mode", "name"))
        self.menu.add_checkbutton(label="启动时隐藏窗口（只留托盘）",
                                  variable=self.start_hidden_var,
                                  command=self._toggle_start_hidden)
        self.menu.add_checkbutton(label="点到别处自动收起（像 Spotlight）",
                                  variable=self.blur_var, command=self._toggle_blur)
        sort_menu = tk.Menu(self.menu, tearoff=0, bg=th["card"], fg=th["text"],
                            activebackground=th["accent"],
                            activeforeground=th["accent_fg"], bd=0,
                            activeborderwidth=0)
        sort_menu.add_radiobutton(label="按名称排序（默认）", variable=self.sort_var,
                                  value="name", command=self._change_sort)
        sort_menu.add_radiobutton(label="常用优先（按启动次数）", variable=self.sort_var,
                                  value="frequent", command=self._change_sort)
        sort_menu.add_separator()
        sort_menu.add_radiobutton(label="手动排序（拖拽卡片调整）", variable=self.sort_var,
                                  value="manual", command=self._change_sort)
        self.menu.add_cascade(label="排序方式", menu=sort_menu)

        self.theme_var = tk.StringVar(value=self.settings.get("theme_mode", "dark"))
        theme_menu = tk.Menu(self.menu, tearoff=0, bg=th["card"], fg=th["text"],
                             activebackground=th["accent"],
                             activeforeground=th["accent_fg"], bd=0,
                             activeborderwidth=0)
        for label, value in (("深色", "dark"), ("浅色", "light"),
                             ("跟随系统", "auto")):
            theme_menu.add_radiobutton(
                label=label, variable=self.theme_var, value=value,
                command=lambda v=value: self._apply_theme_mode(v))
        self.menu.add_cascade(label="主题", menu=theme_menu)
        self.menu.add_separator()
        self.menu.add_command(label="📥  从桌面 / 开始菜单导入…", command=self._import_shortcuts)
        self.menu.add_command(label="🔗  启动链 / 工作模式…", command=self._chains_dialog)
        self.menu.add_command(label="🩺  体检：找出打不开的项目", command=self._health_check)
        self.shell_var = tk.BooleanVar(
            value=bool(self.settings.get("shell_menu", shell_menu_registered())))
        self.menu.add_checkbutton(label="资源管理器右键「添加到启动器」",
                                  variable=self.shell_var,
                                  command=self._toggle_shell_menu)
        self.menu.add_separator()
        self.menu.add_command(label="刷新列表", command=self.refresh)
        self.menu.add_command(label="导入配置…", command=self._import_config)
        self.menu.add_command(label="导出配置…", command=self._export_config)
        self.menu.add_command(label="打开图标缓存目录", command=lambda: self._open_dir(ICON_DIR))
        self.menu.add_command(label="打开配置所在目录", command=lambda: self._open_dir(BASE_DIR))
        self.menu.add_separator()
        self.menu.add_command(label="关于", command=self._about)
        self.menu.add_command(label="退出", command=self._quit)

    # ---------------- 搜索 / 过滤 ----------------
    def _on_search(self, event=None):
        self.filter_text = self.search.value().lower()
        self.refresh()

    def _clear_filter(self):
        self.search.clear()
        self.filter_text = ""
        self.refresh()

    def _on_group(self, event=None):
        label = self.group_cb.get()
        self.filter_group = getattr(self, "_group_labels", {}).get(label, "")
        self.refresh()

    def _refresh_groups(self):
        """分组下拉：多层分组按层级缩进显示，选上级会带出下级"""
        groups = all_groups(self.cfg["items"])
        labels = ["全部分组"]
        self._group_labels = {"全部分组": ""}
        for g in groups:
            parts = split_group(g)
            label = "    " * (len(parts) - 1) + ("└ " if len(parts) > 1 else "") + parts[-1]
            labels.append(label)
            self._group_labels[label] = g
        self.group_cb["values"] = labels
        if self.filter_group and self.filter_group not in groups:
            self.filter_group = ""
        for label, path in self._group_labels.items():
            if path == self.filter_group:
                self.group_cb.set(label)
                return
        self.group_cb.set("全部分组")

    # ---------------- 渲染 ----------------
    def _on_canvas_resize(self, event):
        self.canvas.itemconfig(self.body_win, width=event.width)
        if self.view == "grid":
            cols = max(1, (event.width - 4) // (CARD_W + CARD_PAD * 2))
            if cols != self.cols:
                self.cols = cols
                self.refresh()

    def _on_wheel(self, event):
        try:
            w = event.widget
            # 绑定在根窗口上时，弹出窗口（添加/引导）里的滚轮不该滚动主列表
            if w.winfo_toplevel() is not self:
                return
            if w.winfo_class() in ("Listbox", "TCombobox"):
                return
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except Exception:
            pass

    def _visible_items(self):
        items = list(self.cfg["items"])
        if self.filter_group:
            items = [i for i in items
                     if group_in(i.get("group", ""), self.filter_group)]
        if self.filter_text:
            items = [i for i in items if match_query(i, self.filter_text)]
        mode = self.settings.get("sort_mode", "name")
        if mode == "manual":
            # 手动排序：完全按 order（拖拽和置顶改的都是它）
            items.sort(key=lambda i: (float(i.get("order", 0) or 0),
                                      str(i.get("name", "")).lower()))
        elif mode == "frequent":
            items.sort(key=lambda i: (not i.get("pinned", False),
                                      -int(i.get("run_count", 0) or 0),
                                      str(i.get("name", "")).lower()))
        else:
            items.sort(key=lambda i: (not i.get("pinned", False),
                                      str(i.get("name", "")).lower()))
        return items

    def _next_order(self):
        orders = [float(i.get("order", 0) or 0) for i in self.cfg["items"]]
        return (max(orders) + 1) if orders else 1.0

    def _normalize_orders(self):
        """老配置没有 order 字段：按现有顺序补一遍"""
        changed = False
        for idx, it in enumerate(self.cfg["items"]):
            if it.get("order") in (None, ""):
                it["order"] = float(idx + 1)
                changed = True
        return changed

    # ---------------- 键盘选择 ----------------
    def _select(self, index, scroll=True):
        if not self._shown:
            self.sel_index = 0
            return
        index = max(0, min(index, len(self._shown) - 1))
        changed = (index != self.sel_index)
        self.sel_index = index
        if changed:
            self._paint_selection()
        if scroll:
            self._ensure_visible(index)
        if not changed:
            self._paint_selection()

    def _paint_selection(self, announce=True):
        for i, w in enumerate(self._widgets):
            try:
                w.set_selected(i == self.sel_index)
            except Exception:
                pass
        if announce and self._shown and 0 <= self.sel_index < len(self._shown):
            it = self._shown[self.sel_index]
            self.status.config(text="已选中「%s」——回车启动，↑↓ 换一个"
                                    % it.get("name", ""), fg=self.theme["text"])

    def _ensure_visible(self, index):
        try:
            w = self._widgets[index]
        except Exception:
            return
        try:
            total = max(self.body.winfo_height(), 1)
            ch = max(self.canvas.winfo_height(), 1)
            top = self.canvas.canvasy(0)
            y = w.winfo_y()
            h = w.winfo_height()
            if y < top:
                self.canvas.yview_moveto(max(0.0, float(y) / total))
            elif y + h > top + ch:
                self.canvas.yview_moveto(max(0.0, float(y + h - ch) / total))
        except Exception:
            pass

    def _move_sel(self, delta):
        self._select(self.sel_index + delta)
        return "break"

    def _select_first(self):
        self._select(0)
        return "break"

    def _select_last(self):
        self._select(len(self._shown) - 1)
        return "break"

    def _launch_selected(self, _e=None):
        if self._shown:
            idx = max(0, min(self.sel_index, len(self._shown) - 1))
            item = self._shown[idx]
            self.launch(item)
            self._select(idx, scroll=False)
        else:
            self.status.config(text="还没有启动项，点「＋ 添加」加一个吧",
                               fg=self.theme["warn"])
        return "break"

    def refresh(self):
        """请求重建列表。

        销毁控件不能在几何事件（<Configure>）的派发过程中同步做：
        Tk 9.0 下「设置窗口图标 + 在 Configure 回调里销毁控件」会触发
        access violation。这里统一延迟到空闲时执行，并自动合并多次请求。
        """
        if self._refresh_job is not None:
            return
        try:
            self._refresh_job = self.after_idle(self._do_refresh)
        except Exception:
            self._refresh_job = None
            self._do_refresh()

    def _do_refresh(self):
        self._refresh_job = None
        try:
            if not self.winfo_exists():
                return
            body = getattr(self, "body", None)
            if body is None or not body.winfo_exists():
                return
        except Exception:
            return

        for w in self.body.winfo_children():
            w.destroy()
        self.photos.clear()
        self._widgets = []
        self._refresh_groups()

        items = self._visible_items()
        th = self.theme

        if not items:
            self._shown = []
            self.sel_index = 0
            self._build_empty_state(th)
            total = len(self.cfg["items"])
            self.status.config(text="共 %d 项" % total, fg=th["sub"])
            return

        widgets = []
        if self.view == "grid":
            for idx, item in enumerate(items):
                card = self._make_grid_card(item, th)
                card.grid(row=idx // self.cols, column=idx % self.cols,
                          padx=CARD_PAD, pady=CARD_PAD, sticky="nw")
                widgets.append(card)
        else:
            for item in items:
                row = self._make_list_row(item, th)
                row.pack(fill="x", padx=4, pady=3)
                widgets.append(row)

        self._shown = items
        self._widgets = widgets
        self.sel_index = 0
        total = len(self.cfg["items"])
        self.status.config(text="显示 %d / 共 %d 项" % (len(items), total), fg=th["sub"])
        self._paint_selection(announce=False)

    def _build_empty_state(self, th):
        box = tk.Frame(self.body, bg=th["bg"])
        box.pack(pady=(60, 20))
        tk.Label(box, text="🗂", font=(FONT_EMOJI, 40), bg=th["bg"],
                 fg=th["sub"]).pack()
        text = "还没有启动项 —— 点右上角「＋ 添加」，或把文件 / 文件夹直接拖进来"
        sub = "第一次用？点「❓ 使用引导」看看，30 秒就能上手"
        if self.filter_text or self.filter_group:
            text = "没有匹配的项目，换个关键词试试"
            sub = "按 Esc 可以清空搜索条件"
        tk.Label(box, text=text, bg=th["bg"], fg=th["text"],
                 font=(FONT_UI, 12)).pack(pady=(10, 4))
        tk.Label(box, text=sub, bg=th["bg"], fg=th["sub"],
                 font=(FONT_UI, 9)).pack(pady=(0, 16))
        row = tk.Frame(box, bg=th["bg"])
        row.pack()
        RoundButton(row, "＋  添加启动项", self._add_menu, th, variant="primary",
                    font=(FONT_UI, 10), padx=20, pady=10).pack(side="left")
        RoundButton(row, "❓  使用引导", self._show_guide, th, variant="secondary",
                    font=(FONT_UI, 10), padx=18, pady=10).pack(side="left", padx=(10, 0))

    # ---------- 卡片 / 列表 ----------
    def _make_grid_card(self, item, th):
        return ItemCard(self.body, self, item, th)

    def _make_list_row(self, item, th):
        return ItemRow(self.body, self, item, th)

    # ---------------- 拖拽排序 ----------------
    def _drag_press(self, event, item, widget):
        self._drag = {"item": item, "widget": widget, "x": event.x_root,
                      "y": event.y_root, "active": False, "ghost": None,
                      "target": None}

    def _drag_ready(self):
        """筛选状态下拖拽会乱套，直接拦住并说明"""
        if self.filter_text or self.filter_group:
            self.status.config(text="⚠ 正在按搜索/分组筛选，先清空筛选再拖拽排序",
                               fg=self.theme["warn"])
            self._drag = None
            return False
        if len(self._widgets) < 2:
            self._drag = None
            return False
        return True

    def _drag_motion(self, event):
        d = getattr(self, "_drag", None)
        if not d:
            return
        if not d["active"]:
            if abs(event.x_root - d["x"]) < 8 and abs(event.y_root - d["y"]) < 8:
                return
            if not self._drag_ready():
                return
            d["active"] = True
            self._materialize_order()
            d["ghost"] = self._make_ghost(d["item"])
        self._drag_update(event.x_root, event.y_root)

    def _materialize_order(self):
        """把当前显示顺序写进 order，切到手动排序时画面不会跳"""
        for idx, it in enumerate(self._shown):
            it["order"] = float(idx + 1)

    def _make_ghost(self, item):
        try:
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-topmost", True)
            th = self.theme
            box = tk.Frame(ghost, bg=th["accent"])
            box.pack()
            tk.Label(box, text="↕  %s" % (item.get("name") or ""), bg=th["card"],
                     fg=th["text"], font=(FONT_UI, 10, "bold"), padx=12,
                     pady=6).pack(padx=1, pady=1)
            return ghost
        except Exception:
            return None

    def _drag_update(self, x_root, y_root):
        d = self._drag
        widgets = self._widgets
        target = None
        for i, w in enumerate(widgets):
            try:
                wx, wy = w.winfo_rootx(), w.winfo_rooty()
                ww, wh = w.winfo_width(), w.winfo_height()
            except Exception:
                continue
            if wx <= x_root <= wx + ww and wy <= y_root <= wy + wh:
                if self.view == "grid":
                    target = i if x_root < wx + ww / 2.0 else i + 1
                else:
                    target = i if y_root < wy + wh / 2.0 else i + 1
                break
        if target is None and widgets:
            # 落在卡片之间的空隙 / 行尾 / 空白区
            try:
                if self.view == "list":
                    target = len(widgets) if y_root > widgets[-1].winfo_rooty() else 0
                else:
                    rows = {}
                    for i, w in enumerate(widgets):
                        rows.setdefault(w.winfo_rooty() // 8, []).append((i, w))
                    picked = None
                    for key in sorted(rows):
                        group = rows[key]
                        wy = group[0][1].winfo_rooty()
                        if wy <= y_root <= wy + group[0][1].winfo_height():
                            picked = group
                            break
                    if picked is None:
                        target = len(widgets) if y_root > widgets[-1].winfo_rooty() else 0
                    else:
                        target = picked[-1][0] + 1
                        for i, w in picked:
                            if x_root < w.winfo_rootx() + w.winfo_width() / 2.0:
                                target = i
                                break
            except Exception:
                target = None
        d["target"] = target
        if d.get("ghost") is not None:
            try:
                d["ghost"].geometry("+%d+%d" % (x_root + 14, y_root + 12))
            except Exception:
                pass
        self._draw_drop_marker(target)

    def _draw_drop_marker(self, index):
        try:
            self.canvas.delete("dropmarker")
        except Exception:
            return
        if index is None or not self._widgets:
            return
        try:
            th = self.theme
            if self.view == "grid":
                if index >= len(self._widgets):
                    ref = self._widgets[-1]
                    x = ref.winfo_x() + ref.winfo_width() + CARD_PAD
                else:
                    ref = self._widgets[index]
                    x = max(1, ref.winfo_x() - CARD_PAD / 2.0)
                self.canvas.create_line(x, ref.winfo_y(), x,
                                        ref.winfo_y() + ref.winfo_height(),
                                        fill=th["accent"], width=3, tags="dropmarker")
            else:
                if index >= len(self._widgets):
                    y = self._widgets[-1].winfo_y() + self._widgets[-1].winfo_height() + 2
                else:
                    y = max(1, self._widgets[index].winfo_y() - 2)
                width = max(self.canvas.winfo_width(), 120)
                self.canvas.create_line(6, y, width - 6, y, fill=th["accent"],
                                        width=3, tags="dropmarker")
        except Exception:
            pass

    def _drag_cleanup(self, d):
        try:
            self.canvas.delete("dropmarker")
        except Exception:
            pass
        if d.get("ghost") is not None:
            try:
                d["ghost"].destroy()
            except Exception:
                pass
            d["ghost"] = None

    def _drag_release(self, event, item):
        d = getattr(self, "_drag", None)
        self._drag = None
        if not d:
            return
        if not d.get("active"):
            self.launch(item)              # 没拖动 = 普通单击
            return
        self._drag_finish(d)

    def _drag_finish(self, d):
        target = d.get("target")
        src = d["item"]
        self._drag_cleanup(d)
        order = list(self._shown)
        if src not in order or target is None:
            self.refresh()
            return
        old = order.index(src)
        if target > old:
            target -= 1
        target = max(0, min(target, len(order) - 1))
        if target == old:
            self.refresh()
            self.status.config(text="顺序没变", fg=self.theme["sub"])
            return
        order.pop(old)
        order.insert(target, src)
        for idx, it in enumerate(order):
            it["order"] = float(idx + 1)
        self.settings["sort_mode"] = "manual"
        try:
            self.sort_var.set("manual")
        except Exception:
            pass
        self._after_items_changed()
        self.status.config(text="✅ 顺序已调整（排序方式已切成「手动排序」）",
                           fg=self.theme["ok"])

    def _popup(self, event, item):
        th = self.theme
        m = tk.Menu(self, tearoff=0, bg=th["card"], fg=th["text"],
                    activebackground=th["hover"], activeforeground=th["text"])
        m.add_command(label="▶  启动", command=lambda: self.launch(item))
        m.add_command(label="🖥  显示窗口启动", command=lambda: self.launch(item, console=CONSOLE_SHOW))
        m.add_command(label="🙈  无窗口启动", command=lambda: self.launch(item, console=CONSOLE_HIDE))
        m.add_command(label="🛡  以管理员运行", command=lambda: self.launch(item, admin=True))
        m.add_separator()
        m.add_command(label="📂  打开文件所在位置", command=lambda: self._reveal(item))
        m.add_command(label="📋  复制路径", command=lambda: self._copy(item))
        m.add_command(label="✏️  编辑…", command=lambda: self._edit(item))
        m.add_command(label="📌  置顶 / 取消置顶", command=lambda: self._toggle_pin(item))
        m.add_separator()
        m.add_command(label="🗑  删除", command=lambda: self._delete(item))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ---------------- 操作 ----------------
    def _new_file(self, kind):
        try:
            CreateFileDialog(self, self, kind)
        except Exception:
            self._dialog_failed(*sys.exc_info())

    def _blank_menu(self, event):
        """列表空白处右键：快速新建 / 添加 / 刷新"""
        th = self.theme
        m = tk.Menu(self, tearoff=0, bg=th["card"], fg=th["text"],
                    activebackground=th["accent"], activeforeground=th["accent_fg"],
                    bd=0, activeborderwidth=0)
        m.add_command(label="📁  新建文件夹…", command=lambda: self._new_file("folder"))
        m.add_command(label="📄  新建 BAT 脚本…", command=lambda: self._new_file("bat"))
        m.add_command(label="📝  新建 TXT 文本…", command=lambda: self._new_file("txt"))
        m.add_separator()
        m.add_command(label="＋  添加程序 / 脚本…", command=lambda: self.add_item())
        m.add_command(label="📁  添加文件夹…", command=self._add_folder)
        m.add_command(label="🌐  添加网址…", command=self._add_url)
        m.add_separator()
        m.add_command(label="📥  从桌面 / 开始菜单导入…", command=self._import_shortcuts)
        m.add_command(label="🩺  体检：找出打不开的项目", command=self._health_check)
        m.add_separator()
        m.add_command(label="🔄  刷新列表", command=self.refresh)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _add_menu(self):
        th = self.theme
        m = tk.Menu(self, tearoff=0, bg=th["card"], fg=th["text"],
                    activebackground=th["accent"], activeforeground=th["accent_fg"],
                    bd=0, activeborderwidth=0)
        m.add_command(label="📄  添加程序 / 脚本…", command=lambda: self.add_item())
        m.add_command(label="📁  添加文件夹…", command=self._add_folder)
        m.add_command(label="🌐  添加网址…", command=self._add_url)
        m.add_separator()
        m.add_command(label="📁  新建文件夹…", command=lambda: self._new_file("folder"))
        m.add_command(label="📄  新建 BAT 脚本…", command=lambda: self._new_file("bat"))
        m.add_command(label="📝  新建 TXT 文本…", command=lambda: self._new_file("txt"))
        m.add_separator()
        m.add_command(label="📥  从桌面 / 开始菜单导入…", command=self._import_shortcuts)
        try:
            m.tk_popup(self.add_btn.winfo_rootx(),
                       self.add_btn.winfo_rooty() + self.add_btn.winfo_height() + 2)
        finally:
            m.grab_release()

    def _health_check(self):
        try:
            HealthDialog(self, self)
        except Exception:
            self._dialog_failed(*sys.exc_info())

    def _chains_dialog(self):
        try:
            ChainDialog(self, self)
        except Exception:
            self._dialog_failed(*sys.exc_info())

    def item_by_id(self, item_id):
        for item in self.cfg["items"]:
            if item.get("id") == item_id:
                return item
        return None

    def _toggle_shell_menu(self):
        want = bool(self.shell_var.get())
        ok, info = set_shell_menu(want)
        if not ok:
            self.shell_var.set(not want)
            messagebox.showerror("失败", "改注册表失败：%s" % info, parent=self)
            return
        self.settings["shell_menu"] = want
        save_config(self.cfg)
        if want:
            self.status.config(text="✅ 已经在右键菜单里加了「添加到启动器」（文件/文件夹都生效）",
                               fg=self.theme["ok"])
        else:
            self.status.config(text="已移除右键菜单项", fg=self.theme["sub"])

    def _import_shortcuts(self):
        try:
            ImportDialog(self, self)
        except Exception:
            self._dialog_failed(*sys.exc_info())

    def _add_folder(self):
        d = self._native(filedialog.askdirectory, title="选择文件夹")
        if d:
            self.add_item(os.path.normpath(d))

    def _add_url(self):
        url = ask_text(self, self, "添加网址",
                       "把网址粘进来就行，会自动用默认浏览器打开。\n"
                       "（不带 http:// 也没关系，会自动补上）",
                       initial="https://")
        if not url:
            return
        url = url.strip()
        if not is_url(url):
            url = "https://" + url
        self.add_item(url)

    def _open_dir(self, path):
        if os.path.isdir(path):
            shell_execute("open", path)

    def _after_items_changed(self):
        """项目变动后的统一收尾：存盘、重绘"""
        save_config(self.cfg)
        self._normalize_orders()
        self.refresh()

    def _quick_add(self, path):
        """批量拖入时用默认名称快速添加"""
        path = os.path.normpath(path) if not is_url(path) else path
        if any(i.get("path") == path for i in self.cfg["items"]):
            return
        key = human_key(path)
        icon = extract_system_icon(path, icon_path_for(key))
        if is_url(path):
            name = path.split("//")[-1].split("/")[0]
        else:
            name = os.path.splitext(os.path.basename(path.rstrip("\\/")))[0] or "未命名"
        item = normalize_item({
            "id": key, "name": name, "path": path, "icon": icon,
            "group": self.filter_group, "order": self._next_order(),
        })
        self.cfg["items"].append(item)
        self._after_items_changed()

    def add_item(self, path=None):
        if not path:
            path = self._native(
                filedialog.askopenfilename,
                title="选择要添加的文件",
                filetypes=[("可执行文件", "*.exe"),
                           ("脚本", "*.bat;*.cmd;*.py;*.pyw;*.ps1;*.ahk;*.js;*.vbs"),
                           ("快捷方式", "*.lnk"), ("所有文件", "*.*")]
            )
        if not path:
            return
        groups = [i.get("group", "") for i in self.cfg["items"] if i.get("group")]
        try:
            dlg = ItemDialog(self, self, path=path, groups=groups)
        except Exception:
            self._dialog_failed(*sys.exc_info())
            return
        data = dlg.show()
        if not data:
            return
        data["id"] = human_key(data["path"] + data["name"])
        data["run_count"] = 0
        data["order"] = self._next_order()
        self.cfg["items"].append(normalize_item(data))
        self._after_items_changed()

    def _edit(self, item):
        groups = [i.get("group", "") for i in self.cfg["items"] if i.get("group")]
        try:
            dlg = ItemDialog(self, self, item=item, groups=groups)
        except Exception:
            self._dialog_failed(*sys.exc_info())
            return
        data = dlg.show()
        if not data:
            return
        item.update(data)
        self._after_items_changed()

    def _delete(self, item):
        if messagebox.askyesno("确认删除", "确定删除「%s」吗？" % item.get("name", "")):
            self.cfg["items"] = [i for i in self.cfg["items"] if i is not item]
            self._after_items_changed()
            self.status.config(text="已删除：%s" % item.get("name", ""))

    def _toggle_pin(self, item):
        item["pinned"] = not item.get("pinned", False)
        if item.get("pinned") and self.settings.get("sort_mode") == "manual":
            # 手动排序模式下，「置顶」= 挪到最前面
            orders = [float(i.get("order", 0) or 0) for i in self.cfg["items"]
                      if i is not item]
            item["order"] = (min(orders) - 1) if orders else 1.0
        self._after_items_changed()

    def _copy(self, item):
        try:
            self.clipboard_clear()
            self.clipboard_append(item.get("path", ""))
            self.status.config(text="已复制路径")
        except Exception:
            pass

    def _reveal(self, item):
        p = item.get("path", "")
        if is_url(p):
            messagebox.showinfo("提示", "这是一个网址：\n%s" % p)
            return
        if os.path.isdir(p):
            shell_execute("open", p)
        elif os.path.exists(p):
            try:
                _spawn('explorer.exe /select,"%s"' % os.path.normpath(p), None, True)
            except Exception:
                shell_execute("open", os.path.dirname(p))
        else:
            messagebox.showwarning("提示", "路径不存在：\n%s" % p)

    def launch(self, item, admin=False, console=None):
        path = item.get("path", "")
        if not is_url(path) and not os.path.exists(path):
            if messagebox.askyesno(
                    "找不到目标",
                    "路径不存在：\n%s\n\n是否重新指定文件位置？" % path):
                new = filedialog.askopenfilename(title="重新选择文件",
                                                 initialdir=os.path.dirname(path) or None)
                if new:
                    item["path"] = os.path.normpath(new)
                    if not item.get("name"):
                        item["name"] = os.path.splitext(os.path.basename(new))[0]
                    save_config(self.cfg)
                    self.refresh()
                    path = item["path"]
                else:
                    return
            else:
                return

        mode = console or item.get("console", CONSOLE_AUTO)
        ok, msg = launch_path(path, item.get("args", ""), mode, admin,
                              item.get("cwd") or None)
        if ok:
            item["run_count"] = item.get("run_count", 0) + 1
            save_config(self.cfg)
            self.status.config(text="✅ %s：%s" % (msg, item.get("name", "")),
                               fg=self.theme["ok"])
        else:
            self.status.config(text="❌ 启动失败：%s" % msg, fg=self.theme["err"])
            messagebox.showerror("启动失败", "%s\n\n%s" % (msg, path))

    def _on_drop(self, event):
        try:
            files = [f for f in self.tk.splitlist(event.data) if f]
        except Exception:
            files = [event.data] if event.data else []
        if not files:
            return
        if len(files) > 1:
            detail = messagebox.askyesno(
                "批量添加",
                "检测到 %d 个项目。\n\n是否逐个设置名称和图标？\n"
                "（选「否」将使用默认名称快速添加）" % len(files))
            if not detail:
                for f in files:
                    self._quick_add(f)
                return
        for f in files:
            self.add_item(f)

    # ---------------- 视图 / 主题 / 菜单 ----------------
    def _toggle_view(self):
        self.view = "list" if self.view == "grid" else "grid"
        self.settings["view"] = self.view
        save_config(self.cfg)
        try:
            self.view_btn.set_text("▤" if self.view == "grid" else "▦")
        except Exception:
            pass
        self.refresh()

    def _toggle_theme(self):
        """顶栏按钮：深色 / 浅色 快速对调（跟随系统请用菜单）"""
        target = "light" if self.theme_name == "dark" else "dark"
        self._apply_theme_mode(target, announce=False)

    def _theme_glyph(self):
        if self.settings.get("theme_mode", "dark") == "auto":
            return "🌗"
        return "☀" if self.theme_name == "dark" else "🌙"

    def _update_theme_btn(self):
        try:
            if hasattr(self, "theme_btn"):
                self.theme_btn.set_text(self._theme_glyph())
        except Exception:
            pass

    def _apply_theme_mode(self, mode=None, persist=True, announce=True):
        """mode: dark / light / auto（auto = 跟随 Windows 应用模式）"""
        if mode:
            self.settings["theme_mode"] = mode
        mode = self.settings.get("theme_mode", "dark")
        name = system_theme() if mode == "auto" else mode
        if name not in THEMES:
            name = "dark"
        changed = (name != self.theme_name)
        self.theme_name = name
        self.theme = THEMES[name]
        self.settings["theme"] = name
        if hasattr(self, "theme_var"):
            self.theme_var.set(mode)
        if persist:
            save_config(self.cfg)
        if changed and getattr(self, "body", None) is not None:
            self._rebuild_ui()
        else:
            self._update_theme_btn()
        if announce:
            self.status.config(text="主题：%s"
                                    % {"dark": "深色", "light": "浅色",
                                       "auto": "跟随系统"}.get(mode, mode),
                               fg=self.theme["sub"])

    def _poll_system_theme(self):
        """跟随系统时，Windows 换了深浅色这里自动跟上"""
        try:
            if self.settings.get("theme_mode", "dark") == "auto":
                want = system_theme()
                if want != self.theme_name:
                    self._apply_theme_mode(announce=False)
                    self.status.config(text="系统主题变了，已自动切换", fg=self.theme["sub"])
        except Exception:
            pass
        try:
            self.after(20000, self._poll_system_theme)
        except Exception:
            pass

    def _apply_ttk_style(self):
        th = self.theme
        try:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure("TCombobox", fieldbackground=th["entry"],
                            background=th["entry"], foreground=th["text"],
                            arrowcolor=th["sub"], bordercolor=th["border_hi"],
                            lightcolor=th["entry"], darkcolor=th["entry"],
                            insertcolor=th["text"], borderwidth=1,
                            relief="flat", padding=6)
            style.map("TCombobox",
                      fieldbackground=[("readonly", th["entry"]),
                                       ("focus", th["entry"])],
                      background=[("readonly", th["entry"]),
                                  ("active", th["entry"])],
                      foreground=[("readonly", th["text"])],
                      arrowcolor=[("active", th["accent"])],
                      bordercolor=[("focus", th["accent"])],
                      lightcolor=[("readonly", th["entry"])],
                      darkcolor=[("readonly", th["entry"])])
            style.configure("Vertical.TScrollbar", background=th["border_hi"],
                            troughcolor=th["bg"], bordercolor=th["bg"],
                            arrowcolor=th["sub"], borderwidth=0, width=10,
                            relief="flat")
            style.map("Vertical.TScrollbar",
                      background=[("active", th["accent"]),
                                  ("pressed", th["accent"])])
            for opt, val in (("*TCombobox*Listbox.background", th["card"]),
                             ("*TCombobox*Listbox.foreground", th["text"]),
                             ("*TCombobox*Listbox.selectBackground", th["accent"]),
                             ("*TCombobox*Listbox.selectForeground", th["accent_fg"])):
                self.option_add(opt, val)
        except Exception:
            pass

    def _rebuild_ui(self):
        """重建整个界面（用于主题切换后立即生效）"""
        keep_search = self.search.value() if hasattr(self, "search") else ""
        for w in list(self.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        self.configure(bg=self.theme["bg"])
        self._apply_ttk_style()
        self._build_topbar()
        self._build_canvas()
        self._build_statusbar()
        self._build_menu()
        if keep_search:
            self.search.set(keep_search)
            self.filter_text = keep_search.lower()
        if self.filter_group:
            self.group_cb.set(self.filter_group)
        if self.settings.get("always_on_top", False):
            self.attributes("-topmost", True)
        self.refresh()

    def _show_menu(self):
        try:
            self.menu.tk_popup(self.menu_btn.winfo_rootx() if hasattr(self, "menu_btn")
                               else self.winfo_pointerx(),
                               (self.menu_btn.winfo_rooty() + self.menu_btn.winfo_height() + 2)
                               if hasattr(self, "menu_btn") else self.winfo_pointery())
        finally:
            self.menu.grab_release()

    def _toggle_autostart(self):
        ok = set_autostart(self.autostart_var.get())
        if not ok:
            messagebox.showerror("失败", "无法写入注册表开机启动项")

    def _toggle_start_hidden(self):
        self.settings["start_hidden"] = bool(self.start_hidden_var.get())
        save_config(self.cfg)
        if self.settings["start_hidden"]:
            self.status.config(text="下次启动会直接缩到托盘（点托盘图标唤出）",
                               fg=self.theme["sub"])
        else:
            self.status.config(text="下次启动会正常显示窗口", fg=self.theme["sub"])

    def _toggle_blur(self):
        self.settings["hide_on_blur"] = bool(self.blur_var.get())
        save_config(self.cfg)
        self.status.config(text="点到别处自动收起：%s"
                                % ("开" if self.settings["hide_on_blur"] else "关"),
                           fg=self.theme["sub"])

    def _change_sort(self):
        self.settings["sort_mode"] = self.sort_var.get()
        save_config(self.cfg)
        self.refresh()
        # refresh 是延迟执行的，提示要再晚一点点写，否则会被覆盖
        self.after(150, lambda: self.status.config(
            text="排序方式：%s" % ("常用优先" if self.sort_var.get() == "frequent"
                                   else "按名称"),
            fg=self.theme["sub"]))

    def _native(self, func, *args, **kwargs):
        """包一层系统对话框：这期间不要触发「失焦自动收起」"""
        self._busy = getattr(self, "_busy", 0) + 1
        try:
            return func(*args, **kwargs)
        finally:
            self._busy = max(0, getattr(self, "_busy", 1) - 1)

    def _on_focus_out(self, _event=None):
        """可选：鼠标点到别的程序时自动收起窗口（默认关闭）"""
        if not self.settings.get("hide_on_blur", False):
            return
        try:
            if self.grab_current() is not None:
                return
        except Exception:
            return

        def maybe_hide():
            try:
                if self.state() != "normal" or self.grab_current() is not None:
                    return
                if getattr(self, "_busy", 0) > 0:
                    return
                # 自己还开着别的窗口（添加 / 引导 / 输入框）就别收
                for w in self.winfo_children():
                    if isinstance(w, tk.Toplevel) and w.winfo_exists():
                        return
                if self.focus_displayof() is None:
                    self.withdraw()
            except Exception:
                pass

        try:
            self.after(280, maybe_hide)
        except Exception:
            pass

    def _toggle_topmost(self):
        self.attributes("-topmost", self.topmost_var.get())
        self.settings["always_on_top"] = self.topmost_var.get()
        save_config(self.cfg)

    def _toggle_tray(self):
        self.settings["tray_enabled"] = self.tray_var.get()
        save_config(self.cfg)
        if self.tray_var.get() and self.tray_icon is None:
            self._setup_tray()
        elif not self.tray_var.get() and self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    def _poll_events(self):
        """主线程轮询：第二实例唤出 / 右键菜单「添加到启动器」都只是入队，不跨线程碰 Tk"""
        for _ in range(4):
            try:
                payload = self._wake_q.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            try:
                self._handle_wake(payload)
            except Exception:
                self._on_tk_error(*sys.exc_info())
        try:
            self.after(150, self._poll_events)
        except Exception:
            pass

    def _handle_wake(self, payload):
        """第二实例发来的请求：SHOW 或 ADD\\n<路径>"""
        text = str(payload or "").strip()
        if text.upper().startswith("ADD"):
            path = text[3:].strip()
            self._on_remote_add(path)
        else:
            self._on_second_launch()

    def _on_remote_add(self, path):
        """别人在资源管理器里点了「添加到启动器」"""
        self._summon()
        path = str(path or "").strip().strip('"')
        if not path:
            return
        if not is_url(path) and not os.path.exists(path):
            messagebox.showwarning("找不到这个路径", path, parent=self)
            return
        for item in self.cfg["items"]:
            if os.path.normcase(str(item.get("path", ""))) == os.path.normcase(path):
                self.search.set(item.get("name", ""))
                self.filter_text = str(item.get("name", "")).lower()
                self.refresh()
                self.status.config(text="这个已经在列表里了：%s" % item.get("name", ""),
                                   fg=self.theme["accent"])
                return
        self.status.config(text="正在添加：%s" % path, fg=self.theme["accent"])
        self.add_item(path)

    def _summon(self):
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(250, self._drop_topmost)
            # 先硬抢焦点，再用 Tk 的 focus_force 补一下（抢不到前台时窗口至少在最上层）
            force_foreground(self)
            self.focus_force()
            self.search.focus_set()
            self.search.select_all()
            tip = "已唤出 —— 直接输入可搜索"
            if self.settings.get("hide_on_blur", False):
                tip += "（点到别处自动收起）"
            else:
                tip += "（点 × 缩到托盘）"
            self.status.config(text=tip, fg=self.theme["sub"])
        except Exception:
            pass

    def _drop_topmost(self):
        try:
            if not self.settings.get("always_on_top", False):
                self.attributes("-topmost", False)
        except Exception:
            pass

    def _show_guide(self, first_run=False):
        try:
            GuideDialog(self, self, self.theme, first_run=first_run)
        except Exception:
            self._dialog_failed(*sys.exc_info())

    def _import_config(self):
        p = self._native(filedialog.askopenfilename, title="选择配置文件",
                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                raise ValueError("配置文件格式不正确")
            self.cfg["items"] = [normalize_item(i) for i in data.get("items", [])
                                 if isinstance(i, dict)]
            save_config(self.cfg)
            self.refresh()
            messagebox.showinfo("完成", "配置已导入")
        except Exception as exc:
            messagebox.showerror("失败", str(exc))

    def _export_config(self):
        p = self._native(filedialog.asksaveasfilename, title="导出配置到",
                         defaultextension=".json",
                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, ensure_ascii=False, indent=2)
            messagebox.showinfo("完成", "配置已导出")
        except Exception as exc:
            messagebox.showerror("失败", str(exc))

    def _about(self):
        messagebox.showinfo(
            "关于",
            "%s v%s\n\n"
            "把 BAT / EXE / Python / 文件夹 / 网址 拖进来，\n"
            "起中文名、配图标，点一下就启动。\n\n"
            "· 托盘图标 / 再双击一次图标，就能把窗口叫回来\n"
            "· 打字即搜索，↑↓ 选择、Enter 启动\n"
            "· 重复双击图标不会重复启动，而是把已有窗口叫出来\n"
            "· 添加 / 编辑窗口里有分步引导和「推荐设置」\n"
            "· Python 脚本默认用 pythonw 启动（不弹黑窗）\n"
            "· 批处理在独立窗口里运行，跑完自动关闭\n"
            "· 每个项目可单独设置工作目录 / 启动参数 / 控制台窗口\n\n"
            "配置保存在程序目录下的 launcher_config.json"
            % (APP_NAME, APP_VERSION)
        )

    # ---------------- 托盘 ----------------
    def _tray_recent(self, limit=6):
        """托盘里直接列出的常用项：置顶优先，然后按启动次数"""
        items = [i for i in self.cfg["items"] if i.get("name") and i.get("path")]
        items.sort(key=lambda i: (not i.get("pinned", False),
                                  -int(i.get("run_count", 0) or 0),
                                  str(i.get("name", "")).lower()))
        return items[:limit]

    def _tray_make_launcher(self, item):
        def run(_icon=None, _menuitem=None):
            try:
                self.after(0, lambda: self.launch(item))
            except Exception:
                pass
        return run

    def _tray_menu_items(self):
        """托盘菜单：动态生成，每次打开都是最新的常用项"""
        entries = [TrayItem("显示主界面", self._tray_show, default=True)]
        quick = self._tray_recent()
        if quick:
            entries.append(pystray.Menu.SEPARATOR)
            for it in quick:
                prefix = "📌 " if it.get("pinned") else "▶ "
                entries.append(TrayItem(prefix + str(it.get("name", "?")),
                                        self._tray_make_launcher(it)))
        entries.append(pystray.Menu.SEPARATOR)
        entries.append(TrayItem("退出", self._tray_quit))
        return entries

    def _setup_tray(self):
        if not HAS_PYSTRAY or not self.settings.get("tray_enabled", True):
            return
        if self.tray_icon is not None:
            return
        try:
            icon_file = os.path.join(ICON_DIR, "_tray.png")
            if not os.path.exists(icon_file):
                make_default_tray_icon(icon_file)
            image = Image.open(icon_file)
            self.tray_icon = pystray.Icon(APP_NAME, image, APP_NAME,
                                          pystray.Menu(self._tray_menu_items))
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception:
            self.tray_icon = None

    def _on_second_launch(self):
        """又有人双击了启动器（第二个进程通过窗口消息通知）→ 把窗口显示出来"""
        try:
            if self.grab_current() is not None:
                # 有弹窗开着，只把窗口提到前面，别抢弹窗的焦点
                self.deiconify()
                self.lift()
                force_foreground(self)
                return
        except Exception:
            pass
        self._summon()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._restore)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit)

    def _restore(self):
        try:
            self.deiconify()
            self.lift()
            force_foreground(self)
            self.focus_force()
        except Exception:
            pass

    def _on_close(self):
        if HAS_PYSTRAY and self.tray_icon is not None \
                and self.settings.get("tray_enabled", True):
            self.withdraw()
            return
        self._quit()

    def _quit(self):
        try:
            self.settings["geometry"] = self.geometry()
            save_config(self.cfg)
        except Exception:
            pass
        try:
            if getattr(self, "ipc", None) is not None:
                self.ipc.close()
        except Exception:
            pass
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        try:
            self.destroy()
        except Exception:
            pass


def main():
    enable_dpi_awareness()
    # 资源管理器右键菜单会带 --add "<路径>" 过来
    pre_add = None
    argv = sys.argv[1:]
    if "--add" in argv:
        idx = argv.index("--add")
        if idx + 1 < len(argv):
            pre_add = argv[idx + 1]
    if not acquire_single_instance():
        # 已经有实例在跑：把它的窗口叫出来（顺便把要添加的路径递过去）
        command = ("ADD\n%s" % pre_add) if pre_add else "SHOW"
        if not send_to_running(command):
            _msgbox(APP_NAME, "启动器已经在运行了（可能藏在右下角托盘里）。", 0x40)
        return
    try:
        app = LauncherApp()
    except Exception:
        text = traceback.format_exc()
        _write_error_log(text)
        _msgbox(APP_NAME, "启动器启动失败：\n\n%s\n\n详细信息已写入 launcher_error.log"
                % text[-800:], 0x10)
        return
    if pre_add:
        app.after(700, lambda: app.add_item(pre_add))
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

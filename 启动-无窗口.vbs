' 我的启动器 - 无窗口启动
' 双击本文件即可启动，不会出现黑色命令行窗口
' 适合放到桌面 / 开始菜单 / 开机启动
Option Explicit

Dim fso, sh, base, py, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)

If Not fso.FileExists(fso.BuildPath(base, "launcher.py")) Then
    MsgBox "没有找到 launcher.py，请把本文件放在启动器目录里。", 16, "我的启动器"
    WScript.Quit 1
End If

py = FindExe("pythonw.exe")
If py = "" Then py = FindExe("python.exe")
If py = "" Then
    MsgBox "没有检测到 Python，请先安装 Python 3.8 及以上版本，安装时勾选 Add Python to PATH。", 16, "我的启动器"
    WScript.Quit 1
End If

sh.CurrentDirectory = base
cmd = """" & py & """ """ & fso.BuildPath(base, "launcher.py") & """"
' 0 = 隐藏窗口，False = 不等待
sh.Run cmd, 0, False

Function FindExe(name)
    Dim dirs, i, p
    FindExe = ""
    dirs = Split(sh.ExpandEnvironmentStrings("%PATH%"), ";")
    For i = 0 To UBound(dirs)
        p = Trim(dirs(i))
        If Len(p) > 0 Then
            If fso.FileExists(fso.BuildPath(p, name)) Then
                FindExe = fso.BuildPath(p, name)
                Exit Function
            End If
        End If
    Next
End Function

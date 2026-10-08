#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动器自检（不需要 pytest，`python tests/selftest.py` 或双击都行）

它把 launcher.py 复制到一个临时目录里跑，不碰你自己的 launcher_config.json。
覆盖的都是「真出过事」或者一坏就很难受的地方：

  1. 静态检查：源码里不许再出现 `self._w =`（tkinter 的窗口路径名，
     被覆盖就报 TclError: invalid command name "460"，v1.2 就是这么崩的）
  2. 所有对话框能不能打开：使用引导 / 新建文件夹·BAT·TXT / 批量导入 /
     体检 / 启动链 / 添加向导
  3. 添加向导：多层分组、保存、入列
  4. 搜索：中文 + 拼音首字母
  5. 新建文件真的落盘（BAT 模板不能出现 \r\r\n）
  6. 体检能找出打不开的项目
  7. 配置导出 / 导入
  8. 「再双击一次图标唤出窗口」这条链路（主窗口现在靠它叫回来）
  9. 对话框崩了要能自救：不留卡住的空窗口、不攥着 grab

退出码 0 = 全过。
"""
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = []
POPUPS = []


def check(name, fn):
    try:
        fn()
        RESULTS.append(("PASS", name, ""))
    except Exception as exc:
        RESULTS.append(("FAIL", name, "%s: %s" % (type(exc).__name__, exc)))


def info(name, text):
    RESULTS.append(("INFO", name, text))


BASELINE = {"items": [], "chains": [], "settings": {
    "theme": "dark", "theme_mode": "dark", "view": "grid",
    "tray_enabled": False, "guide_shown": True, "hide_on_blur": False,
    "sort_mode": "name", "always_on_top": False}}


def main():
    src = os.path.join(ROOT, "launcher.py")
    if not os.path.exists(src):
        print("找不到 %s" % src)
        return 2

    workdir = tempfile.mkdtemp(prefix="launcher_selftest_")
    shutil.copyfile(src, os.path.join(workdir, "launcher.py"))
    icondir = os.path.join(ROOT, "icons")
    if os.path.isdir(icondir):
        shutil.copytree(icondir, os.path.join(workdir, "icons"))
    with open(os.path.join(workdir, "launcher_config.json"), "w",
              encoding="utf-8") as fh:
        json.dump(BASELINE, fh, ensure_ascii=False, indent=2)

    src_text = open(src, encoding="utf-8").read()
    sys.path.insert(0, workdir)
    import launcher as L                                   # noqa: E402
    import tkinter as tk                                   # noqa: E402

    print("自检目录：%s" % workdir)
    print("Python %s / Tk %s\n" % (sys.version.split()[0], tk.TkVersion))

    # 系统弹窗一律挡掉，免得测试卡在模态框上
    for fn in ("showinfo", "showwarning", "showerror"):
        setattr(L.messagebox, fn,
                (lambda nm: (lambda *a, **k: POPUPS.append((nm, a[:1]))))(fn))
    L.messagebox.askyesno = lambda *a, **k: (POPUPS.append(("askyesno", a[:1])), True)[1]

    tmp = tempfile.mkdtemp(prefix="launcher_selftest_files_")
    app = L.LauncherApp()
    app.withdraw()
    app.update()

    # ---------- 1. 静态守卫 ----------
    def t_static():
        bad = [i + 1 for i, ln in enumerate(src_text.split("\n"))
               if re.search(r"self\._w\s*=", ln)]
        assert not bad, ("源码第 %s 行又给 self._w 赋值了 —— 那是 tkinter 的窗口"
                        "路径名，覆盖后 delete() 会报 invalid command name" % bad)

    check("静态检查：没有 self._w 赋值", t_static)

    # ---------- 2. 所有对话框 ----------
    def dialog(label, factory, poke=None):
        def run():
            dlg = factory()
            app.update()
            if poke:
                poke(dlg)
                app.update()
            try:
                dlg.grab_release()
            except Exception:
                pass
            dlg.destroy()
            app.update()
        check("对话框：%s" % label, run)

    dialog("使用引导", lambda: L.GuideDialog(app, app, app.theme, first_run=False))
    dialog("新建文件夹", lambda: L.CreateFileDialog(app, app, "folder"))
    dialog("新建 BAT", lambda: L.CreateFileDialog(app, app, "bat"))
    dialog("新建 TXT", lambda: L.CreateFileDialog(app, app, "txt"))
    dialog("从桌面 / 开始菜单导入", lambda: L.ImportDialog(app, app),
           lambda d: (d._scan(), d._filter()))
    dialog("体检", lambda: L.HealthDialog(app, app), lambda d: d._scan())
    dialog("启动链", lambda: L.ChainDialog(app, app))

    # ---------- 3. 添加向导 ----------
    def t_add():
        target = r"C:\Windows\System32\notepad.exe"
        assert os.path.exists(target), "自检需要 notepad.exe"
        dlg = L.ItemDialog(app, app, path=target, groups=[])
        dlg.name_var.set("自检项")
        dlg.group_var.set("自检/二级")            # 多层分组
        dlg.pin_var.set(True)
        dlg._ok()
        data = dlg.result
        assert data, "向导没返回数据"
        assert data["group"] == "自检/二级", data["group"]
        assert "hotkey" not in data, "结果里还有 hotkey，专属快捷键没拆干净"
        data["id"] = L.human_key(data["path"] + data["name"])
        data["order"] = app._next_order()
        app.cfg["items"].append(L.normalize_item(data))
        app._after_items_changed()
        app.update()
        assert len(app.cfg["items"]) == 1
        info("添加向导", "条目=%d 分组=%s" % (len(app.cfg["items"]), data["group"]))

    check("添加向导：多层分组 + 入列", t_add)

    # ---------- 4. 搜索 ----------
    def t_search():
        for query, expect in (("自检", 1), ("zj", 1), ("zzzz", 0)):
            app.search.set(query)
            app.filter_text = query.lower()
            app.refresh()
            app.update()
            assert len(app._shown) == expect, \
                "搜「%s」得到 %d 个结果（期望 %d）" % (query, len(app._shown), expect)
        app.search.set("")
        app.filter_text = ""
        app.refresh()
        app.update()

    check("搜索：中文 / 拼音首字母 / 搜不到", t_search)

    # ---------- 5. 新建文件落盘 ----------
    def t_create_files():
        for kind, name in (("folder", "自检文件夹"), ("bat", "自检脚本"),
                           ("txt", "自检文本")):
            dlg = L.CreateFileDialog(app, app, kind)
            dlg.dir_entry.set(tmp)
            dlg.name_entry.set(name)
            dlg.add_var.set(False)        # 否则会弹添加向导把测试挂住
            dlg.open_var.set(False)
            dlg._create()
            app.update()
        assert os.path.isdir(os.path.join(tmp, "自检文件夹"))
        assert os.path.exists(os.path.join(tmp, "自检文本.txt"))
        bat = os.path.join(tmp, "自检脚本.bat")
        assert os.path.exists(bat)
        raw = open(bat, "rb").read()
        assert b"\r\r\n" not in raw, "BAT 模板又变成 \\r\\r\\n 了"
        info("新建文件", "生成 %d 个" % len(os.listdir(tmp)))

    check("空白处右键新建：文件夹 / BAT / TXT", t_create_files)

    # ---------- 6. 体检 ----------
    def t_health():
        broken = L.normalize_item({"id": "broken", "name": "坏掉的自检项",
                                   "path": r"C:\不存在\x.exe"})
        app.cfg["items"].append(broken)
        app._after_items_changed()
        app.update()
        dlg = L.HealthDialog(app, app)
        dlg._scan()
        app.update()
        names = [i.get("name") for i in dlg.broken]
        assert "坏掉的自检项" in names, "体检没找出坏路径：%s" % names
        dlg.destroy()
        app.cfg["items"] = [i for i in app.cfg["items"] if i.get("id") != "broken"]
        app._after_items_changed()
        app.update()

    check("体检：找出打不开的项目", t_health)

    # ---------- 7. 配置导出 / 导入 ----------
    def t_config_io():
        out = os.path.join(tmp, "导出配置.json")
        L.filedialog.asksaveasfilename = lambda *a, **k: out
        app._export_config()
        assert os.path.exists(out), "没导出"
        L.filedialog.askopenfilename = lambda *a, **k: out
        app._import_config()
        app.update()
        assert app.cfg["items"], "导入后列表是空的"

    check("配置导出 / 导入", t_config_io)

    # ---------- 8. 再双击一次图标 → 唤出窗口 ----------
    def t_wake_show():
        app.withdraw()
        app.update()
        assert app.state() == "withdrawn", app.state()
        # 第二个实例是往 loopback socket 里发 "SHOW"，然后由主线程轮询处理
        app._handle_wake("SHOW")
        app.update()
        assert app.state() == "normal", "收到 SHOW 没把窗口叫出来：%s" % app.state()
        info("双击唤出", "state=%s 抢到前台=%s" % (app.state(), L.is_foreground(app)
                                              if hasattr(L, "is_foreground") else "n/a"))

    check("第二个实例 SHOW → 窗口被叫出来", t_wake_show)

    # ---------- 9. 对话框崩了要能自救 ----------
    def t_selfheal():
        class Boom(L.GuideDialog):
            def __init__(self, master, app_, th, first_run=False):
                L.GuideDialog.__init__(self, master, app_, th, first_run=first_run)
                raise RuntimeError("自检故意炸一下")

        app.withdraw()
        app.update()
        try:
            Boom(app, app, app.theme)
        except Exception:
            app._dialog_failed(*sys.exc_info())
        app.update()
        left = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
        assert not left, "崩溃的对话框没被清掉：%s" % left
        assert app.grab_current() is None, "grab 没松开，界面会点不动"
        app._summon()
        app.update()
        assert app.state() == "normal", "自救之后窗口叫不出来"

    check("对话框崩了能自救（不留卡死窗口）", t_selfheal)

    # ---------- 收尾 ----------
    try:
        app.destroy()
    except Exception:
        pass
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 66)
    for kind, name, extra in RESULTS:
        print("[%s] %s %s" % (kind, name, ("| " + extra) if extra else ""))
    print("-" * 66)
    failed = [x for x in RESULTS if x[0] == "FAIL"]
    passed = [x for x in RESULTS if x[0] == "PASS"]
    print("通过 %d / %d" % (len(passed), len(passed) + len(failed)))
    if failed:
        print("有 %d 项没过，上面标着 FAIL 的就是。" % len(failed))
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

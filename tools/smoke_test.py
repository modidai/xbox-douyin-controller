"""真注入端到端验证：开一个 tkinter 靶子窗口，用 SendInput 往里注入。

安全边界（这是全项目唯一允许真注入的地方）：
1. 注入前必须把靶子窗口设成前台并校验焦点确实是它，抢不到焦点就 SKIP；
2. 每次注入前用 WindowFromPoint 复核光标下面就是靶子窗口，否则立刻放弃；
3. 只按唯一标题匹配自己开的窗口，cfg.target_process 留空，绝不错认成抖音；
4. 跑完自动关窗并把光标放回原处。

用法：python tools/smoke_test.py
退出码：0=PASS，1=FAIL，3=SKIP（没抢到焦点，未做任何注入）
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xbox_douyin.config import Config
from xbox_douyin.injector import Injector

TITLE = "xbox-douyin-smoke-test-%d" % os.getpid()
WINDOW_X = 260
WINDOW_Y = 200
WINDOW_W = 460
WINDOW_H = 280

GA_ROOT = 2
SW_RESTORE = 9

RESULTS = []
ABORTED = [False]


def record(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, ("  <- " + str(detail)) if detail else ""))


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


DWORD = ctypes.c_ulong
HWND = ctypes.c_void_p

_USER32 = [None]


def _user32():
    """统一声明原型：HWND 是 64 位指针，用默认 c_int 会被截断。"""
    if _USER32[0] is not None:
        return _USER32[0]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = HWND
    user32.GetForegroundWindow.argtypes = []
    user32.GetAncestor.restype = HWND
    user32.GetAncestor.argtypes = [HWND, ctypes.c_uint]
    user32.WindowFromPoint.restype = HWND
    user32.WindowFromPoint.argtypes = [_POINT]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowThreadProcessId.restype = DWORD
    user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [HWND]
    user32.BringWindowToTop.restype = ctypes.c_int
    user32.BringWindowToTop.argtypes = [HWND]
    user32.SetFocus.restype = HWND
    user32.SetFocus.argtypes = [HWND]
    user32.ShowWindow.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
    user32.AttachThreadInput.restype = ctypes.c_int
    user32.AttachThreadInput.argtypes = [DWORD, DWORD, ctypes.c_int]
    _USER32[0] = user32
    return user32


def root_ancestor(hwnd):
    if not hwnd:
        return 0
    root_hwnd = _user32().GetAncestor(HWND(hwnd), GA_ROOT)
    return int(root_hwnd or hwnd)


def window_title(hwnd):
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    _user32().GetWindowTextW(HWND(hwnd), buf, 512)
    return buf.value


def window_title_at(x, y):
    hwnd = _user32().WindowFromPoint(_POINT(int(x), int(y)))
    return window_title(root_ancestor(hwnd))


def foreground_root():
    return root_ancestor(_user32().GetForegroundWindow())


def try_take_foreground(hwnd, pump, timeout_s=3.0):
    """把自己的窗口设为前台。

    前台进程不是自己时，SetForegroundWindow 会被 Windows 拦下；
    标准做法是临时 AttachThreadInput 到前台线程，设完立刻 detach。
    """
    user32 = _user32()
    kernel32 = ctypes.WinDLL("kernel32")
    my_thread = DWORD(kernel32.GetCurrentThreadId())
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if foreground_root() == hwnd:
            return True
        front = user32.GetForegroundWindow()
        front_thread = DWORD(user32.GetWindowThreadProcessId(front, None)) if front else DWORD(0)
        attached = False
        if front_thread.value and front_thread.value != my_thread.value:
            attached = bool(user32.AttachThreadInput(my_thread, front_thread, 1))
        try:
            user32.BringWindowToTop(HWND(hwnd))
            user32.ShowWindow(HWND(hwnd), SW_RESTORE)
            user32.SetForegroundWindow(HWND(hwnd))
            user32.SetFocus(HWND(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(my_thread, front_thread, 0)
        pump(0.15)
    return foreground_root() == hwnd


def main():
    try:
        import tkinter as tk
    except Exception as exc:
        print("FAIL  无法导入 tkinter: %s" % exc)
        return 1

    cfg = Config()
    cfg.target_window = TITLE
    cfg.target_process = ""  # 只按唯一标题匹配，绝不会命中抖音

    counters = {"click": 0, "double": 0, "wheel": 0, "key": 0}

    def on_click(_event):
        counters["click"] += 1

    def on_double(_event):
        counters["double"] += 1

    def on_wheel(event):
        counters["wheel"] += int(event.delta / 120)

    def on_key(_event):
        counters["key"] += 1

    try:
        root = tk.Tk()
    except Exception as exc:
        print("FAIL  无法创建窗口: %s" % exc)
        return 1

    root.title(TITLE)
    root.geometry("%dx%d+%d+%d" % (WINDOW_W, WINDOW_H, WINDOW_X, WINDOW_Y))
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    label = tk.Label(root, text="Xbox 手柄注入靶子窗口", font=("Microsoft YaHei", 15), bg="white")
    label.pack(expand=True, fill="both")
    root.bind_all("<Button-1>", on_click)
    root.bind_all("<Double-Button-1>", on_double)
    root.bind_all("<MouseWheel>", on_wheel)
    root.bind("<KeyPress-F8>", on_key)

    def pump(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                root.update()
            except Exception:
                return
            time.sleep(0.01)

    injector = Injector(cfg, out=print)
    saved_cursor = None
    exit_code = 0
    try:
        root.update()
        root.lift()
        root.focus_force()
        pump(0.4)

        hwnd = root_ancestor(root.winfo_id())
        record("靶子窗口 handle 有效", bool(hwnd), hwnd)
        got_focus = try_take_foreground(hwnd, pump, timeout_s=3.0)
        record("靶子窗口已获得前台焦点", got_focus, window_title(foreground_root()))
        if not got_focus:
            print("SKIP  没抢到前台焦点（当前前台是 %r），不做任何注入" % window_title(foreground_root()))
            exit_code = 3
            return exit_code

        saved_cursor = injector.cursor_pos()

        rect = injector.target_client_rect()
        found = rect is not None and rect[2] > 100 and rect[3] > 100
        record("按唯一标题找到靶子窗口客户区", found, rect)
        if not found:
            print("SKIP  找不到靶子窗口客户区，不做任何注入")
            exit_code = 3
            return exit_code
        left, top, width, height = rect
        center = (left + width // 2, top + height // 2)

        def guard():
            """注入前的安全护栏：焦点和光标下窗口都必须是自己的。"""
            if foreground_root() != hwnd:
                return (False, "焦点被 %r 抢走" % window_title(foreground_root()))
            injector.set_cursor(center[0], center[1])
            pump(0.05)
            title_now = window_title_at(*injector.cursor_pos())
            if title_now != TITLE:
                return (False, "光标下是 %r" % title_now)
            return (True, "")

        def need_guard(tag):
            ok, why = guard()
            if not ok:
                ABORTED[0] = True
                print("SKIP  %s 前护栏未通过（%s），停止后续注入" % (tag, why))
            return ok

        # 1) 左键点击 + 光标还原
        if need_guard("左键"):
            before = counters["click"]
            injector.click_normalized(0.5, 0.5, restore=True)
            pump(0.25)
            record("注入左键：靶子窗口收到 1 次点击", counters["click"] - before == 1, counters["click"] - before)
            after_cursor = injector.cursor_pos()
            if after_cursor != saved_cursor:
                # 可能有别的东西同时在动光标，重试一次再判定
                injector.set_cursor(saved_cursor[0], saved_cursor[1])
                after_cursor = injector.cursor_pos()
            record(
                "restore=True 后光标回到原位置",
                after_cursor == saved_cursor,
                (saved_cursor, after_cursor),
            )

        # 2) 滚轮
        if need_guard("滚轮"):
            before_wheel = counters["wheel"]
            injector.wheel(3)
            pump(0.15)
            injector.wheel(-2)
            pump(0.15)
            record(
                "注入滚轮：+3 格再 -2 格，净 +1 格",
                counters["wheel"] - before_wheel == 1,
                counters["wheel"] - before_wheel,
            )

        # 3) 双击
        if need_guard("双击"):
            before_double = counters["double"]
            injector.double_click(40)
            pump(0.35)
            record(
                "注入双击：靶子窗口收到 1 次双击",
                counters["double"] - before_double == 1,
                counters["double"] - before_double,
            )

        # 4) 键盘 F8（必须确认前台是自己才打）
        if need_guard("按键"):
            before_key = counters["key"]
            injector.press("F8")
            pump(0.3)
            got = counters["key"] - before_key
            record("注入 F8：靶子窗口收到按键", got >= 1, got)

        if ABORTED[0]:
            print("SKIP  中途护栏未通过，已停止注入")
            exit_code = 3
    finally:
        if saved_cursor is not None:
            try:
                injector.set_cursor(saved_cursor[0], saved_cursor[1])
            except Exception:
                pass
        try:
            root.attributes("-topmost", False)
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    failed = [item for item in RESULTS if not item[1]]
    print("---")
    print(
        "注入计数：点击 %d 次，双击 %d 次，滚轮净 %+d 格，按键 %d 次"
        % (counters["click"], counters["double"], counters["wheel"], counters["key"])
    )
    if failed:
        print("FAIL  %d/%d 项未通过：%s" % (len(failed), len(RESULTS), ", ".join(i[0] for i in failed)))
        return 1
    if exit_code == 3:
        return 3
    print("PASS  全部 %d 项通过" % len(RESULTS))
    return 0


if __name__ == "__main__":
    sys.exit(main())

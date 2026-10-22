# -*- coding: utf-8 -*-
"""抖音窗口校准工具（开发/维护用，不是运行时依赖）。

用途：
  * 定位 douyin.exe 的客户区矩形（物理像素，已做 DPI 感知）；
  * 把配置里的归一化坐标（0..1，相对客户区）换算成屏幕绝对像素；
  * 截图到 %TEMP%（可选 PrintWindow 抓被遮挡的窗口），并在图上画十字准星，
    方便客户端改版/换分辨率后重新量图标位置。

示例：
  python tools/measure.py --report
  python tools/measure.py --report --shot --crosshair
  python tools/measure.py --report --shot --printwindow
  python tools/measure.py --x 0.935 --y 0.51
  python tools/measure.py --report --shot --mark "0.9,0.4" --mark "0.9,0.6"

只使用标准库 + ctypes（截图、PNG 编码都是手写的，不依赖 Pillow）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import struct
import sys
import tempfile
import time
import zlib

# ---- DPI 感知：不做的话 GetClientRect / BitBlt 会拿到被系统缩放过的虚拟坐标 ----
def _enable_dpi_awareness():
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


DPI_MODE = _enable_dpi_awareness()

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
BI_RGB = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _image_name(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _window_text(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def client_rect(hwnd):
    """客户区在屏幕上的绝对矩形 (left, top, w, h)；失败返回 None。"""
    rect = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wt.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    w = rect.right - rect.left
    h = rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    return (pt.x, pt.y, w, h)


def list_windows(process_name, title_keyword=""):
    found = []

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        if _image_name(pid.value) != process_name.lower():
            return True
        title = _window_text(hwnd)
        if title_keyword and title_keyword.lower() not in title.lower():
            return True
        rect = client_rect(hwnd)
        if rect is None:
            return True
        found.append({"hwnd": hwnd, "pid": int(pid.value), "title": title, "rect": rect})
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    found.sort(key=lambda item: item["rect"][2] * item["rect"][3], reverse=True)
    return found


def set_foreground(hwnd):
    """把窗口提到前台（注入与屏幕截图都要求目标在前台）。"""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.keybd_event(0x12, 0, 0, 0)   # 一次 alt 抖动，绕过前台锁
    user32.keybd_event(0x12, 0, 2, 0)
    ok = bool(user32.SetForegroundWindow(hwnd))
    user32.BringWindowToTop(hwnd)
    return ok


def to_absolute(rect, nx, ny):
    left, top, w, h = rect
    return int(round(left + nx * (w - 1))), int(round(top + ny * (h - 1)))


def to_normalized(rect, x, y):
    left, top, w, h = rect
    return (round((x - left) / float(max(1, w - 1)), 4),
            round((y - top) / float(max(1, h - 1)), 4))


# ---------------------------------------------------------------- 截图 / PNG
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def grab_bgra(hwnd, rect, printwindow=False):
    """抓客户区，返回 (w, h, bytearray BGRA 自上而下)。"""
    left, top, w, h = rect
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bitmap)
    try:
        if printwindow:
            user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        else:
            gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, left, top, SRCCOPY)
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h          # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buf = bytearray(w * h * 4)
        cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        if not gdi32.GetDIBits(mem_dc, bitmap, 0, h, cbuf, ctypes.byref(info), 0):
            raise RuntimeError("GetDIBits 失败: %s" % ctypes.get_last_error())
        return w, h, buf
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def draw_cross(buf, w, h, x, y, radius=14, color=(255, 0, 0), thickness=2):
    """在 BGRA 缓冲上画十字（就地修改）。"""
    b, g, r = color[2], color[1], color[0]

    def _put(px, py):
        if 0 <= px < w and 0 <= py < h:
            off = (py * w + px) * 4
            buf[off] = b
            buf[off + 1] = g
            buf[off + 2] = r
            buf[off + 3] = 255

    for dx in range(-radius, radius + 1):
        for t in range(thickness):
            _put(x + dx, y + t - thickness // 2)
    for dy in range(-radius, radius + 1):
        for t in range(thickness):
            _put(x + t - thickness // 2, y + dy)


def _png_chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def save_png(path, w, h, buf):
    """BGRA 缓冲 -> PNG（RGB 24bit，标准库手写）。"""
    view = memoryview(buf)
    rows = [None] * h
    for y in range(h):
        start = y * w * 4
        row = bytearray(w * 3)
        row[0::3] = view[start + 2:start + w * 4:4]
        row[1::3] = view[start + 1:start + w * 4:4]
        row[2::3] = view[start:start + w * 4:4]
        rows[y] = b"\x00" + bytes(row)
    raw = b"".join(rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_png_chunk(b"IHDR", ihdr))
        fh.write(_png_chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(_png_chunk(b"IEND", b""))
    return path


def screenshot(hwnd, rect, path=None, marks=None, printwindow=False):
    """抓客户区存 PNG。marks: [(屏幕绝对 x, 绝对 y), ...] 会画十字。"""
    path = path or os.path.join(
        tempfile.gettempdir(), "douyin_shot_%s.png" % time.strftime("%H%M%S"))
    w, h, buf = grab_bgra(hwnd, rect, printwindow=printwindow)
    left, top = rect[0], rect[1]
    for mark in (marks or []):
        mx, my = int(mark[0]), int(mark[1])
        draw_cross(buf, w, h, mx - left, my - top)
    return save_png(path, w, h, buf)


# ---------------------------------------------------------------- 报告
def load_config():
    """读工程里的 Config（会叠加用户 config.json）。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from xbox_douyin.config import Config
        return Config.load()
    except Exception as exc:
        print("[warn] 读取 Config 失败（%s），退化为内置默认" % exc)
        return None


def _resolver():
    """借 douyin.py 的参数解析，让报告显示"生效"值而不是占位值。"""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from xbox_douyin.douyin import resolve_action_params
        return resolve_action_params
    except Exception as exc:
        print("[warn] 读不到 douyin.py 的动作解析（%s），只能按配置原样显示" % exc)
        return None


def describe_actions(cfg):
    """返回 [(名字, method, key, x, y, 是否用了实测校准兜底)]。"""
    rows = []
    if cfg is None:
        return rows
    resolver = _resolver()
    for name in ("like", "back", "favorite", "comments"):
        act = getattr(cfg, name, None)
        if act is None:
            continue
        method = (getattr(act, "method", "") or "click").lower()
        key = getattr(act, "key", "") or ""
        x = float(getattr(act, "x", 0.5))
        y = float(getattr(act, "y", 0.5))
        fallback = False
        if resolver is not None:
            try:
                r_method, r_key, r_x, r_y = resolver(cfg, name)
                fallback = (r_method, r_key, round(r_x, 4), round(r_y, 4)) != \
                           (method, key, round(x, 4), round(y, 4))
                method, key, x, y = r_method, r_key, r_x, r_y
            except Exception:
                fallback = False
        rows.append((name, method, key, x, y, fallback))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="抖音窗口客户区校准工具")
    parser.add_argument("--report", action="store_true", help="打印窗口矩形与各动作坐标换算")
    parser.add_argument("--process", default=None, help="进程名，默认取配置 target_process")
    parser.add_argument("--title", default="", help="窗口标题关键字过滤")
    parser.add_argument("--x", type=float, default=None, help="单独换算的归一化 x")
    parser.add_argument("--y", type=float, default=None, help="单独换算的归一化 y")
    parser.add_argument("--shot", action="store_true", help="截图到 %%TEMP%%")
    parser.add_argument("--shot-path", default=None, help="截图输出路径")
    parser.add_argument("--printwindow", action="store_true", help="用 PrintWindow 抓（窗口被遮挡时用）")
    parser.add_argument("--crosshair", action="store_true", help="在截图上标出配置里各 click 动作坐标")
    parser.add_argument("--mark", action="append", default=[], metavar="NX,NY",
                        help="额外标记点，可重复，例如 --mark 0.93,0.55")
    parser.add_argument("--foreground", action="store_true", help="截图前把抖音提到前台")
    parser.add_argument("--list", action="store_true", help="列出所有候选窗口")
    parser.add_argument("--wait", type=float, default=0.0, help="截图前等待秒数")
    args = parser.parse_args(argv)

    cfg = load_config()
    process = args.process or (getattr(cfg, "target_process", None) if cfg else None) or "douyin.exe"

    windows = list_windows(process, args.title)
    if not windows:
        print("[error] 没找到进程 %s 的可见窗口（标题过滤=%r）" % (process, args.title))
        return 2
    win = windows[0]
    rect = win["rect"]
    left, top, w, h = rect

    if args.list:
        for item in windows:
            print("hwnd=0x%X pid=%d title=%r client=%s"
                  % (item["hwnd"], item["pid"], item["title"], item["rect"]))

    if args.report or args.list:
        print("DPI 模式: %s" % DPI_MODE)
        print("进程: %s  标题: %r  hwnd=0x%X pid=%d"
              % (process, win["title"], win["hwnd"], win["pid"]))
        print("客户区(屏幕绝对像素): left=%d top=%d w=%d h=%d" % (left, top, w, h))
        print("客户区中心: (%d, %d)" % (left + w // 2, top + h // 2))
        rows = describe_actions(cfg)
        if rows:
            print("")
            print("%-9s %-13s %-12s %-19s %-16s %s"
                  % ("动作", "method", "key", "归一化(x,y)", "绝对像素(x,y)", "来源"))
            for (name, method, key, nx, ny, fallback) in rows:
                ax, ay = to_absolute(rect, nx, ny)
                print("%-9s %-13s %-12s (%.4f, %.4f)     (%5d, %5d)      %s"
                      % (name, method, key or "-", nx, ny, ax, ay,
                         "实测校准值(占位兜底)" if fallback else "配置文件"))
            swipe = getattr(cfg, "swipe", None)
            if swipe is not None:
                print("")
                print("切视频: method=%s notches=%s" % (swipe.method, swipe.notches))

    if args.x is not None or args.y is not None:
        nx = 0.5 if args.x is None else args.x
        ny = 0.5 if args.y is None else args.y
        ax, ay = to_absolute(rect, nx, ny)
        print("归一化 (%.4f, %.4f) -> 绝对像素 (%d, %d)" % (nx, ny, ax, ay))

    if args.shot:
        if args.foreground:
            set_foreground(win["hwnd"])
        time.sleep(max(0.0, args.wait))
        marks = []
        if args.crosshair:
            for row in describe_actions(cfg):
                if row[1] != "click":
                    continue
                marks.append(to_absolute(rect, row[3], row[4]))
        for spec in args.mark:
            try:
                nx, ny = [float(part) for part in spec.split(",")]
            except ValueError:
                print("[warn] 忽略非法 --mark: %r" % spec)
                continue
            marks.append(to_absolute(rect, nx, ny))
        path = screenshot(win["hwnd"], rect, args.shot_path, marks, printwindow=args.printwindow)
        print("截图: %s (%dx%d)" % (path, w, h))

    return 0


if __name__ == "__main__":
    sys.exit(main())

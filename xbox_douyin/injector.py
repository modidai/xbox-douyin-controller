"""Windows 输入注入：ctypes + SendInput，不依赖 pywin32 / keyboard / mouse。"""
from __future__ import annotations

import ctypes
import math
import sys
import time

IS_WINDOWS = sys.platform.startswith("win")

# --- INPUT 类型 ---
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# --- MOUSEEVENTF_* ---
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000

# --- KEYEVENTF_* ---
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

WHEEL_DELTA = 120

# --- GetSystemMetrics 虚拟屏幕 ---
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1

# --- 进程/窗口常量 ---
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
GA_ROOT = 2
SW_RESTORE = 9
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
DWORD = ctypes.c_ulong
#: 必须用指针类型，默认 c_int 会把 64 位 HWND 截断
HWND = ctypes.c_void_p
LONG = ctypes.c_long
WORD = ctypes.c_ushort
SHORT = ctypes.c_short

VK_NAMES = {
    "esc": 0x1B,
    "escape": 0x1B,
    "tab": 0x09,
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "shift": VK_SHIFT,
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "win": VK_LWIN,
    "lwin": VK_LWIN,
    "plus": 0xBB,
    "minus": 0xBD,
    "comma": 0xBC,
    "period": 0xBE,
    "slash": 0xBF,
}
for _i in range(1, 13):
    VK_NAMES["f%d" % _i] = 0x6F + _i  # F1=0x70


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", DWORD),
        ("wParamL", WORD),
        ("wParamH", WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


# --- GDI 截图（亮度采样用）---
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

HDC = ctypes.c_void_p
HBITMAP = ctypes.c_void_p
HANDLE = ctypes.c_void_p


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", WORD),
        ("biBitCount", WORD),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 3)]


_user32 = None
_kernel32 = None
_gdi32 = None


def _get_user32():
    global _user32
    if _user32 is None:
        if not IS_WINDOWS:
            return None
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.restype = ctypes.c_uint
        user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.GetAsyncKeyState.restype = SHORT
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetForegroundWindow.restype = HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetAncestor.restype = HWND
        user32.GetAncestor.argtypes = [HWND, ctypes.c_uint]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [HWND, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextLengthW.argtypes = [HWND]
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
        user32.GetWindowThreadProcessId.restype = DWORD
        user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.c_void_p]
        user32.IsWindowVisible.argtypes = [HWND]
        user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(_RECT)]
        user32.ClientToScreen.argtypes = [HWND, ctypes.POINTER(_POINT)]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetDC.restype = HDC
        user32.GetDC.argtypes = [HWND]
        user32.ReleaseDC.restype = ctypes.c_int
        user32.ReleaseDC.argtypes = [HWND, HDC]
        _user32 = user32
    return _user32


def _get_kernel32():
    global _kernel32
    if _kernel32 is None:
        if not IS_WINDOWS:
            return None
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = DWORD
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.OpenProcess.restype = HWND
        kernel32.OpenProcess.argtypes = [DWORD, ctypes.c_int, DWORD]
        kernel32.CloseHandle.argtypes = [HWND]
        _kernel32 = kernel32
    return _kernel32


def _get_gdi32():
    global _gdi32
    if _gdi32 is None:
        if not IS_WINDOWS:
            return None
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi32.CreateCompatibleDC.restype = HDC
        gdi32.CreateCompatibleDC.argtypes = [HDC]
        gdi32.CreateCompatibleBitmap.restype = HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = HANDLE
        gdi32.SelectObject.argtypes = [HDC, HANDLE]
        gdi32.BitBlt.restype = ctypes.c_int
        gdi32.BitBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HDC, ctypes.c_int, ctypes.c_int, DWORD]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [HDC, HBITMAP, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint]
        gdi32.DeleteObject.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [HANDLE]
        gdi32.DeleteDC.restype = ctypes.c_int
        gdi32.DeleteDC.argtypes = [HDC]
        _gdi32 = gdi32
    return _gdi32


def vk_for(name):
    """键名 -> 虚拟键码。"esc" / "alt" / "down" / "a" / "1" 等。"""
    if not name:
        return None
    key = str(name).strip().lower()
    if not key:
        return None
    if key in VK_NAMES:
        return VK_NAMES[key]
    if len(key) == 1:
        return ord(key.upper())
    return None


def key_is_down(name):
    """GetAsyncKeyState 判当前是否按下（F8 全局热键用）。"""
    vk = vk_for(name)
    if vk is None:
        return False
    user32 = _get_user32()
    if user32 is None:
        return False
    return (user32.GetAsyncKeyState(int(vk)) & 0x8000) != 0


class Injector(object):
    """鼠标 / 键盘注入。相对移动内部累加小数像素，不丢精度。"""

    #: 绝对定位后等一小会儿再点击，避免点早于光标落位
    settle_s = 0.02
    #: 目标窗口矩形缓存时长（秒），避免每次动作都枚举窗口
    rect_ttl_s = 1.0

    def __init__(self, cfg, out=None, dry_run=False):
        self.cfg = cfg
        self._out = out
        #: dry_run=True 时只打印不真的注入（run.py --dry-run）
        self.dry_run = bool(dry_run)
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._rect = None
        self._rect_at = 0.0
        self._warned_no_window = False
        self._warn_at = {}

    # ---- 内部 ----
    def _log(self, message):
        if self._out is not None:
            self._out(message)

    def _warn(self, message):
        if self._warned_no_window:
            return
        self._warned_no_window = True
        self._log(message)

    def _warn_throttled(self, message, window_s=3.0):
        """同一条警告 3 秒内只打一次。

        按消息分别计时：抖音不在前台时用户交替按返回/切视频，
        两种警告各自只出现一次，不会因为互相顶掉而刷屏。
        """
        now = time.time()
        last = self._warn_at.get(message)
        if last is not None and (now - last) < window_s:
            return
        self._warn_at[message] = now
        self._log(message)

    def _foreground_root(self):
        user32 = _get_user32()
        if user32 is None:
            return 0
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        root = user32.GetAncestor(HWND(int(hwnd)), GA_ROOT)
        return int(root or hwnd)

    def focus_target(self):
        """把目标窗口（抖音）拉到前台并校验。找不到窗口/抢不到都返回 False，绝不抛。"""
        try:
            user32 = _get_user32()
            if user32 is None:
                return False
            entry = self._target_window()
            if entry is None:
                return False
            hwnd = int(entry["hwnd"])
            if self._foreground_root() == hwnd:
                return True
            kernel32 = _get_kernel32()
            my_thread = int(kernel32.GetCurrentThreadId()) if kernel32 is not None else 0
            front = user32.GetForegroundWindow()
            front_thread = int(user32.GetWindowThreadProcessId(front, None)) if front else 0
            attached = False
            if my_thread and front_thread and front_thread != my_thread:
                attached = bool(user32.AttachThreadInput(DWORD(my_thread), DWORD(front_thread), 1))
            try:
                user32.BringWindowToTop(HWND(hwnd))
                user32.ShowWindow(HWND(hwnd), SW_RESTORE)
                user32.SetForegroundWindow(HWND(hwnd))
                user32.SetFocus(HWND(hwnd))
            finally:
                if attached:
                    user32.AttachThreadInput(DWORD(my_thread), DWORD(front_thread), 0)
            return self._foreground_root() == hwnd
        except Exception as exc:
            self._log("[注入] 把目标窗口拉到前台时出错: %s" % exc)
            return False

    def _ensure_foreground(self, what):
        """True = 可以发事件；False = 目标不在前台，一个事件都别发。"""
        if not getattr(self.cfg, "ensure_foreground", True):
            return True
        if self.focus_target():
            return True
        self._warn_throttled("[注入] 抖音不在前台，已跳过%s" % what)
        return False

    # ---- 区域亮度采样（判断是不是普通视频；直播房间没有倍速按钮）----
    def sample_bright_ratio(self, roi, lum_threshold=160):
        """采样客户区某区域的亮度占比。拿不到就返回 None，绝不阻断调用方。

        roi: 客户区归一化 (x1, y1, x2, y2)
        返回: luma >= lum_threshold 的像素占比 0..1，或 None
        """
        rect = self.target_client_rect()
        if rect is None:
            self._warn_throttled("[注入] 没找到目标窗口，无法采样亮度")
            return None
        region = self._roi_to_screen(rect, roi)
        if region is None:
            self._warn_throttled("[注入] 亮度采样区域非法，已跳过")
            return None
        if self.dry_run:
            self._dry(
                "采样亮度区域 屏幕(%d, %d, %d, %d)，阈值 %d（dry-run 不读屏）"
                % (region[0], region[1], region[2], region[3], int(lum_threshold))
            )
            return None
        try:
            grab = self._grab_roi(region)
        except Exception as exc:
            self._log("[注入] 亮度采样失败: %s" % exc)
            return None
        if grab is None:
            return None
        return self._bright_ratio_from_pixels(grab, lum_threshold)

    def _roi_to_screen(self, rect, roi):
        """归一化 ROI -> 屏幕像素 (x, y, w, h)；非法返回 None。"""
        try:
            values = [float(item) for item in roi]
        except Exception:
            return None
        if len(values) != 4:
            return None
        if any(math.isnan(value) or math.isinf(value) for value in values):
            return None
        x1, y1, x2, y2 = values
        if x2 <= x1 or y2 <= y1:
            return None
        left, top, width, height = rect
        screen_x1 = int(round(left + min(max(x1, 0.0), 1.0) * width))
        screen_y1 = int(round(top + min(max(y1, 0.0), 1.0) * height))
        screen_x2 = int(round(left + min(max(x2, 0.0), 1.0) * width))
        screen_y2 = int(round(top + min(max(y2, 0.0), 1.0) * height))
        region_w = screen_x2 - screen_x1
        region_h = screen_y2 - screen_y1
        if region_w <= 0 or region_h <= 0:
            return None
        return (screen_x1, screen_y1, region_w, region_h)

    def _grab_roi(self, region):
        """抓屏幕上一小块，返回 (w, h, BGRA bytes) 或 None。

        走屏幕而不是 PrintWindow：此时目标窗口由 ensure_foreground 保证在前台。
        只抓 ROI 那几十像素，GDI 句柄用完立刻释放。
        """
        user32 = _get_user32()
        gdi32 = _get_gdi32()
        if user32 is None or gdi32 is None:
            return None
        x, y, width, height = [int(item) for item in region]
        if width <= 0 or height <= 0:
            return None
        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return None
        mem_dc = None
        bitmap = None
        old_bitmap = None
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not mem_dc:
                return None
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not bitmap:
                return None
            old_bitmap = gdi32.SelectObject(mem_dc, bitmap)
            if not gdi32.BitBlt(mem_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY):
                return None
            info = BITMAPINFO()
            header = info.bmiHeader
            header.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            header.biWidth = width
            header.biHeight = -height  # 负高度 = 自上而下
            header.biPlanes = 1
            header.biBitCount = 32
            header.biCompression = 0  # BI_RGB
            buffer = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info), DIB_RGB_COLORS)
            if not lines:
                return None
            return (width, height, buffer.raw)
        finally:
            # 句柄一律释放，顺序：还原选中的旧对象 -> 删位图 -> 删内存 DC -> 释放屏幕 DC
            if mem_dc and old_bitmap:
                gdi32.SelectObject(mem_dc, old_bitmap)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(None, screen_dc)

    def _bright_ratio_from_pixels(self, grab, lum_threshold):
        """(w, h, BGRA) -> 亮像素占比。像素太多时按步长抽样，保证不卡主循环。"""
        try:
            width, height, data = grab
            width = int(width)
            height = int(height)
        except Exception:
            return None
        if width <= 0 or height <= 0 or not data:
            return None
        need = width * height * 4
        if len(data) < need:
            return None
        threshold = float(lum_threshold)
        total_pixels = width * height
        stride = 1
        if total_pixels > 65536:
            stride = int(math.ceil(math.sqrt(total_pixels / 65536.0)))
        bright = 0
        counted = 0
        row_bytes = width * 4
        for row in range(0, height, stride):
            offset = row * row_bytes
            for col in range(0, width, stride):
                index = offset + col * 4
                blue = data[index]
                green = data[index + 1]
                red = data[index + 2]
                if 0.114 * blue + 0.587 * green + 0.299 * red >= threshold:
                    bright += 1
                counted += 1
        if not counted:
            return None
        return bright / float(counted)

    def _target_window(self):
        """目标窗口条目（hwnd/title/client/process），找不到返回 None。"""
        user32 = _get_user32()
        if user32 is None:
            return None
        title_key = str(getattr(self.cfg, "target_window", "") or "").strip().lower()
        process_key = str(getattr(self.cfg, "target_process", "") or "").strip().lower()
        if not title_key and not process_key:
            return None
        found = []
        _collect_windows(user32, found)
        if not found:
            return None
        if title_key:
            for entry in found:
                if title_key in entry["title"].lower():
                    return entry
        if process_key:
            for entry in found:
                if entry["process"] == process_key:
                    return entry
        return None

    def _dry(self, message):
        self._log("[dry] " + message)

    def _send(self, inputs):
        user32 = _get_user32()
        if user32 is None:
            raise RuntimeError("非 Windows 平台无法注入输入")
        count = len(inputs)
        array = (INPUT * count)(*inputs)
        sent = user32.SendInput(count, array, ctypes.sizeof(INPUT))
        if int(sent) != count:
            raise RuntimeError(
                "SendInput 只发出 %d/%d 个事件（可能被权限更高的窗口拦截）" % (int(sent), count)
            )
        return int(sent)

    def _mouse_input(self, flags, dx=0, dy=0, data=0):
        item = INPUT()
        item.type = INPUT_MOUSE
        item.mi = MOUSEINPUT(int(dx), int(dy), int(data), int(flags), 0, 0)
        return item

    def _key_input(self, vk, up=False):
        item = INPUT()
        item.type = INPUT_KEYBOARD
        flags = KEYEVENTF_KEYUP if up else 0
        item.ki = KEYBDINPUT(int(vk), 0, flags, 0, 0)
        return item

    # ---- 鼠标 ----
    def move_mouse(self, dx, dy):
        """相对移动；不足一像素的部分累加到下次。"""
        self._acc_x += float(dx)
        self._acc_y += float(dy)
        step_x = int(self._acc_x)
        step_y = int(self._acc_y)
        if step_x == 0 and step_y == 0:
            return
        self._acc_x -= step_x
        self._acc_y -= step_y
        if self.dry_run:
            self._dry("移动鼠标相对 (%+d, %+d)" % (step_x, step_y))
            return
        self._send([self._mouse_input(MOUSEEVENTF_MOVE, step_x, step_y)])

    def left_click(self):
        if self.dry_run:
            self._dry("左键单击")
            return
        self._send(
            [
                self._mouse_input(MOUSEEVENTF_LEFTDOWN),
                self._mouse_input(MOUSEEVENTF_LEFTUP),
            ]
        )

    def double_click(self, interval_ms=40):
        self.left_click()
        time.sleep(max(0, int(interval_ms)) / 1000.0)
        self.left_click()

    def wheel(self, notches):
        """正 = 上滚，负 = 下滚。返回 True 表示已发出，False 表示因前台不足被跳过。"""
        notches = int(notches)
        if notches == 0:
            return True
        if self.dry_run:
            self._dry("滚轮 %+d 格（%s滚）" % (notches, "上" if notches > 0 else "下"))
            return True
        # 滚轮打在光标所在的窗口上，抖音不在前台就别滚，免得滚到别人窗口
        if not self._ensure_foreground("滚轮"):
            return False
        data = notches * WHEEL_DELTA
        # mouseData 是 DWORD，负数要转成无符号补码
        if data < 0:
            data += 1 << 32
        self._send([self._mouse_input(MOUSEEVENTF_WHEEL, 0, 0, data)])
        return True

    def cursor_pos(self):
        user32 = _get_user32()
        if user32 is None:
            return (0, 0)
        buf = _POINT()
        if not user32.GetCursorPos(ctypes.byref(buf)):
            return (0, 0)
        return (int(buf.x), int(buf.y))

    def set_cursor(self, x, y):
        if self.dry_run:
            self._dry("光标移到 (%d, %d)" % (int(x), int(y)))
            return True
        user32 = _get_user32()
        if user32 is None:
            return False
        return bool(user32.SetCursorPos(int(x), int(y)))

    def click_normalized(self, x, y, restore=True):
        """按目标窗口客户区的归一化坐标点击一次。

        定位和按下/抬起放在同一次 SendInput 里（绝对坐标），
        这样即使别的东西同时在动光标，点击也落在算好的位置上。
        """
        rect = self.target_client_rect()
        if rect is None:
            self._warn("[注入] 没找到目标窗口（%s / %s），跳过点击" % (
                getattr(self.cfg, "target_window", ""),
                getattr(self.cfg, "target_process", ""),
            ))
            return
        x = min(max(float(x), 0.0), 1.0)
        y = min(max(float(y), 0.0), 1.0)
        target_x, target_y = self._client_to_screen(rect, x, y)
        if self.dry_run:
            self._dry(
                "点击客户区(%.2f, %.2f) -> 屏幕(%d, %d)%s"
                % (x, y, target_x, target_y, "，随后还原光标" if restore else "")
            )
            return
        saved = self.cursor_pos() if restore else None
        self._send(self._absolute_click_inputs(target_x, target_y))
        if saved is not None:
            if self.settle_s > 0:
                time.sleep(self.settle_s)
            self._restore_cursor(saved)

    def _client_to_screen(self, rect, nx, ny):
        """客户区归一化坐标 -> 屏幕像素坐标（越界会夹到 0..1）。"""
        left, top, width, height = rect
        nx = min(max(float(nx), 0.0), 1.0)
        ny = min(max(float(ny), 0.0), 1.0)
        return (int(round(left + nx * width)), int(round(top + ny * height)))

    def click_sequence(self, points, interval_ms=60, restore=True):
        """连续点多个点（hover 菜单专用）。

        用于「点倍速按钮 -> 点档位」这种两段连点：中间**不移动光标、不还原**，
        否则 hover 菜单会消失、第二下落到视频画面上变成播放/暂停。
        每个点都走同一条 _absolute_click_inputs 批次（绝对定位+按下+抬起）。
        """
        try:
            coords = [(float(item[0]), float(item[1])) for item in points]
        except Exception:
            self._warn_throttled("[注入] 连续点击的坐标格式不对，已跳过")
            return False
        if len(coords) < 2:
            self._warn_throttled("[注入] 连续点击至少要 2 个点，已跳过")
            return False
        rect = self.target_client_rect()
        if rect is None:
            self._warn(
                "[注入] 没找到目标窗口（%s / %s），跳过连续点击"
                % (
                    getattr(self.cfg, "target_window", ""),
                    getattr(self.cfg, "target_process", ""),
                )
            )
            return False
        targets = [self._client_to_screen(rect, nx, ny) for nx, ny in coords]
        if self.dry_run:
            self._dry(
                "连续点击 %d 个点 %s（间隔 %dms%s）"
                % (
                    len(targets),
                    ", ".join("(%d, %d)" % point for point in targets),
                    int(interval_ms),
                    "，最后还原光标" if restore else "",
                )
            )
            return True

        saved = self.cursor_pos() if restore else None
        gap_s = max(0, int(interval_ms)) / 1000.0
        for index, (target_x, target_y) in enumerate(targets):
            if index > 0 and gap_s > 0:
                time.sleep(gap_s)
            try:
                self._send(self._absolute_click_inputs(target_x, target_y))
            except Exception as exc:
                self._log("[注入] 连续点击第 %d 个点失败: %s" % (index + 1, exc))
                if saved is not None:
                    self._restore_cursor(saved)
                return False
        if saved is not None:
            if self.settle_s > 0:
                time.sleep(self.settle_s)
            self._restore_cursor(saved)
        return True

    def _absolute_click_inputs(self, x, y):
        """屏幕坐标 -> 单个 SendInput 批次（绝对定位 + 左键按下 + 抬起）。"""
        user32 = _get_user32()
        if user32 is None:
            raise RuntimeError("非 Windows 平台无法注入输入")
        origin_x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        origin_y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        span_x = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        span_y = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if span_x <= 1:
            origin_x = 0
            span_x = max(1, int(user32.GetSystemMetrics(SM_CXSCREEN)))
        if span_y <= 1:
            origin_y = 0
            span_y = max(1, int(user32.GetSystemMetrics(SM_CYSCREEN)))
        max_x = max(1, span_x - 1)
        max_y = max(1, span_y - 1)
        norm_x = int(round((int(x) - origin_x) * 65535.0 / max_x))
        norm_y = int(round((int(y) - origin_y) * 65535.0 / max_y))
        norm_x = min(max(norm_x, 0), 65535)
        norm_y = min(max(norm_y, 0), 65535)
        move_flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
        return [
            self._mouse_input(move_flags, norm_x, norm_y),
            self._mouse_input(MOUSEEVENTF_LEFTDOWN, 0, 0),
            self._mouse_input(MOUSEEVENTF_LEFTUP, 0, 0),
        ]

    def _restore_cursor(self, position, attempts=2):
        """把光标放回原处，并读回校验（放不回去要说出来，不能假装成功）。"""
        for _ in range(max(1, int(attempts))):
            self.set_cursor(position[0], position[1])
            if self.cursor_pos() == tuple(position):
                return True
        self._log("[注入] 光标没能还原到 %s（当前 %s）" % (tuple(position), self.cursor_pos()))
        return False

    # ---- 键盘 ----
    def press(self, combo):
        """"esc" / "alt+left" / "down" / 单字符。返回 True 表示已发出，False 表示被前台守卫跳过。"""
        tokens = [t for t in str(combo).split("+") if t.strip()]
        if not tokens:
            return False
        codes = []
        for token in tokens:
            vk = vk_for(token)
            if vk is None:
                raise ValueError("无法识别的按键: %r" % token)
            codes.append(vk)
        if self.dry_run:
            self._dry("按键 %s" % str(combo))
            return True
        # 按键只会进前台窗口，抖音不在前台就绝不能按下去（否则 esc/alt+left 打进别人窗口）
        if not self._ensure_foreground("按键"):
            return False
        modifiers = [vk for vk in codes if vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN)]
        keys = [vk for vk in codes if vk not in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN)]
        if not keys:
            keys = modifiers
            modifiers = []
        events = []
        for vk in modifiers:
            events.append(self._key_input(vk, up=False))
        for vk in keys:
            events.append(self._key_input(vk, up=False))
            events.append(self._key_input(vk, up=True))
        for vk in reversed(modifiers):
            events.append(self._key_input(vk, up=True))
        self._send(events)
        return True

    # ---- 目标窗口 ----
    def target_client_rect(self):
        """目标窗口客户区 (left, top, w, h)，屏幕坐标；找不到返回 None。"""
        now = time.time()
        if self._rect is not None and (now - self._rect_at) < self.rect_ttl_s:
            return self._rect
        rect = self._find_target_rect()
        self._rect = rect
        self._rect_at = now
        return rect

    def _find_target_rect(self):
        entry = self._target_window()
        if entry is None:
            return None
        return entry["client"]


class _POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p) if IS_WINDOWS else None


def _window_process_name(user32, kernel32, hwnd):
    pid = DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or kernel32 is None:
        return ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        path = buf.value
        return path.rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


def _client_rect(user32, hwnd):
    rect = _RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return None
    origin = _POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return (int(origin.x), int(origin.y), width, height)


def _collect_windows(user32, sink):
    """枚举可见、有客户区面积的顶层窗口。"""
    kernel32 = _get_kernel32()

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return 1
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        client = _client_rect(user32, hwnd)
        if client is None:
            return 1
        sink.append(
            {
                "hwnd": hwnd,
                "title": title,
                "client": client,
                "process": _window_process_name(user32, kernel32, hwnd),
            }
        )
        return 1

    proc = _WNDENUMPROC(callback)
    user32.EnumWindows(proc, 0)

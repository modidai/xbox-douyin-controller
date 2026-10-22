"""XInput 手柄读取：纯 ctypes，无第三方依赖。

非 Windows 平台上导入本模块不会报错，XInputReader.available 返回 False，
因此离线单元测试可以在任意平台跑。
"""
from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = sys.platform.startswith("win")

MAX_CONTROLLERS = 4

# --- 按键位掩码（与 xinput.h 一致）---
BUTTON_DPAD_UP = 0x0001
BUTTON_DPAD_DOWN = 0x0002
BUTTON_DPAD_LEFT = 0x0004
BUTTON_DPAD_RIGHT = 0x0008
BUTTON_START = 0x0010
BUTTON_BACK = 0x0020
BUTTON_LEFT_THUMB = 0x0040
BUTTON_RIGHT_THUMB = 0x0080
BUTTON_LEFT_SHOULDER = 0x0100
BUTTON_RIGHT_SHOULDER = 0x0200
BUTTON_A = 0x1000
BUTTON_B = 0x2000
BUTTON_X = 0x4000
BUTTON_Y = 0x8000

THUMB_MAX = 32767.0
TRIGGER_MAX = 255.0

BUTTON_NAMES = (
    (BUTTON_DPAD_UP, "dpad_up"),
    (BUTTON_DPAD_DOWN, "dpad_down"),
    (BUTTON_DPAD_LEFT, "dpad_left"),
    (BUTTON_DPAD_RIGHT, "dpad_right"),
    (BUTTON_START, "start"),
    (BUTTON_BACK, "back"),
    (BUTTON_LEFT_THUMB, "left_thumb"),
    (BUTTON_RIGHT_THUMB, "right_thumb"),
    (BUTTON_LEFT_SHOULDER, "lb"),
    (BUTTON_RIGHT_SHOULDER, "rb"),
    (BUTTON_A, "a"),
    (BUTTON_B, "b"),
    (BUTTON_X, "x"),
    (BUTTON_Y, "y"),
)


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", ctypes.c_ulong),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


def _clamp(value, low=-1.0, high=1.0):
    if value < low:
        return low
    if value > high:
        return high
    return value


def normalize_thumb(raw):
    """-32768..32767 -> -1.0..1.0（Y 轴正 = 上推，与 XInput 原生一致）。"""
    return _clamp(float(raw) / THUMB_MAX)


def normalize_trigger(raw):
    """0..255 -> 0.0..1.0。"""
    return _clamp(float(raw) / TRIGGER_MAX, 0.0, 1.0)


class PadState(object):
    """一帧手柄快照。浮点归一化，测试里可直接构造，不依赖 ctypes。"""

    __slots__ = (
        "buttons",
        "left_trigger",
        "right_trigger",
        "lx",
        "ly",
        "rx",
        "ry",
        "packet",
        "connected",
    )

    def __init__(
        self,
        buttons=0,
        left_trigger=0.0,
        right_trigger=0.0,
        lx=0.0,
        ly=0.0,
        rx=0.0,
        ry=0.0,
        packet=0,
        connected=True,
    ):
        self.buttons = int(buttons)
        self.left_trigger = float(left_trigger)
        self.right_trigger = float(right_trigger)
        self.lx = float(lx)
        self.ly = float(ly)
        self.rx = float(rx)
        self.ry = float(ry)
        self.packet = int(packet)
        self.connected = bool(connected)

    def pressed(self, mask):
        return (self.buttons & mask) != 0

    def __repr__(self):
        pressed = [name for mask, name in BUTTON_NAMES if self.buttons & mask]
        return "PadState(buttons=%s lt=%.2f rt=%.2f l=(%.2f,%.2f) r=(%.2f,%.2f))" % (
            "|".join(pressed) or "-",
            self.left_trigger,
            self.right_trigger,
            self.lx,
            self.ly,
            self.rx,
            self.ry,
        )


def to_pad_state(raw):
    """把 ctypes 的 XINPUT_STATE 转成 PadState。"""
    pad = raw.Gamepad
    return PadState(
        buttons=pad.wButtons,
        left_trigger=normalize_trigger(pad.bLeftTrigger),
        right_trigger=normalize_trigger(pad.bRightTrigger),
        lx=normalize_thumb(pad.sThumbLX),
        ly=normalize_thumb(pad.sThumbLY),
        rx=normalize_thumb(pad.sThumbRX),
        ry=normalize_thumb(pad.sThumbRY),
        packet=int(raw.dwPacketNumber),
    )


class XInputReader(object):
    """轮询 Xbox 手柄。slot=None 时自动找第一个插着手柄的槽位。"""

    def __init__(self, slot=None):
        self.slot = slot
        self._dll = None
        self._loaded = False

    # ---- DLL 装载 ----
    def _load(self):
        if self._loaded:
            return self._dll
        self._loaded = True
        if not IS_WINDOWS:
            return None
        for name in ("XInput1_4.dll", "XInput1_3.dll", "XInput9_1_0.dll"):
            try:
                dll = ctypes.WinDLL(name)
            except OSError:
                continue
            try:
                dll.XInputGetState.restype = ctypes.c_ulong
                dll.XInputGetState.argtypes = [
                    ctypes.c_ulong,
                    ctypes.POINTER(XINPUT_STATE),
                ]
            except AttributeError:
                continue
            self._dll = dll
            return dll
        return None

    @property
    def available(self):
        return self._load() is not None

    # ---- 读取 ----
    def raw_state(self, slot):
        """返回原始 XINPUT_STATE（调试/验证用），读不到返回 None。"""
        dll = self._load()
        if dll is None:
            return None
        state = XINPUT_STATE()
        code = dll.XInputGetState(ctypes.c_ulong(int(slot)), ctypes.byref(state))
        if code != 0:
            return None
        return state

    def find_slot(self):
        """返回可用槽位下标，没有手柄返回 None。"""
        if self.slot is not None:
            if self.raw_state(self.slot) is not None:
                return self.slot
            self.slot = None
        for slot in range(MAX_CONTROLLERS):
            if self.raw_state(slot) is not None:
                self.slot = slot
                return slot
        return None

    def read(self, slot=None):
        """读一帧，未连接返回 None。"""
        target = self.slot if slot is None else slot
        if target is None:
            return None
        raw = self.raw_state(target)
        if raw is None:
            return None
        return to_pad_state(raw)

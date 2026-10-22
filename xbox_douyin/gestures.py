"""手势 / 输入态识别：纯逻辑，无 Windows 依赖，时钟可注入，可直接单测。"""
from __future__ import annotations

import math


def _field(obj, name, default):
    """同时兼容 dataclass 和 dict 的取字段。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class EdgeTrigger(object):
    """带迟滞（hysteresis）的阈值上穿检测。

    - value >= threshold 且处于武装状态 -> 返回 True 并解除武装（上穿）
    - value <= release -> 重新武装
    - cooldown_ms > 0 时，两次触发至少间隔这么久（抑制连发）
    """

    def __init__(self, threshold, release, cooldown_ms=0):
        self.threshold = float(threshold)
        self.release = float(release)
        if self.release > self.threshold:
            raise ValueError("release 不能大于 threshold")
        self.cooldown = max(0.0, float(cooldown_ms) / 1000.0)
        self._armed = True
        self._last = None

    @property
    def armed(self):
        return self._armed

    def reset(self):
        self._armed = True
        self._last = None

    def update(self, value, now):
        value = float(value)
        now = float(now)
        if value <= self.release:
            # 回落到释放阈值以下 -> 重新武装（迟滞区：release < value < threshold 保持原状态）
            self._armed = True
            return False
        if not self._armed:
            return False
        if value < self.threshold:
            return False
        if self._last is not None and (now - self._last) < self.cooldown:
            return False
        self._armed = False
        self._last = now
        return True


class DoubleTapDetector(object):
    """双击判定：窗口内第二次按下返回 True，超时则重新计时。"""

    def __init__(self, window_ms):
        self.window = max(0.0, float(window_ms) / 1000.0)
        self._last = None

    def reset(self):
        self._last = None

    def tap(self, now):
        now = float(now)
        if self._last is not None and (now - self._last) <= self.window:
            self._last = None
            return True
        self._last = now
        return False


def stick_to_mouse_delta(x, y, dt, cfg):
    """右摇杆 -> 鼠标相对位移（px）。死区 + 响应曲线 + 限速，Y 轴可反转。"""
    x = _clamp_unit(float(x))
    y = _clamp_unit(float(y))
    dt = float(dt)
    if dt <= 0.0:
        return (0.0, 0.0)

    mouse = _field(cfg, "mouse", None)
    if mouse is None:
        mouse = cfg

    deadzone = _field(mouse, "deadzone", None)
    if deadzone is None:
        deadzone = _field(cfg, "deadzone", 0.15)
    deadzone = float(deadzone or 0.0)
    if deadzone < 0.0:
        deadzone = 0.0
    if deadzone > 0.95:
        deadzone = 0.95

    magnitude = math.hypot(x, y)
    if magnitude <= 0.0 or magnitude <= deadzone:
        return (0.0, 0.0)

    # 死区之外重新映射到 0..1，保证刚出死区时是慢速
    scaled = (min(magnitude, 1.0) - deadzone) / (1.0 - deadzone)
    if scaled <= 0.0:
        return (0.0, 0.0)

    curve = float(_field(mouse, "curve", 1.0) or 1.0)
    if curve <= 0.0:
        curve = 1.0
    shaped = scaled ** curve

    max_speed = float(_field(mouse, "max_speed_px_s", 0.0) or 0.0)
    speed = max_speed * shaped

    unit_x = x / magnitude
    unit_y = y / magnitude
    dx = unit_x * speed * dt
    dy = unit_y * speed * dt

    if _field(mouse, "invert_y", False):
        dy = -dy
    return (dx, dy)


def _clamp_unit(value):
    if value < -1.0:
        return -1.0
    if value > 1.0:
        return 1.0
    return value

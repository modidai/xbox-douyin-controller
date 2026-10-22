"""主循环：手柄轮询 -> 手势识别 -> 动作 / 输入注入。

注意：本模块被导入时不依赖 douyin.py（那是另一个模块交付的），
真正把 DouyinActions 装配进来只发生在 run.py 里。
"""
from __future__ import annotations

import time
from dataclasses import fields as _dataclass_fields

from .config import (
    ActionConfig,
    Config,
    MouseConfig,
    StickConfig,
    SwipeConfig,
    TapConfig,
    TriggerConfig,
)
from .gestures import DoubleTapDetector, EdgeTrigger, stick_to_mouse_delta
from .xinput import (
    BUTTON_A,
    BUTTON_B,
    BUTTON_LEFT_SHOULDER,
    BUTTON_RIGHT_SHOULDER,
    BUTTON_X,
    BUTTON_Y,
    PadState,
)

try:  # 老版本 config.py 没有这些 group，缺了就退回内置默认值
    from .config import HoldConfig
except ImportError:  # pragma: no cover
    HoldConfig = None

try:
    from .config import SpeedConfig
except ImportError:  # pragma: no cover
    SpeedConfig = None

#: 日志级别：数字越大越严重
_LOG_RANK = {"debug": 0, "info": 1, "warn": 2, "error": 3}
#: cfg.log_level -> 最低输出级别（quiet 只留错误）
_LOG_MIN = {"debug": 0, "info": 1, "quiet": 3}

_GROUP_TYPES = {
    "stick": StickConfig,
    "mouse": MouseConfig,
    "trigger": TriggerConfig,
    "tap": TapConfig,
    "swipe": SwipeConfig,
    "like": ActionConfig,
    "back": ActionConfig,
    "favorite": ActionConfig,
    "comments": ActionConfig,
}

if HoldConfig is not None:
    _GROUP_TYPES["rb"] = HoldConfig
    _GROUP_TYPES["lb"] = HoldConfig
if SpeedConfig is not None:
    _GROUP_TYPES["speed"] = SpeedConfig

ACTION_LABELS = {
    "swipe_up": "上一条",
    "swipe_down": "下一条",
    "like": "点赞",
    "back": "返回",
    "favorite": "收藏",
    "open_comments": "打开评论",
    "mouse_move": "移动鼠标",
    "click": "点击",
    "seek_forward": "快进",
    "seek_backward": "快退",
    "set_speed": "切换倍速",
}

#: 肩键在配置缺失时的兜底（与 config.py 的默认值一致）
_SHOULDER_DEFAULTS = {
    "rb": ("none", "2x"),
    "lb": ("seek_backward", "0.75x"),
}

SHOULDER_BUTTONS = (
    (BUTTON_RIGHT_SHOULDER, "rb"),
    (BUTTON_LEFT_SHOULDER, "lb"),
)


def _get(obj, name, default):
    """兼容 dataclass / dict / None 的取字段。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class _ShoulderState(object):
    """单个肩键的状态机：按下只计时，到阈值才切倍速，松开时按长短分流。"""

    __slots__ = (
        "name",
        "long_ms",
        "short_action",
        "hold_speed",
        "restore_speed",
        "down",
        "armed",
        "pressed_at",
        "hold_fired",
    )

    def __init__(self, name, hold_cfg=None):
        fallback_short, fallback_speed = _SHOULDER_DEFAULTS.get(name, ("none", "2x"))
        self.name = name
        self.long_ms = max(0.0, float(_get(hold_cfg, "long_ms", 400) or 0))
        self.short_action = str(_get(hold_cfg, "short_action", fallback_short) or "none").strip().lower()
        self.hold_speed = str(_get(hold_cfg, "hold_speed", fallback_speed) or "")
        self.restore_speed = str(_get(hold_cfg, "restore_speed", "1x") or "1x")
        self.down = False
        self.armed = False
        self.pressed_at = 0.0
        self.hold_fired = False

    def reset(self):
        self.down = False
        self.armed = False
        self.pressed_at = 0.0
        self.hold_fired = False

#: 按键类动作的按下/松开判定点
_BUTTON_PRESS = 0.5


def normalize_config(cfg):
    """修 config.py 的嵌套 group 解析问题。

    config.py 用了 from __future__ import annotations，dataclasses 拿到的
    Field.type 是字符串，Config.from_dict 里 is_dataclass(sub) 恒为 False，
    于是 JSON 里的 stick/mouse/... 会被原样塞成 dict。这里补回 dataclass。
    """
    if cfg is None:
        return Config()
    for name, group_type in _GROUP_TYPES.items():
        value = getattr(cfg, name, None)
        if isinstance(value, dict):
            allowed = {item.name for item in _dataclass_fields(group_type)}
            setattr(cfg, name, group_type(**{k: v for k, v in value.items() if k in allowed}))
    mouse = getattr(cfg, "mouse", None)
    if isinstance(mouse, MouseConfig) and not getattr(mouse, "deadzone", None):
        # cfg.deadzone 是顶层冗余字段，用作 mouse.deadzone 的兜底
        setattr(mouse, "deadzone", float(getattr(cfg, "deadzone", 0.15) or 0.15))
    return cfg


class NullActions(object):
    """六个动作的空实现，自检 / dry-run 用。"""

    def swipe_up(self):
        pass

    def swipe_down(self):
        pass

    def like(self):
        pass

    def back(self):
        pass

    def favorite(self):
        pass

    def open_comments(self):
        pass

    def seek_forward(self):
        pass

    def seek_backward(self):
        pass

    def set_speed(self, label):
        pass


class _RecordingInjector(object):
    """自检专用假注入器：只记账，绝不碰真实输入。"""

    def __init__(self):
        self.events = []

    def move_mouse(self, dx, dy):
        self.events.append(("move_mouse", round(float(dx), 3), round(float(dy), 3)))

    def left_click(self):
        self.events.append(("left_click",))


class App(object):
    """轮询手柄并执行动作。动作对象只需六个方法：swipe_up/swipe_down/like/
    back/favorite/open_comments。"""

    def __init__(self, cfg, actions, injector=None, clock=None, out=print, key_state=None):
        self.cfg = normalize_config(cfg)
        self.actions = actions if actions is not None else NullActions()
        self.injector = injector
        self.clock = clock or time.monotonic
        self.out = out if out is not None else (lambda message: None)
        self.min_log = _LOG_MIN.get(str(getattr(self.cfg, "log_level", "info")), 1)
        self._key_down = key_state
        self._hotkey_prev = False
        self.enabled = bool(getattr(self.cfg, "start_enabled", True))
        self._last_now = None
        self._running = False
        self._frame = []
        self._stick_dir = None
        self._stick_next = 0.0
        self._shoulders = {
            key: _ShoulderState(key, _get(self.cfg, key, None)) for _mask, key in SHOULDER_BUTTONS
        }
        self._reset_gestures()

    # ---- 日志 ----
    def log(self, message, level="info"):
        if _LOG_RANK.get(level, 1) < self.min_log:
            return
        try:
            self.out(message)
        except Exception:
            pass

    # ---- 手势状态 ----
    def _reset_gestures(self):
        stick = self.cfg.stick
        trigger = self.cfg.trigger
        tap = self.cfg.tap
        self._stick_up = EdgeTrigger(float(stick.threshold), float(stick.release))
        self._stick_down = EdgeTrigger(float(stick.threshold), float(stick.release))
        self._stick_dir = None
        self._stick_next = 0.0
        self._rt = EdgeTrigger(
            float(trigger.threshold), float(trigger.release), float(trigger.cooldown_ms)
        )
        self._a_edge = EdgeTrigger(_BUTTON_PRESS, _BUTTON_PRESS)
        self._b_edge = EdgeTrigger(_BUTTON_PRESS, _BUTTON_PRESS)
        self._x_edge = EdgeTrigger(_BUTTON_PRESS, _BUTTON_PRESS)
        self._y_edge = EdgeTrigger(_BUTTON_PRESS, _BUTTON_PRESS)
        self._a_tap = DoubleTapDetector(float(tap.window_ms))
        self._y_tap = DoubleTapDetector(float(tap.window_ms))
        for shoulder in getattr(self, "_shoulders", {}).values():
            shoulder.reset()

    # ---- 启停 ----
    def _poll_toggle(self):
        try:
            getter = self._key_down
            if getter is None:
                from .injector import key_is_down

                getter = key_is_down
            down = bool(getter(self.cfg.toggle_key))
        except Exception:
            down = False
        fired = down and not self._hotkey_prev
        self._hotkey_prev = down
        return fired

    def toggle_enabled(self, enabled=None):
        if enabled is None:
            enabled = not self.enabled
        enabled = bool(enabled)
        if enabled == self.enabled:
            return self.enabled
        self.enabled = enabled
        self._frame.append("toggle_on" if enabled else "toggle_off")
        self.log("[启停] 手柄控制" + ("已启用" if enabled else "已停用"), "info")
        if not enabled:
            # 停用瞬间若正处在长按倍速，立刻复位，别把播放速度丢在 2x
            self._release_active_holds("停用")
        return self.enabled

    # ---- 单帧处理 ----
    def step(self, state, now=None, toggle=None):
        """处理一帧手柄状态，返回本帧产生的动作名列表。"""
        if now is None:
            now = self.clock()
        now = float(now)
        self._frame = []
        if toggle is None:
            toggle = self._poll_toggle()
        if toggle:
            self.toggle_enabled()
        if not self.enabled:
            self._last_now = now
            # 只跟随真实按键状态，绝不产生动作；松手时也不会补发
            self._discard_shoulders(state)
            fired = self._frame
            self._frame = []
            return fired

        dt = 0.0 if self._last_now is None else (now - self._last_now)
        if dt < 0.0:
            dt = 0.0
        self._last_now = now

        self._step_stick(state, now)
        self._step_buttons(state, now)
        self._step_shoulders(state, now)
        self._step_trigger(state, now)
        self._step_mouse(state, dt)

        fired = self._frame
        self._frame = []
        return fired

    def _call(self, action_name):
        func = getattr(self.actions, action_name, None)
        if not callable(func):
            self.log("[动作] 动作对象缺少方法 %s" % action_name, "error")
            return False
        label = ACTION_LABELS.get(action_name, action_name)
        try:
            func()
        except Exception as exc:
            self.log("[动作] %s（%s）执行失败: %s" % (label, action_name, exc), "error")
            return False
        self._frame.append(action_name)
        self.log("[动作] %s（%s）" % (label, action_name), "info")
        return True

    def _fire_stick(self, direction, now):
        stick = self.cfg.stick
        action_name = stick.up_action if direction == "up" else stick.down_action
        self._stick_dir = direction
        self._stick_next = now + max(0.0, float(stick.repeat_ms) / 1000.0)
        self._call(action_name)

    def _step_stick(self, state, now):
        stick = self.cfg.stick
        release = float(stick.release)
        ly = float(state.ly)

        if self._stick_up.update(ly, now):
            self._fire_stick("up", now)
        if self._stick_down.update(-ly, now):
            self._fire_stick("down", now)

        held = None
        if ly >= release:
            held = "up"
        elif -ly >= release:
            held = "down"
        if self._stick_dir is not None and held != self._stick_dir:
            self._stick_dir = None
        if self._stick_dir is not None and now >= self._stick_next:
            self._fire_stick(self._stick_dir, now)

    def _step_buttons(self, state, now):
        if self._a_edge.update(1.0 if state.pressed(BUTTON_A) else 0.0, now):
            if self._a_tap.tap(now):
                self._call("like")
        if self._b_edge.update(1.0 if state.pressed(BUTTON_B) else 0.0, now):
            self._call("back")
        if self._y_edge.update(1.0 if state.pressed(BUTTON_Y) else 0.0, now):
            if self._y_tap.tap(now):
                self._call("favorite")
        if self._x_edge.update(1.0 if state.pressed(BUTTON_X) else 0.0, now):
            self._call("open_comments")

    # ---- 肩键：短按快进/快退，长按倍速 ----
    def _step_shoulders(self, state, now):
        for mask, key in SHOULDER_BUTTONS:
            shoulder = self._shoulders[key]
            pressed = state.pressed(mask)
            if pressed and not shoulder.down:
                # 按下：只开始计时，绝不立刻发短按动作
                shoulder.down = True
                shoulder.armed = True
                shoulder.pressed_at = now
                shoulder.hold_fired = False
            elif pressed and shoulder.down:
                if shoulder.armed and not shoulder.hold_fired:
                    if (now - shoulder.pressed_at) * 1000.0 >= shoulder.long_ms:
                        # 到阈值：切倍速，且只切一次（不是每帧重发）
                        shoulder.hold_fired = True
                        self._apply_speed(shoulder, shoulder.hold_speed)
            elif not pressed and shoulder.down:
                shoulder.down = False
                armed = shoulder.armed
                shoulder.armed = False
                if armed:
                    self._release_shoulder(shoulder, now)
                shoulder.hold_fired = False

    def _release_shoulder(self, shoulder, now):
        elapsed_ms = (now - shoulder.pressed_at) * 1000.0
        if not shoulder.hold_fired and elapsed_ms >= shoulder.long_ms:
            # 松手这一帧才发现已过阈值（轮询卡顿等）：仍按长按处理，序列保持完整
            shoulder.hold_fired = True
            self._apply_speed(shoulder, shoulder.hold_speed)
        if shoulder.hold_fired:
            self._apply_speed(shoulder, shoulder.restore_speed)
            return
        action = shoulder.short_action
        if action in ("", "none"):
            self.log("[动作] %s 短按未配置动作，忽略" % shoulder.name.upper(), "debug")
            return
        self._call_shoulder(action)

    def _apply_speed(self, shoulder, speed_label):
        if not speed_label:
            return False
        return self._call_shoulder("set_speed", speed_label)

    def _call_shoulder(self, method_name, arg=None):
        func = getattr(self.actions, method_name, None)
        if not callable(func):
            self.log("[动作] 动作对象缺少方法 %s" % method_name, "error")
            return False
        try:
            result = func() if arg is None else func(arg)
        except Exception as exc:
            self.log("[动作] %s 执行失败: %s" % (method_name, exc), "error")
            return False
        record = method_name if arg is None else "%s:%s" % (method_name, arg)
        self._frame.append(record)
        label = ACTION_LABELS.get(method_name, method_name)
        if result is False:
            # 动作层可能因场景不支持而返回 False（如直播房间没有倍速）：只记录，不重试
            self.log("[动作] %s（%s）返回失败，已记录不重试" % (label, record), "warn")
        else:
            self.log("[动作] %s（%s）" % (label, record), "info")
        return result is not False

    def _discard_shoulders(self, state):
        """停用期间：跟随真实按键状态但不产生动作，也不给松手留残局。"""
        for mask, key in SHOULDER_BUTTONS:
            shoulder = self._shoulders[key]
            shoulder.down = state.pressed(mask)
            shoulder.armed = False
            shoulder.hold_fired = False
            shoulder.pressed_at = 0.0

    def _release_active_holds(self, reason):
        """把还挂在长按倍速上的肩键复位（停用 / 断开 / 退出时用）。"""
        for shoulder in self._shoulders.values():
            if shoulder.hold_fired:
                shoulder.hold_fired = False
                self.log("[动作] %s 长按未正常松开（%s），补一次速度复位" % (shoulder.name.upper(), reason), "debug")
                self._apply_speed(shoulder, shoulder.restore_speed)

    def handle_disconnect(self):
        """手柄断开：按「视为松开」处理，宁可多发一次复位，也不卡在 2x。"""
        self._release_active_holds("断开")
        for shoulder in self._shoulders.values():
            shoulder.reset()
        for edge in (self._stick_up, self._stick_down, self._a_edge, self._b_edge, self._x_edge, self._y_edge, self._rt):
            edge.reset()
        self._a_tap.reset()
        self._y_tap.reset()
        self._stick_dir = None
        self._stick_next = 0.0
        self.log("[手柄] 已清空输入状态", "debug")

    def _step_trigger(self, state, now):
        if not self._rt.update(float(state.right_trigger), now):
            return
        if self.injector is None:
            self._frame.append("click")
            self.log("[动作] 点击（RT，未接注入器，跳过）", "debug")
            return
        try:
            self.injector.left_click()
        except Exception as exc:
            self.log("[动作] 点击（RT）失败: %s" % exc, "error")
            return
        self._frame.append("click")
        self.log("[动作] 点击（RT）", "info")

    def _step_mouse(self, state, dt):
        if self.injector is None or dt <= 0.0:
            return
        dx, dy = stick_to_mouse_delta(state.rx, state.ry, dt, self.cfg)
        if dx == 0.0 and dy == 0.0:
            return
        try:
            self.injector.move_mouse(dx, dy)
        except Exception as exc:
            self.log("[动作] 移动鼠标失败: %s" % exc, "error")
            return
        self._frame.append("mouse_move")
        self.log("[动作] 移动鼠标（%+.1f, %+.1f）" % (dx, dy), "debug")

    # ---- 主循环 ----
    def run(self):
        try:
            from .xinput import XInputReader
        except Exception as exc:
            self.log("[手柄] 无法加载 XInput 模块: %s" % exc, "error")
            return 1

        reader = XInputReader()
        if not reader.available:
            self.log("[手柄] 当前平台/系统没有可用的 XInput 运行时", "error")
            return 1
        slot = reader.find_slot()
        if slot is None:
            self.log("[手柄] 没检测到手柄，请先连接 Xbox 手柄（XInput 槽位 0-3）", "error")
            return 1

        poll_hz = max(1, int(getattr(self.cfg, "poll_hz", 125) or 125))
        period = 1.0 / poll_hz
        self.log(
            "[手柄] 已连接槽位 %d，轮询 %dHz，当前状态：%s"
            % (slot, poll_hz, "启用" if self.enabled else "停用"),
            "info",
        )
        self._running = True
        self._last_now = self.clock()
        try:
            while self._running:
                frame_start = self.clock()
                state = reader.read(slot)
                if state is None:
                    self.log("[手柄] 槽位 %d 连接断开" % slot, "error")
                    self.handle_disconnect()
                    slot = self._reconnect(reader)
                    if slot is None:
                        break
                    self.log("[手柄] 已重新连接，槽位 %d" % slot, "info")
                    self._last_now = self.clock()
                    continue
                self.step(state)
                elapsed = self.clock() - frame_start
                if elapsed < period:
                    time.sleep(period - elapsed)
        except KeyboardInterrupt:
            self.log("[退出] 收到 Ctrl+C，正在停止", "info")
        finally:
            self.close()
        return 0

    def _reconnect(self, reader, timeout_s=5.0):
        reader.slot = None
        deadline = self.clock() + timeout_s
        while self._running and self.clock() < deadline:
            time.sleep(0.25)
            slot = reader.find_slot()
            if slot is not None:
                return slot
        return None

    def close(self):
        self._running = False
        self._release_active_holds("退出")
        closer = getattr(self.injector, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        self.log("[退出] 已停止", "debug")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    # ---- 离线自检 ----
    def run_selftest(self):
        """脚本化假手柄状态走完整流水线（不真注入），返回动作序列。"""
        cfg = self.cfg
        stick = cfg.stick
        poll_hz = max(1, int(getattr(cfg, "poll_hz", 125) or 125))
        frame_ms = int(1000.0 / poll_hz)
        repeat_ms = int(max(0.0, float(stick.repeat_ms)))
        tap_half_ms = int(max(0.0, float(cfg.tap.window_ms)) / 2) + 10
        cooldown_ms = int(max(0.0, float(cfg.trigger.cooldown_ms)))

        script = []

        def add(ms, toggle=False, **pad):
            script.append((int(max(1, ms)), bool(toggle), pad))

        # 左摇杆上下 + 重复
        add(frame_ms, ly=0.0)
        add(frame_ms, ly=0.95)
        add(frame_ms, ly=0.95)
        add(repeat_ms + 20, ly=0.95)
        add(frame_ms, ly=0.0)
        add(frame_ms, ly=-0.95)
        add(frame_ms, ly=0.0)
        # A 双击 -> like
        add(frame_ms, buttons=BUTTON_A)
        add(frame_ms, buttons=0)
        add(tap_half_ms, buttons=BUTTON_A)
        add(frame_ms, buttons=0)
        # B 单击 -> back
        add(frame_ms, buttons=BUTTON_B)
        add(frame_ms, buttons=0)
        # Y 双击 -> favorite
        add(frame_ms, buttons=BUTTON_Y)
        add(frame_ms, buttons=0)
        add(tap_half_ms, buttons=BUTTON_Y)
        add(frame_ms, buttons=0)
        # X 单击 -> open_comments
        add(frame_ms, buttons=BUTTON_X)
        add(frame_ms, buttons=0)
        # 肩键短按（松开才发）：RB 默认 short_action="none" 因此无动作，LB 默认 seek_backward
        shoulder_short_ms = max(1, int(float(getattr(cfg, "rb", None).long_ms if getattr(cfg, "rb", None) else 400) // 2))
        add(frame_ms, buttons=BUTTON_RIGHT_SHOULDER)
        add(shoulder_short_ms, buttons=0)
        add(frame_ms, buttons=BUTTON_LEFT_SHOULDER)
        add(shoulder_short_ms, buttons=0)
        # 肩键长按 -> 倍速（阈值那帧切一次），松开恢复
        add(frame_ms, buttons=BUTTON_RIGHT_SHOULDER)
        add(shoulder_short_ms, buttons=BUTTON_RIGHT_SHOULDER)
        add(shoulder_short_ms, buttons=BUTTON_RIGHT_SHOULDER)
        add(int(float(cfg.rb.long_ms)) + 20, buttons=BUTTON_RIGHT_SHOULDER)
        add(frame_ms, buttons=0)
        add(frame_ms, buttons=BUTTON_LEFT_SHOULDER)
        add(int(float(cfg.lb.long_ms)) + 20, buttons=BUTTON_LEFT_SHOULDER)
        add(frame_ms, buttons=0)
        # 右摇杆 -> 鼠标移动
        add(50, ry=0.9)
        add(frame_ms, ry=0.0)
        # RT -> 点击（冷却期内再按不触发）
        add(frame_ms, right_trigger=1.0)
        add(frame_ms, right_trigger=0.0)
        add(max(1, cooldown_ms // 4), right_trigger=1.0)
        add(frame_ms, right_trigger=0.0)
        # F8 停用：手柄输入不应产生任何动作
        add(frame_ms, toggle=True)
        add(frame_ms, ly=0.95)
        add(repeat_ms + 20, ly=0.95)
        add(frame_ms, buttons=BUTTON_A)
        add(frame_ms, buttons=BUTTON_A)
        add(frame_ms, right_trigger=1.0)
        add(frame_ms, ry=0.9)
        add(frame_ms, buttons=BUTTON_RIGHT_SHOULDER)
        add(int(float(cfg.rb.long_ms)) + 20, buttons=BUTTON_RIGHT_SHOULDER)
        add(frame_ms, buttons=0)
        # F8 启用：恢复
        add(frame_ms, toggle=True)
        add(frame_ms, ly=0.0)
        add(frame_ms, ly=0.95)
        add(frame_ms, ly=0.0)

        saved_injector = self.injector
        real_out = self.out
        recorder = _RecordingInjector()
        self.injector = recorder
        self._reset_gestures()
        self._last_now = None
        self.enabled = bool(getattr(cfg, "start_enabled", True))
        self._hotkey_prev = False
        sequence = []
        now_ms = 0
        self.log("[自检] 开始脚本化流水线（不真注入）", "info")
        try:
            for delta_ms, toggle, pad in script:
                now_ms += delta_ms
                state = PadState(**pad)
                collected = []
                self.out = collected.append
                try:
                    fired = self.step(state, now=now_ms / 1000.0, toggle=toggle)
                finally:
                    self.out = real_out
                sequence.extend(fired)
                self.log(
                    "[自检] t=%5dms %-46s -> %s"
                    % (now_ms, _describe_pad(state), ", ".join(fired) or "-"),
                    "info",
                )
                for line in collected:
                    real_out(line)
        finally:
            self.injector = saved_injector
            self._reset_gestures()
            self._last_now = None
        self.log("[自检] 注入器实际调用: %d 次（假注入器）" % len(recorder.events), "info")
        self.log("[自检] 动作序列: %s" % (" -> ".join(sequence) or "-"), "info")
        return sequence


def _describe_pad(state):
    parts = []
    if state.buttons & BUTTON_A:
        parts.append("A")
    if state.buttons & BUTTON_B:
        parts.append("B")
    if state.buttons & BUTTON_X:
        parts.append("X")
    if state.buttons & BUTTON_Y:
        parts.append("Y")
    if state.buttons & BUTTON_LEFT_SHOULDER:
        parts.append("LB")
    if state.buttons & BUTTON_RIGHT_SHOULDER:
        parts.append("RB")
    parts_text = ("按键 " + "+".join(parts)) if parts else "按键 -"
    return "左(%.2f) 右(%.2f) RT=%.2f %s" % (state.ly, state.ry, state.right_trigger, parts_text)

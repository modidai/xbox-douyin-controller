"""app 引擎离线测试：不碰真实手柄、不碰真实输入。"""
from __future__ import annotations

import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xbox_douyin.app import App, NullActions, normalize_config
from xbox_douyin.config import Config, HoldConfig, MouseConfig, StickConfig
from xbox_douyin.injector import (
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYEVENTF_KEYUP,
    MOUSEEVENTF_ABSOLUTE,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE,
    MOUSEEVENTF_VIRTUALDESK,
    MOUSEEVENTF_WHEEL,
    Injector,
    key_is_down,
)
from xbox_douyin.xinput import (
    BUTTON_A,
    BUTTON_B,
    BUTTON_LEFT_SHOULDER,
    BUTTON_RIGHT_SHOULDER,
    BUTTON_X,
    BUTTON_Y,
    PadState,
)

FAILED = []
COUNT = [0]

SKIP_IN_SEQUENCE = ("toggle_off", "toggle_on", "mouse_move", "click")


def check(name, condition, detail=""):
    COUNT[0] += 1
    if condition:
        print("PASS  %s" % name)
    else:
        print("FAIL  %s%s" % (name, ("  <- " + str(detail)) if detail else ""))
        FAILED.append(name)


class FakeActions(object):
    """测试用假动作对象，只记录被调用的方法名。"""

    def __init__(self):
        self.calls = []

    def swipe_up(self):
        self.calls.append("swipe_up")

    def swipe_down(self):
        self.calls.append("swipe_down")

    def like(self):
        self.calls.append("like")

    def back(self):
        self.calls.append("back")

    def favorite(self):
        self.calls.append("favorite")

    def open_comments(self):
        self.calls.append("open_comments")

    def seek_forward(self):
        self.calls.append("seek_forward")

    def seek_backward(self):
        self.calls.append("seek_backward")

    def set_speed(self, label):
        self.calls.append("set_speed:%s" % label)


def quiet_app(cfg=None, actions=None, **kwargs):
    return App(cfg or Config(), actions or FakeActions(), out=None, **kwargs)


def test_selftest_sequence():
    actions = FakeActions()
    app = App(Config(), actions, injector=None, out=None)
    sequence = app.run_selftest()
    head = [
        "swipe_up",
        "swipe_up",
        "swipe_down",
        "like",
        "back",
        "favorite",
        "open_comments",
        "seek_backward",
        "set_speed:2x",
        "set_speed:1x",
        "set_speed:0.75x",
        "set_speed:1x",
        "mouse_move",
        "click",
    ]
    check("自检：动作序列前半段符合预期", sequence[: len(head)] == head, sequence)

    check("自检：序列里有停用/启用标记", "toggle_off" in sequence and "toggle_on" in sequence, sequence)
    off = sequence.index("toggle_off")
    on = sequence.index("toggle_on")
    check("自检：F8 停用后到启用前没有任何动作", sequence[off + 1 : on] == [], sequence[off : on + 1])
    check("自检：重新启用后恢复响应手柄", sequence[on + 1 :] == ["swipe_up"], sequence[on + 1 :])

    expected_calls = [name for name in sequence if name not in SKIP_IN_SEQUENCE]
    check("自检：动作对象被真实调用且顺序一致", actions.calls == expected_calls, (actions.calls, expected_calls))


def test_selftest_does_not_touch_real_input():
    app = App(Config(), FakeActions(), injector=None, out=None)
    app.run_selftest()
    check("自检：跑完不残留假注入器", app.injector is None)
    check("自检：跑完回到启用态之外的确定状态", isinstance(app.enabled, bool))


def test_single_tap_is_not_double_tap():
    cfg = Config()
    app = quiet_app(cfg)
    check("按键：A 单次按下不点赞", app.step(PadState(buttons=BUTTON_A), now=0.0) == [])
    check("按键：A 松开无动作", app.step(PadState(), now=0.1) == [])
    far = (cfg.tap.window_ms + 100) / 1000.0
    check("按键：超出双击窗口的第二次不点赞", app.step(PadState(buttons=BUTTON_A), now=0.1 + far) == [])
    check("按键：A 松开无动作", app.step(PadState(), now=0.1 + far + 0.01) == [])
    near = (cfg.tap.window_ms / 2) / 1000.0
    check(
        "按键：窗口内第二次 A 触发点赞",
        app.step(PadState(buttons=BUTTON_A), now=0.1 + far + 0.01 + near) == ["like"],
    )


def test_single_click_buttons():
    app = quiet_app()
    check("按键：B 单击 = 返回", app.step(PadState(buttons=BUTTON_B), now=0.0) == ["back"])
    check("按键：B 按住不重复返回", app.step(PadState(buttons=BUTTON_B), now=0.1) == [])
    check("按键：X 单击 = 打开评论", app.step(PadState(buttons=BUTTON_X), now=0.2) == ["open_comments"])
    check("按键：松开不产生动作", app.step(PadState(), now=0.25) == [])
    check(
        "按键：同时按 B+X 各触发一次",
        app.step(PadState(buttons=BUTTON_B | BUTTON_X), now=0.3) == ["back", "open_comments"],
    )


def test_stick_repeat_and_hysteresis():
    cfg = Config()
    cfg.stick = StickConfig(threshold=0.6, release=0.35, repeat_ms=450, up_action="swipe_up", down_action="swipe_down")
    app = quiet_app(cfg)
    repeat_s = cfg.stick.repeat_ms / 1000.0

    check("摇杆：上推触发上一条", app.step(PadState(ly=0.9), now=0.0) == ["swipe_up"])
    check("摇杆：保持推住在重复间隔内不动作", app.step(PadState(ly=0.9), now=0.1) == [])
    check("摇杆：超过 repeat_ms 重复触发", app.step(PadState(ly=0.9), now=repeat_s + 0.05) == ["swipe_up"])
    check("摇杆：迟滞区内(0.4)不重复触发", app.step(PadState(ly=0.4), now=repeat_s + 0.1) == [])
    check("摇杆：回到 0.9 但未过释放阈值，仍需等到期", app.step(PadState(ly=0.9), now=repeat_s + 0.15) == [])
    app.step(PadState(ly=0.0), now=repeat_s + 0.2)
    check("摇杆：回中后重新推立刻触发", app.step(PadState(ly=0.9), now=repeat_s + 0.3) == ["swipe_up"])
    app.step(PadState(ly=0.0), now=repeat_s + 0.4)
    check("摇杆：下推触发下一条", app.step(PadState(ly=-0.9), now=repeat_s + 0.5) == ["swipe_down"])


def test_toggle_with_hotkey_edges():
    held = {"down": True}
    app = quiet_app(key_state=lambda name: held["down"])
    check("热键：F8 按住的第一帧触发停用", app.step(PadState(), now=0.0) == ["toggle_off"])
    check("热键：F8 一直按住不会反复切换", app.step(PadState(), now=0.1) == [])
    check("热键：停用后摇杆不产生动作", app.step(PadState(ly=0.9), now=0.2) == [])
    check("热键：停用后按键不产生动作", app.step(PadState(buttons=BUTTON_A), now=0.3) == [])
    held["down"] = False
    check("热键：松开无动作", app.step(PadState(), now=0.4) == [])
    held["down"] = True
    check("热键：再次按下重新启用", app.step(PadState(), now=0.5) == ["toggle_on"])
    check("热键：启用后摇杆恢复", app.step(PadState(ly=0.9), now=0.6) == ["swipe_up"])


def test_start_disabled():
    cfg = Config()
    cfg.start_enabled = False
    app = App(cfg, FakeActions(), injector=None, out=None)
    check("启动态：start_enabled=False 时初始停用", app.enabled is False)
    check("启动态：停用时不响应摇杆", app.step(PadState(ly=0.9), now=0.0) == [])


def test_trigger_cooldown():
    cfg = Config()
    app = quiet_app(cfg)
    cooldown_s = cfg.trigger.cooldown_ms / 1000.0
    check("扳机：RT 上穿 = 点击", app.step(PadState(right_trigger=1.0), now=0.0) == ["click"])
    app.step(PadState(), now=0.05)
    check("扳机：冷却期内再扣不点击", app.step(PadState(right_trigger=1.0), now=0.05 + cooldown_s / 4) == [])
    app.step(PadState(), now=0.06 + cooldown_s / 4)
    check("扳机：冷却期满后再扣可点击", app.step(PadState(right_trigger=1.0), now=0.1 + cooldown_s) == ["click"])


def test_no_injector_is_safe():
    cfg = Config()
    app = App(cfg, FakeActions(), injector=None, out=None)
    check("无注入器：右摇杆不崩且无动作", app.step(PadState(rx=1.0, ry=1.0), now=0.0) == [])
    app.step(PadState(rx=1.0, ry=1.0), now=0.1)
    check("无注入器：RT 只记账不注入", app.step(PadState(right_trigger=1.0), now=0.2) == ["click"])


def test_action_failure_does_not_crash():
    class Broken(FakeActions):
        def like(self):
            raise RuntimeError("模拟动作异常")

    app = App(Config(), Broken(), injector=None, out=None)
    app.step(PadState(buttons=BUTTON_A), now=0.0)
    app.step(PadState(), now=0.01)
    check("容错：动作抛异常不会中断主循环", app.step(PadState(buttons=BUTTON_A), now=0.05) == [])


def test_normalize_config_fixes_dict_groups():
    raw = Config.from_dict({"poll_hz": 200, "stick": {"threshold": 0.9}, "mouse": {"curve": 1.5}})
    check("配置：from_dict 现在能正确建出嵌套 group", isinstance(raw.stick, StickConfig), type(raw.stick).__name__)
    # 兜底路径：万一 cfg 里被塞了 dict（老版本 config.py / 手写配置），normalize 要能修回来
    raw.stick = {"threshold": 0.9}
    fixed = normalize_config(raw)
    check("配置：normalize 后 stick 是 StickConfig", isinstance(fixed.stick, StickConfig))
    check("配置：normalize 后保留 JSON 里的值", fixed.stick.threshold == 0.9, fixed.stick.threshold)
    check("配置：normalize 后 mouse.curve 生效", fixed.mouse.curve == 1.5, fixed.mouse.curve)
    check("配置：顶层字段没被破坏", fixed.poll_hz == 200)
    app = App(raw, FakeActions(), injector=None, out=None)
    check("配置：坏 group 交给 App 也能用", app.cfg.stick.threshold == 0.9)


def test_default_invert_y_end_to_end():
    cfg = Config()
    check("默认配置：invert_y 打开", cfg.mouse.invert_y is True, cfg.mouse.invert_y)
    stub = StubInjector(cfg)
    app = App(cfg, FakeActions(), injector=stub, out=None)
    app.step(PadState(ry=0.9), now=0.0)
    app.step(PadState(ry=0.9), now=0.05)
    moves = [event for event in stub.events() if event[1] == MOUSEEVENTF_MOVE]
    check("默认配置：右摇杆上推产生向上的鼠标位移(dy<0)", bool(moves) and all(event[3] < 0 for event in moves), moves)

    cfg2 = Config()
    cfg2.mouse = MouseConfig(deadzone=0.15, max_speed_px_s=900.0, curve=2.0, invert_y=False)
    stub2 = StubInjector(cfg2)
    app2 = App(cfg2, FakeActions(), injector=stub2, out=None)
    app2.step(PadState(ry=0.9), now=0.0)
    app2.step(PadState(ry=0.9), now=0.05)
    moves2 = [event for event in stub2.events() if event[1] == MOUSEEVENTF_MOVE]
    check("配置：invert_y=False 时同输入产生向下位移(dy>0)", bool(moves2) and all(event[3] > 0 for event in moves2), moves2)


def test_no_douyin_import():
    check("依赖：导入 app 不会顺带导入 douyin 模块", "xbox_douyin.douyin" not in sys.modules)
    check("依赖：NullActions 六个方法齐全", all(
        callable(getattr(NullActions(), name))
        for name in ("swipe_up", "swipe_down", "like", "back", "favorite", "open_comments")
    ))


class StubInjector(Injector):
    """打桩注入器：记录调用与事件，绝不真正调用 SendInput。"""

    def __init__(self, cfg, rect=(100, 200, 400, 300), cursor=(10, 20), out=None, focus=True):
        Injector.__init__(self, cfg, out=out)
        self.rect = rect
        self.cursor = tuple(cursor)
        self.calls = []
        self.focus_result = bool(focus)
        self.focus_calls = 0

    def focus_target(self):
        self.focus_calls += 1
        return self.focus_result

    def target_client_rect(self):
        return self.rect

    def cursor_pos(self):
        return self.cursor

    def set_cursor(self, x, y):
        self.calls.append(("set_cursor", int(x), int(y)))
        self.cursor = (int(x), int(y))
        return True

    def _send(self, inputs):
        events = []
        for item in inputs:
            if int(item.type) == INPUT_MOUSE:
                events.append(
                    ("mouse", int(item.mi.dwFlags), int(item.mi.dx), int(item.mi.dy), int(item.mi.mouseData))
                )
            elif int(item.type) == INPUT_KEYBOARD:
                events.append(("key", int(item.ki.wVk), int(item.ki.dwFlags)))
        self.calls.append(("send", events))
        return len(inputs)

    def events(self):
        return [event for call in self.calls if call[0] == "send" for event in call[1]]


def test_injector_click_restore():
    cfg = Config()
    stub = StubInjector(cfg, rect=(100, 200, 400, 300), cursor=(10, 20))
    stub.click_normalized(0.5, 0.5, restore=True)
    batches = [call for call in stub.calls if call[0] == "send"]
    check("注入器：一次点击只发一个批次", len(batches) == 1, stub.calls)
    flags = [event[1] for event in batches[0][1]] if batches else []
    expected = [MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP]
    check("注入器：批次 = 绝对定位 + 按下 + 抬起", flags == expected, flags)
    check("注入器：restore=True 后光标真的回到原处", stub.cursor == (10, 20), stub.cursor)
    check("注入器：还原用的是 set_cursor(10, 20)", ("set_cursor", 10, 20) in stub.calls, stub.calls)

    stub2 = StubInjector(cfg, rect=(100, 200, 400, 300), cursor=(10, 20))
    stub2.click_normalized(0.5, 0.5, restore=False)
    check("注入器：restore=False 不额外挪光标", all(call[0] != "set_cursor" for call in stub2.calls), stub2.calls)

    stub3 = StubInjector(cfg, rect=None)
    stub3.click_normalized(0.5, 0.5)
    check("注入器：找不到目标窗口时一个事件都不发", stub3.events() == [], stub3.calls)

    stub4 = StubInjector(cfg, rect=(0, 0, 200, 100), cursor=(5, 5))
    stub4.dry_run = True
    stub4.click_normalized(0.25, 0.5, restore=True)
    check("注入器：dry_run 的点击不发任何事件", stub4.events() == [], stub4.calls)


def test_injector_move_accumulation():
    cfg = Config()
    stub = StubInjector(cfg)
    for _ in range(20):
        stub.move_mouse(0.2, 0.0)
    moves = [event for event in stub.events() if event[0] == "mouse"]
    check("注入器：20 次 0.2px 累积成 4px", sum(event[2] for event in moves) == 4, moves)
    check("注入器：不足 1px 的帧不发事件", len(moves) == 4, moves)

    stub2 = StubInjector(cfg)
    for _ in range(20):
        stub2.move_mouse(-0.2, -0.3)
    moves2 = [event for event in stub2.events() if event[0] == "mouse"]
    total_x = sum(event[2] for event in moves2)
    total_y = sum(event[3] for event in moves2)
    check("注入器：负方向累积同样精确", abs(total_x - (-4)) <= 1, (total_x, moves2))
    # 浮点残差留在累加器里，允许不足 1px 的偏差
    check("注入器：Y 轴累积 20*0.3=6px", abs(total_y - (-6)) <= 1, (total_y, moves2))

    stub3 = StubInjector(cfg)
    stub3.move_mouse(0.5, 0.5)
    check("注入器：单帧不足 1px 直接不发", stub3.events() == [], stub3.calls)


def test_injector_wheel_and_press():
    cfg = Config()
    stub = StubInjector(cfg)
    stub.wheel(3)
    stub.wheel(-2)
    stub.wheel(0)
    events = [event for event in stub.events() if event[1] == MOUSEEVENTF_WHEEL]
    check("注入器：滚轮 0 格不发送", len(events) == 2, len(events))
    check("注入器：正数 = 上滚 3 格 = 360", events[0][4] == 360, events[0][4])
    check("注入器：负数按无符号补码下发", events[1][4] == -240 + (1 << 32), events[1][4])

    stub2 = StubInjector(cfg)
    stub2.press("alt+left")
    keys = [(event[1], event[2]) for event in stub2.events()]
    check(
        "注入器：alt+left 顺序 = alt↓ left↓ left↑ alt↑",
        keys == [(0x12, 0), (0x25, 0), (0x25, KEYEVENTF_KEYUP), (0x12, KEYEVENTF_KEYUP)],
        keys,
    )

    stub3 = StubInjector(cfg)
    stub3.press("F8")
    keys3 = [(event[1], event[2]) for event in stub3.events()]
    check("注入器：F8 解析为 0x77 一下一上", keys3 == [(0x77, 0), (0x77, KEYEVENTF_KEYUP)], keys3)

    stub4 = StubInjector(cfg)
    stub4.press("down")
    keys4 = [event[1] for event in stub4.events()]
    check("注入器：down 解析为方向键 0x28", keys4 == [0x28, 0x28], keys4)

    try:
        StubInjector(cfg).press("nope")
        raised = False
    except ValueError:
        raised = True
    check("注入器：无法识别的键名会报错", raised)
    check("注入器：未知键名的 GetAsyncKeyState 查询直接返回 False", key_is_down("nope") is False)


class _FastClock(object):
    """每次读时间跳 10 秒：让 run() 的重连等待不会真的等下去。"""

    def __init__(self):
        self.value = 0.0

    def __call__(self):
        self.value += 10.0
        return self.value


class _FakeReader(object):
    """假 XInputReader：按脚本吐帧，吐完返回 None 模拟拔线。"""

    def __init__(self, frames, slot=0):
        self.frames = list(frames)
        self.slot = slot
        self.reads = 0

    @property
    def available(self):
        return True

    def find_slot(self):
        return self.slot

    def read(self, slot=None):
        self.reads += 1
        if not self.frames:
            return None
        item = self.frames.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _run_with_reader(app, reader):
    import xbox_douyin.xinput

    original = xbox_douyin.xinput.XInputReader
    xbox_douyin.xinput.XInputReader = lambda *args, **kwargs: reader
    try:
        return app.run()
    finally:
        xbox_douyin.xinput.XInputReader = original


def test_run_loop_connect_disconnect():
    lines = []
    actions = FakeActions()
    cfg = Config()
    app = App(cfg, actions, injector=None, clock=_FastClock(), out=lines.append)
    reader = _FakeReader([PadState(ly=0.95), PadState(ly=0.0)])
    code = _run_with_reader(app, reader)
    text = "\n".join(lines)
    check("主循环：正常返回 0", code == 0, code)
    check("主循环：日志里报告了连接与槽位", "[手柄] 已连接槽位 0" in text, text)
    check("主循环：轮询真的读到了帧", reader.reads >= 2, reader.reads)
    check("主循环：摇杆上推触发了动作", "swipe_up" in actions.calls, actions.calls)
    check("主循环：拔线后报告断开", "连接断开" in text, text)


def test_run_loop_ctrl_c():
    lines = []
    app = App(Config(), FakeActions(), injector=None, clock=_FastClock(), out=lines.append)
    reader = _FakeReader([KeyboardInterrupt()])
    code = _run_with_reader(app, reader)
    text = "\n".join(lines)
    check("主循环：Ctrl+C 干净退出（返回 0）", code == 0, code)
    check("主循环：Ctrl+C 有退出日志", "Ctrl+C" in text, text)


def test_press_wheel_foreground_guard():
    # ensure_foreground=False -> 完全不调 focus_target
    cfg_off = Config()
    cfg_off.ensure_foreground = False
    stub_off = StubInjector(cfg_off)
    ok_press = stub_off.press("esc")
    check("前台守卫：ensure_foreground=False 时根本不调 focus_target", stub_off.focus_calls == 0, stub_off.focus_calls)
    check("前台守卫：关掉守卫后按键照发", ok_press is True and len(stub_off.events()) == 2, (ok_press, stub_off.events()))

    stub_off2 = StubInjector(cfg_off)
    ok_wheel = stub_off2.wheel(1)
    check("前台守卫：关掉守卫后滚轮照发", ok_wheel is True and len(stub_off2.events()) == 1, stub_off2.events())
    check("前台守卫：关掉守卫后滚轮也不查前台", stub_off2.focus_calls == 0, stub_off2.focus_calls)

    # ensure_foreground=True + 抢到前台 -> 先 focus_target 再发事件
    cfg_on = Config()
    cfg_on.ensure_foreground = True
    stub_on = StubInjector(cfg_on, focus=True)
    ok_on = stub_on.press("alt+left")
    check(
        "前台守卫：抢到前台时先调 focus_target 再发事件",
        stub_on.focus_calls == 1 and len(stub_on.events()) == 4 and ok_on is True,
        (stub_on.focus_calls, stub_on.events(), ok_on),
    )
    stub_on2 = StubInjector(cfg_on, focus=True)
    ok_on2 = stub_on2.wheel(1)
    check("前台守卫：滚轮同样先过守卫", stub_on2.focus_calls == 1 and len(stub_on2.events()) == 1, (stub_on2.focus_calls, ok_on2))

    # ensure_foreground=True + 抢不到 -> 0 事件 + False + 告警
    lines = []
    stub_no = StubInjector(cfg_on, focus=False, out=lines.append)
    ok_no = stub_no.press("esc")
    check("前台守卫：抢不到前台时按键 0 事件且返回 False", stub_no.events() == [] and ok_no is False, (stub_no.events(), ok_no))
    check(
        "前台守卫：给出可读告警",
        any("[注入] 抖音不在前台" in line and "按键" in line for line in lines),
        lines,
    )
    ok_no_wheel = stub_no.wheel(2)
    check("前台守卫：抢不到前台时滚轮 0 事件且返回 False", stub_no.events() == [] and ok_no_wheel is False, (ok_no_wheel, stub_no.events()))

    # 3 秒去重：连续多次只告警一次
    press_warns = [line for line in lines if "已跳过按键" in line]
    check("前台守卫：首次告警已打出", len(press_warns) == 1, lines)
    for _ in range(3):
        stub_no.press("esc")
        stub_no.wheel(1)
    check("前台守卫：3 秒内重复告警只打一次", len([l for l in lines if "已跳过按键" in l]) == 1, lines)
    check(
        "前台守卫：按键与滚轮两种告警各自节流（交替动作不刷屏）",
        len([l for l in lines if "已跳过滚轮" in l]) == 1,
        lines,
    )

    # click_normalized 不受前台守卫影响（绝对坐标，落点由坐标决定）
    stub_click = StubInjector(cfg_on, rect=(100, 200, 400, 300), focus=False)
    stub_click.click_normalized(0.5, 0.5, restore=False)
    check(
        "前台守卫：click_normalized 不因前台不足被跳过",
        len(stub_click.events()) == 3 and stub_click.focus_calls == 0,
        (stub_click.events(), stub_click.focus_calls),
    )

    # 老配置没有 ensure_foreground 字段时按 True 兜底
    class _OldCfg(object):
        pass

    old = _OldCfg()
    old.target_window = ""
    old.target_process = ""
    stub_old = StubInjector(cfg_on, focus=False)
    stub_old.cfg = old
    old_ok = stub_old.press("esc")
    check("前台守卫：老配置缺 ensure_foreground 时按 True 处理", old_ok is False and stub_old.focus_calls == 1, old_ok)


class _ActionsInjectorFirst(object):
    """真实 douyin.py 的签名：__init__(self, injector, cfg)。"""

    def __init__(self, injector, cfg):
        self.injector = injector
        self.cfg = cfg


class _ActionsCfgFirst(object):
    """另一种可能的签名：__init__(self, cfg, injector)。"""

    def __init__(self, cfg, injector):
        self.cfg = cfg
        self.injector = injector


class _ActionsUnnamed(object):
    """名字完全看不出含义，按契约顺序 (injector, cfg) 兜底。"""

    def __init__(self, alpha, beta):
        self.alpha = alpha
        self.beta = beta


class _ActionsKeywordOnly(object):
    def __init__(self, *, injector, cfg):
        self.injector = injector
        self.cfg = cfg


def _run_module():
    import run as run_module

    return run_module


def _build_with(fake_class, cfg, stub):
    import xbox_douyin.douyin as douyin_module

    original = douyin_module.DouyinActions
    douyin_module.DouyinActions = fake_class
    try:
        return _run_module().build_actions(cfg, stub)
    finally:
        douyin_module.DouyinActions = original


def test_build_actions_signature_dispatch():
    cfg = Config()
    stub = StubInjector(cfg)

    first = _build_with(_ActionsInjectorFirst, cfg, stub)
    check(
        "装配：(injector, cfg) 签名拿到注入器在第一位、配置在第二位",
        first.injector is stub and first.cfg is cfg,
        (type(first.injector).__name__, type(first.cfg).__name__),
    )

    second = _build_with(_ActionsCfgFirst, cfg, stub)
    check(
        "装配：(cfg, injector) 签名也能正确分派",
        second.cfg is cfg and second.injector is stub,
        (type(second.cfg).__name__, type(second.injector).__name__),
    )

    third = _build_with(_ActionsUnnamed, cfg, stub)
    check(
        "装配：认不出名字时按契约顺序 injector, cfg 兜底",
        third.alpha is stub and third.beta is cfg,
        (type(third.alpha).__name__, type(third.beta).__name__),
    )

    fourth = _build_with(_ActionsKeywordOnly, cfg, stub)
    check(
        "装配：keyword-only 参数按名字传",
        fourth.injector is stub and fourth.cfg is cfg,
        (type(fourth.injector).__name__, type(fourth.cfg).__name__),
    )


def test_build_actions_with_real_douyin():
    from xbox_douyin.douyin import DouyinActions

    cfg = Config()
    stub = StubInjector(cfg)
    actions = _run_module().build_actions(cfg, stub)
    check("装配：真实 douyin.py 用的是真 DouyinActions", isinstance(actions, DouyinActions), type(actions).__name__)
    check("装配：真实 DouyinActions.injector 就是传进去的注入器", actions.injector is stub, type(actions.injector).__name__)
    check("装配：真实 DouyinActions.cfg 就是传进去的配置", actions.cfg is cfg, type(actions.cfg).__name__)


def test_build_actions_error_is_loud():
    class _Boom(object):
        def __init__(self, injector, cfg):
            raise TypeError("模拟构造签名爆炸")

    cfg = Config()
    raised = False
    message = ""
    try:
        _build_with(_Boom, cfg, StubInjector(cfg))
    except RuntimeError as exc:
        raised = True
        message = str(exc)
    check("装配：构造失败时抛 RuntimeError 而不是静默把错传下去", raised, message)
    check("装配：错误信息里点名 DouyinActions", "DouyinActions" in message, message)


class _ActionsAlwaysFalse(object):
    def __init__(self, injector, cfg):
        self.injector = injector
        self.cfg = cfg

    def back(self):
        return False

    def like(self):
        return True


def test_cmd_action_exit_codes():
    import contextlib
    import io

    import xbox_douyin.douyin as douyin_module

    run_module = _run_module()
    cfg = Config()
    original = douyin_module.DouyinActions
    try:
        douyin_module.DouyinActions = _ActionsAlwaysFalse
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            failed_code = run_module.cmd_action(cfg, "back", dry_run=True)
        failed_text = buffer.getvalue()
        check("退出码：动作返回 False 时 --action 返回非 0", failed_code == 1, failed_code)
        check("退出码：失败时打印明确原因", "返回 False" in failed_text, failed_text)

        buffer2 = io.StringIO()
        with contextlib.redirect_stdout(buffer2):
            ok_code = run_module.cmd_action(cfg, "like", dry_run=True)
        check("退出码：动作返回 True 时 --action 返回 0", ok_code == 0, ok_code)
    finally:
        douyin_module.DouyinActions = original


def _rb_seek_cfg():
    """RB 默认 short_action="none"（用户决定留空），验证 seek 路径时显式配成 seek_forward。"""
    cfg = Config()
    cfg.rb = HoldConfig(long_ms=400, short_action="seek_forward", hold_speed="2x", restore_speed="1x")
    return cfg


def _shoulder_app(cfg=None, actions=None):
    actions = actions if actions is not None else FakeActions()
    app = App(cfg or Config(), actions, injector=None, out=None)
    return app, actions


def test_shoulder_short_press():
    cfg = _rb_seek_cfg()
    app, actions = _shoulder_app(cfg)
    long_s = cfg.rb.long_ms / 1000.0
    check("肩键：按下当帧不发任何动作", app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0) == [])
    check(
        "肩键：按住但未到阈值也不发动作",
        app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s / 2) == [],
    )
    fired = app.step(PadState(), now=long_s / 2 + 0.01)
    check("肩键：短按在松开时才发 seek_forward", fired == ["seek_forward"], fired)
    check("肩键：动作层确实只收到 seek_forward", actions.calls == ["seek_forward"], actions.calls)


def test_shoulder_long_press():
    cfg = Config()
    long_s = cfg.rb.long_ms / 1000.0
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s + 0.1)
    check("肩键：长按到阈值切 2x", fired == ["set_speed:2x"], fired)
    fired2 = app.step(PadState(), now=long_s + 0.2)
    check("肩键：长按松开只恢复 1x", fired2 == ["set_speed:1x"], fired2)
    check("肩键：长按全程不发 seek", actions.calls == ["set_speed:2x", "set_speed:1x"], actions.calls)


def test_shoulder_hold_fires_once():
    cfg = Config()
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = []
    for step in range(1, 30):
        fired.extend(app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=step * 0.02))
    check("肩键：按住 30 帧 set_speed 只发一次", fired.count("set_speed:2x") == 1, fired)
    check("肩键：按住期间没有任何 seek", "seek_forward" not in actions.calls, actions.calls)


def test_shoulder_lb_mirrors_rb():
    cfg = Config()
    long_s = cfg.lb.long_ms / 1000.0
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_LEFT_SHOULDER), now=0.0)
    fired = app.step(PadState(), now=long_s / 2)
    check("肩键：LB 短按发 seek_backward", fired == ["seek_backward"], fired)

    app2, actions2 = _shoulder_app(cfg)
    app2.step(PadState(buttons=BUTTON_LEFT_SHOULDER), now=0.0)
    fired2 = app2.step(PadState(buttons=BUTTON_LEFT_SHOULDER), now=long_s + 0.1)
    check("肩键：LB 长按切 0.75x 慢速", fired2 == ["set_speed:0.75x"], fired2)
    fired3 = app2.step(PadState(), now=long_s + 0.2)
    check("肩键：LB 松开恢复 1x", fired3 == ["set_speed:1x"], fired3)
    check("肩键：LB 长按全程不发 seek_backward", actions2.calls == ["set_speed:0.75x", "set_speed:1x"], actions2.calls)


def test_shoulder_short_action_none():
    cfg = Config()
    cfg.rb = HoldConfig(long_ms=400, short_action="none", hold_speed="2x", restore_speed="1x")
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = app.step(PadState(), now=0.05)
    check("肩键：short_action=none 时短按什么也不发", fired == [] and actions.calls == [], (fired, actions.calls))


def test_shoulder_disabled():
    cfg = Config()
    app, actions = _shoulder_app(cfg)
    app.step(PadState(), now=0.0, toggle=True)
    check("肩键：停用后按下不响应", app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.05) == [])
    check("肩键：停用期间长按也不切倍速", app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.6) == [])
    check("肩键：停用期间松手不补发 seek", app.step(PadState(), now=0.7) == [])
    check("肩键：停用期间肩键全程零动作", actions.calls == [], actions.calls)

    # 启用时按下 -> 中途停用 -> 松手不应补发
    app2, actions2 = _shoulder_app(cfg)
    app2.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    app2.step(PadState(), now=0.1, toggle=True)
    fired2 = app2.step(PadState(), now=0.2)
    check("肩键：按下后中途停用，松手静默丢弃", fired2 == [] and actions2.calls == [], (fired2, actions2.calls))

    # 长按中停用 -> 立刻复位速度，不能卡在 2x
    app3, actions3 = _shoulder_app(cfg)
    long_s = cfg.rb.long_ms / 1000.0
    app3.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    app3.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s + 0.1)
    fired3 = app3.step(PadState(), now=long_s + 0.2, toggle=True)
    check("肩键：长按中停用会立刻复位速度（不卡在 2x）", "set_speed:1x" in fired3, fired3)
    check("肩键：复位只发一次", actions3.calls.count("set_speed:1x") == 1, actions3.calls)
    check("肩键：停用后的松手不再补发动作", app3.step(PadState(), now=long_s + 0.3) == [])


def test_shoulder_disconnect_mid_hold():
    cfg = _rb_seek_cfg()
    app, actions = _shoulder_app(cfg)
    long_s = cfg.rb.long_ms / 1000.0
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s + 0.1)
    check("肩键：断开前已切到 2x", actions.calls == ["set_speed:2x"], actions.calls)
    app.handle_disconnect()
    check("肩键：长按中断开补一次 set_speed(1x)", actions.calls == ["set_speed:2x", "set_speed:1x"], actions.calls)
    app.handle_disconnect()
    check("肩键：重复断开不会重复复位", actions.calls.count("set_speed:1x") == 1, actions.calls)
    check(
        "肩键：断开后重新按下仍是干净的短按",
        app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s + 0.5) == []
        and app.step(PadState(), now=long_s + 0.55) == ["seek_forward"],
    )


def test_shoulder_threshold_boundary():
    cfg = _rb_seek_cfg()
    long_s = cfg.rb.long_ms / 1000.0
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s)
    check("肩键：按住正好等于 long_ms 判为长按", fired == ["set_speed:2x"], fired)
    fired2 = app.step(PadState(), now=long_s + 0.01)
    check("肩键：边界松开恢复 1x 且不发 seek", fired2 == ["set_speed:1x"], fired2)

    # 轮询卡顿：松手那一帧才发现已经过了阈值
    app2, actions2 = _shoulder_app(cfg)
    app2.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired3 = app2.step(PadState(), now=long_s + 0.05)
    check("肩键：松手才跨过阈值仍按长按处理", fired3 == ["set_speed:2x", "set_speed:1x"], fired3)
    check("肩键：该情形不发 seek", "seek_forward" not in actions2.calls, actions2.calls)

    # 差一点点（long_ms - 1ms）应算短按
    app3, actions3 = _shoulder_app(cfg)
    app3.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired4 = app3.step(PadState(), now=(long_s * 1000.0 - 1.0) / 1000.0)
    check("肩键：差 1ms 到阈值算短按", fired4 == ["seek_forward"], fired4)


def test_shoulder_action_false_is_logged_only():
    class _RefusingActions(FakeActions):
        def set_speed(self, label):
            self.calls.append("set_speed:%s" % label)
            return False

    cfg = Config()
    actions = _RefusingActions()
    lines = []
    app = App(cfg, actions, injector=None, out=lines.append)
    long_s = cfg.rb.long_ms / 1000.0
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=long_s + 0.1)
    check("肩键：动作返回 False 仍记录事件", fired == ["set_speed:2x"], fired)
    check("肩键：返回 False 有明确告警", any("返回失败" in line for line in lines), lines)
    check("肩键：不会因为 False 而重试", actions.calls.count("set_speed:2x") == 1, actions.calls)
    app.step(PadState(), now=long_s + 0.15)
    check(
        "肩键：返回 False 不影响主循环后续输入",
        app.step(PadState(buttons=BUTTON_B), now=long_s + 0.2) == ["back"],
    )


def test_shoulder_default_rb_short_is_none():
    cfg = Config()
    check("肩键：默认配置 RB 短按留空", cfg.rb.short_action == "none", cfg.rb.short_action)
    check("肩键：默认配置 LB 短按是后退", cfg.lb.short_action == "seek_backward", cfg.lb.short_action)
    check("肩键：默认配置 LB 长按档位是 0.75x", cfg.lb.hold_speed == "0.75x", cfg.lb.hold_speed)
    app, actions = _shoulder_app(cfg)
    app.step(PadState(buttons=BUTTON_RIGHT_SHOULDER), now=0.0)
    fired = app.step(PadState(), now=0.05)
    check("肩键：默认配置下 RB 短按不产生任何动作", fired == [] and actions.calls == [], (fired, actions.calls))


def test_injector_click_sequence():
    cfg = Config()
    expected_flags = [
        MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        MOUSEEVENTF_LEFTDOWN,
        MOUSEEVENTF_LEFTUP,
    ]

    stub = StubInjector(cfg, rect=(100, 200, 400, 300), cursor=(10, 20))
    ok = stub.click_sequence([(0.2, 0.5), (0.8, 0.5)], interval_ms=0, restore=True)
    batches = [call for call in stub.calls if call[0] == "send"]
    check("连续点击：两个点 = 恰好两个批次", len(batches) == 2, len(batches))
    check(
        "连续点击：每个批次都是 定位+按下+抬起",
        all([event[1] for event in batch[1]] == expected_flags for batch in batches),
        [event[1] for batch in batches for event in batch[1]],
    )
    flags = [event[1] for batch in batches for event in batch[1]]
    check(
        "连续点击：批与批之间没有多余的 MOVE 事件",
        flags == expected_flags * 2,
        flags,
    )
    xs = [batch[1][0][2] for batch in batches]
    check("连续点击：两批落在不同 x 上（第二下更靠右）", xs[0] != xs[1] and xs[1] > xs[0], xs)
    check("连续点击：restore=True 光标回到起点", stub.cursor == (10, 20), stub.cursor)
    check("连续点击：整体返回 True", ok is True, ok)

    stub2 = StubInjector(cfg, rect=(100, 200, 400, 300), cursor=(10, 20))
    ok2 = stub2.click_sequence([(0.2, 0.5), (0.8, 0.5)], interval_ms=0, restore=False)
    check("连续点击：restore=False 不发批次之外的挪动", all(c[0] != "set_cursor" for c in stub2.calls), stub2.calls)
    check(
        "连续点击：restore=False 仍完成两个批次",
        ok2 is True and len([c for c in stub2.calls if c[0] == "send"]) == 2,
        stub2.calls,
    )

    stub3 = StubInjector(cfg, rect=(100, 200, 400, 300))
    check("连续点击：只给 1 个点返回 False", stub3.click_sequence([(0.5, 0.5)]) is False, stub3.calls)
    check("连续点击：空列表返回 False", stub3.click_sequence([]) is False, stub3.calls)
    check("连续点击：坐标格式非法返回 False", stub3.click_sequence([("a", "b"), (0.5, 0.5)]) is False, stub3.calls)
    check("连续点击：上面三种非法输入都是零事件", stub3.events() == [], stub3.events())

    stub4 = StubInjector(cfg, rect=None)
    check(
        "连续点击：找不到窗口返回 False 且零事件",
        stub4.click_sequence([(0.2, 0.5), (0.8, 0.5)]) is False and stub4.events() == [],
        stub4.calls,
    )

    lines = []
    stub5 = StubInjector(cfg, rect=(100, 200, 400, 300), out=lines.append)
    stub5.dry_run = True
    ok5 = stub5.click_sequence([(0.2, 0.5), (0.8, 0.5)], interval_ms=0, restore=True)
    check("连续点击：dry_run 返回 True 但零事件", ok5 is True and stub5.events() == [], (ok5, stub5.events()))
    check("连续点击：dry_run 有可读输出", any("连续点击" in line for line in lines), lines)


class _GrabStub(StubInjector):
    """把 _grab_roi 打桩，专门验证亮度统计与各种失败分支。"""

    def __init__(self, cfg, grab=None, **kwargs):
        StubInjector.__init__(self, cfg, **kwargs)
        self.grab = grab
        self.grab_calls = 0
        self.last_region = None

    def _grab_roi(self, region):
        self.grab_calls += 1
        self.last_region = region
        return self.grab


class _FakeScreenUser32(object):
    def __init__(self):
        self.released = 0

    def GetDC(self, hwnd):
        return 0x0DC0

    def ReleaseDC(self, hwnd, dc):
        self.released += 1
        return 1


class _FakeGdi32(object):
    """记录 GDI 句柄的创建/释放次数，不碰真实屏幕。"""

    def __init__(self, pixel=(255, 255, 255, 0), bitblt_ok=1):
        self.pixel = bytes(pixel)
        self.bitblt_ok = bitblt_ok
        self.deleted_bitmaps = 0
        self.deleted_dcs = 0
        self.select_calls = 0
        self.bitblt_args = None
        self.getdibits_calls = 0

    def CreateCompatibleDC(self, dc):
        return 0x1111

    def CreateCompatibleBitmap(self, dc, width, height):
        return 0x2222

    def SelectObject(self, dc, obj):
        self.select_calls += 1
        return 0x3333

    def BitBlt(self, *args):
        self.bitblt_args = args
        return self.bitblt_ok

    def GetDIBits(self, dc, bitmap, start, lines, buffer, info, usage):
        self.getdibits_calls += 1
        size = ctypes.sizeof(buffer)
        ctypes.memmove(buffer, self.pixel * (size // 4), size)
        return lines

    def DeleteObject(self, obj):
        self.deleted_bitmaps += 1
        return 1

    def DeleteDC(self, dc):
        self.deleted_dcs += 1
        return 1


def _uniform(count, bgra):
    return bytes(bgra) * count


def test_bright_ratio_statistics():
    cfg = Config()
    roi = (0.0, 0.0, 0.2, 0.2)

    white = _GrabStub(cfg, grab=(4, 4, _uniform(16, (255, 255, 255, 0))))
    check("亮度采样：全白 -> 1.0", white.sample_bright_ratio(roi) == 1.0, white.last_region)

    black = _GrabStub(cfg, grab=(4, 4, _uniform(16, (0, 0, 0, 0))))
    check("亮度采样：全黑 -> 0.0", black.sample_bright_ratio(roi) == 0.0, black.last_region)

    half = _GrabStub(cfg, grab=(4, 4, _uniform(8, (255, 255, 255, 0)) + _uniform(8, (0, 0, 0, 0))))
    ratio = half.sample_bright_ratio(roi)
    check("亮度采样：一半白一半黑 -> 0.5", ratio == 0.5, ratio)

    gray = _GrabStub(cfg, grab=(4, 4, _uniform(16, (128, 128, 128, 0))))
    check("亮度采样：中灰按 luma 低于默认阈值 -> 0.0", gray.sample_bright_ratio(roi) == 0.0)
    check("亮度采样：中灰把阈值降到 100 就算亮 -> 1.0", gray.sample_bright_ratio(roi, lum_threshold=100) == 1.0)

    white_high = _GrabStub(cfg, grab=(4, 4, _uniform(16, (255, 255, 255, 0))))
    check("亮度采样：阈值高于最大 luma -> 0.0", white_high.sample_bright_ratio(roi, lum_threshold=300) == 0.0)

    no_grab = _GrabStub(cfg, grab=None)
    check("亮度采样：抓图返回 None -> 返回 None", no_grab.sample_bright_ratio(roi) is None)
    check("亮度采样：抓图返回 None 前确实试过", no_grab.grab_calls == 1, no_grab.grab_calls)

    short_data = _GrabStub(cfg, grab=(4, 4, b"\x00" * 10))
    check("亮度采样：像素数据长度不对 -> None", short_data.sample_bright_ratio(roi) is None)


def test_bright_ratio_invalid_inputs():
    cfg = Config()
    stub = _GrabStub(cfg, grab=(4, 4, _uniform(16, (255, 255, 255, 0))))
    bad_rois = [
        (0.5, 0.0, 0.2, 0.2),
        (0.0, 0.0, 0.0, 0.0),
        ("a", "b", "c", "d"),
        (0.0, 0.0, 1.0, float("nan")),
        (0.0, 0.0, 1.0),
    ]
    for bad in bad_rois:
        check("亮度采样：非法 ROI %r 返回 None" % (bad,), stub.sample_bright_ratio(bad) is None)
    check("亮度采样：非法 ROI 完全不去抓图", stub.grab_calls == 0, stub.grab_calls)

    no_window = _GrabStub(cfg, grab=(4, 4, _uniform(16, (255, 255, 255, 0))), rect=None)
    check("亮度采样：没有目标窗口 -> None", no_window.sample_bright_ratio((0.0, 0.0, 0.2, 0.2)) is None)
    check("亮度采样：没有窗口时不去抓图", no_window.grab_calls == 0, no_window.grab_calls)

    lines = []
    dry = _GrabStub(cfg, grab=(4, 4, _uniform(16, (255, 255, 255, 0))), out=lines.append)
    dry.dry_run = True
    check("亮度采样：dry_run 返回 None", dry.sample_bright_ratio((0.1, 0.1, 0.3, 0.3)) is None)
    check("亮度采样：dry_run 不读屏", dry.grab_calls == 0, dry.grab_calls)
    check("亮度采样：dry_run 打印了要采样的区域", any("采样亮度区域" in line for line in lines), lines)


def _run_grab_with_fakes(stub, fake_user, fake_gdi):
    import xbox_douyin.injector as injector_module

    original_user = injector_module._get_user32
    original_gdi = injector_module._get_gdi32
    injector_module._get_user32 = lambda: fake_user
    injector_module._get_gdi32 = lambda: fake_gdi
    try:
        return stub.sample_bright_ratio((0.0, 0.0, 0.1, 0.1))
    finally:
        injector_module._get_user32 = original_user
        injector_module._get_gdi32 = original_gdi


def test_grab_roi_releases_gdi_handles():
    cfg = Config()
    stub = StubInjector(cfg, rect=(100, 200, 100, 100))
    fake_user = _FakeScreenUser32()
    fake_gdi = _FakeGdi32(pixel=(255, 255, 255, 0))
    ratio = _run_grab_with_fakes(stub, fake_user, fake_gdi)
    check("亮度采样：真实 _grab_roi 路径能跑通", ratio is not None, ratio)
    check("亮度采样：全白算出 1.0", ratio == 1.0, ratio)
    check("亮度采样：位图句柄已 DeleteObject", fake_gdi.deleted_bitmaps == 1, fake_gdi.deleted_bitmaps)
    check("亮度采样：内存 DC 已 DeleteDC", fake_gdi.deleted_dcs == 1, fake_gdi.deleted_dcs)
    check("亮度采样：屏幕 DC 已 ReleaseDC", fake_user.released == 1, fake_user.released)
    check("亮度采样：选中的旧对象已还原", fake_gdi.select_calls == 2, fake_gdi.select_calls)
    check("亮度采样：只抓 ROI 那 10x10 像素", tuple(fake_gdi.bitblt_args[3:5]) == (10, 10), fake_gdi.bitblt_args)
    check("亮度采样：真的调了 GetDIBits", fake_gdi.getdibits_calls == 1, fake_gdi.getdibits_calls)

    fake_user2 = _FakeScreenUser32()
    fake_gdi2 = _FakeGdi32(bitblt_ok=0)
    failed = _run_grab_with_fakes(stub, fake_user2, fake_gdi2)
    check("亮度采样：BitBlt 失败返回 None", failed is None, failed)
    check(
        "亮度采样：失败时 GDI 句柄照样释放干净",
        fake_gdi2.deleted_bitmaps == 1 and fake_gdi2.deleted_dcs == 1 and fake_user2.released == 1,
        (fake_gdi2.deleted_bitmaps, fake_gdi2.deleted_dcs, fake_user2.released),
    )


class _FakeKernel32(object):
    """桩 kernel32：按脚本返回 (handle, last_error)。"""

    def __init__(self, results):
        self.results = list(results)
        self.created = []
        self.closed = []
        self.raises = False
        self._error = 0

    def CreateMutexW(self, attrs, initial, name):
        if self.raises:
            raise RuntimeError("模拟 CreateMutexW 失败")
        self.created.append(name)
        if self.results:
            handle, error = self.results.pop(0)
        else:
            handle, error = (None, 0)
        self._error = error
        return handle

    def GetLastError(self):
        return self._error

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def _with_fake_kernel32(fake, func):
    import run as run_module

    original_kernel = run_module._kernel32
    original_handle = run_module._SINGLETON_HANDLE
    original_blocked = run_module._SINGLETON_BLOCKED_BY
    run_module._kernel32 = fake
    run_module._SINGLETON_HANDLE = None
    run_module._SINGLETON_BLOCKED_BY = None
    try:
        return func(run_module)
    finally:
        run_module._kernel32 = original_kernel
        run_module._SINGLETON_HANDLE = original_handle
        run_module._SINGLETON_BLOCKED_BY = original_blocked


def test_singleton_acquire():
    import run as run_module

    fake = _FakeKernel32([(0x1234, 0)])
    first = _with_fake_kernel32(fake, lambda module: module.acquire_singleton())
    check("单实例：第一次抢到互斥体返回 True", first is True, first)
    check("单实例：优先用 Global 前缀", fake.created and fake.created[0].startswith("Global\\"), fake.created)
    check("单实例：抢到时不关闭句柄", fake.closed == [], fake.closed)

    already = _FakeKernel32([(0x5678, 183)])
    second = _with_fake_kernel32(already, lambda module: module.acquire_singleton())
    check("单实例：ERROR_ALREADY_EXISTS 时返回 False", second is False, second)
    check("单实例：已有实例时把自己的句柄关掉", already.closed == [0x5678], already.closed)

    fallback = _FakeKernel32([(None, 0), (0x9ABC, 0)])
    third = _with_fake_kernel32(fallback, lambda module: module.acquire_singleton())
    check("单实例：Global 建不出来时退回普通名字并成功", third is True, third)
    check(
        "单实例：确实先试 Global 再试普通名",
        len(fallback.created) == 2 and fallback.created[0].startswith("Global\\") and not fallback.created[1].startswith("Global\\"),
        fallback.created,
    )

    broken = _FakeKernel32([(None, 0), (None, 0)])
    fourth = _with_fake_kernel32(broken, lambda module: module.acquire_singleton())
    check("单实例：两个名字都建不出来时放行（不阻断启动）", fourth is True, fourth)

    raising = _FakeKernel32([])
    raising.raises = True
    fifth = None
    try:
        fifth = _with_fake_kernel32(raising, lambda module: module.acquire_singleton())
        raised = False
    except Exception:
        raised = True
    check("单实例：CreateMutexW 抛异常也不外传", raised is False, raised)
    check("单实例：异常路径同样放行", fifth is True, fifth)

    check("单实例：互斥体名字符合约定", run_module.SINGLETON_NAME == "xbox-douyin-controller-singleton", run_module.SINGLETON_NAME)


def test_main_exits_when_singleton_taken():
    import contextlib
    import io

    import run as run_module

    calls = {"injector": 0, "build": 0, "app": 0, "run": 0}

    def _boom_injector(*args, **kwargs):
        calls["injector"] += 1
        raise AssertionError("已有实例时不该创建 Injector")

    def _boom_build(*args, **kwargs):
        calls["build"] += 1
        raise AssertionError("已有实例时不该装配动作")

    class _BoomApp(object):
        def __init__(self, *args, **kwargs):
            calls["app"] += 1
            raise AssertionError("已有实例时不该创建 App")

        def run(self):
            calls["run"] += 1
            return 0

    originals = (run_module.acquire_singleton, run_module.Injector, run_module.build_actions, run_module.App)
    run_module.acquire_singleton = lambda *args, **kwargs: False
    run_module.Injector = _boom_injector
    run_module.build_actions = _boom_build
    run_module.App = _BoomApp
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = run_module.main(["--dry-run"])
    finally:
        (
            run_module.acquire_singleton,
            run_module.Injector,
            run_module.build_actions,
            run_module.App,
        ) = originals
    text = buffer.getvalue()
    check("单实例：已有实例时退出码是 0", code == 0, code)
    check("单实例：不创建 App、不轮询", calls["app"] == 0 and calls["run"] == 0, calls)
    check("单实例：不创建 Injector、不装配动作", calls["injector"] == 0 and calls["build"] == 0, calls)
    check("单实例：打印了明确提示", "已经有一个实例在运行了" in text, text)
    check("单实例：提示里说明为什么退出", "避免重复注入" in text, text)


def test_log_file_option():
    import contextlib
    import io
    import os as os_module
    import tempfile

    import run as run_module

    original_output = run_module._output
    handle, path = tempfile.mkstemp(suffix="-run-log.txt")
    os_module.close(handle)
    try:
        os_module.remove(path)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run_module.main(["--selftest", "--log-file", path])
        check("日志文件：--selftest + --log-file 退出码 0", code == 0, code)
        check("日志文件：确实创建了日志文件", os_module.path.isfile(path), path)
        content = ""
        if os_module.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        lines = [line for line in content.splitlines() if line.strip()]
        check("日志文件：文件里至少有一行", len(lines) >= 1, lines[:3])
        check("日志文件：抓到自检输出", any("[自检]" in line for line in lines), lines[:3])
        check("日志文件：stdout 仍然照常输出", "[自检]" in buffer.getvalue())
        check("日志文件：main 结束后输出出口已还原", run_module._output is original_output, run_module._output)

        bad_path = os_module.path.join(tempfile.gettempdir(), "xdc_no_such_dir_42", "run.log")
        buffer2 = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer2):
                code2 = run_module.main(["--selftest", "--log-file", bad_path])
            crashed = False
        except Exception:
            crashed = True
            code2 = None
        check("日志文件：路径不可写时不崩", crashed is False, crashed)
        check("日志文件：路径不可写仍按正常流程退出", code2 == 0, code2)
        check("日志文件：不可写时有提示", "无法写入日志文件" in buffer2.getvalue(), buffer2.getvalue()[:200])
    finally:
        run_module._output = original_output
        if os_module.path.isfile(path):
            try:
                os_module.remove(path)
            except Exception:
                pass


def main():
    print("=== app 引擎离线测试 ===")
    for func in (
        test_selftest_sequence,
        test_selftest_does_not_touch_real_input,
        test_single_tap_is_not_double_tap,
        test_single_click_buttons,
        test_stick_repeat_and_hysteresis,
        test_toggle_with_hotkey_edges,
        test_start_disabled,
        test_trigger_cooldown,
        test_no_injector_is_safe,
        test_action_failure_does_not_crash,
        test_normalize_config_fixes_dict_groups,
        test_default_invert_y_end_to_end,
        test_no_douyin_import,
        test_run_loop_connect_disconnect,
        test_run_loop_ctrl_c,
        test_injector_click_restore,
        test_injector_move_accumulation,
        test_injector_wheel_and_press,
        test_press_wheel_foreground_guard,
        test_build_actions_signature_dispatch,
        test_build_actions_with_real_douyin,
        test_build_actions_error_is_loud,
        test_cmd_action_exit_codes,
        test_shoulder_short_press,
        test_shoulder_long_press,
        test_shoulder_hold_fires_once,
        test_shoulder_lb_mirrors_rb,
        test_shoulder_short_action_none,
        test_shoulder_disabled,
        test_shoulder_disconnect_mid_hold,
        test_shoulder_threshold_boundary,
        test_shoulder_action_false_is_logged_only,
        test_shoulder_default_rb_short_is_none,
        test_injector_click_sequence,
        test_bright_ratio_statistics,
        test_bright_ratio_invalid_inputs,
        test_grab_roi_releases_gdi_handles,
        test_singleton_acquire,
        test_main_exits_when_singleton_taken,
        test_log_file_option,
    ):
        func()
    print("---")
    if FAILED:
        print("失败 %d/%d 项：%s" % (len(FAILED), COUNT[0], ", ".join(FAILED)))
        return 1
    print("全部通过：%d 项" % COUNT[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""app 引擎离线测试：不碰真实手柄、不碰真实输入。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xbox_douyin.app import App, NullActions, normalize_config
from xbox_douyin.config import Config, MouseConfig, StickConfig
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
from xbox_douyin.xinput import BUTTON_A, BUTTON_B, BUTTON_X, BUTTON_Y, PadState

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


def quiet_app(cfg=None, actions=None, **kwargs):
    return App(cfg or Config(), actions or FakeActions(), out=None, **kwargs)


def test_selftest_sequence():
    actions = FakeActions()
    app = App(Config(), actions, injector=None, out=None)
    sequence = app.run_selftest()
    head = ["swipe_up", "swipe_up", "swipe_down", "like", "back", "favorite", "open_comments", "mouse_move", "click"]
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

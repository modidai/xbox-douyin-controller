# -*- coding: utf-8 -*-
"""douyin.py 的单元测试 + 假 Injector 演示（不触碰真实抖音）。

跑法：python -m unittest tests.test_douyin -v   或   python tests/test_douyin.py
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xbox_douyin.config import Config
from xbox_douyin.douyin import DouyinActions

RECT = (100, 50, 1651, 928)          # 客户区(左, 上, 宽, 高)
LIKE_PT = (100 + int(round(0.5 * 1650)), 50 + int(round(0.45 * 927)))


class FakeInjector(object):
    """记录所有调用的假注入器。"""

    def __init__(self, rect=RECT, cursor=None, fail_on=None):
        self.calls = []
        self.rect = rect
        self._cursor = list(cursor or LIKE_PT)
        self._fail_on = fail_on

    def _record(self, name, *args):
        if self._fail_on == name:
            raise RuntimeError("注入失败(测试用): %s" % name)
        self.calls.append((name,) + args)

    def move_mouse(self, dx, dy):
        self._record("move_mouse", dx, dy)
        self._cursor[0] += dx
        self._cursor[1] += dy

    def left_click(self):
        self._record("left_click")

    def double_click(self, interval_ms=40):
        self._record("double_click", interval_ms)

    def wheel(self, notches):
        self._record("wheel", notches)

    def press(self, key):
        self._record("press", key)

    def click_normalized(self, x, y, restore=True):
        self._record("click_normalized", round(float(x), 4), round(float(y), 4), restore)

    def cursor_pos(self):
        return (self._cursor[0], self._cursor[1])

    def target_client_rect(self):
        return self.rect


class FakeInjectorWithSequence(FakeInjector):
    """带 click_sequence 原语的假注入器（injector.py 现在已有该原语）。"""

    def click_sequence(self, points, interval_ms=60, restore=True):
        self._record("click_sequence",
                     [(round(float(x), 4), round(float(y), 4)) for x, y in points],
                     interval_ms, restore)


class FakeInjectorWithSampler(FakeInjectorWithSequence):
    """带 sample_bright_ratio 的假注入器（倍速守卫用）。"""

    def __init__(self, ratio=None, raises=False, **kwargs):
        super(FakeInjectorWithSampler, self).__init__(**kwargs)
        self.ratio = ratio
        self.raises = raises
        self.sampled = []

    def sample_bright_ratio(self, roi, lum_threshold=160):
        self.sampled.append(tuple(round(float(v), 4) for v in roi))
        if self.raises:
            raise RuntimeError("采样失败(测试用)")
        return self.ratio


def make_config():
    return Config.from_dict({
        "restore_cursor": True,
        "swipe": {"method": "wheel", "notches": 1, "key_up": "up", "key_down": "down"},
        "like": {"method": "double_click", "x": 0.5, "y": 0.45},
        "back": {"method": "key", "key": "esc|alt+left"},
        "favorite": {"method": "click", "x": 0.9685, "y": 0.6753},
        "comments": {"method": "click", "x": 0.9685, "y": 0.5922},
    })


class DouyinActionsTest(unittest.TestCase):

    def setUp(self):
        self.cfg = make_config()
        self.inj = FakeInjector()
        self.actions = DouyinActions(self.inj, self.cfg)

    def test_swipe_wheel_opposite_signs(self):
        self.assertTrue(self.actions.swipe_up())
        self.assertTrue(self.actions.swipe_down())
        wheels = [c for c in self.inj.calls if c[0] == "wheel"]
        self.assertEqual(len(wheels), 2)
        self.assertEqual(wheels[0][1], 1)     # 上一条 = 正
        self.assertEqual(wheels[1][1], -1)    # 下一条 = 负

    def test_swipe_key_method(self):
        self.cfg.swipe.method = "key"
        self.actions.swipe_up()
        self.actions.swipe_down()
        presses = [c for c in self.inj.calls if c[0] == "press"]
        self.assertEqual(presses, [("press", "up"), ("press", "down")])

    def test_swipe_parks_cursor_when_over_sidebar(self):
        # 光标停在左侧导航栏（x 归一化 ~0.02）时，滚轮会被导航栏吞掉 -> 先挪进视频区
        self.inj._cursor = [100 + 30, 50 + 400]
        self.actions.swipe_down()
        names = [c[0] for c in self.inj.calls]
        self.assertIn("move_mouse", names)
        self.assertIn("wheel", names)
        self.assertLess(names.index("move_mouse"), names.index("wheel"))
        # 挪过去之后还要挪回来（restore_cursor=True）
        self.assertGreaterEqual(names.count("move_mouse"), 2)

    def test_like_uses_double_click(self):
        self.assertTrue(self.actions.like())
        names = [c[0] for c in self.inj.calls]
        self.assertIn("double_click", names)
        self.assertNotIn("click_normalized", names)

    def test_favorite_and_comments_click_different_points(self):
        self.assertTrue(self.actions.favorite())
        self.assertTrue(self.actions.open_comments())
        clicks = [c for c in self.inj.calls if c[0] == "click_normalized"]
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0][1:3], (0.9685, 0.6753))
        self.assertEqual(clicks[1][1:3], (0.9685, 0.5922))
        self.assertNotEqual(clicks[0][2], clicks[1][2])

    def test_back_sends_measured_key_sequence(self):
        self.assertTrue(self.actions.back())
        presses = [c for c in self.inj.calls if c[0] == "press"]
        self.assertEqual(presses, [("press", "esc"), ("press", "alt+left")])

    def test_back_single_key_from_config(self):
        self.cfg.back.key = "esc"
        self.actions.back()
        self.assertEqual([c for c in self.inj.calls if c[0] == "press"], [("press", "esc")])

    def test_placeholder_coords_fall_back_to_calibrated(self):
        cfg = Config()   # config.py 里的占位默认值就是 (0.5, 0.5)
        blind = FakeInjector()
        actions = DouyinActions(blind, cfg)
        self.assertTrue(actions.open_comments())
        clicks = [c for c in blind.calls if c[0] == "click_normalized"]
        self.assertEqual(clicks[0][1:3], (0.9685, 0.5922))

    def test_no_exception_leaks(self):
        broken = FakeInjector(fail_on="wheel")
        actions = DouyinActions(broken, self.cfg)
        self.assertFalse(actions.swipe_up())
        broken2 = FakeInjector(fail_on="click_normalized")
        actions2 = DouyinActions(broken2, self.cfg)
        self.assertFalse(actions2.favorite())
        broken3 = FakeInjector(fail_on="press")
        actions3 = DouyinActions(broken3, self.cfg)
        self.assertFalse(actions3.back())

    def test_missing_window_is_handled(self):
        blind = FakeInjector(rect=None)
        actions = DouyinActions(blind, self.cfg)
        self.assertFalse(actions.like())      # 拿不到客户区 -> False，不抛
        self.assertTrue(actions.swipe_down())  # 滚轮不依赖客户区，仍然发


class DouyinSpeedTest(unittest.TestCase):
    """快退 / 快进 / 倍速（set_speed 的连点、粘性记忆、未知档位）。"""

    def setUp(self):
        self.cfg = Config()
        self.inj = FakeInjectorWithSequence()
        self.actions = DouyinActions(self.inj, self.cfg)

    def _calls(self, injector, name):
        return [c for c in injector.calls if c[0] == name]

    def test_seek_backward_presses_configured_key(self):
        self.assertTrue(self.actions.seek_backward())
        self.assertEqual(self._calls(self.inj, "press"), [("press", "left")])
        self.assertEqual(self._calls(self.inj, "click_normalized"), [])

    def test_seek_backward_uses_config_key(self):
        self.cfg.speed.back_key = "shift+left"
        self.actions.seek_backward()
        self.assertEqual(self._calls(self.inj, "press"), [("press", "shift+left")])

    def test_seek_forward_injects_nothing(self):
        with self.assertLogs("xbox_douyin.douyin", level="WARNING") as logs:
            self.assertFalse(self.actions.seek_forward())
        self.assertEqual(self.inj.calls, [])                 # 零注入
        self.assertTrue(any("没有前进 seek" in line for line in logs.output))

    def test_set_speed_uses_single_click_sequence(self):
        self.assertTrue(self.actions.set_speed("2x"))
        seq = self._calls(self.inj, "click_sequence")
        self.assertEqual(len(seq), 1)
        self.assertEqual(seq[0][1], [(0.86, 0.9633), (0.8618, 0.8727)])   # 按钮 -> 2x 档
        self.assertEqual(seq[0][2], 60)
        self.assertTrue(seq[0][3])
        self.assertEqual(self._calls(self.inj, "click_normalized"), [])    # 没走退化路径

    def test_set_speed_label_forms_normalize(self):
        for label in ("2x", "2.0x", "2X", "2", 2.0):
            actions = DouyinActions(FakeInjectorWithSequence(), self.cfg)
            self.assertTrue(actions.set_speed(label), label)
            seq = [c for c in actions.injector.calls if c[0] == "click_sequence"]
            self.assertEqual(seq[0][1][1], (0.8618, 0.8727), label)
            self.assertEqual(actions._speed_label, "2x", label)

    def test_set_speed_fallback_without_click_sequence(self):
        plain = FakeInjector()          # 没有 click_sequence -> 退化路径
        actions = DouyinActions(plain, self.cfg)
        self.assertTrue(actions.set_speed("2x"))
        clicks = self._calls(plain, "click_normalized")
        self.assertEqual(len(clicks), 2)
        self.assertEqual(clicks[0][1:3], (0.86, 0.9633))    # 第一次：不还原光标
        self.assertFalse(clicks[0][3])
        self.assertEqual(clicks[1][1:3], (0.8618, 0.8727))  # 第二次：末尾还原
        self.assertTrue(clicks[1][3])

    def test_set_speed_is_sticky(self):
        self.assertTrue(self.actions.set_speed("2x"))
        first = len(self.inj.calls)
        self.assertTrue(self.actions.set_speed("2.0x"))     # 同一档位（写法不同）
        self.assertEqual(len(self.inj.calls), first)        # 第二次零注入
        self.assertTrue(self.actions.set_speed("1x"))
        self.assertEqual(len(self.inj.calls), first + 1)    # 换档才注入

    def test_fresh_instance_1x_must_inject(self):
        # 新进程不知道播放器现在是多少：请求 1x 也必须真的点一次，否则会误报成功
        self.assertIsNone(self.actions._speed_label)
        self.assertTrue(self.actions.set_speed("1x"))
        self.assertEqual(len(self._calls(self.inj, "click_sequence")), 1)
        self.assertEqual(self.actions._speed_label, "1x")

    def test_set_speed_unknown_label_injects_nothing(self):
        with self.assertLogs("xbox_douyin.douyin", level="WARNING") as logs:
            self.assertFalse(self.actions.set_speed("0.5x"))   # 菜单里没有 0.5x
        self.assertEqual(self.inj.calls, [])
        joined = " ".join(logs.output)
        self.assertIn("0.5x", joined)
        self.assertIn("0.75x", joined)                      # 日志里列出可用档位

    def test_set_speed_interval_is_clamped(self):
        self.cfg.speed.click_interval_ms = 500              # 实测必须 <= 120ms
        self.actions.set_speed("2x")
        seq = self._calls(self.inj, "click_sequence")
        self.assertEqual(seq[0][2], 120)

    def test_set_speed_memory_updates_only_on_success(self):
        broken = FakeInjectorWithSequence(fail_on="click_sequence")
        actions = DouyinActions(broken, self.cfg)
        self.assertFalse(actions.set_speed("2x"))
        self.assertIsNone(actions._speed_label)             # 失败不改记忆（仍是未知）

    def test_sticky_skips_only_after_real_click(self):
        # 本进程点过 2x 之后，再请求 2x 才允许零注入
        self.assertTrue(self.actions.set_speed("2x"))
        clicked = len(self._calls(self.inj, "click_sequence"))
        self.assertTrue(self.actions.set_speed("2x"))
        self.assertEqual(len(self._calls(self.inj, "click_sequence")), clicked)   # 零注入

    def test_sticky_then_back_to_1x_really_clicks(self):
        self.assertTrue(self.actions.set_speed("2x"))
        before = len(self._calls(self.inj, "click_sequence"))
        self.assertTrue(self.actions.set_speed("1x"))
        seq = self._calls(self.inj, "click_sequence")
        self.assertEqual(len(seq), before + 1)              # 换档必须真点
        self.assertEqual(seq[-1][1][1], (0.8618, 0.7195))   # 点的是 1x 档
        self.assertEqual(self.actions._speed_label, "1x")


class DouyinVideoGuardTest(unittest.TestCase):
    """倍速守卫：只有普通视频才点倍速（直播房间/无播放器时零注入）。

    三态：采样 None -> 照常执行；低占比 -> 阻断；高占比 -> 执行。
    另外两条容错：采样抛异常不阻断；注入器没有采样原语时向后兼容不阻断。
    """

    def setUp(self):
        self.cfg = Config()
        # 阈值随 config 走（Lead 可能调 probe_min_ratio / 换 probe_roi），测试别硬编码常量
        self.min_ratio = float(self.cfg.speed.probe_min_ratio)
        self.low_ratio = self.min_ratio / 2.0
        self.high_ratio = max(self.min_ratio * 2.0, self.min_ratio + 0.05)

    def _clicks(self, injector):
        return [c for c in injector.calls if c[0] == "click_sequence"]

    def test_unknown_ratio_executes(self):
        inj = FakeInjectorWithSampler(ratio=None)
        with self.assertLogs("xbox_douyin.douyin", level="INFO") as logs:
            self.assertTrue(DouyinActions(inj, self.cfg).set_speed("2x"))
        self.assertEqual(len(self._clicks(inj)), 1)          # 照常执行
        self.assertTrue(any("不可用" in line for line in logs.output))

    def test_low_ratio_blocks_everything(self):
        inj = FakeInjectorWithSampler(ratio=self.low_ratio)   # < probe_min_ratio，必然是"疑似直播"
        with self.assertLogs("xbox_douyin.douyin", level="WARNING") as logs:
            self.assertFalse(DouyinActions(inj, self.cfg).set_speed("2x"))
        self.assertEqual(inj.calls, [])                      # 一个点击都没发
        self.assertTrue(any("疑似直播房间" in line for line in logs.output))

    def test_high_ratio_executes(self):
        inj = FakeInjectorWithSampler(ratio=self.high_ratio)
        actions = DouyinActions(inj, self.cfg)
        self.assertTrue(actions.set_speed("2x"))
        self.assertEqual(len(self._clicks(inj)), 1)
        self.assertEqual(actions._speed_label, "2x")

    def test_guard_samples_configured_roi(self):
        inj = FakeInjectorWithSampler(ratio=self.high_ratio)
        DouyinActions(inj, self.cfg).set_speed("2x")
        self.assertEqual(inj.sampled,
                         [tuple(round(float(v), 4) for v in self.cfg.speed.probe_roi)])

    def test_sampler_exception_does_not_block(self):
        inj = FakeInjectorWithSampler(raises=True)
        self.assertTrue(DouyinActions(inj, self.cfg).set_speed("2x"))
        self.assertEqual(len(self._clicks(inj)), 1)

    def test_missing_sampler_is_backward_compatible(self):
        inj = FakeInjectorWithSequence()                     # 老注入器：没有采样原语
        self.assertTrue(DouyinActions(inj, self.cfg).set_speed("2x"))
        self.assertEqual(len(self._clicks(inj)), 1)

    def test_guard_not_reached_when_speed_already_matches(self):
        inj = FakeInjectorWithSampler(ratio=self.low_ratio)
        actions = DouyinActions(inj, self.cfg)
        actions._speed_label = "1x"                          # 模拟"本进程已经点成 1x"
        self.assertTrue(actions.set_speed("1x"))
        self.assertEqual(inj.sampled, [])                    # 连采样都不做
        self.assertEqual(inj.calls, [])

    def test_guard_runs_for_fresh_instance_even_for_1x(self):
        inj = FakeInjectorWithSampler(ratio=self.high_ratio)
        actions = DouyinActions(inj, self.cfg)               # 新实例：记忆未知
        self.assertTrue(actions.set_speed("1x"))
        self.assertEqual(len(inj.sampled), 1)                # 必须采样 + 真点
        self.assertEqual(len([c for c in inj.calls if c[0] == "click_sequence"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

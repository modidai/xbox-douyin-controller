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


if __name__ == "__main__":
    unittest.main(verbosity=2)

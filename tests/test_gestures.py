"""gestures 单元测试：纯标准库，直接 python tests/test_gestures.py 运行。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from xbox_douyin.config import Config, MouseConfig
from xbox_douyin.gestures import DoubleTapDetector, EdgeTrigger, stick_to_mouse_delta

FAILED = []
COUNT = [0]


def check(name, condition, detail=""):
    COUNT[0] += 1
    if condition:
        print("PASS  %s" % name)
    else:
        print("FAIL  %s%s" % (name, ("  <- " + str(detail)) if detail else ""))
        FAILED.append(name)


def test_edge_trigger_hysteresis():
    trigger = EdgeTrigger(0.6, 0.35)
    check("迟滞：越过阈值触发一次", trigger.update(0.7, 0.0) is True)
    check("迟滞：仍高于阈值但未回落，不重复触发", trigger.update(0.7, 0.1) is False)
    check("迟滞：回落到释放阈值之上(0.5)仍是解武装", trigger.update(0.5, 0.2) is False)
    check("迟滞：释放阈值以下重新武装", trigger.update(0.3, 0.3) is False)
    check("迟滞：重新上穿再次触发", trigger.update(0.6, 0.4) is True)
    check("迟滞：正好等于阈值也触发", EdgeTrigger(0.6, 0.35).update(0.6, 0.0) is True)
    check("迟滞：低于阈值不触发", EdgeTrigger(0.6, 0.35).update(0.59, 0.0) is False)


def test_edge_trigger_cooldown():
    trigger = EdgeTrigger(0.5, 0.2, cooldown_ms=100)
    check("冷却：首次上穿立即触发", trigger.update(0.9, 0.0) is True)
    trigger.update(0.0, 0.01)
    check("冷却：冷却期内上穿被抑制", trigger.update(0.9, 0.05) is False)
    trigger.update(0.0, 0.06)
    check("冷却：冷却期满后再次触发", trigger.update(0.9, 0.2) is True)


def test_double_tap_window():
    detector = DoubleTapDetector(350)
    check("双击：第一次按下不算双击", detector.tap(0.0) is False)
    check("双击：窗口正边界(350ms)算双击", detector.tap(0.35) is True)
    check("双击：命中后立即重置", detector.tap(0.36) is False)
    detector.reset()
    check("双击：reset 后第一次仍是单击", detector.tap(1.0) is False)
    check("双击：超出窗口(351ms)按新的第一次算", detector.tap(1.351) is False)
    check("双击：紧接着的第二次仍算双击", detector.tap(1.6) is True)
    tight = DoubleTapDetector(0)
    first = tight.tap(5.0)
    second = tight.tap(5.0)
    check("双击：窗口 0 时同刻算双击", first is False and second is True, (first, second))
    tight.reset()
    third = tight.tap(5.0)
    fourth = tight.tap(5.001)
    check("双击：窗口 0 时任何正间隔都算新按下", third is False and fourth is False, (third, fourth))


def test_deadzone():
    cfg = Config()
    check("死区：中心为 0", stick_to_mouse_delta(0.0, 0.0, 0.1, cfg) == (0.0, 0.0))
    check("死区：默认 0.15 内为 0", stick_to_mouse_delta(0.1, 0.0, 0.1, cfg) == (0.0, 0.0))
    check("死区：斜向模长在死区内也为 0", stick_to_mouse_delta(0.1, 0.1, 0.1, cfg) == (0.0, 0.0))
    dx, dy = stick_to_mouse_delta(0.3, 0.0, 0.1, cfg)
    check("死区：刚好出死区有位移", dx > 0.0 and dy == 0.0, (dx, dy))


def test_curve_and_speed():
    cfg = Config()
    cfg.mouse = MouseConfig(deadzone=0.0, max_speed_px_s=1000.0, curve=1.0, invert_y=False)
    dx_linear, _ = stick_to_mouse_delta(0.5, 0.0, 0.1, cfg)
    check("曲线：curve=1 线性 = 500px/s*0.1s", abs(dx_linear - 50.0) < 1e-6, dx_linear)

    cfg.mouse = MouseConfig(deadzone=0.0, max_speed_px_s=1000.0, curve=2.0, invert_y=False)
    dx_curve, _ = stick_to_mouse_delta(0.5, 0.0, 0.1, cfg)
    check("曲线：curve=2 半推更慢(25px)", abs(dx_curve - 25.0) < 1e-6, dx_curve)
    check("曲线：curve=2 比线性慢", dx_curve < dx_linear)

    dx_full, dy_full = stick_to_mouse_delta(1.0, 0.0, 0.1, cfg)
    check("曲线：满推 = max_speed*dt", abs(dx_full - 100.0) < 1e-6, dx_full)
    check("曲线：满推横向无 Y 位移", dy_full == 0.0, dy_full)

    cfg.mouse = MouseConfig(deadzone=0.2, max_speed_px_s=900.0, curve=3.0, invert_y=False)
    dx, dy = stick_to_mouse_delta(1.0, 1.0, 0.05, cfg)
    speed = (dx * dx + dy * dy) ** 0.5
    check("限速：斜向满推不超过 max_speed*dt", speed <= 900.0 * 0.05 + 1e-9, speed)
    check("限速：每次位移都是有限数", abs(dx) < 1e6 and abs(dy) < 1e6)


def test_invert_y():
    cfg = Config()
    cfg.mouse = MouseConfig(deadzone=0.0, max_speed_px_s=1000.0, curve=1.0, invert_y=False)
    _, dy_normal = stick_to_mouse_delta(0.0, 1.0, 0.1, cfg)
    check("Y 轴：invert_y=False 时上推为正（不反转）", dy_normal > 0.0, dy_normal)

    cfg.mouse = MouseConfig(deadzone=0.0, max_speed_px_s=1000.0, curve=1.0, invert_y=True)
    _, dy_inverted = stick_to_mouse_delta(0.0, 1.0, 0.1, cfg)
    check("Y 轴：invert_y=True 时上推为负", dy_inverted < 0.0, dy_inverted)
    check("Y 轴：反转后大小不变", abs(abs(dy_inverted) - abs(dy_normal)) < 1e-9)

    default_cfg = Config()
    check("Y 轴：默认配置 invert_y 为 True", default_cfg.mouse.invert_y is True, default_cfg.mouse.invert_y)
    _, dy_default = stick_to_mouse_delta(0.0, 0.9, 0.1, default_cfg)
    check("Y 轴：默认配置下右摇杆上推 = 光标上移(dy<0)", dy_default < 0.0, dy_default)


def test_guard_cases():
    cfg = Config()
    check("异常输入：dt=0 无位移", stick_to_mouse_delta(0.9, 0.9, 0.0, cfg) == (0.0, 0.0))
    check("异常输入：dt<0 无位移", stick_to_mouse_delta(0.9, 0.9, -1.0, cfg) == (0.0, 0.0))
    dx, _ = stick_to_mouse_delta(5.0, 0.0, 0.1, cfg)
    check("异常输入：超出 1 的摇杆值被夹住", dx <= cfg.mouse.max_speed_px_s * 0.1 + 1e-9, dx)

    dict_cfg = {
        "mouse": {
            "deadzone": 0.1,
            "curve": 1.0,
            "max_speed_px_s": 100.0,
            "invert_y": True,
        }
    }
    dx, dy = stick_to_mouse_delta(1.0, 0.0, 0.1, dict_cfg)
    check("配置形态：dict 形式的 cfg 也能算", abs(dx - 10.0) < 1e-6, dx)


def main():
    print("=== gestures 单元测试 ===")
    for func in (
        test_edge_trigger_hysteresis,
        test_edge_trigger_cooldown,
        test_double_tap_window,
        test_deadzone,
        test_curve_and_speed,
        test_invert_y,
        test_guard_cases,
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

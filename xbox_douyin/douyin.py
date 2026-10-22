# -*- coding: utf-8 -*-
"""抖音动作层：把「切视频 / 点赞 / 返回 / 收藏 / 评论」翻译成注入器调用。

设计要点：
  * 只通过 Injector 的 8 个方法注入（move_mouse / left_click / double_click /
    wheel / press / click_normalized / cursor_pos / target_client_rect），
    自己绝不直接调 Win32；注入器的实现在 injector.py 里。
  * 每单次 sleep 都不超过 50ms，不在主循环里阻塞。
  * 公开方法一律返回 True/False 并把异常吞进日志，绝不向调用方抛。
  * 导入本模块没有任何副作用。

坐标与手法全部来自本机实测（见 docs/CALIBRATION.md 与 config.example.json）：
  * 切视频：滚轮一次事件 = 恰好切一条（zDelta 用 120 还是 360 都是 1 条 ——
    决定步长的是事件个数，不是 zDelta），所以 notches 用 1；
    方向：滚轮向"下"（负）= 下一条，向"上"（正）= 上一条；
    方向键 ArrowDown / ArrowUp 效果完全等价，可作备选。
  * 滚轮只落在视频区域内才会被抖音当成"切视频"（实测：光标停在左侧导航栏时
    滚轮被导航栏吞掉，视频不动），所以滚轮前会先把光标挪进视频区。
  * 右侧竖排按钮栏（抽屉关闭态）图标中心 x≈0.9685：点赞心形 y≈0.5124、
    评论气泡 y≈0.5922、收藏五角星 y≈0.6753。
  * 点赞走双击视频区（落点 0.5 / 0.45，避开右侧按钮栏与底部进度条）。
  * 返回：Esc 能关掉弹出层（如清晰度菜单），alt+left 是应用内历史后退；
    两者在首页 feed 上都是空操作（实测无副作用），所以默认串起来发。
"""
import logging
import time

log = logging.getLogger(__name__)

# —— 时间常量（保证任何一次 sleep 都 <= 50ms）——
_TICK_S = 0.05              # 两个注入动作之间的最小间隔
_CURSOR_STEP_S = 0.01       # 光标挪动重试间隔
_CURSOR_MAX_STEPS = 8       # 光标挪动最多重试次数（防死循环）
_CURSOR_TOLERANCE_PX = 2    # 光标到位的容差
_DOUBLE_CLICK_MS = 40       # 双击两下之间的间隔（与 Injector.double_click 默认一致）

# —— 实测校准值：当配置仍是占位值 (0.5, 0.5) 时用它兜底 ——
# 直接把占位值当点击目标会把点击打到视频正中间（那里是播放/暂停开关）。
_CALIBRATED = {
    "like": ("double_click", "", 0.5000, 0.4500),
    "favorite": ("click", "", 0.9685, 0.6753),
    "comments": ("click", "", 0.9685, 0.5922),
    "back": ("key", "esc|alt+left", 0.5000, 0.5000),
}
_PLACEHOLDER = (0.5, 0.5)

# 滚轮安全的落点范围（客户区归一化）：左侧 10% 是导航栏，实测会吞掉滚轮
_WHEEL_SAFE = (0.10, 0.12, 0.99, 0.96)
_WHEEL_PARK = (0.5000, 0.4500)


def resolve_action_params(cfg, name):
    """解析某个动作的 (method, key, x, y)。

    配置优先；只有当整组仍是 config.py 的占位默认值
    （method="click" + key="" + (0.5, 0.5)）时，才整组换成实测校准值 ——
    否则会把"点赞/返回"变成在视频正中间单击一次（那是播放/暂停）。
    tools/measure.py 也用这个函数，保证报告和实际行为一致。
    """
    method, key, x, y = _CALIBRATED.get(name, ("click", "", 0.5, 0.5))
    act = getattr(cfg, name, None)
    if act is None:
        return method, key, x, y
    cfg_method = (getattr(act, "method", "") or "").strip().lower()
    cfg_key = getattr(act, "key", "") or ""
    try:
        cfg_x = float(getattr(act, "x", 0.5))
        cfg_y = float(getattr(act, "y", 0.5))
    except (TypeError, ValueError):
        cfg_x = cfg_y = None
    unconfigured = (
        cfg_x is not None and cfg_y is not None
        and (round(cfg_x, 4), round(cfg_y, 4)) == _PLACEHOLDER
        and cfg_method in ("", "click") and not cfg_key
    )
    if unconfigured:
        log.debug("%s 仍是占位配置，整组改用实测校准值: %s", name, (method, key, x, y))
        return method, key, x, y
    return (cfg_method or method), (cfg_key or key),            (x if cfg_x is None else cfg_x), (y if cfg_y is None else cfg_y)


class DouyinActions(object):
    """把语义动作翻译成注入调用；每个方法返回 True/False。"""

    def __init__(self, injector, cfg):
        self.injector = injector
        self.cfg = cfg

    # ------------------------------------------------------------------ 切视频
    def swipe_up(self):
        """上一条视频。"""
        return self._swipe(+1)

    def swipe_down(self):
        """下一条视频。"""
        return self._swipe(-1)

    def _swipe(self, direction):
        try:
            method, key_up, key_down, notches = self._swipe_params()
            if method == "key":
                key = key_up if direction > 0 else key_down
                return self._press_sequence(key)
            count = max(1, abs(int(notches)))
            return self._wheel(direction * count)
        except Exception as exc:  # 动作层绝不把异常抛给调用方
            log.warning("切视频失败(%s): %s", "上一条" if direction > 0 else "下一条", exc)
            return False

    def _swipe_params(self):
        swipe = getattr(self.cfg, "swipe", None)
        method = (getattr(swipe, "method", "wheel") or "wheel").strip().lower()
        notches = getattr(swipe, "notches", 1)
        key_up = getattr(swipe, "key_up", "up") or "up"
        key_down = getattr(swipe, "key_down", "down") or "down"
        try:
            notches = int(notches)
        except (TypeError, ValueError):
            notches = 1
        return method, key_up, key_down, notches

    def _wheel(self, notches):
        restore_to = None
        try:
            restore_to = self._ensure_cursor_over_video()
            self.injector.wheel(notches)
        finally:
            if restore_to is not None:
                self._move_cursor_to(restore_to[0], restore_to[1], quiet=True)
        log.debug("滚轮 %+d 格", notches)
        return True

    # ------------------------------------------------------------------ 点赞
    def like(self):
        """双击视频区 = 点赞。"""
        try:
            method, key, x, y = self._action_params("like")
            if method == "key":
                return self._press_sequence(key)
            if method == "click":
                return self._click_at(x, y, "like")
            return self._double_click_at(x, y)
        except Exception as exc:
            log.warning("点赞失败: %s", exc)
            return False

    def _double_click_at(self, x, y):
        rect = self._client_rect()
        if rect is None:
            log.warning("点赞跳过：拿不到抖音客户区")
            return False
        origin = self._park_cursor(x, y, rect)
        try:
            self.injector.double_click(interval_ms=_DOUBLE_CLICK_MS)
        finally:
            if origin is not None and self._restore_cursor():
                self._move_cursor_to(origin[0], origin[1], quiet=True)
        log.debug("双击点赞 @ (%.4f, %.4f)", x, y)
        return True

    # ------------------------------------------------------------------ 收藏
    def favorite(self):
        """点右侧「收藏」五角星。"""
        return self._run_action("favorite", "收藏")

    # ------------------------------------------------------------------ 评论
    def open_comments(self):
        """点右侧「评论」气泡。"""
        return self._run_action("comments", "打开评论")

    # ------------------------------------------------------------------ 返回
    def back(self):
        """返回：默认按 esc|alt+left 顺序发（Esc 先关弹出层，再历史后退）。"""
        try:
            method, key, x, y = self._action_params("back")
            if method == "click":
                return self._click_at(x, y, "back")
            if method == "double_click":
                return self._double_click_at(x, y)
            return self._press_sequence(key or "esc")
        except Exception as exc:
            log.warning("返回失败: %s", exc)
            return False

    # ------------------------------------------------------------------ 内部
    def _run_action(self, name, label):
        try:
            method, key, x, y = self._action_params(name)
            if method == "key":
                return self._press_sequence(key)
            if method == "double_click":
                return self._double_click_at(x, y)
            return self._click_at(x, y, name)
        except Exception as exc:
            log.warning("%s失败: %s", label, exc)
            return False

    def _action_params(self, name):
        return resolve_action_params(self.cfg, name)

    def _click_at(self, x, y, name):
        restore = self._restore_cursor()
        self.injector.click_normalized(x, y, restore=restore)
        log.debug("%s 点击 (%.4f, %.4f) restore=%s", name, x, y, restore)
        return True

    def _press_sequence(self, spec):
        """"esc|alt+left" 这种用 | 分隔的组合逐条发送。"""
        keys = [part.strip() for part in str(spec or "").split("|") if part.strip()]
        if not keys:
            log.warning("返回键为空，没发任何按键")
            return False
        for index, key in enumerate(keys):
            self.injector.press(key)
            if index + 1 < len(keys):
                time.sleep(_TICK_S)
        log.debug("按键序列: %s", " | ".join(keys))
        return True

    def _client_rect(self):
        try:
            rect = self.injector.target_client_rect()
        except Exception as exc:
            log.debug("target_client_rect 失败: %s", exc)
            return None
        if not rect or len(rect) != 4:
            return None
        left, top, w, h = rect
        if w <= 1 or h <= 1:
            return None
        return (left, top, w, h)

    def _restore_cursor(self):
        return bool(getattr(self.cfg, "restore_cursor", True))

    def _park_cursor(self, x, y, rect):
        """把光标挪到客户区归一化点，返回原位置（没挪则 None）。"""
        left, top, w, h = rect
        target_x = left + x * (w - 1)
        target_y = top + y * (h - 1)
        try:
            origin = self.injector.cursor_pos()
        except Exception as exc:
            log.debug("cursor_pos 失败: %s", exc)
            origin = None
        if origin is not None:
            if abs(origin[0] - target_x) <= _CURSOR_TOLERANCE_PX and \
                    abs(origin[1] - target_y) <= _CURSOR_TOLERANCE_PX:
                return None
        if not self._move_cursor_to(target_x, target_y):
            log.debug("光标没挪到位（继续执行双击）")
        return origin

    def _ensure_cursor_over_video(self):
        """滚轮前确保光标落在视频区，返回原光标位置（无需挪动则 None）。"""
        rect = self._client_rect()
        if rect is None:
            return None
        left, top, w, h = rect
        try:
            cx, cy = self.injector.cursor_pos()
        except Exception:
            return None
        nx = (cx - left) / float(w - 1)
        ny = (cy - top) / float(h - 1)
        x0, y0, x1, y1 = _WHEEL_SAFE
        if x0 <= nx <= x1 and y0 <= ny <= y1:
            return None
        target_x = left + _WHEEL_PARK[0] * (w - 1)
        target_y = top + _WHEEL_PARK[1] * (h - 1)
        if not self._move_cursor_to(target_x, target_y):
            log.debug("滚轮前挪光标失败（仍发滚轮）")
        return (cx, cy)

    def _move_cursor_to(self, target_x, target_y, quiet=False):
        """用相对位移一步步逼近目标点（move_mouse 可能被指针加速缩放）。"""
        for _ in range(_CURSOR_MAX_STEPS):
            try:
                cx, cy = self.injector.cursor_pos()
            except Exception as exc:
                if not quiet:
                    log.debug("cursor_pos 失败: %s", exc)
                return False
            dx = int(round(target_x - cx))
            dy = int(round(target_y - cy))
            if abs(dx) <= _CURSOR_TOLERANCE_PX and abs(dy) <= _CURSOR_TOLERANCE_PX:
                return True
            self.injector.move_mouse(dx, dy)
            time.sleep(_CURSOR_STEP_S)
        if not quiet:
            log.debug("光标未在 %d 步内到位", _CURSOR_MAX_STEPS)
        return False

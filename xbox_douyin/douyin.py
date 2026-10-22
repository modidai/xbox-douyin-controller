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
  * 快退：键盘 ← / Shift+← = 客户端原生后退 5 秒（可靠，不依赖画面状态）。
  * 快进：客户端**没有**前进 seek（→ / Shift+→ / J / L / , / . / PgUp / PgDn /
    Shift+, / Shift+. 全部实测无效），按用户决定留空 —— seek_forward() 只记日志、
    零注入、返回 False，用于 run.py --action seek_forward 时给出诚实的失败原因。
  * 倍速：只能"点倍速按钮 → 点菜单档位"两段连点，中间绝不能移动光标
    （菜单是 hover 层，光标一离开就消失，第二次点击会落到视频画面上 = 播放/暂停）。
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

# 倍速菜单的实测坐标兜底（与 config.py 的 SpeedConfig 默认值一致，配置优先）
_SPEED_FALLBACK = {
    "button_x": 0.8600,
    "button_y": 0.9633,
    "menu_x": 0.8618,
    "click_interval_ms": 60,
    "back_key": "left",
    "back_seconds": 5,
    # "只有普通视频才点倍速"的守卫参数（实测见 docs/CALIBRATION.md §8.11）
    "probe_roi": (0.1242, 0.9397, 0.1878, 0.9763),
    "probe_min_ratio": 0.02,
    "menu_y": {
        "0.75x": 0.6817,
        "1x": 0.7195,
        "1.25x": 0.7573,
        "1.5x": 0.7961,
        "1.75x": 0.8339,
        "2x": 0.8727,
        "3x": 0.9115,
    },
}


def _speed_sort_key(label):
    """按数值排序档位名（"0.75x" < "1x" < "3x"）。"""
    text = str(label).strip().lower()
    try:
        return float(text[:-1]) if text.endswith("x") else float(text)
    except ValueError:
        return 999.0


def normalize_speed_label(label, menu_y):
    """把 "2x" / "2.0x" / "2X" / "1.0" 这类宽松写法归一化到菜单里的档位键。

    认不出来返回 None —— 调用方据此拒绝注入，绝不瞎点。
    """
    if label is None or not menu_y:
        return None
    text = str(label).strip().lower().replace(" ", "").replace("×", "x")
    if not text:
        return None
    if not text.endswith("x"):
        text = text + "x"          # 允许直接写 "2" / "1.0"
    try:
        value = float(text[:-1])
    except ValueError:
        for key in menu_y:
            if str(key).strip().lower() == text:
                return key
        return None
    for key in menu_y:
        candidate = str(key).strip().lower()
        if not candidate.endswith("x"):
            continue
        try:
            if abs(float(candidate[:-1]) - value) < 1e-9:
                return key
        except ValueError:
            continue
    return None


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
    return ((cfg_method or method), (cfg_key or key),
            (x if cfg_x is None else cfg_x), (y if cfg_y is None else cfg_y))


class DouyinActions(object):
    """把语义动作翻译成注入调用；每个方法返回 True/False。"""

    def __init__(self, injector, cfg):
        self.injector = injector
        self.cfg = cfg
        # 粘性状态记忆：抖音的倍速是粘性的（改了会一直保持到下一条视频），
        # 记住"本进程成功点过的档位"，重复请求同一档位就不再点菜单（少打扰用户 UI）。
        # **初始必须是 None（未知）而不是 "1x"**：新进程并不知道播放器现在是多少，
        # 擅自假设成 1x 会让 set_speed("1x") 零注入还返回 True —— 那是误报成功，
        # 用户会一直卡在别人设的 2x 上（真机复现过）。
        self._speed_label = None
        self._no_click_sequence = False

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

    # ------------------------------------------------------------- 快退 / 快进
    def seek_backward(self):
        """后退（客户端原生：键盘 ← / Shift+← = 后退 5 秒，不依赖画面状态）。"""
        try:
            speed = self._speed_cfg()
            key = str(speed["back_key"] or "left")
            ok = self._press_sequence(key)
            if ok:
                log.debug("快退 %s 秒（按键 %s）", speed["back_seconds"], key)
            return ok
        except Exception as exc:
            log.warning("快退失败: %s", exc)
            return False

    def seek_forward(self):
        """前进：抖音 PC 客户端没有前进 seek，按用户决定留空（零注入 + 诚实失败）。"""
        log.warning("抖音 PC 客户端没有前进 seek（→/Shift+→/J/L/,/./PgUp/PgDn 实测全部无效），"
                    "按用户决定此动作留空：不注入任何输入，返回 False")
        return False

    # ------------------------------------------------------------------ 倍速
    def set_speed(self, label):
        """切换播放速度：点「倍速」按钮 → 点菜单档位（两段连点，中间不移动光标）。

        **返回值语义：True 表示"点击已经发出去了"，不表示"播放器速度已验证"。**
        动作层没有读数能力（Injector 不能读画面数值），直播房间没有倍速按钮、
        客户端改版导致坐标漂移时都会"点了但没生效"，只能靠日志和 tools/measure.py 复校。

        粘性记忆的含义也是"本进程成功点过的档位"，不是"播放器当前状态"：
        初始为 None（未知），此时任何请求（包括 1x）都必须真的发一次点击。
        """
        try:
            speed = self._speed_cfg()
            menu_y = speed["menu_y"]
            target = normalize_speed_label(label, menu_y)
            if target is None:
                available = ", ".join(sorted(menu_y, key=_speed_sort_key))
                log.warning("倍速档位 %r 不在抖音菜单里（可用档位: %s），不发送任何点击", label, available)
                return False
            current = normalize_speed_label(self._speed_label, menu_y) \
                if self._speed_label is not None else None
            if current is not None and current == target:
                log.debug("本进程已把倍速点成 %s，跳过重复点击（粘性记忆）", target)
                return True
            if current is None:
                log.debug("倍速实际档位未知（本进程还没点过），必须真发一次点击：目标 %s", target)
            point_a = (float(speed["button_x"]), float(speed["button_y"]))
            point_b = (float(speed["menu_x"]), float(menu_y[target]))
            interval = self._click_interval_ms(speed)
            if not self._video_only_ok(target):
                return False
            self._click_pair(point_a, point_b, interval)
            log.debug("倍速记忆: %s -> %s", self._speed_label or "未知", target)
            self._speed_label = target
            log.info("已请求倍速 %s（未校验画面：直播房间没有倍速按钮时会无效）", target)
            return True
        except Exception as exc:
            log.warning("切换倍速失败(%s): %s", label, exc)
            return False

    def _video_only_ok(self, target_label):
        """倍速守卫：只在"普通视频"上动手，直播房间/无播放器时一个点击都不发。

        判据来自 Injector.sample_bright_ratio(roi)（客户区某区域的亮像素占比）。
        **行为是不对称的**（宁可功能可用，也不能误点直播间）：
          * 注入器没有采样能力（老版本）→ 跳过守卫，照常执行（debug 日志）
          * 采样返回 None（窗口找不到/采样失败）→ 视为"未知"，照常执行（info 日志）
          * 采样返回数值但低于阈值 → 判定不是普通视频，**阻断**（warning 日志）
        """
        sampler = getattr(self.injector, "sample_bright_ratio", None)
        if not callable(sampler):
            log.debug("注入器没有 sample_bright_ratio，跳过“只在普通视频上生效”的守卫（向后兼容）")
            return True
        speed = self._speed_cfg()
        roi = speed.get("probe_roi")
        min_ratio = speed.get("probe_min_ratio")
        if roi is None or min_ratio is None:
            log.debug("配置缺 probe_roi/probe_min_ratio，跳过倍速守卫")
            return True
        try:
            ratio = sampler(tuple(roi))
        except Exception as exc:
            log.info("亮度采样异常（%s），无法判断是不是普通视频，照常执行", exc)
            return True
        if ratio is None:
            log.info("亮度采样不可用（返回 None），无法判断是不是普通视频，照常执行倍速 %s", target_label)
            return True
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            log.info("亮度采样返回值异常（%r），照常执行倍速 %s", ratio, target_label)
            return True
        if ratio < min_ratio:
            log.warning("疑似直播房间/没有播放器（亮像素占比 %.4f < 阈值 %.4f），"
                        "跳过倍速 %s：不发送任何点击", ratio, min_ratio, target_label)
            return False
        log.debug("亮像素占比 %.4f >= 阈值 %.4f，判定为普通视频，执行倍速 %s",
                  ratio, min_ratio, target_label)
        return True

    def _click_pair(self, point_a, point_b, interval_ms):
        """两段连点：优先用 injector.click_sequence（一次调用、中间不挪光标、末尾统一还原）。"""
        restore = self._restore_cursor()
        seq = getattr(self.injector, "click_sequence", None)
        if callable(seq) and not self._no_click_sequence:
            try:
                seq([point_a, point_b], interval_ms=interval_ms, restore=restore)
                log.debug("连点(click_sequence): %s -> %s 间隔 %dms", point_a, point_b, interval_ms)
                return True
            except TypeError as exc:
                # 签名不匹配（注入器版本不同）→ 永久退化到分两次点击
                self._no_click_sequence = True
                log.debug("click_sequence 签名不兼容(%s)，改用两次 click_normalized", exc)
        # 退化路径：第一次 click 不还原光标（否则菜单会因光标离开而关闭），第二次再还原
        self.injector.click_normalized(point_a[0], point_a[1], restore=False)
        time.sleep(max(0.01, min(interval_ms, 120) / 1000.0))
        self.injector.click_normalized(point_b[0], point_b[1], restore=restore)
        log.debug("连点(fallback): %s -> %s 间隔 %dms restore=%s",
                  point_a, point_b, interval_ms, restore)
        return True

    # ------------------------------------------------------------------ 内部
    def _speed_cfg(self):
        """读 SpeedConfig（配置优先，缺失/类型不对时用实测兜底值）。"""
        speed = getattr(self.cfg, "speed", None)
        out = dict(_SPEED_FALLBACK)
        out["menu_y"] = dict(_SPEED_FALLBACK["menu_y"])
        if speed is None:
            return out
        for name in ("button_x", "button_y", "menu_x", "back_key"):
            value = getattr(speed, name, None)
            if value is not None:
                out[name] = value
        for name in ("click_interval_ms", "back_seconds"):
            value = getattr(speed, name, None)
            try:
                out[name] = int(value)
            except (TypeError, ValueError):
                pass
        roi = getattr(speed, "probe_roi", None)
        if roi is not None:
            try:
                values = tuple(float(item) for item in roi)
                if len(values) == 4:
                    out["probe_roi"] = values
            except (TypeError, ValueError):
                pass
        try:
            out["probe_min_ratio"] = float(getattr(speed, "probe_min_ratio", out["probe_min_ratio"]))
        except (TypeError, ValueError):
            pass
        menu_y = getattr(speed, "menu_y", None)
        if isinstance(menu_y, dict) and menu_y:
            cleaned = {}
            for key, value in menu_y.items():
                try:
                    cleaned[str(key)] = float(value)
                except (TypeError, ValueError):
                    continue
            if cleaned:
                out["menu_y"] = cleaned
        return out

    @staticmethod
    def _click_interval_ms(speed):
        try:
            interval = int(speed.get("click_interval_ms", 60))
        except (TypeError, ValueError):
            interval = 60
        return max(10, min(interval, 120))   # 实测必须 <= 120ms，否则菜单可能已关

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

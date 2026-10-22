"""配置对象（接口契约文件，六个 group 的字段名不可随意改动）。

命令行 `run.py --init-config` 会把默认配置写到 user config.json，
用户改 JSON 即可覆盖默认值，无需改代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Dict, Optional, Tuple


@dataclass
class StickConfig:
    """左摇杆上下 = 上/下一条视频。"""
    threshold: float = 0.6      # 推过该值触发一次
    release: float = 0.35       # 回落到该值以下才重新武装（迟滞，防抖）
    repeat_ms: int = 450        # 一直推住时的重复间隔
    up_action: str = "swipe_up"
    down_action: str = "swipe_down"


@dataclass
class MouseConfig:
    """右摇杆 = 鼠标移动。"""
    deadzone: float = 0.15
    max_speed_px_s: float = 900.0
    curve: float = 2.0          # 响应曲线指数，1=线性，2=细腻慢速
    invert_y: bool = True       # XInput 上推是 +LY、屏幕向下是 +Y，默认反转才是"推上=光标上移"


@dataclass
class TriggerConfig:
    """RT = 鼠标点击。"""
    threshold: float = 0.5
    release: float = 0.35
    cooldown_ms: int = 200


@dataclass
class TapConfig:
    """双击判定窗口。"""
    window_ms: int = 350


@dataclass
class SpeedConfig:
    """倍速菜单的坐标（抖音 PC 客户端只有「后退 5 秒」，没有前进 seek，倍速也没快捷键）。

    实现是「点倍速按钮 → 点档位」两段连点，中间绝不能移动光标：
    菜单是 hover 层，光标一离开就消失，第二次点击会落到视频画面上（= 播放/暂停）。
    """
    button_x: float = 0.8600
    button_y: float = 0.9633
    menu_x: float = 0.8618
    click_interval_ms: int = 60      # 两段连点的间隔，实测需 <=120ms
    back_key: str = "left"           # 后退快捷键（客户端原生后退 5 秒）
    back_seconds: int = 5
    # 直播房间没有倍速按钮，点下去会打到连播/弹幕区。用"右侧收藏五角星区域有没有亮像素"判断
    # 当前是不是普通视频：按钮栏不随控制条淡出（实测普通视频稳定 ~0.30），直播房间明显更低。
    # 归一化的 (x1, y1, x2, y2)，实测收藏星形在客户区 (1585,612)-(1615,642)
    probe_roi: Tuple[float, float, float, float] = (0.9600, 0.6591, 0.9782, 0.6915)
    # 竖屏视频版面：右侧按钮栏整体左移约 213px（实测 x≈1385 vs 1598），收藏星形 ROI 跟着左移
    probe_roi_portrait: Tuple[float, float, float, float] = (0.8310, 0.6591, 0.8492, 0.6915)
    probe_min_ratio: float = 0.15    # ROI 里亮度 >= 阈值 的像素占比低于此值 → 判定不是普通视频
    menu_y: Dict[str, float] = field(default_factory=lambda: {
        "0.75x": 0.6817,
        "1x": 0.7195,
        "1.25x": 0.7573,
        "1.5x": 0.7961,
        "1.75x": 0.8339,
        "2x": 0.8727,
        "3x": 0.9115,
    })


@dataclass
class HoldConfig:
    """肩键的「短按 / 长按」双动作（RB 快进+倍速，LB 后退+慢放）。"""
    long_ms: int = 400            # 按住超过这么久算长按；短按动作在松开时才发
    short_action: str = "seek_forward"   # seek_forward | seek_backward | none
    hold_speed: str = "2x"        # 长按期间切换到的播放速度（抖音菜单里的档位名）
    restore_speed: str = "1x"     # 松开后恢复的速度


@dataclass
class SwipeConfig:
    """切视频的实现方式：滚轮 或 方向键。"""
    method: str = "wheel"       # "wheel" | "key"
    notches: int = 1            # 每次滚多少格；实测抖音按"事件个数"切视频，zDelta 大小无关，故恒为 1
    key_up: str = "up"
    key_down: str = "down"


@dataclass
class ActionConfig:
    """一个动作的实现方式：点窗口内某点 / 发按键 / 双击视频区。"""
    method: str = "click"       # "click" | "key" | "double_click"
    key: str = ""               # method=key 时，例如 "esc"、"alt+left"
    x: float = 0.5              # method=click 时，相对抖音窗口客户区的归一化坐标 0..1
    y: float = 0.5


@dataclass
class Config:
    poll_hz: int = 125
    toggle_key: str = "F8"
    start_enabled: bool = True
    deadzone: float = 0.15
    target_window: str = ""            # 目标窗口标题关键字；默认空=只按进程匹配（抖音主窗口标题是中文「抖音」）
    target_process: str = "douyin.exe"
    log_level: str = "info"            # debug | info | quiet
    restore_cursor: bool = True        # 点 UI 图标后把鼠标挪回原处，避免打扰
    ensure_foreground: bool = True     # 发键盘/滚轮前把抖音拉到前台，否则按键会打到别的窗口
    stick: StickConfig = field(default_factory=StickConfig)
    mouse: MouseConfig = field(default_factory=MouseConfig)
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    tap: TapConfig = field(default_factory=TapConfig)
    swipe: SwipeConfig = field(default_factory=SwipeConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)
    # 用户决定：RB 短按留空（客户端没有前进 seek），RB 长按 2x；LB 短按后退 5 秒，LB 长按 0.75x（菜单最低档）
    rb: HoldConfig = field(default_factory=lambda: HoldConfig(short_action="none", hold_speed="2x"))
    lb: HoldConfig = field(default_factory=lambda: HoldConfig(short_action="seek_backward", hold_speed="0.75x"))
    # 下面四组默认值来自本机抖音客户端实测（docs/CALIBRATION.md），换分辨率/改版后请用
    # tools/measure.py 重新量，或直接改 %APPDATA%\xbox-douyin-controller\config.json
    like: ActionConfig = field(default_factory=lambda: ActionConfig(method="double_click", x=0.5000, y=0.4500))
    back: ActionConfig = field(default_factory=lambda: ActionConfig(method="key", key="esc|alt+left", x=0.5000, y=0.5000))
    favorite: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.9685, y=0.6753))
    comments: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.9685, y=0.5922))
    # 评论抽屉打开后：按钮栏会整体左移 419px，再点原坐标会点进抽屉内容。
    # 所以关闭走抽屉右上角的 ×（实测能关掉），坐标见 docs/CALIBRATION.md
    comments_close: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.9830, y=0.0863))
    # 竖屏视频版面：按钮栏左移约 199px、下移约 69px。坐标来自单帧实测（blob 像素数与横屏图标一一对应），
    # y 偏移可能随宽高比变化，所以动作层会先采样目标图标位置确认有图标才点击（见 douyin.py）
    comments_portrait: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.8476, y=0.6667))
    favorite_portrait: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.8479, y=0.7494))

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        kwargs: Dict[str, Any] = {}
        known = {f.name for f in fields(cls)}
        for key, value in (data or {}).items():
            if key not in known:
                continue
            group = _GROUPS.get(key)
            if group is not None and isinstance(value, dict):
                allowed = {f.name for f in fields(group)}
                kwargs[key] = group(**{k: v for k, v in value.items() if k in allowed})
            else:
                kwargs[key] = value
        return cls(**kwargs)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        path = path or default_config_path()
        if path and os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                return cls.from_dict(json.load(fh))
        return cls()

    def save(self, path: Optional[str] = None) -> str:
        path = path or default_config_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)
        return path


# 嵌套 group 用显式映射：本文件有 from __future__ import annotations，
# Field.type 是字符串，is_dataclass(Field.type) 恒为 False，不能拿它做分派。
_GROUPS = {
    "stick": StickConfig,
    "mouse": MouseConfig,
    "trigger": TriggerConfig,
    "tap": TapConfig,
    "swipe": SwipeConfig,
    "speed": SpeedConfig,
    "rb": HoldConfig,
    "lb": HoldConfig,
    "like": ActionConfig,
    "back": ActionConfig,
    "favorite": ActionConfig,
    "comments": ActionConfig,
    "comments_close": ActionConfig,
    "comments_portrait": ActionConfig,
    "favorite_portrait": ActionConfig,
}


def default_config_path() -> str:
    """%APPDATA%\\xbox-douyin-controller\\config.json（工程目录下的 config.json 优先）。"""
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    if os.path.isfile(local):
        return local
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "xbox-douyin-controller", "config.json")

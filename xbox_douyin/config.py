"""配置对象（接口契约文件，六个 group 的字段名不可随意改动）。

命令行 `run.py --init-config` 会把默认配置写到 user config.json，
用户改 JSON 即可覆盖默认值，无需改代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, asdict
from typing import Any, Dict, Optional


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
    # 下面四组默认值来自本机抖音客户端实测（docs/CALIBRATION.md），换分辨率/改版后请用
    # tools/measure.py 重新量，或直接改 %APPDATA%\xbox-douyin-controller\config.json
    like: ActionConfig = field(default_factory=lambda: ActionConfig(method="double_click", x=0.5000, y=0.4500))
    back: ActionConfig = field(default_factory=lambda: ActionConfig(method="key", key="esc|alt+left", x=0.5000, y=0.5000))
    favorite: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.9685, y=0.6753))
    comments: ActionConfig = field(default_factory=lambda: ActionConfig(method="click", x=0.9685, y=0.5922))

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
    "like": ActionConfig,
    "back": ActionConfig,
    "favorite": ActionConfig,
    "comments": ActionConfig,
}


def default_config_path() -> str:
    """%APPDATA%\\xbox-douyin-controller\\config.json（工程目录下的 config.json 优先）。"""
    local = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    if os.path.isfile(local):
        return local
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(appdata, "xbox-douyin-controller", "config.json")

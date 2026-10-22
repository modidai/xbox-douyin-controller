r"""命令行入口。

  python run.py                 正常运行（真注入）
  python run.py --dry-run       不注入只打印
  python run.py --selftest      离线自检，打印动作序列
  python run.py --init-config   写默认配置到 %APPDATA%\xbox-douyin-controller\config.json
  python run.py --action NAME   一次性执行某个动作（现场调试）
"""
from __future__ import annotations

import argparse
import inspect
import os
import sys

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbox_douyin.app import App, NullActions, normalize_config
from xbox_douyin.config import Config, default_config_path
from xbox_douyin.injector import Injector

#: --action 可用的名字 -> DouyinActions 的方法名
ACTION_METHOD = {
    "like": "like",
    "back": "back",
    "favorite": "favorite",
    "comments": "open_comments",
    "swipe_up": "swipe_up",
    "swipe_down": "swipe_down",
    "seek_forward": "seek_forward",
    "seek_backward": "seek_backward",
}

ACTION_NAMES = tuple(ACTION_METHOD.keys())


def load_config(path=None):
    """读配置并修正嵌套 group（见 app.normalize_config）。"""
    return normalize_config(Config.load(path))


#: 构造函数里代表注入器 / 配置的参数名（真实签名是 __init__(self, injector, cfg)）
INJECTOR_PARAM_NAMES = ("injector", "inj", "input", "sender")
CONFIG_PARAM_NAMES = ("cfg", "config", "conf")


def build_actions(cfg, injector):
    """装配 DouyinActions。

    分派规则：先认参数名（injector / cfg），名字认不出来才按契约顺序
    (injector, cfg) 兜底。绝不按位置硬塞，否则 (injector, cfg) 和
    (cfg, injector) 两种签名必然有一种会把两个参数传反。
    """
    from xbox_douyin.douyin import DouyinActions

    try:
        params = list(inspect.signature(DouyinActions).parameters.values())
    except (TypeError, ValueError):
        params = None

    args = []
    kwargs = {}
    if params is None:
        args = [injector, cfg]  # 拿不到签名就按契约顺序来
    else:
        unknown_seen = 0
        for param in params:
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            name = (param.name or "").lower()
            if name in INJECTOR_PARAM_NAMES:
                value = injector
            elif name in CONFIG_PARAM_NAMES:
                value = cfg
            elif param.default is not inspect.Parameter.empty:
                continue  # 名字不认识但自带默认值：尊重默认值，不乱塞
            else:
                value = injector if unknown_seen == 0 else cfg
                unknown_seen += 1
            if param.kind == param.KEYWORD_ONLY:
                kwargs[param.name] = value
            else:
                args.append(value)

    try:
        return DouyinActions(*args, **kwargs)
    except TypeError as exc:
        raise RuntimeError(
            "装配 DouyinActions 失败：构造签名与传参对不上（args=%r, kwargs=%r）—— %s"
            % (args, kwargs, exc)
        )


def cmd_init_config(path=None):
    target = path or default_config_path()
    saved = Config().save(target)
    print("[配置] 已写入默认配置: %s" % saved)
    print("[配置] 改这个 JSON 就能调整阈值/按键/坐标，不必改代码")
    return 0


def cmd_selftest(cfg):
    lines = []
    app = App(cfg, NullActions(), injector=None, out=lines.append)
    sequence = app.run_selftest()
    for line in lines:
        print(line)
    print("[自检] 完成：共 %d 个动作事件" % len(sequence))
    return 0


def cmd_action(cfg, action_name, dry_run):
    injector = Injector(cfg, out=print, dry_run=dry_run)
    actions = build_actions(cfg, injector)
    method_name = ACTION_METHOD[action_name]
    method = getattr(actions, method_name, None)
    if not callable(method):
        print(
            "[错误] %s 装配出来的对象没有 %s 方法（类型 %s）"
            % (action_name, method_name, type(actions).__name__)
        )
        return 2
    try:
        result = method()
    except Exception as exc:
        print("[错误] 执行 %s（%s）时抛异常: %s" % (action_name, method_name, exc))
        return 1
    if result is False:
        print("[错误] 执行 %s（%s）失败：动作层返回 False，原因见上面的日志" % (action_name, method_name))
        return 1
    print("[动作] 已执行 %s（%s）%s" % (action_name, method_name, "（dry-run，未真注入）" if dry_run else ""))
    return 0


def cmd_run(cfg, dry_run):
    injector = Injector(cfg, out=print, dry_run=dry_run)
    actions = build_actions(cfg, injector)
    app = App(cfg, actions, injector=injector, out=print)
    if dry_run:
        print("[启动] dry-run：不会真的注入任何输入")
    with app:
        return app.run()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Xbox 手柄操作抖音（左摇杆切视频，A 双击点赞，B 返回，Y 双击收藏，X 评论）",
    )
    parser.add_argument("--config", metavar="PATH", default=None, help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="不注入，只打印将要执行的动作")
    parser.add_argument("--selftest", action="store_true", help="离线自检：脚本化假手柄走完整流水线")
    parser.add_argument("--init-config", action="store_true", help="写默认配置到用户目录")
    parser.add_argument(
        "--action", metavar="NAME", choices=list(ACTION_NAMES), default=None, help="一次性执行某个动作"
    )
    parser.add_argument("--verbose", action="store_true", help="输出 debug 日志")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.init_config:
        return cmd_init_config(args.config)

    cfg = load_config(args.config)
    if args.verbose:
        cfg.log_level = "debug"

    try:
        if args.selftest:
            return cmd_selftest(cfg)
        if args.action:
            return cmd_action(cfg, args.action, args.dry_run)
        return cmd_run(cfg, args.dry_run)
    except KeyboardInterrupt:
        print("[退出] 收到 Ctrl+C，已停止")
        return 0
    except ModuleNotFoundError as exc:
        if "douyin" in str(exc) or "douyin" in (getattr(exc, "name", "") or ""):
            print("[错误] xbox_douyin/douyin.py 模块未交付，真实动作无法执行（当前只有引擎可用）")
            print("[提示] python run.py --selftest 可离线验证引擎，不需要该模块")
            print("[提示] 或等 DouyinActions 装配完成后再试；--dry-run 同样需要它提供动作实现")
            return 2
        raise
    except ImportError as exc:
        print("[错误] 装配 DouyinActions 失败: %s" % exc)
        print("[提示] 多半是 douyin.py 内部 import 出错，先用 python run.py --selftest 确认引擎本身没问题")
        return 2
    except RuntimeError as exc:
        print("[错误] %s" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())

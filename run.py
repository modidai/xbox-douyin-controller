r"""命令行入口。

  python run.py                 正常运行（真注入）
  python run.py --dry-run       不注入只打印
  python run.py --selftest      离线自检，打印动作序列
  python run.py --init-config   写默认配置到 %APPDATA%\xbox-douyin-controller\config.json
  python run.py --action NAME   一次性执行某个动作（现场调试）
  python run.py --log-file PATH 同时把日志写进文件（开机自启用，没有可见控制台时排查）

自带单实例守卫：用 Windows 具名互斥体，防止开机自启 + 手动双击同时跑两份、把输入注入两遍。
"""
from __future__ import annotations

import argparse
import ctypes
import inspect
import logging
import os
import sys

if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from xbox_douyin.app import App, NullActions, normalize_config
from xbox_douyin.config import Config, default_config_path
from xbox_douyin.injector import Injector

#: 输出出口：默认 print；带 --log-file 时换成同时写文件的 tee
_output = print

#: 单实例互斥体名字
SINGLETON_NAME = "xbox-douyin-controller-singleton"
ERROR_ALREADY_EXISTS = 183

_kernel32 = None
_SINGLETON_HANDLE = None
_SINGLETON_BLOCKED_BY = None


class _TeeLog(object):
    """每行日志同时输出到 stdout 和文件。文件写不进去只告警，不中断运行。"""

    def __init__(self, path, echo=print):
        self.path = path
        self._echo = echo
        self._handle = None
        self._failed = False
        try:
            self._handle = open(path, "a", encoding="utf-8")
        except Exception as exc:
            self._failed = True
            try:
                echo("[提示] 无法写入日志文件 %s：%s（继续只打印到控制台）" % (path, exc))
            except Exception:
                pass

    def __call__(self, message):
        try:
            self._echo(message)
        except Exception:
            pass
        if self._handle is None:
            return
        try:
            self._handle.write("%s\n" % (message,))
            self._handle.flush()
        except Exception as exc:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None
            if not self._failed:
                self._failed = True
                try:
                    self._echo("[提示] 写日志文件失败：%s（继续只打印到控制台）" % exc)
                except Exception:
                    pass

    def close(self):
        if self._handle is None:
            return
        try:
            self._handle.close()
        except Exception:
            pass
        self._handle = None


def _install_logging_file(path, level):
    """让 douyin.py 那些 log.warning 也落进同一个文件。"""
    try:
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    except Exception as exc:
        _output("[提示] 无法为 logging 打开日志文件 %s：%s" % (path, exc))
        return None
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    return handler


def _get_kernel32():
    global _kernel32
    if _kernel32 is None:
        if not sys.platform.startswith("win"):
            return None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError:
            return None
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetLastError.restype = ctypes.c_ulong
        kernel32.GetLastError.argtypes = []
        _kernel32 = kernel32
    return _kernel32


def _last_error(kernel32):
    """读 CreateMutexW 之后的 last error（优先问 DLL，退回 ctypes 记录的）。"""
    getter = getattr(kernel32, "GetLastError", None)
    if callable(getter):
        try:
            value = int(getter())
            if value:
                return value
        except Exception:
            pass
    try:
        return int(ctypes.get_last_error())
    except Exception:
        return 0


def _close_mutex(kernel32, handle):
    try:
        kernel32.CloseHandle(handle)
    except Exception:
        pass


def acquire_singleton(name=None):
    """抢单实例互斥体：True = 归我们，False = 已有实例在跑。

    Global\\ 前缀失败时退化到不带前缀的名字再试一次；
    非 Windows 或整套机制不可用时返回 True（宁可跑起来，也别因为守卫坏了启动不了）。
    """
    global _SINGLETON_HANDLE, _SINGLETON_BLOCKED_BY
    if _SINGLETON_HANDLE is not None:
        return True
    kernel32 = _get_kernel32()
    if kernel32 is None:
        return True
    base = name or SINGLETON_NAME
    for candidate in ("Global\\" + base, base):
        try:
            handle = kernel32.CreateMutexW(None, 0, candidate)
        except Exception:
            continue
        if not handle:
            continue  # 这个名字建不出来（例如无权创建 Global 对象）→ 换下一个
        if _last_error(kernel32) == ERROR_ALREADY_EXISTS:
            _SINGLETON_BLOCKED_BY = candidate
            _close_mutex(kernel32, handle)
            return False
        _SINGLETON_HANDLE = handle
        return True
    return True


def release_singleton():
    """释放互斥体（进程退出时系统也会放开，手动关更干净）。"""
    global _SINGLETON_HANDLE
    if _SINGLETON_HANDLE is None:
        return
    kernel32 = _get_kernel32()
    if kernel32 is not None:
        _close_mutex(kernel32, _SINGLETON_HANDLE)
    _SINGLETON_HANDLE = None


def _require_singleton():
    """抢不到就打印一行提示并返回 False（调用方以退出码 0 退出）。"""
    if acquire_singleton():
        return True
    blocked_by = _SINGLETON_BLOCKED_BY or ("Global\\" + SINGLETON_NAME)
    _output("[提示] 已经有一个实例在运行了（互斥体 %s），本进程直接退出，避免重复注入。" % blocked_by)
    return False


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
    _output("[配置] 已写入默认配置: %s" % saved)
    _output("[配置] 改这个 JSON 就能调整阈值/按键/坐标，不必改代码")
    return 0


def cmd_selftest(cfg):
    app = App(cfg, NullActions(), injector=None, out=_output)
    sequence = app.run_selftest()
    _output("[自检] 完成：共 %d 个动作事件" % len(sequence))
    return 0


def cmd_action(cfg, action_name, dry_run):
    injector = Injector(cfg, out=_output, dry_run=dry_run)
    actions = build_actions(cfg, injector)
    method_name = ACTION_METHOD[action_name]
    method = getattr(actions, method_name, None)
    if not callable(method):
        _output(
            "[错误] %s 装配出来的对象没有 %s 方法（类型 %s）"
            % (action_name, method_name, type(actions).__name__)
        )
        return 2
    try:
        result = method()
    except Exception as exc:
        _output("[错误] 执行 %s（%s）时抛异常: %s" % (action_name, method_name, exc))
        return 1
    if result is False:
        _output("[错误] 执行 %s（%s）失败：动作层返回 False，原因见上面的日志" % (action_name, method_name))
        return 1
    _output("[动作] 已执行 %s（%s）%s" % (action_name, method_name, "（dry-run，未真注入）" if dry_run else ""))
    return 0


def cmd_run(cfg, dry_run):
    injector = Injector(cfg, out=_output, dry_run=dry_run)
    actions = build_actions(cfg, injector)
    app = App(cfg, actions, injector=injector, out=_output)
    if dry_run:
        _output("[启动] dry-run：不会真的注入任何输入")
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
    parser.add_argument(
        "--log-file",
        metavar="PATH",
        default=None,
        help="把日志同时追加写入该文件（开机自启、没有可见控制台时用）",
    )
    return parser


def main(argv=None):
    global _output
    args = build_parser().parse_args(argv)

    previous_output = _output
    tee = None
    logging_handler = None
    if args.log_file:
        tee = _TeeLog(args.log_file)
        _output = tee
        logging_handler = _install_logging_file(
            args.log_file, logging.DEBUG if args.verbose else logging.INFO
        )

    try:
        if args.init_config:
            return cmd_init_config(args.config)

        cfg = load_config(args.config)
        if args.verbose:
            cfg.log_level = "debug"

        if args.selftest:
            return cmd_selftest(cfg)
        if args.action:
            # 一次性动作不占单实例名额：它不会持续响应手柄，也就不会和主循环抢输入
            return cmd_action(cfg, args.action, args.dry_run)
        # 会持续轮询手柄的路径才需要单实例守卫
        if not _require_singleton():
            return 0
        return cmd_run(cfg, args.dry_run)
    except KeyboardInterrupt:
        _output("[退出] 收到 Ctrl+C，已停止")
        return 0
    except ModuleNotFoundError as exc:
        if "douyin" in str(exc) or "douyin" in (getattr(exc, "name", "") or ""):
            _output("[错误] xbox_douyin/douyin.py 模块未交付，真实动作无法执行（当前只有引擎可用）")
            _output("[提示] python run.py --selftest 可离线验证引擎，不需要该模块")
            _output("[提示] 或等 DouyinActions 装配完成后再试；--dry-run 同样需要它提供动作实现")
            return 2
        raise
    except ImportError as exc:
        _output("[错误] 装配 DouyinActions 失败: %s" % exc)
        _output("[提示] 多半是 douyin.py 内部 import 出错，先用 python run.py --selftest 确认引擎本身没问题")
        return 2
    except RuntimeError as exc:
        _output("[错误] %s" % exc)
        return 2
    finally:
        if logging_handler is not None:
            try:
                logging.getLogger().removeHandler(logging_handler)
                logging_handler.close()
            except Exception:
                pass
        _output = previous_output
        if tee is not None:
            tee.close()
        release_singleton()


if __name__ == "__main__":
    sys.exit(main())

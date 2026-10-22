# xbox-douyin-controller · Xbox 手柄刷抖音

用手柄躺着刷抖音：**左摇杆上下切视频、双击 A 点赞、B 返回、双击 Y 收藏、X 打开评论、右摇杆当鼠标、RT 点击。**
纯 Python 标准库实现（`ctypes` 直调 XInput + SendInput），**不需要 pip install 任何东西**，也不需要 DS4Windows / reWASD 之类的中间层。

## 按键映射

| 手柄 | 抖音动作 | 实现方式 |
| --- | --- | --- |
| 左摇杆 上 / 下 | 上一条 / 下一条视频 | 滚轮 ±1 事件（自动先把光标停进视频区，滚完还原） |
| 双击 A | 点赞 | 双击视频区 (0.50, 0.45) —— 抖音原生"双击点赞"手势 |
| B | 返回 | 依次发 `Esc` → `Alt+←`（关弹出层 / 应用内历史后退） |
| 双击 Y | 收藏 | 点击右侧五角星图标 (0.9685, 0.6753) |
| X | 打开评论 | 点击右侧评论气泡 (0.9685, 0.5922) |
| 右摇杆 | 鼠标移动 | 相对位移，带死区 + 响应曲线 + 限速 |
| RT | 鼠标左键点击 | 在光标当前位置点击 |
| F8 | 手柄映射 开 / 关 | 全局热键（不用手柄玩游戏时关掉） |

坐标是相对**抖音窗口客户区**的归一化值，全部来自真机实测（见 [docs/CALIBRATION.md](docs/CALIBRATION.md)），换分辨率或客户端改版可以用 `tools/measure.py` 重新量。

## 环境要求

- Windows 10 / 11
- Python 3.8+（开发与验证环境是 3.8.6）
- 有线 / 无线 Xbox 手柄（XInput 能识别即可，槽位 1-4 自动探测）
- 抖音桌面客户端（`douyin.exe`）；浏览器网页版也能用（见下文"兼容性"）

## 快速开始

```powershell
git clone https://github.com/modidai/xbox-douyin-controller.git
cd xbox-douyin-controller

python run.py --selftest      # 先离线自检：不需要手柄、不需要抖音，验证程序本身正常
python run.py                 # 正式运行：把抖音窗口放前面，用手柄开始刷
```

启动后按 **F8** 可以随时把手柄映射关掉/打开（玩游戏前建议关掉）。Ctrl+C 退出。

### 命令行

```
python run.py                    # 正式运行（真实注入键鼠事件）
python run.py --dry-run          # 不注入，只打印将要执行的动作（调参/排障用）
python run.py --selftest         # 离线自检：脚本化假手柄走完整条流水线，打印动作序列
python run.py --init-config      # 把默认配置写到 %APPDATA%\xbox-douyin-controller\config.json
python run.py --action comments  # 只执行一次某个动作（like/back/favorite/comments/swipe_up/swipe_down）
python run.py --config PATH      # 用指定配置文件
python run.py --verbose          # 输出 debug 日志
```

## 配置与手感调参

`python run.py --init-config` 生成 `%APPDATA%\xbox-douyin-controller\config.json`（工程根目录下的 `config.json` 优先），
字段与默认值见 [config.example.json](config.example.json)。常调的几项：

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `stick.threshold` | 0.6 | 左摇杆推过多少算"切视频" |
| `stick.repeat_ms` | 450 | 一直推住时的重复间隔，调小切得更快 |
| `mouse.max_speed_px_s` | 900 | 右摇杆满推时的光标速度 |
| `mouse.curve` | 2.0 | 响应曲线，1=线性，2=慢速更细腻 |
| `mouse.invert_y` | true | XInput 上推是 +Y、屏幕向下是 +Y，所以默认要反转 |
| `tap.window_ms` | 350 | 双击判定窗口（A / Y） |
| `trigger.threshold` | 0.5 | RT 按多深算一次点击 |
| `ensure_foreground` | true | 发键盘/滚轮前先把抖音拉到前台，否则"抖音不在前台就不发"（安全侧失败） |
| `restore_cursor` | true | 点完右侧图标把光标放回原处 |

## 实测出来的三条结论（决定了默认值）

1. **切视频按"事件个数"算，不按滚动量**：`wheel(-1)` / `wheel(-3)` / `ArrowDown` 都只切 1 条，±120 与 ±360 等价。
   所以 `swipe.notches` 恒为 1 —— 填 3 会一次跳 3 条。
2. **滚轮只在光标位于视频区时有效**：光标停在左侧导航栏时滚轮被导航吞掉，视频纹丝不动。
   动作层会先把光标移到视频中部 (0.5, 0.45) 再滚，滚完还原。
3. **Esc / Alt+← 都不是可靠的"返回上一级"**（在 feed、抽屉、一级页、详情页实测都无反应），
   唯一确证有效的是 Esc 关弹出层。所以 B 采用"Esc → Alt+←"串发：两者在首页都是空操作，
   不会把用户带到别处，有弹出层/有历史时至少命中一个。要硬返回可以把 `back` 改成点击
   （关抽屉 × = 0.9830/0.0863、回首页 = 0.0515/0.1435，详见校准文档）。

## 重新校准

客户端改版、换分辨率、换缩放比例后，用自带工具重新量，不用改代码：

```powershell
python tools/measure.py --report                    # 打印客户区矩形 + 每个动作归一化→绝对像素 + 来源
python tools/measure.py --shot --shot-path a.png    # 截抖音窗口（被遮挡时加 --printwindow）
python tools/measure.py --crosshair --shot          # 在截图上画出当前坐标的准星，用来核对图标位置
```

量到新值后写进 `config.json` 即可。手动复校流程与全部实测记录见 [docs/CALIBRATION.md](docs/CALIBRATION.md)。

## 目录结构

```
run.py                 命令行入口（装配 Injector + DouyinActions + App）
xbox_douyin/
  xinput.py            XInput 轮询（ctypes，自动探测槽位 1-4）
  injector.py          SendInput 注入（鼠标/滚轮/按键/绝对点击，含前台守卫与光标还原）
  gestures.py          纯逻辑：迟滞触发 / 双击判定 / 摇杆→鼠标曲线（无 Windows 依赖，可单测）
  app.py               主循环、F8 启停、日志、离线自检
  douyin.py            抖音动作层：切视频 / 点赞 / 收藏 / 评论 / 返回
  config.py            配置对象（dataclass + JSON 读写）
tests/                 test_gestures（38 项）· test_app_selftest（103 项）· test_douyin（10 项）
tools/smoke_test.py    真注入端到端验证：tkinter 靶子窗口，抢不到前台就 SKIP 不注入
tools/measure.py       窗口校准助手（截图 / 准星 / 坐标换算）
docs/CALIBRATION.md    五项真机实验的完整记录与风险登记
```

## 测试

```powershell
python tests/test_gestures.py        # 手势纯逻辑
python tests/test_app_selftest.py    # 主循环 + 注入器（全部打桩，不真注入）
python tests/test_douyin.py          # 动作层（假 Injector）
python run.py --selftest             # 端到端离线自检
python tools/smoke_test.py           # 真注入验证：只打自己的 tkinter 窗口，抢不到前台就 SKIP
```

## 兼容性 / 已知限制

- **浏览器网页版**同样可用：滚轮切视频、双击点赞、Esc 返回都是网页版原生支持的手势；
  收藏/评论坐标是按桌面客户端 1651×928 客户区量的，网页版需要自己用 `tools/measure.py` 重新量。
- **直播房间**右侧 UI 与普通视频不同（没有那条竖排按钮栏），此时 0.9685 一列会点到别的控件 —— 已知限制。
- **评论抽屉打开时按钮栏整体左移 419px**（0.9685 → 0.7145），此时再按原坐标会点进抽屉内容。先按 B 关掉抽屉。
- 键盘类动作（Esc / 方向键）要求抖音在前台；本程序会先抢前台，抢不到就**一个按键都不发**并告警。
- 未做 DPI 感知：绝对是按物理像素算的，本机（per-monitor-v2，100%）落点精确，125%/150% 缩放的机器请用 `measure.py` 复校。
- 只支持 XInput（Xbox 系）手柄；DualShock 需自行用 DS4Windows 之类映射成 XInput。

## 免责声明

本项目通过 Windows 的 SendInput 模拟本地键鼠事件，等价于"用户自己动手点"。请遵守抖音的用户协议，
不要用于刷量、自动化养号等违规用途。账号风险自负。

## License

[MIT](LICENSE)

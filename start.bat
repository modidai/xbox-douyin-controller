@echo off
rem 双击本文件即可启动手柄刷抖音（关掉这个窗口 = 停止）。F8 切换映射开关，Ctrl+C 退出。
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -NoExit -Command "python -u run.py *>&1 | Tee-Object -FilePath run.log"

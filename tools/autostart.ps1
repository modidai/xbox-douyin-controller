<#
.SYNOPSIS
    Xbox 手柄刷抖音 —— 开机自启管理（安装 / 卸载 / 查看状态）

.DESCRIPTION
    在"当前用户登录时"启动 xbox-douyin-controller：
    在 %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup 放一个快捷方式，
    指向 tools\run-hidden.vbs（隐藏窗口启动 python，日志写到项目根目录 run.log）。
    不写注册表、不需要管理员权限、卸载就是删掉那个快捷方式。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Install
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Status
    powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Uninstall
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vbs = Join-Path $projectRoot 'tools\run-hidden.vbs'
$startupDir = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startupDir 'Xbox手柄刷抖音.lnk'

function Show-Status {
    if (Test-Path $linkPath) {
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($linkPath)
        Write-Host '[已安装] 开机自启生效中' -ForegroundColor Green
        Write-Host ('  快捷方式: ' + $linkPath)
        Write-Host ('  目标    : ' + $lnk.TargetPath + ' ' + $lnk.Arguments)
        Write-Host ('  日志    : ' + (Join-Path $projectRoot 'run.log'))
        $n = (Get-Process python -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Host ('  当前 python 进程数: ' + $n)
    } else {
        Write-Host '[未安装] 开机不会自动启动' -ForegroundColor Yellow
        Write-Host ('  安装命令: powershell -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Install')
    }
}

if ($Uninstall) {
    if (Test-Path $linkPath) {
        Remove-Item $linkPath -Force
        Write-Host '[已卸载] 已删除开机自启快捷方式，下次登录不会再自动启动' -ForegroundColor Green
    } else {
        Write-Host '[无需操作] 本来就没有安装' -ForegroundColor Yellow
    }
    return
}

if ($Install) {
    if (-not (Test-Path $vbs)) { throw ('找不到启动脚本: ' + $vbs) }
    New-Item -ItemType Directory -Force -Path $startupDir | Out-Null
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($linkPath)
    $lnk.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $lnk.Arguments = '"' + $vbs + '"'
    $lnk.WorkingDirectory = $projectRoot
    $lnk.WindowStyle = 7
    $lnk.Description = 'Xbox 手柄刷抖音（F8 切换映射开关）'
    $lnk.Save()
    Write-Host '[已安装] 下次登录自动启动（隐藏窗口，日志在 run.log）' -ForegroundColor Green
    Show-Status
    return
}

Show-Status
if (-not ($Install -or $Uninstall -or $Status)) {
    Write-Host ''
    Write-Host '用法: -Install 安装自启 | -Status 查看 | -Uninstall 卸载'
}

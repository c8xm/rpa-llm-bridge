<#
    AgentRPA Windows 安装脚本
    用法：powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/5] 检查 Python..." -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "未找到 python，请先安装 Python 3.11+" }
python -c "import sys; assert sys.version_info >= (3, 11), 'need 3.11+'" 

Write-Host "[2/5] 创建虚拟环境 .venv ..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }

$venvPy = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "[3/5] 安装项目与依赖 ..." -ForegroundColor Cyan
& $venvPy -m pip install -U pip
& $venvPy -m pip install -e ".[dev]"

Write-Host "[4/5] 安装 Chromium ..." -ForegroundColor Cyan
& $venvPy -m playwright install chromium

Write-Host "[5/5] 准备 .env ..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }

Write-Host ""
Write-Host "安装完成。启动：" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe server.py"
Write-Host "自检："
Write-Host "  .\.venv\Scripts\python.exe -m pytest"

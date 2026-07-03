# 一步安装并启动 App 真机执行机 worker（Windows）
# 用法：右键“用 PowerShell 运行”，或： powershell -ExecutionPolicy Bypass -File deploy\worker\install-worker.ps1
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repo

Write-Host "== App 真机执行机 worker 一键安装 ==" -ForegroundColor Cyan

# 1) 依赖
Write-Host "[1/3] 安装 Python 依赖..."
python -m pip install -q -r deploy\worker\requirements-worker.txt

# 2) 首次配置令牌（AI 配置自动复用 backend\.env，无需手填）
$envf = Join-Path $repo "backend\.env"
if (-not (Test-Path $envf)) {
  Write-Host "[警告] 未找到 backend\.env —— AI 配置需要它。请确认本机已配置平台 .env(含 AI_*)。" -ForegroundColor Yellow
} elseif (-not (Select-String -Path $envf -Pattern "^WORKER_TOKEN=" -Quiet)) {
  Write-Host "[2/3] 首次配置：需要平台的 WORKER_TOKEN（找管理员要）"
  $tok = Read-Host "粘贴 WORKER_TOKEN"
  if ($tok) { Add-Content -Path $envf -Value "WORKER_TOKEN=$tok"; Write-Host "已写入 backend\.env" }
} else {
  Write-Host "[2/3] WORKER_TOKEN 已在 backend\.env，跳过"
}

# 3) 启动（AI 配置自动从 backend\.env 读取；WORKER_ID 默认机器名；平台默认生产）
Write-Host "[3/3] 启动 worker..." -ForegroundColor Green
python deploy\worker\worker.py

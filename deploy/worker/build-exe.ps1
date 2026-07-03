# 把 App 真机执行机 worker 打包成单个 tp-worker.exe（内含 Python + 依赖 + adb）
# 用法（在有 Python 的 Windows 上）：powershell -ExecutionPolicy Bypass -File deploy\worker\build-exe.ps1
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repo

Write-Host "== 打包 tp-worker.exe ==" -ForegroundColor Cyan

Write-Host "[1/4] 安装 worker 依赖 + PyInstaller..."
python -m pip install -q -r deploy\worker\requirements-worker.txt
python -m pip install -q pyinstaller

Write-Host "[2/4] 准备内置 adb(Google platform-tools)..."
$ptDir = Join-Path $PSScriptRoot "platform-tools"
if (-not (Test-Path (Join-Path $ptDir "adb.exe"))) {
  $zip = Join-Path $env:TEMP "platform-tools.zip"
  Invoke-WebRequest "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zip
  Expand-Archive $zip -DestinationPath $PSScriptRoot -Force
  Remove-Item $zip
}

Write-Host "[3/4] PyInstaller 打包(几分钟)..."
# 不用 UPX：对 onefile(已 zlib 压缩)只多省 ~13MB，却让启动多一段内存解压、且偶有加载/杀软问题，不划算。
# 不整包打 app(只跟随真实 import) + app/services 与 runners 的包 __init__ 已改懒加载，
# 故 worker 不再拉入 FastAPI/SQLAlchemy/playwright 等，可安全 exclude 进一步瘦身。
python -m PyInstaller --noconfirm --onefile --name tp-worker `
  --paths backend `
  --add-data "deploy\worker\platform-tools;platform-tools" `
  --collect-all uiautomator2 `
  --collect-all adbutils `
  --hidden-import uiautomator2 `
  --hidden-import PIL `
  --hidden-import pydantic_settings `
  --exclude-module fastapi --exclude-module starlette --exclude-module uvicorn `
  --exclude-module sqlalchemy --exclude-module asyncpg --exclude-module alembic `
  --exclude-module anthropic --exclude-module openai --exclude-module greenlet `
  --exclude-module rq --exclude-module redis --exclude-module playwright `
  deploy\worker\worker.py

Write-Host "[4/4] 完成 → dist\tp-worker.exe" -ForegroundColor Green
Get-Item dist\tp-worker.exe | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}

@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo === 一键更新部署到服务器 ===
python deploy\update.py
pause

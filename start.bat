@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
  echo.
  echo 启动失败，把上面的报错发给开发者。
  pause
)


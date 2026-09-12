@echo off
cd /d "%~dp0"

echo [1/3] 生成图标
python assets\make_icon.py

echo [2/3] 打包 exe
python -m PyInstaller --noconfirm --clean --windowed ^
  --name "AI小助理" ^
  --icon assets\icon.ico ^
  --add-data "ui;ui" ^
  --hidden-import webview.platforms.edgechromium ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import clr ^
  --hidden-import pythonnet ^
  main.py
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)

echo [3/3] 完成，产物在 dist\AI小助理\
pause

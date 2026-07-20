@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python не найден. Установи его с https://www.python.org/downloads/
  echo и при установке отметь галочку "Add Python to PATH".
  pause
  exit /b 1
)

if not exist ".deps_installed" (
  echo Первый запуск: устанавливаю зависимости, это займёт несколько минут...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Ошибка установки. Проверь интернет и попробуй ещё раз.
    pause
    exit /b 1
  )
  echo ok> .deps_installed
)

python run.py
pause

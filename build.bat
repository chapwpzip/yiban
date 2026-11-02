@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE=python"
set "PYTHON_ARGS="
%PYTHON_EXE% -c "import sys" >nul 2>nul
if errorlevel 1 (
    set "PYTHON_EXE=py"
    set "PYTHON_ARGS=-3"
)

echo [1/4] 检查 PyInstaller...
%PYTHON_EXE% %PYTHON_ARGS% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo 未安装 PyInstaller。请先运行：
    echo   %PYTHON_EXE% %PYTHON_ARGS% -m pip install -r requirements.txt -r requirements-build.txt
    exit /b 1
)

echo [2/4] 清理旧的构建输出...
if exist "build" rmdir /s /q "build" 2>nul
if exist "dist" rmdir /s /q "dist" 2>nul
if exist "YibanAutoPoster.spec" del /q "YibanAutoPoster.spec" 2>nul

echo [3/4] 使用 PyInstaller 打包...
%PYTHON_EXE% %PYTHON_ARGS% -m PyInstaller ^
    --noconfirm --clean --onefile --windowed ^
    --name YibanAutoPoster ^
    --icon "assets\yiban.ico" ^
    --add-data "assets;assets" ^
    --add-data "drivers\msedgedriver.exe;drivers" ^
    --collect-submodules selenium ^
    --hidden-import websocket ^
    --hidden-import websocket._core ^
    main.py
if errorlevel 1 (
    echo.
    echo 打包失败，请查看上面的错误信息。
    exit /b 1
)

echo [4/4] 复制配置与说明...
if not exist "dist" mkdir "dist"
copy /Y "settings.json" "dist\settings.json" >nul
copy /Y "README.txt" "dist\README.txt" >nul

echo.
echo 打包完成：
echo   %CD%\dist\YibanAutoPoster.exe
echo   %CD%\dist\settings.json
echo   %CD%\dist\README.txt
echo.
echo 可执行自检：
echo   dist\YibanAutoPoster.exe --selftest-basic
echo   dist\YibanAutoPoster.exe --selftest
exit /b 0

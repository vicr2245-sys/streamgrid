@echo off
echo ================================
echo  DroidCtrl — Build EXE
echo ================================
echo.

:: Force remove conflicting versions first
echo Removing conflicting packages...
python -m pip uninstall -y flask werkzeug

:: Install pinned compatible versions
echo Installing pinned dependencies...
python -m pip install --quiet --force-reinstall ^
    "flask==2.3.3" ^
    "werkzeug==2.3.7"

python -m pip install --quiet ^
    "pyinstaller==6.3.0" ^
    "Appium-Python-Client" ^
    "cryptography" ^
    "mitmproxy>=10.0.0" ^
    "pywebview"

:: Verify versions before building
echo.
echo Verifying versions:
python -c "import flask; print('  Flask:    ' + flask.__version__)"
python -c "import werkzeug; print('  Werkzeug: ' + werkzeug.__version__)"
echo.

:: Clean previous build
if exist dist  rmdir /s /q dist
if exist build rmdir /s /q build
if exist __pycache__ rmdir /s /q __pycache__

:: Build
echo Building DroidCtrl.exe...
pyinstaller droidctrl.spec

echo.
if exist dist\DroidCtrl.exe (
    echo  SUCCESS -- dist\DroidCtrl.exe is ready
    echo  Double-click it to launch. Browser opens automatically.
) else (
    echo  BUILD FAILED -- check output above for errors
)
pause

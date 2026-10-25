@echo off
rem Notion to Anki: double-click this file to start the app on Windows.
rem The first start creates a private Python environment in the .venv folder and
rem installs the packages listed in requirements.txt (a few minutes, needs internet once).
setlocal
cd /d "%~dp0"
set "VENV_PYTHON=.venv\Scripts\python.exe"

if not exist "run.py" goto not_unzipped
if exist "%VENV_PYTHON%" goto packages
echo Looking for Python...
call :find_python
if not defined PYTHON goto no_python
echo Setting up for the first start. This takes a few minutes...
%PYTHON% -m venv .venv
if errorlevel 1 goto failed

:packages
rem Install again only when requirements.txt changed since the last successful install.
fc /b requirements.txt .venv\installed-requirements.txt >nul 2>&1
if not errorlevel 1 goto launch
echo Installing the required packages...
"%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
copy /y requirements.txt .venv\installed-requirements.txt >nul

:launch
echo.
echo Starting Notion to Anki. Your browser opens in a moment.
echo Keep this window open while you use the app; close it to stop the app.
echo.
"%VENV_PYTHON%" run.py %*
goto end

:find_python
rem Python's output stays visible: on its first run, the Python install manager
rem may download Python or ask a question, which must not happen invisibly.
set "PYTHON="
where py >nul 2>&1
if errorlevel 1 goto find_python_on_path
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))"
if not errorlevel 1 set "PYTHON=py -3"
if defined PYTHON goto :eof
:find_python_on_path
where python >nul 2>&1
if errorlevel 1 goto :eof
python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if not errorlevel 1 set "PYTHON=python"
goto :eof

:not_unzipped
echo.
echo start.bat has to be run from the unzipped folder.
echo Right-click the downloaded ZIP file, choose "Extract All", then open the
echo extracted folder and double-click start.bat there.
goto end

:no_python
echo.
echo Python 3.11 or newer is needed and was not found on this computer.
echo Install it from https://www.python.org/downloads/ and then double-click start.bat again.
goto end

:failed
echo.
echo The setup did not finish (see the messages above). Check the internet connection and try again.
echo If it keeps failing, delete the .venv folder and double-click start.bat again.

:end
echo.
pause
endlocal

@echo off
echo [*] Operation MurkyPond: Initialization Sequence Started
echo.

echo [*] 1. Creating Virtual Environment (.venv)...
python -m venv .venv

echo [*] 2. Activating Virtual Environment...
call .venv\Scripts\activate

echo [*] 3. Upgrading pip...
python -m pip install --upgrade pip

echo [*] 4. Installing dependencies from requirements.txt...
pip install -r requirements.txt

echo [*] 5. Installing Playwright Chromium engine...
playwright install chromium

echo.
echo [OK] Base secured. All dependencies installed. 
echo [OK] You can now double-click run.bat to start the Harvester.
pause
@echo off
echo [*] Activating MurkyPond Virtual Environment...
call .venv\Scripts\activate

echo [*] Launching Pondweller Harvester...
echo.
python pondweller.py

echo.
echo [*] Harvester sequence terminated.
pause
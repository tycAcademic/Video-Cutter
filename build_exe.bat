@echo off
setlocal

if not exist .venv (
  py -m venv .venv
)

call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

pyinstaller --noconfirm --clean --windowed --onefile --name VideoCutter video_cutter_ui.py

echo.
echo Build complete. EXE path:
echo dist\VideoCutter.exe
pause

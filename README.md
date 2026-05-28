# Video Cutter UI (Python)

## 功能
- 上方影片預覽框支援：
  - 拖拉影片檔
  - 滑鼠點擊開啟選檔對話框
  - Ctrl+V 貼上（檔案或路徑）- Not Work !!
- 選檔後立即背景讀取影片資料（用 thread，不阻塞 UI）
- 開始/結束時間（秒，顯示到小數第 2 位）與 frame textbox 互相同步
- 時間欄位上下箭頭每次跳 1 frame 時間
- [開始frame] 必小於[結束frame]，且均不大於[最後frame] 
- focus在開始欄位時，預覽UI顯示 [開始frame]；焦點在結束欄位時顯示 [結束frame]
- 音軌同步 checkbox（預設不勾）
  - 不勾選：移除音軌
  - 勾選：同步切音訊
- 輸出檔預設為原檔名加 `_cut`
- 開始擷取前若目標檔已存在，會先詢問覆蓋
- 盡可能啟用 GPU（偵測 CUDA + ffmpeg NVENC）

## 安裝與執行
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python video_cutter_ui.py
```

## 打包成 EXE
### 打包方式 1：直接執行批次檔
```powershell
.\build_exe.bat
```

### 打包方式 2：手動執行
```powershell
.\.venv\Scripts\Activate.ps1
pyinstaller --noconfirm --clean --windowed --onefile --name VideoCutter video_cutter_ui.py
```

輸出位置：
- `.\dist\VideoCutter.exe`

## 備註
- 本工具剪輯引擎使用 ffmpeg（由 `imageio-ffmpeg` 提供可執行檔）。
- 若機器未支援 NVENC，會自動回退到 CPU 編碼（libx264）。

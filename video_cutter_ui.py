import os
import re
import subprocess
import sys
from dataclasses import dataclass

import cv2
import imageio_ffmpeg
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".wmv",
    ".flv",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".3gp",
    ".mpeg",
    ".mpg",
}


def is_video_file(path: str) -> bool:
    return os.path.isfile(path) and os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def frame_to_seconds(frame: int, fps: float) -> float:
    if fps <= 0:
        return 0.0
    return frame / fps


def seconds_to_frame(seconds: float, fps: float) -> int:
    if fps <= 0:
        return 0
    return int(round(seconds * fps))


def detect_cuda() -> bool:
    try:
        return cv2.cuda.getCudaEnabledDeviceCount() > 0
    except Exception:
        return False


def has_nvenc(ffmpeg_exe: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="ignore",
        )
        return "h264_nvenc" in result.stdout
    except Exception:
        return False


def parse_clipboard_path(raw: str) -> str:
    text = raw.strip().strip("\"")
    if is_video_file(text):
        return text
    # Support pasting lines like: file:///D:/path/video.mp4
    m = re.search(r"file:///([A-Za-z]:/.+)", text)
    if m:
        path = m.group(1).replace("/", "\\")
        if is_video_file(path):
            return path
    return ""


def normalize_windows_path(path: str) -> str:
    if not path:
        return ""
    return os.path.normpath(path).replace("/", "\\")


def detect_video_codec(path: str, ffmpeg_exe: str) -> str:
    ffprobe_exe = os.path.join(os.path.dirname(ffmpeg_exe), "ffprobe.exe")
    if os.path.isfile(ffprobe_exe):
        try:
            result = subprocess.run(
                [
                    ffprobe_exe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,codec_long_name",
                    "-of",
                    "default=nokey=1:noprint_wrappers=1",
                    path,
                ],
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
                errors="ignore",
            )
            lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
            if lines:
                short_name = lines[0]
                long_name = lines[1] if len(lines) > 1 else ""
                if long_name and long_name.lower() != short_name.lower():
                    return f"{short_name} ({long_name})"
                return short_name
        except Exception:
            pass

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return "unknown"
    fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
    cap.release()
    chars = [chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]
    fourcc = "".join(chars).strip().replace("\x00", "")
    return fourcc if fourcc else "unknown"


@dataclass
class VideoMeta:
    path: str
    fps: float
    frame_count: int
    width: int
    height: int
    duration: float
    codec: str


class ClickDropLabel(QLabel):
    clicked = Signal()
    fileDropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile() and is_video_file(u.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile() and is_video_file(u.toLocalFile()):
                    self.fileDropped.emit(u.toLocalFile())
                    event.acceptProposedAction()
                    return
        event.ignore()


class FocusDoubleSpinBox(QDoubleSpinBox):
    focusGained = Signal()

    def focusInEvent(self, event):
        self.focusGained.emit()
        super().focusInEvent(event)


class FocusSpinBox(QSpinBox):
    focusGained = Signal()

    def focusInEvent(self, event):
        self.focusGained.emit()
        super().focusInEvent(event)


class VideoLoadWorker(QThread):
    loaded = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    def run(self):
        cap = cv2.VideoCapture(self.path)
        if not cap.isOpened():
            self.failed.emit("無法開啟影片檔。")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()

        if fps <= 0 or frame_count <= 0:
            self.failed.emit("影片資料讀取失敗（FPS 或 frame 數無效）。")
            return

        duration = frame_count / fps
        codec = detect_video_codec(self.path, self.ffmpeg_exe)
        meta = VideoMeta(
            path=self.path,
            fps=fps,
            frame_count=frame_count,
            width=width,
            height=height,
            duration=duration,
            codec=codec,
        )
        self.loaded.emit(meta)


class CutWorker(QThread):
    finishedOk = Signal(str)
    failed = Signal(str)
    progressChanged = Signal(int)

    def __init__(
        self,
        ffmpeg_exe: str,
        input_path: str,
        output_path: str,
        start_frame: int,
        end_frame: int,
        fps: float,
        sync_audio: bool,
        use_nvenc: bool,
    ):
        super().__init__()
        self.ffmpeg_exe = ffmpeg_exe
        self.input_path = input_path
        self.output_path = output_path
        self.start_frame = start_frame
        self.end_frame = end_frame
        self.fps = fps
        self.sync_audio = sync_audio
        self.use_nvenc = use_nvenc

    def run(self):
        # ffmpeg trim 的 end_frame 是不包含，所以要 +1 才能包含使用者指定的最後 frame。
        end_exclusive = self.end_frame + 1
        start_sec = frame_to_seconds(self.start_frame, self.fps)
        end_sec = frame_to_seconds(end_exclusive, self.fps)
        clip_duration = max(0.001, end_sec - start_sec)

        cmd = [
            self.ffmpeg_exe,
            "-hide_banner",
            "-y",
            "-nostats",
            "-progress",
            "pipe:1",
            "-hwaccel",
            "auto",
            "-i",
            self.input_path,
            "-vf",
            f"trim=start_frame={self.start_frame}:end_frame={end_exclusive},setpts=PTS-STARTPTS",
        ]

        if self.sync_audio:
            cmd += [
                "-af",
                f"atrim=start={start_sec}:end={end_sec},asetpts=PTS-STARTPTS",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        else:
            cmd += ["-an"]

        cmd += [
            "-c:v",
            "h264_nvenc" if self.use_nvenc else "libx264",
            "-pix_fmt",
            "yuv420p",
            self.output_path,
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )

        last_progress = -1
        log_lines: list[str] = []
        while True:
            line = process.stdout.readline() if process.stdout else ""
            if line == "" and process.poll() is not None:
                break

            line = line.strip()
            if not line:
                continue

            if line.startswith("out_time_ms="):
                try:
                    out_time_ms = int(line.split("=", 1)[1])
                    ratio = min(1.0, max(0.0, (out_time_ms / 1_000_000.0) / clip_duration))
                    progress = int(ratio * 100)
                    if progress != last_progress:
                        last_progress = progress
                        self.progressChanged.emit(progress)
                except Exception:
                    pass
            elif line.startswith("progress=end"):
                self.progressChanged.emit(100)
            elif "=" not in line:
                log_lines.append(line)
                if len(log_lines) > 30:
                    log_lines.pop(0)

        process.wait()

        if process.returncode != 0:
            err_text = "\n".join(log_lines).strip() or "ffmpeg 執行失敗"
            self.failed.emit(err_text)
            return

        self.progressChanged.emit(100)
        self.finishedOk.emit(self.output_path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("影片裁切工具 @3N71-20260528")
        self.resize(980, 760)

        self.meta: VideoMeta | None = None
        self.loading_worker: VideoLoadWorker | None = None
        self.cut_worker: CutWorker | None = None
        self.preview_cap: cv2.VideoCapture | None = None

        self._updating = False

        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.cuda_available = detect_cuda()
        self.nvenc_available = has_nvenc(self.ffmpeg_exe)

        self._build_ui()
        self._bind_events()
        self._set_idle_status()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        self.preview_label = ClickDropLabel()
        self.preview_label.setMinimumHeight(380)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setStyleSheet(
            """
            QLabel {
                border: 2px dashed #888;
                border-radius: 10px;
                background: #1d1f21;
                color: #e6e6e6;
                font-size: 16px;
                padding: 20px;
            }
            """
        )
        self.preview_label.setText(
            "拖拉影片到這裡 / 點一下選擇影片"   ## "/ Ctrl+V 貼上影片路徑或檔案 " issue, not work! 
        )
        layout.addWidget(self.preview_label)

        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setRange(0, 0)
        self.timeline_slider.setEnabled(False)
        self.timeline_label = QLabel("時間軸: frame 0 / 0 (0.00s)")
        layout.addWidget(self.timeline_slider)
        layout.addWidget(self.timeline_label)

        form = QFormLayout()

        self.start_time = FocusDoubleSpinBox()
        self.start_time.setDecimals(2)
        self.start_time.setSuffix(" s")
        self.start_time.setSingleStep(0.01)
        self.start_time.setFixedWidth(190)

        self.start_frame = FocusSpinBox()
        self.start_frame.setRange(0, 0)
        self.start_frame.setFixedWidth(150)

        start_row = QHBoxLayout()
        start_row.setContentsMargins(0, 0, 0, 0)
        start_row.setSpacing(8)
        start_row.addWidget(self.start_time)
        start_row.addWidget(QLabel("Frame"))
        start_row.addWidget(self.start_frame)
        start_row.addStretch(1)
        form.addRow("開始時間", start_row)

        self.end_time = FocusDoubleSpinBox()
        self.end_time.setDecimals(2)
        self.end_time.setSuffix(" s")
        self.end_time.setSingleStep(0.01)
        self.end_time.setFixedWidth(190)

        self.end_frame = FocusSpinBox()
        self.end_frame.setRange(0, 0)
        self.end_frame.setFixedWidth(150)

        end_row = QHBoxLayout()
        end_row.setContentsMargins(0, 0, 0, 0)
        end_row.setSpacing(8)
        end_row.addWidget(self.end_time)
        end_row.addWidget(QLabel("Frame"))
        end_row.addWidget(self.end_frame)
        end_row.addStretch(1)
        form.addRow("結束時間", end_row)

        self.sync_audio_cb = QCheckBox("同步音軌")
        self.sync_audio_cb.setChecked(False)
        form.addRow("音訊", self.sync_audio_cb)

        self.output_edit = QLineEdit()
        browse_output_btn = QPushButton("選擇輸出位置")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(browse_output_btn)
        form.addRow("輸出檔案", output_row)

        layout.addLayout(form)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel("剪輯進度: 0%")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress_bar, 1)
        layout.addLayout(progress_row)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.cut_button = QPushButton("開始擷取")
        self.cut_button.setMinimumHeight(40)
        bottom.addWidget(self.cut_button)
        layout.addLayout(bottom)

        self.browse_output_btn = browse_output_btn

        self.paste_action = QAction("貼上影片", self)
        self.paste_action.setShortcut(QKeySequence.Paste)
        self.addAction(self.paste_action)
        self.paste_shortcut = QShortcut(QKeySequence("Ctrl+V"), self)

        self.cut_button.setEnabled(False)

    def _bind_events(self):
        self.preview_label.clicked.connect(self.open_video_dialog)
        self.preview_label.fileDropped.connect(self.load_video)

        self.start_time.valueChanged.connect(self.on_start_time_changed)
        self.end_time.valueChanged.connect(self.on_end_time_changed)
        self.start_frame.valueChanged.connect(self.on_start_frame_changed)
        self.end_frame.valueChanged.connect(self.on_end_frame_changed)

        self.start_time.focusGained.connect(lambda: self.show_preview(self.start_frame.value()))
        self.start_frame.focusGained.connect(lambda: self.show_preview(self.start_frame.value()))
        self.end_time.focusGained.connect(lambda: self.show_preview(self.end_frame.value()))
        self.end_frame.focusGained.connect(lambda: self.show_preview(self.end_frame.value()))

        self.cut_button.clicked.connect(self.start_cut)
        self.browse_output_btn.clicked.connect(self.pick_output_file)
        self.timeline_slider.valueChanged.connect(self.on_timeline_changed)

        self.paste_action.triggered.connect(self.load_video_from_clipboard)
        self.paste_shortcut.activated.connect(self.load_video_from_clipboard)

    def _set_idle_status(self):
        gpu_info = "啟用" if (self.cuda_available and self.nvenc_available) else "未啟用"
        self.status_label.setText(f"等待選擇影片。GPU 加速: {gpu_info}")

    def open_video_dialog(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇影片",
            "",
            "Video Files (*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm *.m4v *.ts *.m2ts *.3gp *.mpeg *.mpg);;All Files (*.*)",
        )
        if file_path:
            self.load_video(file_path)

    def load_video_from_clipboard(self):
        cb = QApplication.clipboard()
        mime = cb.mimeData()

        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile() and is_video_file(u.toLocalFile()):
                    self.load_video(u.toLocalFile())
                    return

        text_path = parse_clipboard_path(cb.text())
        if text_path:
            self.load_video(text_path)
            return

        QMessageBox.warning(self, "貼上失敗", "剪貼簿中沒有可用的影片檔路徑。")

    def load_video(self, path: str):
        path = normalize_windows_path(path)
        if not is_video_file(path):
            QMessageBox.warning(self, "格式不支援", "請選擇常見影片格式檔案。")
            return

        self.cut_button.setEnabled(False)
        self.status_label.setText("正在讀取影片資料中（背景執行，不影響 UI）...")

        if self.loading_worker and self.loading_worker.isRunning():
            self.loading_worker.quit()
            self.loading_worker.wait()

        self.loading_worker = VideoLoadWorker(path)
        self.loading_worker.loaded.connect(self.on_video_loaded)
        self.loading_worker.failed.connect(self.on_video_load_failed)
        self.loading_worker.start()

    def on_video_load_failed(self, msg: str):
        QMessageBox.critical(self, "讀取失敗", msg)
        self._set_idle_status()

    def on_video_loaded(self, meta: VideoMeta):
        self.meta = meta

        if self.preview_cap is not None:
            self.preview_cap.release()
        self.preview_cap = cv2.VideoCapture(meta.path)

        last_frame = max(0, meta.frame_count - 1)

        self._updating = True
        self.start_frame.setRange(0, last_frame)
        self.end_frame.setRange(0, last_frame)
        self.timeline_slider.setRange(0, last_frame)
        self.timeline_slider.setEnabled(True)

        self.start_time.setRange(0.0, meta.duration)
        self.end_time.setRange(0.0, meta.duration)

        # 時間欄位箭頭每次調整一個 frame 時間。
        step = 1.0 / meta.fps
        self.start_time.setSingleStep(step)
        self.end_time.setSingleStep(step)

        self.start_frame.setValue(0)
        self.end_frame.setValue(last_frame)
        self.start_time.setValue(0.0)
        self.end_time.setValue(frame_to_seconds(last_frame, meta.fps))
        self._updating = False

        self.prepare_default_output_path(meta.path)

        self.status_label.setText(
            f"讀取完成: {meta.width}x{meta.height}, FPS={meta.fps:.3f}, 總 frame={meta.frame_count}, 解碼格式={meta.codec}"
        )
        self.update_timeline_label(0)
        self.cut_button.setEnabled(True)
        self.show_preview(0)

    def prepare_default_output_path(self, input_path: str):
        folder = os.path.dirname(input_path)
        base, ext = os.path.splitext(os.path.basename(input_path))
        out_name = f"{base}_cut{ext}"
        self.output_edit.setText(normalize_windows_path(os.path.join(folder, out_name)))

    def pick_output_file(self):
        start_path = self.output_edit.text().strip() or ""
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "選擇輸出檔案",
            start_path,
            "Video Files (*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm *.m4v *.ts *.m2ts *.3gp *.mpeg *.mpg);;All Files (*.*)",
        )
        if selected:
            self.output_edit.setText(normalize_windows_path(selected))

    def show_preview(self, frame_idx: int):
        if self.meta is None or self.preview_cap is None:
            return

        frame_idx = max(0, min(frame_idx, self.meta.frame_count - 1))
        self.preview_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = self.preview_cap.read()
        if not ok:
            return

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, _ = rgb.shape
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(image)
        scaled = pixmap.scaled(
            self.preview_label.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.update_timeline_label(frame_idx)
        self.set_timeline_frame(frame_idx)

    def set_timeline_frame(self, frame_idx: int):
        if self.meta is None:
            return
        frame_idx = max(0, min(frame_idx, self.meta.frame_count - 1))
        if self.timeline_slider.value() == frame_idx:
            return
        self.timeline_slider.blockSignals(True)
        self.timeline_slider.setValue(frame_idx)
        self.timeline_slider.blockSignals(False)

    def update_timeline_label(self, frame_idx: int):
        if self.meta is None:
            return
        frame_idx = max(0, min(frame_idx, self.meta.frame_count - 1))
        t = frame_to_seconds(frame_idx, self.meta.fps)
        self.timeline_label.setText(
            f"時間軸: frame {frame_idx} / {self.meta.frame_count - 1} ({t:.2f}s)"
        )

    def on_timeline_changed(self, frame_idx: int):
        self.show_preview(frame_idx)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.meta is not None:
            target_frame = self.start_frame.value()
            if self.end_time.hasFocus() or self.end_frame.hasFocus():
                target_frame = self.end_frame.value()
            self.show_preview(target_frame)

    def _sync_start_with_time(self):
        if self.meta is None:
            return
        sf = seconds_to_frame(self.start_time.value(), self.meta.fps)
        sf = max(0, min(sf, self.meta.frame_count - 1))
        self.start_frame.setValue(sf)

    def _sync_end_with_time(self):
        if self.meta is None:
            return
        ef = seconds_to_frame(self.end_time.value(), self.meta.fps)
        ef = max(0, min(ef, self.meta.frame_count - 1))
        self.end_frame.setValue(ef)

    def _sync_start_with_frame(self):
        if self.meta is None:
            return
        self.start_time.setValue(frame_to_seconds(self.start_frame.value(), self.meta.fps))

    def _sync_end_with_frame(self):
        if self.meta is None:
            return
        self.end_time.setValue(frame_to_seconds(self.end_frame.value(), self.meta.fps))

    def _enforce_range(self):
        if self.meta is None:
            return
        last_frame = self.meta.frame_count - 1

        s = max(0, min(self.start_frame.value(), last_frame))
        e = max(0, min(self.end_frame.value(), last_frame))

        if s >= e:
            if self.sender() in (self.start_time, self.start_frame):
                e = min(last_frame, s + 1)
                if e <= s:
                    s = max(0, e - 1)
                self.end_frame.setValue(e)
                self.start_frame.setValue(s)
            else:
                s = max(0, e - 1)
                if s >= e:
                    e = min(last_frame, s + 1)
                self.start_frame.setValue(s)
                self.end_frame.setValue(e)

    def on_start_time_changed(self):
        if self.meta is None or self._updating:
            return
        self._updating = True
        self._sync_start_with_time()
        self._enforce_range()
        self._sync_start_with_frame()
        self._sync_end_with_frame()
        self._updating = False

        if self.start_time.hasFocus():
            self.show_preview(self.start_frame.value())

    def on_end_time_changed(self):
        if self.meta is None or self._updating:
            return
        self._updating = True
        self._sync_end_with_time()
        self._enforce_range()
        self._sync_start_with_frame()
        self._sync_end_with_frame()
        self._updating = False

        if self.end_time.hasFocus():
            self.show_preview(self.end_frame.value())

    def on_start_frame_changed(self):
        if self.meta is None or self._updating:
            return
        self._updating = True
        self._sync_start_with_frame()
        self._enforce_range()
        self._sync_start_with_frame()
        self._sync_end_with_frame()
        self._updating = False

        if self.start_frame.hasFocus():
            self.show_preview(self.start_frame.value())

    def on_end_frame_changed(self):
        if self.meta is None or self._updating:
            return
        self._updating = True
        self._sync_end_with_frame()
        self._enforce_range()
        self._sync_start_with_frame()
        self._sync_end_with_frame()
        self._updating = False

        if self.end_frame.hasFocus():
            self.show_preview(self.end_frame.value())

    def start_cut(self):
        if self.meta is None:
            QMessageBox.warning(self, "尚未選檔", "請先選擇影片。")
            return

        output_path = normalize_windows_path(self.output_edit.text().strip())
        self.output_edit.setText(output_path)
        if not output_path:
            QMessageBox.warning(self, "輸出路徑錯誤", "請輸入輸出檔案完整路徑。")
            return

        start_f = self.start_frame.value()
        end_f = self.end_frame.value()

        if start_f >= end_f:
            QMessageBox.warning(self, "範圍錯誤", "開始 frame 必須小於結束 frame。")
            return

        if end_f > self.meta.frame_count - 1:
            QMessageBox.warning(self, "範圍錯誤", "結束 frame 不能超過影片最後一個 frame。")
            return

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if os.path.exists(output_path):
            ans = QMessageBox.question(
                self,
                "檔案已存在",
                "輸出檔案已存在，是否覆蓋？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return

        use_nvenc = self.cuda_available and self.nvenc_available

        self.cut_button.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("剪輯進度: 0%")
        self.status_label.setText("剪輯中，請稍候（背景執行，可繼續操作 UI）...")

        self.cut_worker = CutWorker(
            ffmpeg_exe=self.ffmpeg_exe,
            input_path=self.meta.path,
            output_path=output_path,
            start_frame=start_f,
            end_frame=end_f,
            fps=self.meta.fps,
            sync_audio=self.sync_audio_cb.isChecked(),
            use_nvenc=use_nvenc,
        )
        self.cut_worker.progressChanged.connect(self.on_cut_progress)
        self.cut_worker.finishedOk.connect(self.on_cut_done)
        self.cut_worker.failed.connect(self.on_cut_failed)
        self.cut_worker.start()

    def on_cut_progress(self, percent: int):
        p = max(0, min(100, int(percent)))
        self.progress_bar.setValue(p)
        self.progress_label.setText(f"剪輯進度: {p}%")

    def on_cut_done(self, output_path: str):
        self.cut_button.setEnabled(True)
        self.progress_bar.setValue(100)
        self.progress_label.setText("剪輯進度: 100%")
        self.status_label.setText(f"完成: {output_path}")
        QMessageBox.information(self, "完成", f"影片剪輯完成:\n{output_path}")

    def on_cut_failed(self, message: str):
        self.cut_button.setEnabled(True)
        self.progress_label.setText("剪輯進度: 失敗")
        self.status_label.setText("剪輯失敗")
        QMessageBox.critical(self, "剪輯失敗", message)

    def closeEvent(self, event):
        if self.preview_cap is not None:
            self.preview_cap.release()
        if self.loading_worker and self.loading_worker.isRunning():
            self.loading_worker.quit()
            self.loading_worker.wait()
        if self.cut_worker and self.cut_worker.isRunning():
            self.cut_worker.quit()
            self.cut_worker.wait()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tiddl.cli.config import APP_PATH, CONFIG, CONFIG_FILENAME, Config, load_config_file


def _config_to_toml(config: Config) -> str:
    """Serialize Config pydantic model to TOML string."""
    d = config.model_dump(mode="python")
    lines: list[str] = []

    # Top-level fields (enable_cache, debug)
    for key in ("enable_cache", "debug"):
        val = d.get(key)
        if val is not None:
            lines.append(f'{key} = {"true" if val else "false"}')
    lines.append("")

    # Sections in order: templates, download, metadata, cover, m3u
    sections = ["templates", "download", "metadata", "cover", "m3u"]
    for section in sections:
        sec_data = d.get(section)
        if not isinstance(sec_data, dict):
            continue
        lines.append(f"[{section}]")
        for sub_key, sub_val in sec_data.items():
            if isinstance(sub_val, dict):
                # Nested sub-section like [cover.templates]
                continue  # will handle separately
            lines.append(_toml_kv(sub_key, sub_val))
        lines.append("")

        # Handle nested sub-sections
        for sub_key, sub_val in sec_data.items():
            if isinstance(sub_val, dict):
                lines.append(f"[{section}.{sub_key}]")
                for k, v in sub_val.items():
                    lines.append(_toml_kv(k, v))
                lines.append("")

    return "\n".join(lines).strip() + "\n"


def _toml_kv(key: str, val: Any) -> str:
    """Format a single TOML key=value line."""
    if isinstance(val, bool):
        return f'{key} = {"true" if val else "false"}'
    if isinstance(val, int):
        return f"{key} = {val}"
    if isinstance(val, list):
        items = ", ".join(f'"{x}"' for x in val)
        return f"{key} = [{items}]"
    # Path or str
    return f'{key} = "{val}"'


class SettingsDialog(QDialog):
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(560, 520)

        self._config = CONFIG  # reference at open time

        self._tabs = QTabWidget()

        self._build_general_tab()
        self._build_download_tab()
        self._build_metadata_tab()
        self._build_cover_tab()
        self._build_m3u_tab()
        self._build_templates_tab()

        self._populate_ui()

        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        layout.addWidget(button_box)

    # ---- General Tab ----
    def _build_general_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._enable_cache = QCheckBox()
        self._enable_cache.setToolTip("緩存 API 請求以提高速度")
        layout.addRow("啟用快取 (enable_cache):", self._enable_cache)

        self._debug = QCheckBox()
        self._debug.setToolTip("將 API 調試資訊保存到 api_debug 目錄")
        layout.addRow("除錯模式 (debug):", self._debug)

        self._tabs.addTab(tab, "一般 (General)")

    # ---- Download Tab ----
    def _build_download_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._track_quality = QComboBox()
        self._track_quality.addItems(["low", "normal", "high", "max"])
        self._track_quality.setToolTip("音軌品質：low=96kbps m4a, normal=320kbps m4a, high=16bit FLAC, max=24bit FLAC")
        layout.addRow("音軌品質 (track_quality):", self._track_quality)

        self._video_quality = QComboBox()
        self._video_quality.addItems(["sd", "hd", "fhd"])
        self._video_quality.setToolTip("影片品質：sd=360p, hd=720p, fhd=1080p")
        layout.addRow("影片品質 (video_quality):", self._video_quality)

        self._skip_existing = QCheckBox()
        self._skip_existing.setToolTip("跳過已下載的檔案")
        layout.addRow("跳過已存在 (skip_existing):", self._skip_existing)

        self._threads_count = QSpinBox()
        self._threads_count.setMinimum(1)
        self._threads_count.setMaximum(16)
        self._threads_count.setToolTip("同時下載的執行緒數量，建議保持較低數值")
        layout.addRow("執行緒數 (threads_count):", self._threads_count)

        # download_path
        dl_path_layout = QHBoxLayout()
        self._download_path = QLineEdit()
        self._download_path.setToolTip("下載檔案的基底目錄")
        dl_path_btn = QPushButton("瀏覽...")
        dl_path_btn.clicked.connect(lambda: self._browse_path(self._download_path))
        dl_path_layout.addWidget(self._download_path)
        dl_path_layout.addWidget(dl_path_btn)
        layout.addRow("下載路徑 (download_path):", dl_path_layout)

        # scan_path
        scan_path_layout = QHBoxLayout()
        self._scan_path = QLineEdit()
        self._scan_path.setToolTip("掃描已下載檔案的路徑（用於跳過已存在）")
        scan_path_btn = QPushButton("瀏覽...")
        scan_path_btn.clicked.connect(lambda: self._browse_path(self._scan_path))
        scan_path_layout.addWidget(self._scan_path)
        scan_path_layout.addWidget(scan_path_btn)
        layout.addRow("掃描路徑 (scan_path):", scan_path_layout)

        self._singles_filter = QComboBox()
        self._singles_filter.addItems(["none", "only", "include"])
        self._singles_filter.setToolTip("單曲過濾：none=僅完整專輯, only=僅單曲, include=兩者皆包含")
        layout.addRow("單曲過濾 (singles_filter):", self._singles_filter)

        self._videos_filter = QComboBox()
        self._videos_filter.addItems(["none", "only", "allow"])
        self._videos_filter.setToolTip("影片過濾：none=不允許, only=僅影片, allow=音軌與影片")
        layout.addRow("影片過濾 (videos_filter):", self._videos_filter)

        self._atmos_filter = QComboBox()
        self._atmos_filter.addItems(["none", "only", "allow"])
        self._atmos_filter.setToolTip("Dolby Atmos 過濾：none=僅立體聲, only=僅Atmos, allow=兩者皆可")
        layout.addRow("Atmos 過濾 (atmos_filter):", self._atmos_filter)

        self._update_mtime = QCheckBox()
        self._update_mtime.setToolTip("當 skip_existing 啟用時，更新現有檔案的修改時間")
        layout.addRow("更新修改時間 (update_mtime):", self._update_mtime)

        self._rewrite_metadata = QCheckBox()
        self._rewrite_metadata.setToolTip("啟用時，重新寫入已下載檔案的中繼資料")
        layout.addRow("重寫中繼資料 (rewrite_metadata):", self._rewrite_metadata)

        self._write_lrc_file = QCheckBox()
        self._write_lrc_file.setToolTip("啟用時，在音軌旁建立同名的 .lrc 歌詞檔案")
        layout.addRow("寫入 LRC 歌詞 (write_lrc_file):", self._write_lrc_file)

        self._match_existing_path_case = QCheckBox()
        self._match_existing_path_case.setToolTip("啟用時，沿用已存在路徑的大小寫，避免區分大小寫的檔案系統衝突")
        layout.addRow("匹配現有路徑大小寫 (match_existing_path_case):", self._match_existing_path_case)

        self._tabs.addTab(tab, "下載 (Download)")

    # ---- Metadata Tab ----
    def _build_metadata_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._metadata_enable = QCheckBox()
        self._metadata_enable.setToolTip("在檔案中嵌入中繼資料")
        layout.addRow("啟用中繼資料 (enable):", self._metadata_enable)

        self._metadata_lyrics = QCheckBox()
        self._metadata_lyrics.setToolTip("在中繼資料中嵌入歌詞")
        layout.addRow("嵌入歌詞 (lyrics):", self._metadata_lyrics)

        self._metadata_cover = QCheckBox()
        self._metadata_cover.setToolTip("在音軌檔案中嵌入封面")
        layout.addRow("嵌入封面 (cover):", self._metadata_cover)

        self._metadata_album_review = QCheckBox()
        self._metadata_album_review.setToolTip("將專輯評論文字寫入音軌 COMMENT 中繼資料欄位")
        layout.addRow("專輯評論 (album_review):", self._metadata_album_review)

        self._tabs.addTab(tab, "中繼資料 (Metadata)")

    # ---- Cover Tab ----
    def _build_cover_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._cover_save = QCheckBox()
        self._cover_save.setToolTip("將封面儲存為獨立檔案")
        layout.addRow("儲存封面檔案 (save):", self._cover_save)

        self._cover_size = QSpinBox()
        self._cover_size.setMinimum(128)
        self._cover_size.setMaximum(1280)
        self._cover_size.setSingleStep(128)
        self._cover_size.setToolTip("封面尺寸（像素），最大 1280x1280")
        layout.addRow("封面尺寸 (size):", self._cover_size)

        self._cover_allowed = QListWidget()
        self._cover_allowed.setToolTip("允許儲存封面的資源類型")
        for label in ["track", "album", "playlist"]:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._cover_allowed.addItem(item)
        layout.addRow("允許類型 (allowed):", self._cover_allowed)

        self._cover_templates_track = QLineEdit()
        self._cover_templates_track.setToolTip("音軌封面的路徑模板")
        layout.addRow("封面模板 - 音軌 (templates.track):", self._cover_templates_track)

        self._cover_templates_album = QLineEdit()
        self._cover_templates_album.setToolTip("專輯封面的路徑模板")
        layout.addRow("封面模板 - 專輯 (templates.album):", self._cover_templates_album)

        self._cover_templates_playlist = QLineEdit()
        self._cover_templates_playlist.setToolTip("播放清單封面的路徑模板")
        layout.addRow("封面模板 - 播放清單 (templates.playlist):", self._cover_templates_playlist)

        self._tabs.addTab(tab, "封面 (Cover)")

    # ---- M3U Tab ----
    def _build_m3u_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._m3u_save = QCheckBox()
        self._m3u_save.setToolTip("儲存 M3U 播放清單檔案")
        layout.addRow("儲存 M3U (save):", self._m3u_save)

        self._m3u_allowed = QListWidget()
        self._m3u_allowed.setToolTip("允許建立 M3U 的資源類型")
        for label in ["album", "playlist", "mix"]:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self._m3u_allowed.addItem(item)
        layout.addRow("允許類型 (allowed):", self._m3u_allowed)

        self._m3u_templates_album = QLineEdit()
        self._m3u_templates_album.setToolTip("專輯 M3U 的路徑模板")
        layout.addRow("M3U 模板 - 專輯 (templates.album):", self._m3u_templates_album)

        self._m3u_templates_playlist = QLineEdit()
        self._m3u_templates_playlist.setToolTip("播放清單 M3U 的路徑模板")
        layout.addRow("M3U 模板 - 播放清單 (templates.playlist):", self._m3u_templates_playlist)

        self._m3u_templates_mix = QLineEdit()
        self._m3u_templates_mix.setToolTip("混音 M3U 的路徑模板")
        layout.addRow("M3U 模板 - 混音 (templates.mix):", self._m3u_templates_mix)

        self._tabs.addTab(tab, "M3U")

    # ---- Templates Tab ----
    def _build_templates_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)

        self._templates_default = QLineEdit()
        self._templates_default.setToolTip("預設路徑模板，用於未指定模板的資源")
        layout.addRow("預設 (default):", self._templates_default)

        self._templates_track = QLineEdit()
        self._templates_track.setToolTip("音軌的路徑模板（留空則使用預設）")
        layout.addRow("音軌 (track):", self._templates_track)

        self._templates_video = QLineEdit()
        self._templates_video.setToolTip("影片的路徑模板（留空則使用預設）")
        layout.addRow("影片 (video):", self._templates_video)

        self._templates_album = QLineEdit()
        self._templates_album.setToolTip("專輯的路徑模板（留空則使用預設）")
        layout.addRow("專輯 (album):", self._templates_album)

        self._templates_playlist = QLineEdit()
        self._templates_playlist.setToolTip("播放清單的路徑模板（留空則使用預設）")
        layout.addRow("播放清單 (playlist):", self._templates_playlist)

        self._templates_mix = QLineEdit()
        self._templates_mix.setToolTip("混音的路徑模板（留空則使用預設）")
        layout.addRow("混音 (mix):", self._templates_mix)

        self._tabs.addTab(tab, "模板 (Templates)")

    # ---- Populate UI from config ----
    def _populate_ui(self):
        c = self._config

        # General
        self._enable_cache.setChecked(c.enable_cache)
        self._debug.setChecked(c.debug)

        # Download
        self._track_quality.setCurrentText(c.download.track_quality)
        self._video_quality.setCurrentText(c.download.video_quality)
        self._skip_existing.setChecked(c.download.skip_existing)
        self._threads_count.setValue(c.download.threads_count)
        self._download_path.setText(str(c.download.download_path))
        self._scan_path.setText(str(c.download.scan_path))
        self._singles_filter.setCurrentText(c.download.singles_filter)
        self._videos_filter.setCurrentText(c.download.videos_filter)
        self._atmos_filter.setCurrentText(c.download.atmos_filter)
        self._update_mtime.setChecked(c.download.update_mtime)
        self._rewrite_metadata.setChecked(c.download.rewrite_metadata)
        self._write_lrc_file.setChecked(c.download.write_lrc_file)
        self._match_existing_path_case.setChecked(c.download.match_existing_path_case)

        # Metadata
        self._metadata_enable.setChecked(c.metadata.enable)
        self._metadata_lyrics.setChecked(c.metadata.lyrics)
        self._metadata_cover.setChecked(c.metadata.cover)
        self._metadata_album_review.setChecked(c.metadata.album_review)

        # Cover
        self._cover_save.setChecked(c.cover.save)
        self._cover_size.setValue(c.cover.size)
        self._set_list_checked(self._cover_allowed, c.cover.allowed)
        self._cover_templates_track.setText(c.cover.templates.track)
        self._cover_templates_album.setText(c.cover.templates.album)
        self._cover_templates_playlist.setText(c.cover.templates.playlist)

        # M3U
        self._m3u_save.setChecked(c.m3u.save)
        self._set_list_checked(self._m3u_allowed, c.m3u.allowed)
        self._m3u_templates_album.setText(c.m3u.templates.album)
        self._m3u_templates_playlist.setText(c.m3u.templates.playlist)
        self._m3u_templates_mix.setText(c.m3u.templates.mix)

        # Templates
        self._templates_default.setText(c.templates.default)
        self._templates_track.setText(c.templates.track)
        self._templates_video.setText(c.templates.video)
        self._templates_album.setText(c.templates.album)
        self._templates_playlist.setText(c.templates.playlist)
        self._templates_mix.setText(c.templates.mix)

    # ---- Collect values from UI ----
    def _collect_values(self) -> dict:
        return {
            "enable_cache": self._enable_cache.isChecked(),
            "debug": self._debug.isChecked(),
            "download": {
                "track_quality": self._track_quality.currentText(),
                "video_quality": self._video_quality.currentText(),
                "skip_existing": self._skip_existing.isChecked(),
                "threads_count": self._threads_count.value(),
                "download_path": self._download_path.text(),
                "scan_path": self._scan_path.text(),
                "singles_filter": self._singles_filter.currentText(),
                "videos_filter": self._videos_filter.currentText(),
                "atmos_filter": self._atmos_filter.currentText(),
                "update_mtime": self._update_mtime.isChecked(),
                "rewrite_metadata": self._rewrite_metadata.isChecked(),
                "write_lrc_file": self._write_lrc_file.isChecked(),
                "match_existing_path_case": self._match_existing_path_case.isChecked(),
            },
            "metadata": {
                "enable": self._metadata_enable.isChecked(),
                "lyrics": self._metadata_lyrics.isChecked(),
                "cover": self._metadata_cover.isChecked(),
                "album_review": self._metadata_album_review.isChecked(),
            },
            "cover": {
                "save": self._cover_save.isChecked(),
                "size": self._cover_size.value(),
                "allowed": self._get_list_checked(self._cover_allowed),
                "templates": {
                    "track": self._cover_templates_track.text(),
                    "album": self._cover_templates_album.text(),
                    "playlist": self._cover_templates_playlist.text(),
                },
            },
            "m3u": {
                "save": self._m3u_save.isChecked(),
                "allowed": self._get_list_checked(self._m3u_allowed),
                "templates": {
                    "album": self._m3u_templates_album.text(),
                    "playlist": self._m3u_templates_playlist.text(),
                    "mix": self._m3u_templates_mix.text(),
                },
            },
            "templates": {
                "default": self._templates_default.text(),
                "track": self._templates_track.text(),
                "video": self._templates_video.text(),
                "album": self._templates_album.text(),
                "playlist": self._templates_playlist.text(),
                "mix": self._templates_mix.text(),
            },
        }

    # ---- Helpers ----
    def _browse_path(self, line_edit: QLineEdit):
        directory = QFileDialog.getExistingDirectory(self, "選擇目錄", line_edit.text())
        if directory:
            line_edit.setText(directory)

    @staticmethod
    def _set_list_checked(list_widget: QListWidget, values: list[str]):
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item is None:
                continue
            item.setCheckState(
                Qt.CheckState.Checked if item.text() in values else Qt.CheckState.Unchecked
            )

    @staticmethod
    def _get_list_checked(list_widget: QListWidget) -> list[str]:
        result: list[str] = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                result.append(item.text())
        return result

    # ---- Save logic ----
    def _save(self):
        raw = self._collect_values()

        # Convert string paths to Path objects
        for key in ("download_path", "scan_path"):
            raw["download"][key] = Path(raw["download"][key])

        new_config = Config.model_validate(raw, strict=True)
        toml_str = _config_to_toml(new_config)

        config_file = APP_PATH / CONFIG_FILENAME
        config_file.write_text(toml_str)

        # Reload global config
        global CONFIG
        import tiddl.cli.config as cfg
        cfg.CONFIG = load_config_file(config_file)
        self._config = cfg.CONFIG

        self.settings_saved.emit()

    def _on_apply(self):
        self._save()

    def _on_ok(self):
        self._save()
        self.accept()

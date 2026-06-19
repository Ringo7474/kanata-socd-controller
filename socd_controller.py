from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "方向键急停"
CONFIG_NAME = "kanata.kbd"
SETTINGS_NAME = "socd_settings.json"
KANATA_EXE = "kanata_windows_gui_wintercept_x64.exe"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


@dataclass
class SocdSettings:
    enabled: bool = True
    mode: str = "once"
    duration_ms: int = 33
    ws_enabled: bool = True
    ad_enabled: bool = True

    def normalized(self) -> "SocdSettings":
        self.mode = "duration" if self.mode == "duration" else "once"
        self.duration_ms = max(1, min(200, int(self.duration_ms)))
        return self


def infer_settings(config_text: str) -> SocdSettings:
    match = re.search(r"hold-for-duration\s+(\d+)", config_text)
    ws_enabled = "state-w" in config_text and "state-s" in config_text
    ad_enabled = "state-a" in config_text and "state-d" in config_text
    return SocdSettings(
        enabled=ws_enabled or ad_enabled,
        mode="duration" if match else "once",
        duration_ms=int(match.group(1)) if match else 33,
        ws_enabled=ws_enabled,
        ad_enabled=ad_enabled,
    ).normalized()


def load_settings(root: Path) -> SocdSettings:
    settings_path = root / SETTINGS_NAME
    if settings_path.exists():
        try:
            return SocdSettings(**json.loads(settings_path.read_text(encoding="utf-8"))).normalized()
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    config_path = root / CONFIG_NAME
    if config_path.exists():
        return infer_settings(config_path.read_text(encoding="utf-8"))
    return SocdSettings()


def pulse_action(output_key: str, settings: SocdSettings) -> str:
    if settings.mode == "duration":
        return f"(hold-for-duration {settings.duration_ms} out-{output_key})"
    return f"(on-press tap-vkey out-{output_key})"


def release_virtual_key(current: str, opposite: str, settings: SocdSettings) -> str:
    pulse = pulse_action(opposite, settings)
    return f"""  release-{current} (switch
    ((input virtual state-{current})) (switch
      ((input real {opposite})) (multi
        (on-press release-vkey state-{current})
        (on-press press-vkey state-{opposite})
        (on-press release-vkey out-{current})
        (on-press press-vkey out-{opposite})
      ) break
      () (multi
        (on-press release-vkey state-{current})
        (on-press release-vkey out-{current})
        {pulse}
      ) break
    ) break
    () XX break
  )"""


def direction_alias(current: str, opposite: str) -> str:
    return f"""  {current}-on-release (multi
    (on-press release-vkey state-{opposite})
    (on-press press-vkey state-{current})
    (on-press release-vkey out-{opposite})
    (on-press press-vkey out-{current})
    (on-release tap-vkey release-{current})
  )"""


def generate_config(settings: SocdSettings) -> str:
    settings = settings.normalized()
    ws = settings.enabled and settings.ws_enabled
    ad = settings.enabled and settings.ad_enabled

    header = """(defcfg
  process-unmapped-keys no
)

(defsrc
  w s
  a d
)"""

    if not ws and not ad:
        return header + "\n\n(deflayer default\n  w s\n  a d\n)\n"

    key_ids = {"w": 0, "s": 1, "a": 2, "d": 3}
    enabled_pairs: list[tuple[str, str]] = []
    if ws:
        enabled_pairs.append(("w", "s"))
    if ad:
        enabled_pairs.append(("a", "d"))

    virtual_lines: list[str] = []
    release_blocks: list[str] = []
    alias_blocks: list[str] = []

    for first, second in enabled_pairs:
        virtual_lines.extend(
            [
                f"  out-{first} {first}",
                f"  out-{second} {second}",
                f"  state-{first} nop{key_ids[first]}",
                f"  state-{second} nop{key_ids[second]}",
            ]
        )
        release_blocks.extend(
            [
                release_virtual_key(first, second, settings),
                release_virtual_key(second, first, settings),
            ]
        )
        alias_blocks.extend(
            [
                direction_alias(first, second),
                direction_alias(second, first),
            ]
        )

    virtual_section = "(defvirtualkeys\n" + "\n".join(virtual_lines + release_blocks) + "\n)"
    alias_section = "(defalias\n" + "\n".join(alias_blocks) + "\n)"
    ws_layer = "@w-on-release @s-on-release" if ws else "w s"
    ad_layer = "@a-on-release @d-on-release" if ad else "a d"
    layer_section = f"(deflayer default\n  {ws_layer}\n  {ad_layer}\n)"

    return "\n\n".join((header, virtual_section, alias_section, layer_section)) + "\n"


def validate_config(root: Path, config_text: str) -> tuple[bool, str]:
    executable = root / KANATA_EXE
    if not executable.exists():
        return False, f"未找到 {KANATA_EXE}"

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".kbd", prefix="socd-check-", dir=root, delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(config_text)
            temp_path = Path(temp_file.name)

        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [str(executable), "--check", "--cfg", str(temp_path)],
            cwd=root,
            timeout=12,
            creationflags=creation_flags,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return True, "配置校验通过"
        detail = (result.stderr or result.stdout or "Kanata 返回校验错误").strip()
        return False, detail
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


def save_config(root: Path, settings: SocdSettings) -> tuple[bool, str]:
    config_text = generate_config(settings)
    valid, message = validate_config(root, config_text)
    if not valid:
        return False, message

    config_path = root / CONFIG_NAME
    backup_path = root / f"{CONFIG_NAME}.bak"
    temporary_path = root / f"{CONFIG_NAME}.new"
    if config_path.exists():
        shutil.copy2(config_path, backup_path)
    temporary_path.write_text(config_text, encoding="utf-8", newline="\n")
    os.replace(temporary_path, config_path)
    (root / SETTINGS_NAME).write_text(
        json.dumps(asdict(settings.normalized()), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True, message


def kanata_running() -> bool:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {KANATA_EXE}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creation_flags,
            errors="ignore",
        )
        return KANATA_EXE.lower() in result.stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def stop_kanata_background() -> tuple[bool, str]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for image_name in (KANATA_EXE, "kanata_windows_tty_wintercept_x64.exe"):
            subprocess.run(
                ["taskkill", "/IM", image_name, "/F", "/T"],
                capture_output=True,
                timeout=5,
                creationflags=creation_flags,
            )
        return True, "Kanata 已停止"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def start_kanata_background(root: Path) -> tuple[bool, str]:
    executable = root / KANATA_EXE
    config_path = root / CONFIG_NAME
    if not executable.exists():
        return False, f"未找到 {KANATA_EXE}"
    if not config_path.exists():
        return False, f"未找到 {CONFIG_NAME}"

    if kanata_running():
        stopped, message = stop_kanata_background()
        if not stopped:
            return False, message
        time.sleep(0.6)

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [str(executable), "--cfg", str(config_path), "--nodelay"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True,
        )
        return True, "正在后台启动 Kanata"
    except OSError as exc:
        return False, str(exc)


class ToggleSwitch(QAbstractButton):
    toggledByUser = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(52, 28)
        self.clicked.connect(self.toggledByUser.emit)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor("#2f7d5c" if self.isChecked() else "#9aa0a8")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(0, 2, 52, 24, 12, 12)
        painter.setBrush(QColor("#ffffff"))
        knob_x = 28 if self.isChecked() else 4
        painter.drawEllipse(knob_x, 4, 20, 20)


class DarkCheckBox(QCheckBox):
    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        size = 18
        top = (self.height() - size) // 2
        indicator = QRect(0, top, size, size)
        if self.isChecked():
            painter.setPen(QPen(QColor("#67b48e"), 1.2))
            painter.setBrush(QColor("#edf7f2"))
        else:
            painter.setPen(QPen(QColor("#727984"), 1.0))
            painter.setBrush(QColor("#24282e"))
        painter.drawRoundedRect(indicator, 4, 4)

        if self.isChecked():
            check_pen = QPen(QColor("#185c43"), 2.3)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.drawLine(4, top + 9, 8, top + 13)
            painter.drawLine(8, top + 13, 15, top + 5)

        painter.setPen(QColor("#f1f3f5" if self.isEnabled() else "#717781"))
        painter.drawText(
            QRect(28, 0, self.width() - 28, self.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )


class AxisRow(QFrame):
    def __init__(self, keys: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("axisRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(18)

        key_label = QLabel(keys)
        key_label.setObjectName("keyPair")
        key_label.setFixedWidth(108)
        detail = QVBoxLayout()
        detail.setSpacing(4)
        title = QLabel("方向冲突处理")
        title.setObjectName("axisTitle")
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("axisSubtitle")
        detail.addWidget(title)
        detail.addWidget(self.subtitle)

        self.state = QLabel("已启用")
        self.state.setObjectName("axisState")
        self.state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state.setFixedSize(74, 28)

        layout.addWidget(key_label)
        layout.addLayout(detail, 1)
        layout.addWidget(self.state)

    def update_state(self, enabled: bool, mode_text: str) -> None:
        self.subtitle.setText(mode_text if enabled else "按键直通")
        self.state.setText("已启用" if enabled else "已关闭")
        self.state.setProperty("active", enabled)
        self.state.style().unpolish(self.state)
        self.state.style().polish(self.state)


class MainWindow(QMainWindow):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.settings = load_settings(root)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1040, 700)
        self.resize(1120, 760)
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._load_controls()
        self._refresh_preview()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_process_status)
        self.timer.start(1200)
        self._refresh_process_status()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(310)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(28, 30, 28, 28)
        side.setSpacing(20)

        brand = QLabel("KANATA · SOCD")
        brand.setObjectName("brand")
        product = QLabel("方向键急停")
        product.setObjectName("product")
        side.addWidget(brand)
        side.addWidget(product)
        side.addSpacing(12)

        switch_row = QHBoxLayout()
        switch_text = QVBoxLayout()
        switch_text.setSpacing(3)
        switch_title = QLabel("急停功能")
        switch_title.setObjectName("controlTitle")
        switch_subtitle = QLabel("SOCD 方向冲突处理")
        switch_subtitle.setObjectName("controlHint")
        switch_text.addWidget(switch_title)
        switch_text.addWidget(switch_subtitle)
        self.master_switch = ToggleSwitch()
        self.master_switch.toggled.connect(self._controls_changed)
        switch_row.addLayout(switch_text, 1)
        switch_row.addWidget(self.master_switch)
        side.addLayout(switch_row)

        mode_label = QLabel("补偿模式")
        mode_label.setObjectName("sectionLabel")
        side.addWidget(mode_label)

        mode_container = QFrame()
        mode_container.setObjectName("modeContainer")
        mode_row = QHBoxLayout(mode_container)
        mode_row.setContentsMargins(3, 3, 3, 3)
        mode_row.setSpacing(3)
        self.once_button = QPushButton("触发一次")
        self.duration_button = QPushButton("固定时长")
        for button in (self.once_button, self.duration_button):
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            mode_row.addWidget(button)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.once_button)
        self.mode_group.addButton(self.duration_button)
        self.mode_group.buttonClicked.connect(self._controls_changed)
        side.addWidget(mode_container)

        duration_header = QHBoxLayout()
        duration_label = QLabel("反向补偿时长")
        duration_label.setObjectName("sectionLabel")
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 200)
        self.duration_spin.setSuffix(" ms")
        self.duration_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.duration_spin.setFixedWidth(92)
        self.duration_spin.valueChanged.connect(self._spin_changed)
        duration_header.addWidget(duration_label)
        duration_header.addStretch(1)
        duration_header.addWidget(self.duration_spin)
        side.addLayout(duration_header)

        self.duration_slider = QSlider(Qt.Orientation.Horizontal)
        self.duration_slider.setRange(1, 200)
        self.duration_slider.valueChanged.connect(self._slider_changed)
        side.addWidget(self.duration_slider)

        axis_label = QLabel("生效方向")
        axis_label.setObjectName("sectionLabel")
        side.addWidget(axis_label)
        self.ws_check = DarkCheckBox("W / S")
        self.ad_check = DarkCheckBox("A / D")
        for checkbox in (self.ws_check, self.ad_check):
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.toggled.connect(self._controls_changed)
            side.addWidget(checkbox)

        side.addStretch(1)
        file_label = QLabel(CONFIG_NAME)
        file_label.setObjectName("sidebarFile")
        file_label.setToolTip(str(self.root / CONFIG_NAME))
        side.addWidget(file_label)

        content = QFrame()
        content.setObjectName("content")
        main = QVBoxLayout(content)
        main.setContentsMargins(40, 32, 40, 32)
        main.setSpacing(20)

        header = QHBoxLayout()
        heading_box = QVBoxLayout()
        heading_box.setSpacing(4)
        heading = QLabel("急停设置")
        heading.setObjectName("heading")
        self.mode_summary = QLabel()
        self.mode_summary.setObjectName("subheading")
        heading_box.addWidget(heading)
        heading_box.addWidget(self.mode_summary)

        self.process_status = QLabel("未运行")
        self.process_status.setObjectName("processStatus")
        self.process_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.process_status.setFixedSize(92, 32)
        header.addLayout(heading_box, 1)
        header.addWidget(self.process_status)
        main.addLayout(header)

        self.ws_row = AxisRow("W   S", "")
        self.ad_row = AxisRow("A   D", "")
        main.addWidget(self.ws_row)
        main.addWidget(self.ad_row)

        preview_header = QHBoxLayout()
        preview_title = QLabel("当前配置")
        preview_title.setObjectName("panelTitle")
        self.validation_label = QLabel("等待保存")
        self.validation_label.setObjectName("validationLabel")
        preview_header.addWidget(preview_title)
        preview_header.addStretch(1)
        preview_header.addWidget(self.validation_label)
        main.addLayout(preview_header)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName("preview")
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        fixed_font.setPointSize(10)
        self.preview.setFont(fixed_font)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        main.addWidget(self.preview, 1)

        footer = QHBoxLayout()
        self.notice = QLabel("配置未修改")
        self.notice.setObjectName("notice")
        footer.addWidget(self.notice, 1)

        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self._stop_kanata)

        self.save_button = QPushButton("保存配置")
        self.save_button.setObjectName("secondaryButton")
        self.save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.save_button.clicked.connect(self._save)

        self.start_button = QPushButton("保存并启动")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self._save_and_start)

        footer.addWidget(self.stop_button)
        footer.addWidget(self.save_button)
        footer.addWidget(self.start_button)
        main.addLayout(footer)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(content, 1)

    def _load_controls(self) -> None:
        settings = self.settings
        controls = (
            self.master_switch,
            self.once_button,
            self.duration_button,
            self.duration_slider,
            self.duration_spin,
            self.ws_check,
            self.ad_check,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        self.master_switch.setChecked(settings.enabled)
        self.once_button.setChecked(settings.mode == "once")
        self.duration_button.setChecked(settings.mode == "duration")
        self.duration_slider.setValue(settings.duration_ms)
        self.duration_spin.setValue(settings.duration_ms)
        self.ws_check.setChecked(settings.ws_enabled)
        self.ad_check.setChecked(settings.ad_enabled)
        del blockers

        duration_enabled = settings.enabled and settings.mode == "duration"
        self.once_button.setEnabled(settings.enabled)
        self.duration_button.setEnabled(settings.enabled)
        self.duration_slider.setEnabled(duration_enabled)
        self.duration_spin.setEnabled(duration_enabled)
        self.ws_check.setEnabled(settings.enabled)
        self.ad_check.setEnabled(settings.enabled)

    def _settings_from_controls(self) -> SocdSettings:
        return SocdSettings(
            enabled=self.master_switch.isChecked(),
            mode="duration" if self.duration_button.isChecked() else "once",
            duration_ms=self.duration_spin.value(),
            ws_enabled=self.ws_check.isChecked(),
            ad_enabled=self.ad_check.isChecked(),
        ).normalized()

    def _spin_changed(self, value: int) -> None:
        if self.duration_slider.value() != value:
            self.duration_slider.setValue(value)
        self._controls_changed()

    def _slider_changed(self, value: int) -> None:
        if self.duration_spin.value() != value:
            self.duration_spin.setValue(value)
        self._controls_changed()

    def _controls_changed(self, *args) -> None:
        self.settings = self._settings_from_controls()
        enabled = self.settings.enabled
        duration_enabled = enabled and self.settings.mode == "duration"
        self.once_button.setEnabled(enabled)
        self.duration_button.setEnabled(enabled)
        self.duration_slider.setEnabled(duration_enabled)
        self.duration_spin.setEnabled(duration_enabled)
        self.ws_check.setEnabled(enabled)
        self.ad_check.setEnabled(enabled)
        self._refresh_preview()
        self.validation_label.setText("尚未保存")
        self.validation_label.setProperty("valid", False)
        self.notice.setText("设置已修改")

    def _refresh_preview(self) -> None:
        mode_text = "触发一次" if self.settings.mode == "once" else f"固定 {self.settings.duration_ms} ms"
        if not self.settings.enabled:
            self.mode_summary.setText("SOCD 已关闭 · W/S、A/D 直通")
        else:
            self.mode_summary.setText(f"后按优先 · 松键回切 · 反向补偿 {mode_text}")

        ws_active = self.settings.enabled and self.settings.ws_enabled
        ad_active = self.settings.enabled and self.settings.ad_enabled
        self.ws_row.update_state(ws_active, mode_text)
        self.ad_row.update_state(ad_active, mode_text)
        self.preview.setPlainText(generate_config(self.settings))

    def _refresh_process_status(self) -> None:
        running = kanata_running()
        self.process_status.setText("运行中" if running else "未运行")
        self.process_status.setProperty("running", running)
        self.process_status.style().unpolish(self.process_status)
        self.process_status.style().polish(self.process_status)

    def _save(self) -> bool:
        self.settings = self._settings_from_controls()
        if self.settings.enabled and not (self.settings.ws_enabled or self.settings.ad_enabled):
            QMessageBox.warning(self, APP_NAME, "请至少选择一组生效方向，或者关闭 SOCD。")
            return False

        valid, message = save_config(self.root, self.settings)
        self.validation_label.setText(message)
        self.validation_label.setProperty("valid", valid)
        self.validation_label.style().unpolish(self.validation_label)
        self.validation_label.style().polish(self.validation_label)
        self.notice.setText("配置已保存" if valid else "配置未保存")
        if not valid:
            QMessageBox.critical(self, APP_NAME, f"配置校验失败：\n{message}")
        return valid

    def _save_and_start(self) -> None:
        if not self._save():
            return
        started, message = start_kanata_background(self.root)
        if not started:
            self.notice.setText("Kanata 启动失败")
            QMessageBox.critical(self, APP_NAME, f"后台启动失败：\n{message}")
            return
        self.notice.setText(message)
        QTimer.singleShot(1000, self._confirm_start)

    def _confirm_start(self) -> None:
        self._refresh_process_status()
        if kanata_running():
            self.notice.setText("Kanata 已在后台运行")
        else:
            self.notice.setText("Kanata 未能启动")
            QMessageBox.critical(self, APP_NAME, "Kanata 启动后立即退出，请检查配置或驱动。")

    def _stop_kanata(self) -> None:
        stopped, message = stop_kanata_background()
        self.notice.setText(message if stopped else "Kanata 停止失败")
        if not stopped:
            QMessageBox.critical(self, APP_NAME, f"后台停止失败：\n{message}")
        QTimer.singleShot(300, self._refresh_process_status)


STYLESHEET = """
QWidget {
  font-family: "Segoe UI", "Microsoft YaHei UI";
  font-size: 14px;
  color: #1e2228;
}
QFrame#sidebar {
  background: #181b20;
  color: #f7f8fa;
}
QFrame#content { background: #f3f5f6; }
QLabel#brand {
  color: #78c6a3;
  font-size: 12px;
  font-weight: 800;
}
QLabel#product {
  color: #ffffff;
  font-size: 27px;
  font-weight: 750;
}
QLabel#controlTitle { color: #ffffff; font-size: 16px; font-weight: 650; }
QLabel#controlHint, QLabel#sidebarFile { color: #9ba2ac; font-size: 12px; }
QLabel#sectionLabel { color: #c9ced5; font-size: 12px; font-weight: 650; }
QPushButton#segment {
  min-height: 38px;
  border: 0;
  border-radius: 19px;
  background: transparent;
  color: #cfd4db;
  padding: 0 12px;
}
QPushButton#segment:checked {
  background: #e9f5ef;
  color: #245f48;
  font-weight: 700;
}
QFrame#modeContainer {
  background: #24282e;
  border: 1px solid #454b54;
  border-radius: 23px;
}
QPushButton#segment:disabled { color: #6f7680; background: transparent; }
QCheckBox { color: #f1f3f5; spacing: 10px; min-height: 28px; }
QCheckBox:disabled { color: #717781; }
QSlider::groove:horizontal { height: 5px; border-radius: 2px; background: #454b54; }
QSlider::sub-page:horizontal { background: #78c6a3; border-radius: 2px; }
QSlider::handle:horizontal { width: 18px; margin: -7px 0; border-radius: 9px; background: #ffffff; border: 2px solid #2f7d5c; }
QSpinBox {
  min-height: 30px;
  border: 1px solid #4b515a;
  border-radius: 16px;
  background: #24282e;
  color: #ffffff;
  padding: 0 12px;
}
QLabel#heading { font-size: 28px; font-weight: 750; color: #171a1f; }
QLabel#subheading { color: #626a74; font-size: 13px; }
QLabel#processStatus {
  border-radius: 16px;
  background: #e2e5e8;
  color: #626a74;
  font-weight: 700;
}
QLabel#processStatus[running="true"] { background: #dcefe5; color: #246044; }
QFrame#axisRow {
  background: #ffffff;
  border: 1px solid #dfe3e6;
  border-radius: 7px;
}
QLabel#keyPair { font-size: 23px; font-weight: 800; color: #171a1f; }
QLabel#axisTitle { font-size: 15px; font-weight: 700; color: #262b32; }
QLabel#axisSubtitle { font-size: 12px; color: #717983; }
QLabel#axisState { border-radius: 14px; background: #eceff1; color: #6b737c; font-size: 12px; font-weight: 700; }
QLabel#axisState[active="true"] { background: #dcefe5; color: #246044; }
QLabel#panelTitle { font-size: 16px; font-weight: 750; color: #252a31; }
QLabel#validationLabel { color: #8a6b2e; font-size: 12px; font-weight: 650; }
QLabel#validationLabel[valid="true"] { color: #2f7d5c; }
QPlainTextEdit#preview {
  border: 1px solid #d9dde1;
  border-radius: 7px;
  background: #ffffff;
  color: #29313a;
  padding: 14px;
  selection-background-color: #b9ddca;
}
QLabel#notice { color: #6b737c; font-size: 12px; }
QPushButton#primaryButton, QPushButton#secondaryButton {
  min-height: 42px;
  border-radius: 21px;
  padding: 0 20px;
  font-weight: 700;
}
QPushButton#primaryButton { background: #2f7d5c; color: #ffffff; border: 1px solid #2f7d5c; }
QPushButton#primaryButton:hover { background: #286b50; }
QPushButton#secondaryButton { background: #ffffff; color: #30363d; border: 1px solid #cfd4d9; }
QPushButton#secondaryButton:hover { background: #f7f8f9; }
QToolTip { background: #20242a; color: #ffffff; border: 0; padding: 6px; }
"""


def run_self_test(root: Path) -> int:
    once = generate_config(SocdSettings())
    duration = generate_config(SocdSettings(mode="duration", duration_ms=33))
    disabled = generate_config(SocdSettings(enabled=False))
    assert once.count("tap-vkey out-") == 4
    assert "hold-for-duration" not in once
    assert duration.count("hold-for-duration 33 out-") == 4
    assert "@w-on-release @s-on-release" in duration
    assert "@a-on-release @d-on-release" in duration
    assert "defvirtualkeys" not in disabled

    for label, text in (("once", once), ("33ms", duration), ("off", disabled)):
        valid, message = validate_config(root, text)
        if not valid:
            print(f"{label}: {message}")
            return 1
    print("self-test: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--screenshot")
    args, _ = parser.parse_known_args()
    root = app_dir()

    if args.self_test:
        return run_self_test(root)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow(root)
    window.show()

    if args.screenshot:
        screenshot_path = Path(args.screenshot).resolve()

        def capture() -> None:
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(screenshot_path))
            app.quit()

        QTimer.singleShot(800, capture)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

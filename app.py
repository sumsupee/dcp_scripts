#!/usr/bin/env python3
"""Cross-platform desktop interface for MP4-to-DCP.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from tool_discovery import bundled_tools_root


APP_NAME = "MP4-to-DCP"
MAX_LOG_BLOCKS = 4000


def application_resources() -> Path:
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parent.name == "MacOS":
        return executable.parent.parent / "Resources"
    if getattr(sys, "frozen", False):
        return executable.parent
    return Path(__file__).resolve().parent


def converter_command() -> tuple[str, list[str]]:
    """Return the converter program and its fixed leading arguments."""
    # Do not resolve a virtual-environment symlink when previewing from source.
    executable = Path(sys.executable).absolute()
    suffix = ".exe" if os.name == "nt" else ""
    packaged_cli = executable.parent / f"mp4_to_dcp_cli{suffix}"
    if packaged_cli.is_file():
        return os.fspath(packaged_cli), []

    script = Path(__file__).resolve().parent / "mp4_to_dcp.py"
    return os.fspath(executable), [os.fspath(script)]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.process: QProcess | None = None
        self.output_buffer = ""
        self.last_output_dir: Path | None = None

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(760, 650)
        self._build_menu()
        self._build_ui()
        self._set_idle()

    def _build_menu(self) -> None:
        help_menu = self.menuBar().addMenu("&Help")
        licenses = QAction("Licenses and notices", self)
        licenses.triggered.connect(self.show_licenses)
        help_menu.addAction(licenses)

        about = QAction(f"About {APP_NAME}", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _path_picker(self, line_edit: QLineEdit, callback) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, 1)
        button = QPushButton("Browse…")
        button.clicked.connect(callback)
        layout.addWidget(button)
        return container

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        form = QFormLayout()

        self.input_edit = QLineEdit()
        form.addRow("Source video:", self._path_picker(self.input_edit, self.pick_input))

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Defaults to the source filename")
        form.addRow("DCP title:", self.name_edit)

        self.output_edit = QLineEdit(os.fspath(Path.cwd()))
        form.addRow(
            "Output folder:", self._path_picker(self.output_edit, self.pick_output)
        )

        self.content_combo = QComboBox()
        self.content_combo.addItem("Trailer (TLR)", "trailer")
        self.content_combo.addItem("Feature (FTR)", "feature")
        form.addRow("Content type:", self.content_combo)

        self.format_combo = QComboBox()
        self.format_combo.addItem("Flat and Scope", "both")
        self.format_combo.addItem("Flat (1.85:1)", "flat")
        self.format_combo.addItem("Scope (2.39:1)", "scope")
        form.addRow("DCP format:", self.format_combo)

        self.audio_mode_combo = QComboBox()
        self.audio_mode_combo.addItem("Automatic 5.1 surround", "auto")
        self.audio_mode_combo.addItem("Preserve mono/stereo fronts", "preserve")
        self.audio_mode_combo.setToolTip(
            "Automatic uses a restrained FFT upmix for stereo and leaves generated "
            "LFE silent. Native 5.1 is always preserved."
        )
        form.addRow("Audio processing:", self.audio_mode_combo)

        self.audio_stream_spin = QSpinBox()
        self.audio_stream_spin.setRange(0, 99)
        self.audio_stream_spin.setValue(0)
        self.audio_stream_spin.setToolTip(
            "Zero-based audio stream number; use 0 for the first soundtrack."
        )
        form.addRow("Audio stream:", self.audio_stream_spin)

        self.lufs_spin = QDoubleSpinBox()
        self.lufs_spin.setRange(-70.0, -5.0)
        self.lufs_spin.setDecimals(1)
        self.lufs_spin.setValue(-24.0)
        self.lufs_spin.setSuffix(" LUFS")
        form.addRow("Target loudness:", self.lufs_spin)

        self.peak_spin = QDoubleSpinBox()
        self.peak_spin.setRange(-9.0, 0.0)
        self.peak_spin.setDecimals(1)
        self.peak_spin.setValue(-2.0)
        self.peak_spin.setSuffix(" dBTP")
        form.addRow("Maximum true peak:", self.peak_spin)

        self.overwrite_check = QCheckBox("Replace existing project outputs")
        self.keep_master_check = QCheckBox("Keep normalized MKV master")
        checks = QWidget()
        checks_layout = QVBoxLayout(checks)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.addWidget(self.overwrite_check)
        checks_layout.addWidget(self.keep_master_check)
        form.addRow("Options:", checks)
        outer.addLayout(form)

        controls = QHBoxLayout()
        self.start_button = QPushButton("Create DCP")
        self.start_button.clicked.connect(self.start_conversion)
        controls.addWidget(self.start_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_conversion)
        controls.addWidget(self.cancel_button)
        self.open_button = QPushButton("Open output folder")
        self.open_button.clicked.connect(self.open_output)
        controls.addWidget(self.open_button)
        controls.addStretch(1)
        outer.addLayout(controls)

        self.status_label = QLabel()
        outer.addWidget(self.status_label)
        self.progress = QProgressBar()
        outer.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.log.setPlaceholderText("Conversion output will appear here.")
        outer.addWidget(self.log, 1)
        self.setCentralWidget(central)

    def pick_input(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select source video",
            self.input_edit.text() or os.fspath(Path.home()),
            "Video files (*.mp4 *.mov *.mkv *.m4v);;All files (*)",
        )
        if selected:
            self.input_edit.setText(selected)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(selected).stem)

    def pick_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select output folder",
            self.output_edit.text() or os.fspath(Path.home()),
        )
        if selected:
            self.output_edit.setText(selected)

    def _arguments(self) -> list[str] | None:
        input_file = Path(self.input_edit.text().strip()).expanduser()
        if not input_file.is_file():
            QMessageBox.warning(self, APP_NAME, "Select an existing source video.")
            return None

        output_text = self.output_edit.text().strip()
        if not output_text:
            QMessageBox.warning(self, APP_NAME, "Select an output folder.")
            return None
        output_dir = Path(output_text).expanduser().resolve()
        self.last_output_dir = output_dir

        arguments = [
            os.fspath(input_file.resolve()),
            "--output-dir",
            os.fspath(output_dir),
            "--content-type",
            str(self.content_combo.currentData()),
            "--format",
            str(self.format_combo.currentData()),
            "--audio-mode",
            str(self.audio_mode_combo.currentData()),
            "--audio-stream",
            str(self.audio_stream_spin.value()),
            "--target-lufs",
            str(self.lufs_spin.value()),
            "--max-true-peak",
            str(self.peak_spin.value()),
        ]
        name = self.name_edit.text().strip()
        if name:
            arguments.extend(["--name", name])
        if self.overwrite_check.isChecked():
            answer = QMessageBox.question(
                self,
                "Confirm replacement",
                "Existing project outputs with the same names will be deleted. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            arguments.append("--overwrite")
        if self.keep_master_check.isChecked():
            arguments.append("--keep-master")
        return arguments

    def start_conversion(self) -> None:
        if self.process is not None:
            return
        arguments = self._arguments()
        if arguments is None:
            return

        program, prefix = converter_command()
        self.log.clear()
        self.output_buffer = ""
        self._append_log(f"Starting: {program}")

        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        tools_root = bundled_tools_root()
        if tools_root.is_dir():
            environment.insert("MP4_DCP_TOOLS_DIR", os.fspath(tools_root))
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(self.read_process_output)
        process.finished.connect(self.process_finished)
        process.errorOccurred.connect(self.process_error)
        self.process = process
        self._set_running()
        process.start(program, prefix + arguments)

    def read_process_output(self) -> None:
        if self.process is None:
            return
        chunk = bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        self.output_buffer += chunk.replace("\r\n", "\n").replace("\r", "\n")
        lines = self.output_buffer.split("\n")
        self.output_buffer = lines.pop()
        for line in lines:
            self._append_log(line)

    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def process_finished(self, exit_code: int, _exit_status) -> None:
        if self.output_buffer:
            self._append_log(self.output_buffer)
            self.output_buffer = ""
        succeeded = exit_code == 0
        self._append_log("Conversion completed." if succeeded else "Conversion failed.")
        self.status_label.setText("Complete" if succeeded else f"Failed ({exit_code})")
        self.process.deleteLater()
        self.process = None
        self._set_idle(success=succeeded)

    def process_error(self, error) -> None:
        if self.process is not None:
            self._append_log(f"Could not run converter: {self.process.errorString()}")

    def cancel_conversion(self) -> None:
        if self.process is None:
            return
        self.status_label.setText("Cancelling…")
        process_id = int(self.process.processId())
        if os.name == "nt" and process_id:
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.process.terminate()
        QTimer.singleShot(3000, self._force_kill_if_running)

    def _force_kill_if_running(self) -> None:
        if self.process is not None:
            self.process.kill()

    def _set_running(self) -> None:
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.open_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status_label.setText("Processing…")

    def _set_idle(self, success: bool = False) -> None:
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(success and self.last_output_dir is not None)
        self.progress.setRange(0, 1)
        self.progress.setValue(1 if success else 0)
        if not success and self.process is None and not self.status_label.text():
            self.status_label.setText("Ready")

    def open_output(self) -> None:
        if self.last_output_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(self.last_output_dir)))

    def show_licenses(self) -> None:
        resources = application_resources()
        candidates = [
            resources / "THIRD_PARTY_NOTICES.md",
            resources / "licenses" / "mp4-to-dcp" / "COPYING",
            resources / "LICENSE",
        ]
        sections = []
        for path in candidates:
            if path.is_file():
                sections.append(path.read_text(encoding="utf-8", errors="replace"))
        viewer = QMessageBox(self)
        viewer.setWindowTitle("Licenses and notices")
        viewer.setText("MP4-to-DCP is free software under GPL-2.0-or-later.")
        viewer.setDetailedText("\n\n".join(sections) or "License files are missing.")
        viewer.exec()

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            APP_NAME,
            "MP4-to-DCP\n\nCreates 5.1 SMPTE 2K DCPs with conservative "
            "automatic upmixing and loudness normalization. Automatic upmixes "
            "must be auditioned before delivery.\n\nGPL-2.0-or-later; "
            "absolutely no warranty.",
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.process is None:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "A conversion is running. Cancel it and exit?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.cancel_conversion()
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

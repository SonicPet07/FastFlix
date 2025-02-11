# -*- coding: utf-8 -*-
import logging

from box import Box
from PySide6 import QtCore, QtWidgets, QtGui

from fastflix.encoders.common.setting_panel import SettingPanel
from fastflix.language import t
from fastflix.models.encode import VCEEncCAV1Settings
from fastflix.models.fastflix_app import FastFlixApp
from fastflix.shared import link
from fastflix.resources import get_icon, loading_movie


logger = logging.getLogger("fastflix")

presets = ["balanced", "fast", "slow", "slower"]

recommended_bitrates = [
    "200k     (320x240p @ 30fps)",
    "300k     (640x360p @ 30fps)",
    "1000k   (640x480p @ 30fps)",
    "1750k   (1280x720p @ 30fps)",
    "2500k   (1280x720p @ 60fps)",
    "4000k   (1920x1080p @ 30fps)",
    "5000k   (1920x1080p @ 60fps)",
    "7000k   (2560x1440p @ 30fps)",
    "10000k (2560x1440p @ 60fps)",
    "15000k (3840x2160p @ 30fps)",
    "20000k (3840x2160p @ 60fps)",
    "Custom",
]

recommended_crfs = [
    "28",
    "27",
    "26",
    "25",
    "24",
    "23",
    "22",
    "21",
    "20",
    "19",
    "18",
    "17",
    "16",
    "15",
    "14",
    "Custom",
]


def get_breaker():
    breaker_line = QtWidgets.QWidget()
    breaker_line.setMaximumHeight(2)
    breaker_line.setStyleSheet("background-color: #ccc; margin: auto 0; padding: auto 0;")
    return breaker_line


class VCEENCC(SettingPanel):
    profile_name = "vceencc_av1"
    hdr10plus_signal = QtCore.Signal(str)
    hdr10plus_ffmpeg_signal = QtCore.Signal(str)

    def __init__(self, parent, main, app: FastFlixApp):
        super().__init__(parent, main, app)
        self.main = main
        self.app = app

        grid = QtWidgets.QGridLayout()

        self.widgets = Box(mode=None)

        self.mode = "Bitrate"
        self.updating_settings = False

        grid.addLayout(self.init_modes(), 0, 2, 4, 4)
        grid.addLayout(self._add_custom(title="Custom VCEEncC options", disable_both_passes=True), 10, 0, 1, 6)
        grid.addLayout(self.init_preset(), 0, 0, 1, 2)
        grid.addLayout(self.init_bitrate_mode(), 1, 0, 1, 2)
        grid.addLayout(self.init_mv_precision(), 2, 0, 1, 2)
        grid.addLayout(self.init_pre(), 3, 0, 1, 2)

        breaker = QtWidgets.QHBoxLayout()
        breaker_label = QtWidgets.QLabel(t("Advanced"))
        breaker_label.setFont(QtGui.QFont("helvetica", 8, weight=55))

        breaker.addWidget(get_breaker(), stretch=1)
        breaker.addWidget(breaker_label, alignment=QtCore.Qt.AlignHCenter)
        breaker.addWidget(get_breaker(), stretch=1)

        grid.addLayout(breaker, 4, 0, 1, 6)

        qp_line = QtWidgets.QHBoxLayout()
        qp_line.addLayout(self.init_min_q())
        qp_line.addStretch(1)
        qp_line.addLayout(self.init_max_q())
        qp_line.addStretch(1)
        qp_line.addLayout(self.init_ref())
        qp_line.addStretch(1)
        qp_line.addLayout(self.init_level())
        qp_line.addStretch(1)
        qp_line.addLayout(self.init_decoder())
        qp_line.addStretch(1)
        qp_line.addLayout(self.init_metrics())
        grid.addLayout(qp_line, 6, 0, 1, 6)

        grid.addLayout(self.init_dhdr10_info(), 7, 2, 1, 4)

        self.ffmpeg_level = QtWidgets.QLabel()
        grid.addWidget(self.ffmpeg_level, 8, 2, 1, 4)

        grid.setRowStretch(9, 1)

        guide_label = QtWidgets.QLabel(
            link(
                "https://github.com/rigaya/VCEEnc/blob/master/VCEEncC_Options.en.md",
                t("VCEEncC Options"),
                app.fastflix.config.theme,
            )
        )

        warning_label = QtWidgets.QLabel()
        warning_label.setPixmap(QtGui.QIcon(get_icon("onyx-warning", self.app.fastflix.config.theme)).pixmap(22))

        guide_label.setAlignment(QtCore.Qt.AlignBottom)
        guide_label.setOpenExternalLinks(True)
        grid.addWidget(guide_label, 11, 0, 1, 4)
        grid.addWidget(warning_label, 11, 4, 1, 1, alignment=QtCore.Qt.AlignRight)
        grid.addWidget(QtWidgets.QLabel(t("VCEEncC AV1 Encoder is untested!")), 11, 5, 1, 1)

        self.setLayout(grid)
        self.hide()
        self.hdr10plus_signal.connect(self.done_hdr10plus_extract)
        self.hdr10plus_ffmpeg_signal.connect(lambda x: self.ffmpeg_level.setText(x))

    def init_preset(self):
        return self._add_combo_box(
            label="Preset",
            widget_name="preset",
            options=presets,
            tooltip="preset: The slower the preset, the better the compression and quality",
            connect="default",
            opt="preset",
        )

    def init_pre(self):
        layout = QtWidgets.QHBoxLayout()
        layout.addLayout(
            self._add_check_box(
                label="VBAQ", widget_name="vbaq", opt="vbaq", tooltip="Variance Based Adaptive Quantization"
            )
        )
        layout.addLayout(self._add_check_box(label="Pre Encode", widget_name="pre_encode", opt="pre_encode"))
        layout.addLayout(self._add_check_box(label="Pre Analysis", widget_name="pre_analysis", opt="pre_analysis"))
        return layout

    def init_mv_precision(self):
        return self._add_combo_box(
            label="Motion vector accuracy",
            tooltip="Q-pel is highest precision",
            widget_name="mv_precision",
            options=["q-pel", "half-pel", "full-pel"],
            opt="mv_precision",
        )

    def init_level(self):
        layout = self._add_combo_box(
            label="Level",
            tooltip="Set the encoding level restriction",
            widget_name="level",
            options=[
                t("Auto"),
                "1.0",
                "2.0",
                "2.1",
                "3.0",
                "3.1",
                "4.0",
                "4.1",
                "5.0",
                "5.1",
                "5.2",
                "6.0",
                "6.1",
                "6.2",
            ],
            opt="level",
        )
        self.widgets.level.setMinimumWidth(60)
        return layout

    @staticmethod
    def _qp_range():
        return [str(x) for x in range(0, 52)]

    def init_min_q(self):
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel(t("Min Q")))
        layout.addWidget(
            self._add_combo_box(widget_name="min_q", options=["I"] + self._qp_range(), min_width=45, opt="min_q")
        )
        return layout

    def init_max_q(self):
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel(t("Max Q")))
        layout.addWidget(
            self._add_combo_box(widget_name="max_q", options=["I"] + self._qp_range(), min_width=45, opt="max_q")
        )
        return layout

    def init_bitrate_mode(self):
        layout = QtWidgets.QHBoxLayout()
        layout.addWidget(QtWidgets.QLabel(t("Bitrate Mode")))
        layout.addWidget(
            self._add_combo_box(
                widget_name="bitrate_mode",
                options=["vbr", "cbr", "cbrhq", "vbrhq", "qvbr"],
                min_width=45,
                opt="bitrate_mode",
            )
        )
        return layout

    def init_ref(self):
        return self._add_combo_box(
            widget_name="ref",
            label="Ref Frames",
            options=[t("Auto")] + [str(x) for x in range(17)],
            opt="ref",
            min_width=60,
        )

    def init_decoder(self):
        return self._add_combo_box(
            widget_name="decoder",
            label="Decoder",
            options=["Hardware", "Software"],
            opt="decoder",
            tooltip="Hardware: use libavformat + hardware decoder for input\nSoftware: use avcodec + software decoder",
            min_width=80,
        )

    def init_dhdr10_info(self):
        layout = self._add_file_select(
            label="HDR10+ Metadata",
            widget_name="hdr10plus_metadata",
            button_action=lambda: self.dhdr10_update(),
            tooltip="dhdr10_info: Path to HDR10+ JSON metadata file",
        )
        self.labels["hdr10plus_metadata"].setFixedWidth(200)
        self.extract_button = QtWidgets.QPushButton(t("Extract HDR10+"))
        self.extract_button.hide()
        self.extract_button.clicked.connect(self.extract_hdr10plus)

        self.extract_label = QtWidgets.QLabel(self)
        self.extract_label.hide()
        self.movie = QtGui.QMovie(loading_movie)
        self.movie.setScaledSize(QtCore.QSize(25, 25))
        self.extract_label.setMovie(self.movie)

        layout.addWidget(self.extract_button)
        layout.addWidget(self.extract_label)

        warning_label = QtWidgets.QLabel()
        warning_label.setPixmap(QtGui.QIcon(get_icon("onyx-warning", self.app.fastflix.config.theme)).pixmap(22))
        layout.addWidget(warning_label)
        layout.addWidget(QtWidgets.QLabel("7.12+"))

        return layout

    def init_metrics(self):
        return self._add_check_box(
            widget_name="metrics",
            opt="metrics",
            label="Metrics",
            tooltip="Calculate PSNR and SSIM and show in the encoder output",
        )

    def init_modes(self):
        layout = self._add_modes(recommended_bitrates, recommended_crfs, qp_name="cqp")
        self.widgets.cqp.setFixedWidth(250)
        return layout

    def mode_update(self):
        self.widgets.custom_cqp.setDisabled(self.widgets.cqp.currentText() != "Custom")
        self.widgets.custom_bitrate.setDisabled(self.widgets.bitrate.currentText() != "Custom")
        self.main.build_commands()

    def setting_change(self, update=True):
        if self.updating_settings:
            return
        self.updating_settings = True

        if update:
            self.main.page_update()
        self.updating_settings = False

    def update_video_encoder_settings(self):
        settings = VCEEncCAV1Settings(
            preset=self.widgets.preset.currentText().split("-")[0].strip(),
            mv_precision=self.widgets.mv_precision.currentText(),
            max_q=self.widgets.max_q.currentText() if self.widgets.max_q.currentIndex() != 0 else None,
            min_q=self.widgets.min_q.currentText() if self.widgets.min_q.currentIndex() != 0 else None,
            extra=self.ffmpeg_extras,
            metrics=self.widgets.metrics.isChecked(),
            level=self.widgets.level.currentText() if self.widgets.level.currentIndex() != 0 else None,
            ref=self.widgets.ref.currentText() if self.widgets.ref.currentIndex() != 0 else None,
            pre_encode=self.widgets.pre_encode.isChecked(),
            pre_analysis=self.widgets.pre_analysis.isChecked(),
            vbaq=self.widgets.vbaq.isChecked(),
            decoder=self.widgets.decoder.currentText(),
            hdr10plus_metadata=self.widgets.hdr10plus_metadata.text().strip(),
            bitrate_mode=self.widgets.bitrate_mode.currentText(),
        )

        encode_type, q_value = self.get_mode_settings()
        settings.cqp = q_value if encode_type == "qp" else None
        settings.bitrate = q_value if encode_type == "bitrate" else None
        self.app.fastflix.current_video.video_settings.video_encoder_settings = settings

    def set_mode(self, x):
        self.mode = x.text()
        self.widgets.min_q.setEnabled(self.mode.lower() == "bitrate")
        self.widgets.max_q.setEnabled(self.mode.lower() == "bitrate")
        self.main.build_commands()

    def new_source(self):
        if not self.app.fastflix.current_video:
            return
        super().new_source()
        if self.app.fastflix.current_video.hdr10_plus:
            self.extract_button.show()
        else:
            self.extract_button.hide()

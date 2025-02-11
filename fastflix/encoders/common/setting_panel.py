# -*- coding: utf-8 -*-
import logging
from typing import List, Tuple, Union
from pathlib import Path

from box import Box
from PySide6 import QtGui, QtWidgets, QtCore

from fastflix.exceptions import FastFlixInternalException
from fastflix.language import t
from fastflix.models.fastflix_app import FastFlixApp
from fastflix.widgets.background_tasks import ExtractHDR10
from fastflix.resources import group_box_style, get_icon

logger = logging.getLogger("fastflix")

ffmpeg_extra_command = ""

pix_fmts = ["8-bit: yuv420p", "10-bit: yuv420p10le", "12-bit: yuv420p12le"]


class SettingPanel(QtWidgets.QWidget):
    def __init__(self, parent, main, app: FastFlixApp, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.main = main
        self.app = app
        self.widgets = Box()
        self.labels = Box()
        self.opts = Box()
        self.only_int = QtGui.QIntValidator()
        self.only_float = QtGui.QDoubleValidator()

    def paintEvent(self, event):
        o = QtWidgets.QStyleOption()
        o.initFrom(self)
        p = QtGui.QPainter(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, o, p, self)

    @staticmethod
    def translate_tip(tooltip):
        return "\n".join([t(x) for x in tooltip.split("\n") if x.strip()])

    def determine_default(self, widget_name, opt, items: List, raise_error: bool = False):
        if widget_name == "pix_fmt":
            items = [x.split(":")[1].strip() for x in items]
        elif widget_name in ("crf", "qp"):
            if not opt:
                return 6
            opt = str(opt)
            items = [x.split("(")[0].split("-")[0].strip() for x in items]
        elif widget_name == "bitrate":
            if not opt:
                return 5
            items = [x.split("(")[0].split("-")[0].strip() for x in items]
        elif widget_name == "gpu":
            if opt == -1:
                return 0
        if isinstance(opt, str):
            try:
                return items.index(opt)
            except Exception:
                if raise_error:
                    raise FastFlixInternalException
                else:
                    logger.error(f"Could not set default for {widget_name} to {opt} as it's not in the list: {items}")
                return 0
        if isinstance(opt, bool):
            return int(opt)
        return opt

    def _add_combo_box(
        self,
        options,
        widget_name,
        label=None,
        opt=None,
        connect="default",
        enabled=True,
        default=0,
        tooltip="",
        min_width=None,
        width=None,
    ):
        layout = QtWidgets.QHBoxLayout()
        if label:
            self.labels[widget_name] = QtWidgets.QLabel(t(label))
            if tooltip:
                self.labels[widget_name].setToolTip(self.translate_tip(tooltip))

        self.widgets[widget_name] = QtWidgets.QComboBox()
        self.widgets[widget_name].addItems(options)
        if min_width:
            self.widgets[widget_name].setMinimumWidth(min_width)
        if width:
            self.widgets[widget_name].setFixedWidth(width)

        if opt:
            default = self.determine_default(
                widget_name, self.app.fastflix.config.encoder_opt(self.profile_name, opt), options
            )
            self.opts[widget_name] = opt
        self.widgets[widget_name].setCurrentIndex(default or 0)
        self.widgets[widget_name].setDisabled(not enabled)
        new_width = self.widgets[widget_name].minimumSizeHint().width() + 20
        if new_width > self.widgets[widget_name].view().width():
            self.widgets[widget_name].view().setFixedWidth(new_width)
        if tooltip:
            self.widgets[widget_name].setToolTip(self.translate_tip(tooltip))
        if connect:
            if connect == "default":
                self.widgets[widget_name].currentIndexChanged.connect(
                    lambda: self.main.page_update(build_thumbnail=False)
                )
            elif connect == "self":
                self.widgets[widget_name].currentIndexChanged.connect(lambda: self.page_update())
            else:
                self.widgets[widget_name].currentIndexChanged.connect(connect)

        if not label:
            return self.widgets[widget_name]

        layout.addWidget(self.labels[widget_name])
        layout.addWidget(self.widgets[widget_name])

        return layout

    def _add_text_box(
        self,
        label,
        widget_name,
        opt=None,
        connect="default",
        enabled=True,
        default="",
        tooltip="",
        validator=None,
        width=None,
    ):
        layout = QtWidgets.QHBoxLayout()
        self.labels[widget_name] = QtWidgets.QLabel(t(label))
        if tooltip:
            self.labels[widget_name].setToolTip(self.translate_tip(tooltip))

        self.widgets[widget_name] = QtWidgets.QLineEdit()

        if opt:
            default = str(self.app.fastflix.config.encoder_opt(self.profile_name, opt)) or default
            self.opts[widget_name] = opt
        self.widgets[widget_name].setText(default)
        self.widgets[widget_name].setDisabled(not enabled)
        if tooltip:
            self.widgets[widget_name].setToolTip(self.translate_tip(tooltip))
        if connect:
            if connect == "default":
                self.widgets[widget_name].textChanged.connect(lambda: self.main.page_update(build_thumbnail=False))
            elif connect == "self":
                self.widgets[widget_name].textChanged.connect(lambda: self.page_update())
            else:
                self.widgets[widget_name].textChanged.connect(connect)

        if validator:
            if validator == "int":
                self.widgets[widget_name].setValidator(self.only_int)
            if validator == "float":
                self.widgets[widget_name].setValidator(self.only_int)

        if width:
            self.widgets[widget_name].setFixedWidth(width)

        layout.addWidget(self.labels[widget_name])
        layout.addWidget(self.widgets[widget_name])

        return layout

    def _add_check_box(self, label, widget_name, opt, connect="default", enabled=True, tooltip=""):
        layout = QtWidgets.QHBoxLayout()

        self.widgets[widget_name] = QtWidgets.QCheckBox(t(label))
        self.opts[widget_name] = opt
        self.widgets[widget_name].setChecked(self.app.fastflix.config.encoder_opt(self.profile_name, opt))
        self.widgets[widget_name].setDisabled(not enabled)
        if tooltip:
            self.widgets[widget_name].setToolTip(self.translate_tip(tooltip))
        if connect:
            if connect == "default":
                self.widgets[widget_name].toggled.connect(lambda: self.main.page_update(build_thumbnail=False))
            elif connect == "self":
                self.widgets[widget_name].toggled.connect(lambda: self.page_update())
            else:
                self.widgets[widget_name].toggled.connect(connect)

        # layout.addWidget(self.labels[widget_name])
        layout.addWidget(self.widgets[widget_name])

        return layout

    def _add_custom(self, title="Custom ffmpeg options", connect="default", disable_both_passes=False):
        layout = QtWidgets.QHBoxLayout()
        self.labels.ffmpeg_options = QtWidgets.QLabel(t(title))
        self.labels.ffmpeg_options.setToolTip(t("Extra flags or options, cannot modify existing settings"))
        layout.addWidget(self.labels.ffmpeg_options)
        self.ffmpeg_extras_widget = QtWidgets.QLineEdit()
        self.ffmpeg_extras_widget.setText(ffmpeg_extra_command)
        self.widgets["extra_both_passes"] = QtWidgets.QCheckBox(t("Both Passes"))
        self.opts["extra_both_passes"] = "extra_both_passes"

        if connect and connect != "default":
            self.ffmpeg_extras_widget.disconnect()
            if connect == "self":
                connect = lambda: self.page_update()
            self.ffmpeg_extras_widget.textChanged.connect(connect)
            self.widgets["extra_both_passes"].toggled.connect(connect)
        else:
            self.ffmpeg_extras_widget.textChanged.connect(lambda: self.ffmpeg_extra_update())
            self.widgets["extra_both_passes"].toggled.connect(lambda: self.main.page_update(build_thumbnail=False))
        layout.addWidget(self.ffmpeg_extras_widget)
        if not disable_both_passes:
            layout.addWidget(self.widgets["extra_both_passes"])
        return layout

    def _add_file_select(self, label, widget_name, button_action, connect="default", enabled=True, tooltip=""):
        layout = QtWidgets.QHBoxLayout()
        self.labels[widget_name] = QtWidgets.QLabel(t(label))
        self.labels[widget_name].setToolTip(tooltip)

        self.widgets[widget_name] = QtWidgets.QLineEdit()
        self.widgets[widget_name].setDisabled(not enabled)
        self.widgets[widget_name].setToolTip(tooltip)

        self.opts[widget_name] = widget_name

        if connect:
            if connect == "default":
                self.widgets[widget_name].textChanged.connect(lambda: self.main.page_update(build_thumbnail=False))
            elif connect == "self":
                self.widgets[widget_name].textChanged.connect(lambda: self.page_update())
            else:
                self.widgets[widget_name].textChanged.connect(connect)

        button = QtWidgets.QPushButton(icon=QtGui.QIcon(get_icon("onyx-file-search", self.app.fastflix.config.theme)))
        button.clicked.connect(button_action)

        layout.addWidget(self.labels[widget_name])
        layout.addWidget(self.widgets[widget_name])
        layout.addWidget(button)
        return layout

    def extract_hdr10plus(self):
        self.extract_button.hide()
        self.extract_label.show()
        self.movie.start()
        # self.extracting_hdr10 = True
        self.extract_thrad = ExtractHDR10(
            self.app, self.main, signal=self.hdr10plus_signal, ffmpeg_signal=self.hdr10plus_ffmpeg_signal
        )
        self.extract_thrad.start()

    def done_hdr10plus_extract(self, metadata: str):
        self.extract_button.show()
        self.extract_label.hide()
        self.movie.stop()
        if Path(metadata).exists():
            self.widgets.hdr10plus_metadata.setText(metadata)
        self.ffmpeg_level.setText("")

    def dhdr10_update(self):
        dirname = Path(self.widgets.hdr10plus_metadata.text()).parent
        if not dirname.exists():
            dirname = Path()
        filename = QtWidgets.QFileDialog.getOpenFileName(
            self, caption="hdr10_metadata", dir=str(dirname), filter="HDR10+ Metadata (*.json)"
        )
        if not filename or not filename[0]:
            return
        self.widgets.hdr10plus_metadata.setText(filename[0])
        self.main.page_update()

    def _add_modes(self, recommended_bitrates, recommended_qps, qp_name="crf", add_qp=True, disable_custom_qp=False):
        self.recommended_bitrates = recommended_bitrates
        self.recommended_qps = recommended_qps
        self.qp_name = qp_name
        layout = QtWidgets.QGridLayout()
        qp_group_box = QtWidgets.QGroupBox()
        qp_group_box.setStyleSheet(group_box_style())
        qp_box_layout = QtWidgets.QHBoxLayout()
        bitrate_group_box = QtWidgets.QGroupBox()
        bitrate_group_box.setStyleSheet(group_box_style())
        bitrate_box_layout = QtWidgets.QHBoxLayout()
        self.widgets.mode = QtWidgets.QButtonGroup()
        self.widgets.mode.buttonClicked.connect(self.set_mode)

        self.bitrate_radio = QtWidgets.QRadioButton("Bitrate")
        self.bitrate_radio.setFixedWidth(80)
        self.widgets.mode.addButton(self.bitrate_radio)
        self.widgets.bitrate = QtWidgets.QComboBox()
        # self.widgets.bitrate.setFixedWidth(250)
        self.widgets.bitrate.addItems(recommended_bitrates)
        config_opt = self.app.fastflix.config.encoder_opt(self.profile_name, "bitrate")
        custom_bitrate = False
        try:
            default_bitrate_index = self.determine_default(
                "bitrate", config_opt, recommended_bitrates, raise_error=True
            )
        except FastFlixInternalException:
            custom_bitrate = True
            self.widgets.bitrate.setCurrentText("Custom")
        else:
            self.widgets.bitrate.setCurrentIndex(default_bitrate_index)
        self.widgets.bitrate.currentIndexChanged.connect(lambda: self.mode_update())
        self.widgets.custom_bitrate = QtWidgets.QLineEdit("3000" if not custom_bitrate else config_opt)
        self.widgets.custom_bitrate.setFixedWidth(100)
        self.widgets.custom_bitrate.setEnabled(custom_bitrate)
        self.widgets.custom_bitrate.textChanged.connect(lambda: self.main.build_commands())
        self.widgets.custom_bitrate.setValidator(self.only_int)
        bitrate_box_layout.addWidget(self.bitrate_radio)
        bitrate_box_layout.addWidget(self.widgets.bitrate, 1)
        bitrate_box_layout.addStretch(1)
        bitrate_box_layout.addStretch(1)
        bitrate_box_layout.addWidget(QtWidgets.QLabel("Custom:"))
        bitrate_box_layout.addWidget(self.widgets.custom_bitrate)
        bitrate_box_layout.addWidget(QtWidgets.QLabel("k"))

        qp_help = (
            f"{qp_name.upper()} {t('is extremely source dependant')},\n"
            f"{t('the resolution-to-')}{qp_name.upper()}{t('are mere suggestions!')}"
        )
        self.qp_radio = QtWidgets.QRadioButton(qp_name.upper())
        self.qp_radio.setChecked(True)
        self.qp_radio.setFixedWidth(80)
        self.qp_radio.setToolTip(qp_help)
        self.widgets.mode.addButton(self.qp_radio)

        self.widgets[qp_name] = QtWidgets.QComboBox()
        self.widgets[qp_name].setToolTip(qp_help)
        self.widgets[qp_name].addItems(recommended_qps)
        custom_qp = False
        qp_value = self.app.fastflix.config.encoder_opt(self.profile_name, qp_name)
        try:
            default_qp_index = self.determine_default(qp_name, qp_value, recommended_qps, raise_error=True)
        except FastFlixInternalException:
            if not disable_custom_qp:
                custom_qp = True
                self.widgets[qp_name].setCurrentText("Custom")
        else:
            if default_qp_index is not None:
                self.widgets[qp_name].setCurrentIndex(default_qp_index)

        self.widgets[qp_name].currentIndexChanged.connect(lambda: self.mode_update())
        if not disable_custom_qp:
            self.widgets[f"custom_{qp_name}"] = QtWidgets.QLineEdit("30" if not custom_qp else str(qp_value))
            self.widgets[f"custom_{qp_name}"].setFixedWidth(100)
            self.widgets[f"custom_{qp_name}"].setEnabled(custom_qp)
            self.widgets[f"custom_{qp_name}"].setValidator(self.only_float)
            self.widgets[f"custom_{qp_name}"].textChanged.connect(lambda: self.main.build_commands())

        if config_opt:
            self.mode = "Bitrate"
            self.qp_radio.setChecked(False)
            self.bitrate_radio.setChecked(True)
        qp_box_layout.addWidget(self.qp_radio)
        qp_box_layout.addWidget(self.widgets[qp_name], 1)
        qp_box_layout.addStretch(1)
        qp_box_layout.addStretch(1)
        if disable_custom_qp:
            qp_box_layout.addStretch(1)
        else:
            qp_box_layout.addWidget(QtWidgets.QLabel("Custom:"))
            qp_box_layout.addWidget(self.widgets[f"custom_{qp_name}"])
        qp_box_layout.addWidget(QtWidgets.QLabel("  "))

        bitrate_group_box.setLayout(bitrate_box_layout)
        qp_group_box.setLayout(qp_box_layout)

        layout.addWidget(qp_group_box, 0, 0)
        layout.addWidget(bitrate_group_box, 1, 0)

        if not add_qp:
            qp_group_box.hide()

        return layout

    @property
    def ffmpeg_extras(self):
        return ffmpeg_extra_command

    def ffmpeg_extra_update(self):
        global ffmpeg_extra_command
        ffmpeg_extra_command = self.ffmpeg_extras_widget.text().strip()
        self.main.page_update(build_thumbnail=False)

    def new_source(self):
        if not self.app.fastflix.current_video or not self.app.fastflix.current_video.streams:
            return

    def update_profile(self):
        global ffmpeg_extra_command
        for widget_name, opt in self.opts.items():
            if isinstance(self.widgets[widget_name], QtWidgets.QComboBox):
                default = self.determine_default(
                    widget_name,
                    self.app.fastflix.config.encoder_opt(self.profile_name, opt),
                    [self.widgets[widget_name].itemText(i) for i in range(self.widgets[widget_name].count())],
                )
                if default is not None:
                    self.widgets[widget_name].setCurrentIndex(default)
            elif isinstance(self.widgets[widget_name], QtWidgets.QCheckBox):
                checked = self.app.fastflix.config.encoder_opt(self.profile_name, opt)
                if checked is not None:
                    self.widgets[widget_name].setChecked(checked)
            elif isinstance(self.widgets[widget_name], QtWidgets.QLineEdit):
                data = self.app.fastflix.config.encoder_opt(self.profile_name, opt)
                if widget_name in ("x265_params", "svtav1_params"):
                    data = ":".join(data)
                self.widgets[widget_name].setText(str(data) or "")
        try:
            bitrate = self.app.fastflix.config.encoder_opt(self.profile_name, "bitrate")
        except AttributeError:
            pass
        else:
            if bitrate:
                self.mode = "Bitrate"
                self.qp_radio.setChecked(False)
                self.bitrate_radio.setChecked(True)
                for i, rec in enumerate(self.recommended_bitrates):
                    if rec.startswith(bitrate):
                        self.widgets.bitrate.setCurrentIndex(i)
                        break
                else:
                    self.widgets.bitrate.setCurrentText("Custom")
                    self.widgets.custom_bitrate.setText(bitrate.rstrip("kKmMgGbB"))
            else:
                self.mode = self.qp_name
                self.qp_radio.setChecked(True)
                self.bitrate_radio.setChecked(False)
                qp = str(self.app.fastflix.config.encoder_opt(self.profile_name, self.qp_name))
                for i, rec in enumerate(self.recommended_qps):
                    if rec.startswith(qp):
                        self.widgets[self.qp_name].setCurrentIndex(i)
                        break
                else:
                    self.widgets[self.qp_name].setCurrentText("Custom")
                    self.widgets[f"custom_{self.qp_name}"].setText(qp)
        ffmpeg_extra_command = self.app.fastflix.config.encoder_opt(self.profile_name, "extra")
        self.ffmpeg_extras_widget.setText(ffmpeg_extra_command)

    def init_max_mux(self):
        return self._add_combo_box(
            label="Max Muxing Queue Size",
            tooltip='Useful when you have the "Too many packets buffered for output stream" error',
            widget_name="max_mux",
            options=["default", "1024", "2048", "4096", "8192"],
            opt="max_muxing_queue_size",
        )

    def reload(self):
        """This will reset the current settings to what is set in "current_video", useful for return from queue"""
        global ffmpeg_extra_command
        logger.debug("Update reload called")
        self.updating_settings = True
        for widget_name, opt in self.opts.items():
            data = getattr(self.app.fastflix.current_video.video_settings.video_encoder_settings, opt)
            if isinstance(self.widgets[widget_name], QtWidgets.QComboBox):
                if widget_name == "pix_fmt":
                    for fmt in pix_fmts:
                        if fmt.endswith(data):
                            self.widgets[widget_name].setCurrentText(fmt)
                if isinstance(data, int):
                    self.widgets[widget_name].setCurrentIndex(data)
                else:
                    self.widgets[widget_name].setCurrentText(data)
                    # Do smart check for cleaning up stuff

            elif isinstance(self.widgets[widget_name], QtWidgets.QCheckBox):
                self.widgets[widget_name].setChecked(data)
            elif isinstance(self.widgets[widget_name], QtWidgets.QLineEdit):
                if widget_name in ("x265_params", "svtav1_params"):
                    data = ":".join(data)
                self.widgets[widget_name].setText(str(data) or "")
        if getattr(self, "qp_radio", None):
            bitrate = getattr(self.app.fastflix.current_video.video_settings.video_encoder_settings, "bitrate", None)
            if bitrate:
                self.mode = "Bitrate"
                self.qp_radio.setChecked(False)
                self.bitrate_radio.setChecked(True)
                for i, rec in enumerate(self.recommended_bitrates):
                    if rec.startswith(bitrate):
                        self.widgets.bitrate.setCurrentIndex(i)
                        break
                else:
                    self.widgets.bitrate.setCurrentText("Custom")
                    self.widgets.custom_bitrate.setText(bitrate.rstrip("k"))
            else:
                self.mode = self.qp_name
                self.qp_radio.setChecked(True)
                self.bitrate_radio.setChecked(False)
                qp = str(getattr(self.app.fastflix.current_video.video_settings.video_encoder_settings, self.qp_name))
                for i, rec in enumerate(self.recommended_qps):
                    if rec.startswith(qp):
                        self.widgets[self.qp_name].setCurrentIndex(i)
                        break
                else:
                    self.widgets[self.qp_name].setCurrentText("Custom")
                    self.widgets[f"custom_{self.qp_name}"].setText(qp)
        ffmpeg_extra_command = self.app.fastflix.current_video.video_settings.video_encoder_settings.extra
        self.ffmpeg_extras_widget.setText(ffmpeg_extra_command)
        self.updating_settings = False

    def get_mode_settings(self) -> Tuple[str, Union[float, int, str]]:
        if self.mode.lower() == "bitrate":
            bitrate = self.widgets.bitrate.currentText()
            if bitrate.lower() == "custom":
                if not bitrate:
                    logger.error("No custom bitrate provided, defaulting to 3000k")
                    return "bitrate", "3000k"
                bitrate = self.widgets.custom_bitrate.text().lower().rstrip("k")
                bitrate += "k"
            else:
                bitrate = bitrate.split(" ", 1)[0]
            return "bitrate", bitrate
        else:
            qp_text = self.widgets[self.qp_name].currentText()
            if qp_text.lower() == "custom":
                custom_value = self.widgets[f"custom_{self.qp_name}"].text()
                if not custom_value:
                    logger.error("No value provided for custom QP/CRF value, defaulting to 30")
                    return "qp", 30
                custom_value = float(self.widgets[f"custom_{self.qp_name}"].text().rstrip("."))
                if custom_value.is_integer():
                    custom_value = int(custom_value)
                return "qp", custom_value
            else:
                return "qp", int(qp_text.split(" ", 1)[0])

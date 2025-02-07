#!/usr/bin/env python
# -*- coding: utf-8 -*-
import datetime
import logging
import math
import os
import random
import secrets
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Tuple, Union, Optional
from collections import namedtuple

import pkg_resources
import reusables
from box import Box
from pydantic import BaseModel, Field
from PySide6 import QtCore, QtGui, QtWidgets

from fastflix.encoders.common import helpers
from fastflix.exceptions import FastFlixInternalException, FlixError
from fastflix.flix import (
    detect_hdr10_plus,
    detect_interlaced,
    extract_attachments,
    generate_thumbnail_command,
    get_auto_crop,
    parse,
    parse_hdr_details,
    get_concat_item,
)
from fastflix.language import t
from fastflix.models.fastflix_app import FastFlixApp
from fastflix.models.video import Status, Video, VideoSettings, Crop
from fastflix.resources import (
    get_icon,
    main_icon,
    group_box_style,
    reset_button_style,
    onyx_convert_icon,
    onyx_queue_add_icon,
    get_text_color,
)
from fastflix.shared import error_message, message, time_to_number, yes_no_message, clean_file_string
from fastflix.windows_tools import show_windows_notification, prevent_sleep_mode, allow_sleep_mode
from fastflix.widgets.background_tasks import ThumbnailCreator
from fastflix.widgets.progress_bar import ProgressBar, Task
from fastflix.widgets.video_options import VideoOptions
from fastflix.widgets.windows.large_preview import LargePreview

logger = logging.getLogger("fastflix")

root = os.path.abspath(os.path.dirname(__file__))

only_int = QtGui.QIntValidator()

Request = namedtuple(
    "Request",
    ["request", "video_uuid", "command_uuid", "command", "work_dir", "log_name"],
    defaults=[None, None, None, None, None],
)

Response = namedtuple("Response", ["status", "video_uuid", "command_uuid"])


class CropWidgets(BaseModel):
    top: QtWidgets.QLineEdit = None
    bottom: QtWidgets.QLineEdit = None
    left: QtWidgets.QLineEdit = None
    right: QtWidgets.QLineEdit = None

    class Config:
        arbitrary_types_allowed = True


class ScaleWidgets(BaseModel):
    width: QtWidgets.QLineEdit = None
    height: QtWidgets.QLineEdit = None
    keep_aspect: QtWidgets.QCheckBox = None

    class Config:
        arbitrary_types_allowed = True


class MainWidgets(BaseModel):
    start_time: QtWidgets.QLineEdit = None
    end_time: QtWidgets.QLineEdit = None
    video_track: QtWidgets.QComboBox = None
    rotate: QtWidgets.QComboBox = None
    flip: QtWidgets.QComboBox = None
    crop: CropWidgets = Field(default_factory=CropWidgets)
    scale: ScaleWidgets = Field(default_factory=ScaleWidgets)
    remove_metadata: QtWidgets.QCheckBox = None
    chapters: QtWidgets.QCheckBox = None
    fast_time: QtWidgets.QComboBox = None
    preview: QtWidgets.QLabel = None
    convert_to: QtWidgets.QComboBox = None
    convert_button: QtWidgets.QPushButton = None
    deinterlace: QtWidgets.QCheckBox = None
    remove_hdr: QtWidgets.QCheckBox = None
    video_title: QtWidgets.QLineEdit = None
    profile_box: QtWidgets.QComboBox = None
    thumb_time: QtWidgets.QSlider = None
    thumb_key: QtWidgets.QCheckBox = None

    class Config:
        arbitrary_types_allowed = True

    def items(self):
        for key in dir(self):
            if key.startswith("_"):
                continue
            if key in ("crop", "scale"):
                for sub_field in dir(getattr(self, key)):
                    if sub_field.startswith("_"):
                        continue
                    yield sub_field, getattr(getattr(self, key), sub_field)
            else:
                yield key, getattr(self, key)


class Main(QtWidgets.QWidget):
    completed = QtCore.Signal(int)
    thumbnail_complete = QtCore.Signal(int)
    close_event = QtCore.Signal()
    status_update_signal = QtCore.Signal(tuple)
    thread_logging_signal = QtCore.Signal(str)

    def __init__(self, parent, app: FastFlixApp):
        super().__init__(parent)
        self.app: FastFlixApp = app
        self.setObjectName("Main")
        self.container = parent
        self.video: Video = Video(source=Path(), width=0, height=0, duration=0)

        self.initialized = False
        self.loading_video = True
        self.scale_updating = False
        self.last_thumb_hash = ""

        self.large_preview = LargePreview(self)

        self.notifier = Notifier(self, self.app, self.app.fastflix.status_queue)
        self.notifier.start()

        self.input_defaults = Box(scale=None, crop=None)
        self.initial_duration = 0

        self.temp_dir = self.get_temp_work_path()

        self.setAcceptDrops(True)

        self.input_video = None
        self.video_path_widget = QtWidgets.QLineEdit(t("No Source Selected"))
        motto = ""
        if self.app.fastflix.config.language == "eng":
            motto = random.choice(
                [
                    "Welcome to FastFlix!",
                    "Hope your encoding goes well!",
                    "<Drag and drop your vid here>",
                    "Encoding faster than the speed of light is against the law",
                    "4K HDR is important. Good content is importanter",
                    "Water is wet, the sky is blue, FastFlix is Free",
                    "Grab onto your trousers, it's time for an encode!",
                    "It's cold in here, lets warm up the room with a nice encoding",
                    "It's a good day to encode",
                    "Encode Hard",
                    "Where there's an encode, there's a way",
                    "Start your day off right with a nice encode",
                    "Encoding, encoding, always with the encoding!",
                    "Try VP9 this time, no wait, HEVC, or maybe a GIF?",
                    "Something, Something, Dark Theme",
                    "Where we're going, we don't need transcodes",
                    "May the FastFlix be with you",
                    "Handbrake didn't do it for ya?",
                    "Did you select the right audio track?",
                    "FastFlix? In this economy?",
                    "The name's Flix. FastFlix",
                    "It's pronounced gif",
                    "I'm not trying to convert you, just your video",
                    "I <3 Billionaires (Sponsor link on Github)",
                    "I'm going to make you an encode you can't refuse",
                ]
            )
        self.source_video_path_widget = QtWidgets.QLineEdit(motto)
        self.source_video_path_widget.setFixedHeight(20)
        self.source_video_path_widget.setFont(QtGui.QFont("helvetica", 9))
        self.source_video_path_widget.setDisabled(True)
        self.source_video_path_widget.setStyleSheet(
            f"padding: 0 0 -1px 5px; color: rgb({get_text_color(self.app.fastflix.config.theme)})"
        )

        self.output_video_path_widget = QtWidgets.QLineEdit("")
        self.output_video_path_widget.setDisabled(True)
        self.output_video_path_widget.setFixedHeight(20)
        self.output_video_path_widget.setFont(QtGui.QFont("helvetica", 9))
        self.output_video_path_widget.setStyleSheet("padding: 0 0 -1px 5px")
        self.output_video_path_widget.textChanged.connect(lambda x: self.page_update(build_thumbnail=False))
        self.video_path_widget.setEnabled(False)

        QtCore.QTimer.singleShot(6_000, self.fade_loop)

        self.widgets: MainWidgets = MainWidgets()

        self.buttons = []

        self.thumb_file = Path(self.app.fastflix.config.work_path, "thumbnail_preview.jpg")

        self.video_options = VideoOptions(
            self,
            app=self.app,
            available_audio_encoders=self.app.fastflix.audio_encoders,
        )

        # self.completed.connect(self.conversion_complete)
        # self.cancelled.connect(self.conversion_cancelled)
        self.close_event.connect(self.close)
        self.thumbnail_complete.connect(self.thumbnail_generated)
        self.status_update_signal.connect(self.status_update)
        self.thread_logging_signal.connect(self.thread_logger)
        self.encoding_worker = None
        self.command_runner = None
        self.side_data = Box()
        self.default_options = Box()

        self.grid = QtWidgets.QGridLayout()

        # row: int, column: int, rowSpan: int, columnSpan: int

        self.grid.addLayout(self.init_top_bar(), 0, 0, 1, 6)
        self.grid.addLayout(self.init_top_bar_right(), 0, 11, 1, 3)
        self.grid.addLayout(self.init_video_area(), 1, 0, 6, 6)
        self.grid.addLayout(self.init_right_col(), 1, 11, 6, 3)

        # pi = QtWidgets.QVBoxLayout()
        # pi.addWidget(self.init_preview_image())
        # pi.addLayout(self.())

        self.grid.addWidget(self.init_preview_image(), 0, 6, 6, 5, (QtCore.Qt.AlignTop | QtCore.Qt.AlignCenter))
        self.grid.addLayout(self.init_thumb_time_selector(), 6, 6, 1, 5, (QtCore.Qt.AlignTop | QtCore.Qt.AlignCenter))
        # self.grid.addLayout(pi, 0, 6, 7, 5)

        spacer = QtWidgets.QLabel()
        spacer.setFixedHeight(5)
        self.grid.addWidget(spacer, 8, 0, 1, 14)
        self.grid.addWidget(self.video_options, 9, 0, 10, 14)

        self.grid.setSpacing(5)
        self.paused = False

        self.disable_all()
        self.setLayout(self.grid)
        self.show()
        self.initialized = True
        self.loading_video = False
        self.last_page_update = time.time()

    def fade_loop(self, percent=90):
        if self.input_video:
            self.source_video_path_widget.setStyleSheet(
                f"color: rgb({get_text_color(self.app.fastflix.config.theme)}); padding: 0 0 -1px 5px;"
            )
            return
        if percent > 0:
            op = QtWidgets.QGraphicsOpacityEffect()
            op.setOpacity(percent)
            self.source_video_path_widget.setStyleSheet(
                f"color: rgba({get_text_color(self.app.fastflix.config.theme)}, {percent / 100}); padding: 0 0 -1px 5px;"
            )
            self.source_video_path_widget.setGraphicsEffect(op)
            QtCore.QTimer.singleShot(200, lambda: self.fade_loop(percent - 10))
        else:
            self.source_video_path_widget.setStyleSheet(
                f"color: rgb({get_text_color(self.app.fastflix.config.theme)}); padding: 0 0 -1px 5px;"
            )
            self.source_video_path_widget.setText("")

    def init_top_bar(self):
        top_bar = QtWidgets.QHBoxLayout()

        source = QtWidgets.QPushButton(QtGui.QIcon(self.get_icon("onyx-source")), f"  {t('Source')}")
        source.setIconSize(QtCore.QSize(22, 22))
        source.setFixedHeight(50)
        source.setDefault(True)
        source.clicked.connect(lambda: self.open_file())

        self.widgets.profile_box = QtWidgets.QComboBox()
        self.widgets.profile_box.setStyleSheet("text-align: center;")
        self.widgets.profile_box.addItems(self.app.fastflix.config.profiles.keys())
        self.widgets.profile_box.view().setFixedWidth(self.widgets.profile_box.minimumSizeHint().width() + 50)
        self.widgets.profile_box.setCurrentText(self.app.fastflix.config.selected_profile)
        self.widgets.profile_box.currentIndexChanged.connect(self.set_profile)
        self.widgets.profile_box.setFixedWidth(250)
        self.widgets.profile_box.setFixedHeight(50)

        top_bar.addWidget(source)
        top_bar.addWidget(QtWidgets.QSplitter(QtCore.Qt.Horizontal))
        top_bar.addLayout(self.init_encoder_drop_down())
        top_bar.addWidget(QtWidgets.QSplitter(QtCore.Qt.Horizontal))
        top_bar.addWidget(self.widgets.profile_box)
        top_bar.addWidget(QtWidgets.QSplitter(QtCore.Qt.Horizontal))

        self.add_profile = QtWidgets.QPushButton(
            QtGui.QIcon(self.get_icon("onyx-new-profile")), f'  {t("New Profile")}'
        )
        # add_profile.setFixedSize(QtCore.QSize(40, 40))
        self.add_profile.setFixedHeight(50)
        self.add_profile.setIconSize(QtCore.QSize(20, 20))
        self.add_profile.setToolTip(t("Profile_newprofiletooltip"))
        # add_profile.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.add_profile.clicked.connect(lambda: self.container.new_profile())
        self.add_profile.setDisabled(True)
        # options = QtWidgets.QPushButton(QtGui.QIcon(self.get_icon("settings")), "")
        # options.setFixedSize(QtCore.QSize(40, 40))
        # options.setIconSize(QtCore.QSize(22, 22))
        # options.setToolTip(t("Settings"))
        # options.clicked.connect(lambda: self.container.show_setting())

        top_bar.addWidget(self.add_profile)
        top_bar.addStretch(1)
        # top_bar.addWidget(options)

        return top_bar

    def init_top_bar_right(self):
        top_bar_right = QtWidgets.QHBoxLayout()
        theme = "QPushButton{ padding: 0 10px; font-size: 14px; }"
        if self.app.fastflix.config.theme in ("dark", "onyx"):
            theme = """
            QPushButton {
              padding: 0 10px;
              font-size: 14px;
              background-color: #4f4f4f;
              border: none;
              color: white; }
            QPushButton:hover {
              background-color: #6b6b6b; }"""

        queue = QtWidgets.QPushButton(QtGui.QIcon(onyx_queue_add_icon), f"{t('Add to Queue')}  ")
        queue.setIconSize(QtCore.QSize(26, 26))
        queue.setFixedHeight(50)
        queue.setStyleSheet(theme)
        queue.setLayoutDirection(QtCore.Qt.RightToLeft)
        queue.clicked.connect(lambda: self.add_to_queue())

        self.widgets.convert_button = QtWidgets.QPushButton(QtGui.QIcon(onyx_convert_icon), f"{t('Convert')}  ")
        self.widgets.convert_button.setIconSize(QtCore.QSize(26, 26))
        self.widgets.convert_button.setFixedHeight(50)
        self.widgets.convert_button.setStyleSheet(theme)
        self.widgets.convert_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.widgets.convert_button.clicked.connect(lambda: self.encode_video())
        top_bar_right.addStretch(1)
        top_bar_right.addWidget(queue)
        top_bar_right.addWidget(self.widgets.convert_button)
        return top_bar_right

    def init_thumb_time_selector(self):
        layout = QtWidgets.QHBoxLayout()

        self.widgets.thumb_key = QtWidgets.QCheckBox("Keyframe")
        self.widgets.thumb_key.setChecked(False)
        self.widgets.thumb_key.clicked.connect(self.thumb_time_change)

        self.widgets.thumb_time = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.widgets.thumb_time.setMinimum(1)
        self.widgets.thumb_time.setMaximum(10)
        self.widgets.thumb_time.setValue(2)
        self.widgets.thumb_time.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.widgets.thumb_time.setTickInterval(1)
        self.widgets.thumb_time.setAutoFillBackground(False)
        self.widgets.thumb_time.sliderReleased.connect(self.thumb_time_change)

        spacer = QtWidgets.QLabel()
        spacer.setFixedWidth(4)
        layout.addWidget(spacer)
        layout.addWidget(self.widgets.thumb_key)
        layout.addWidget(spacer)
        layout.addWidget(self.widgets.thumb_time)
        layout.addWidget(spacer)
        return layout

    def thumb_time_change(self):
        self.generate_thumbnail()

    def get_temp_work_path(self):
        new_temp = self.app.fastflix.config.work_path / f"temp_{secrets.token_hex(12)}"
        if new_temp.exists():
            return self.get_temp_work_path()
        new_temp.mkdir()
        return new_temp

    def pause_resume(self):
        if not self.paused:
            self.paused = True
            self.app.fastflix.worker_queue.put(["pause"])
            self.widgets.pause_resume.setText("Resume")
            self.widgets.pause_resume.setStyleSheet("background-color: green;")
            logger.info("Pausing FFmpeg conversion via pustils")
        else:
            self.paused = False
            self.app.fastflix.worker_queue.put(["resume"])
            self.widgets.pause_resume.setText("Pause")
            self.widgets.pause_resume.setStyleSheet("background-color: orange;")
            logger.info("Resuming FFmpeg conversion")

    def config_update(self):
        self.thumb_file = Path(self.app.fastflix.config.work_path, "thumbnail_preview.jpg")
        self.change_output_types()
        self.page_update(build_thumbnail=True)

    def init_video_area(self):
        layout = QtWidgets.QVBoxLayout()
        spacer = QtWidgets.QLabel()
        spacer.setFixedHeight(2)
        layout.addWidget(spacer)

        source_layout = QtWidgets.QHBoxLayout()
        source_label = QtWidgets.QLabel(t("Source"))
        source_label.setFixedWidth(85)
        self.source_video_path_widget.setFixedHeight(23)
        source_layout.addWidget(source_label)
        source_layout.addWidget(self.source_video_path_widget, stretch=True)

        output_layout = QtWidgets.QHBoxLayout()
        output_label = QtWidgets.QLabel(t("Output"))
        output_label.setFixedWidth(85)
        self.output_video_path_widget.setFixedHeight(23)
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_video_path_widget, stretch=True)
        self.output_path_button = QtWidgets.QPushButton(icon=QtGui.QIcon(self.get_icon("onyx-output")))
        self.output_path_button.clicked.connect(lambda: self.save_file())
        self.output_path_button.setDisabled(True)
        self.output_path_button.setFixedHeight(23)
        # self.output_path_button.setFixedHeight(12)
        self.output_path_button.setIconSize(QtCore.QSize(16, 16))
        self.output_path_button.setFixedSize(QtCore.QSize(16, 16))
        self.output_path_button.setStyleSheet("border: none; padding: 0; margin: 0")

        output_layout.addWidget(self.output_path_button)
        layout.addLayout(source_layout)
        layout.addLayout(output_layout)

        title_layout = QtWidgets.QHBoxLayout()

        title_label = QtWidgets.QLabel(t("Title"))
        title_label.setFixedWidth(85)
        title_label.setToolTip(t('Set the "title" tag, sometimes shown as "Movie Name"'))
        self.widgets.video_title = QtWidgets.QLineEdit()
        self.widgets.video_title.setFixedHeight(23)
        self.widgets.video_title.setToolTip(t('Set the "title" tag, sometimes shown as "Movie Name"'))
        self.widgets.video_title.textChanged.connect(lambda: self.page_update(build_thumbnail=False))

        title_layout.addWidget(title_label)
        title_layout.addWidget(self.widgets.video_title)

        layout.addLayout(title_layout)
        layout.addLayout(self.init_video_track_select())
        layout.addWidget(self.init_start_time())
        layout.addWidget(self.init_scale())

        layout.addStretch(1)
        return layout

    def init_right_col(self):
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.init_crop())
        layout.addWidget(self.init_transforms())

        layout.addLayout(self.init_checkboxes())
        layout.addStretch(1)
        # custom_options = QtWidgets.QTextEdit()
        # # custom_options.setWidt
        # custom_options.setPlaceholderText(t("Custom Encoder Options"))
        # custom_options.setMaximumHeight(90)
        # layout.addWidget(custom_options)
        return layout

    def init_transforms(self):
        group_box = QtWidgets.QGroupBox()
        group_box.setStyleSheet(group_box_style(pt="0", mt="0"))
        transform_layout = QtWidgets.QHBoxLayout()
        transform_layout.addWidget(self.init_rotate())
        transform_layout.addStretch(1)
        transform_layout.addWidget(self.init_flip())
        group_box.setLayout(transform_layout)
        return group_box

    def init_checkboxes(self):
        transform_layout = QtWidgets.QHBoxLayout()
        metadata_layout = QtWidgets.QVBoxLayout()
        self.widgets.remove_metadata = QtWidgets.QCheckBox(t("Remove Metadata"))
        self.widgets.remove_metadata.setChecked(True)
        self.widgets.remove_metadata.toggled.connect(self.page_update)
        self.widgets.remove_metadata.setToolTip(
            t("Scrub away all incoming metadata, like video titles, unique markings and so on.")
        )
        self.widgets.chapters = QtWidgets.QCheckBox(t("Copy Chapters"))
        self.widgets.chapters.setChecked(True)
        self.widgets.chapters.toggled.connect(self.page_update)
        self.widgets.chapters.setToolTip(t("Copy the chapter markers as is from incoming source."))

        metadata_layout.addWidget(self.widgets.remove_metadata)
        metadata_layout.addWidget(self.widgets.chapters)

        transform_layout.addLayout(metadata_layout)

        self.widgets.deinterlace = QtWidgets.QCheckBox(t("Deinterlace"))
        self.widgets.deinterlace.setChecked(False)
        self.widgets.deinterlace.toggled.connect(self.interlace_update)
        self.widgets.deinterlace.setToolTip(
            f'{t("Enables the yadif filter.")}\n' f'{t("Automatically enabled when an interlaced video is detected")}'
        )

        self.widgets.remove_hdr = QtWidgets.QCheckBox(t("Remove HDR"))
        self.widgets.remove_hdr.setChecked(False)
        self.widgets.remove_hdr.toggled.connect(self.hdr_update)
        self.widgets.remove_hdr.setToolTip(
            f"{t('Convert BT2020 colorspace into bt709')}\n"
            f"{t('WARNING: This will take much longer and result in a larger file')}"
        )

        extra_details_layout = QtWidgets.QVBoxLayout()
        extra_details_layout.addWidget(self.widgets.deinterlace)
        extra_details_layout.addWidget(self.widgets.remove_hdr)

        transform_layout.addLayout(extra_details_layout)
        return transform_layout

    def init_video_track_select(self):
        layout = QtWidgets.QHBoxLayout()
        self.widgets.video_track = QtWidgets.QComboBox()
        self.widgets.video_track.addItems([])
        self.widgets.video_track.setFixedHeight(23)
        self.widgets.video_track.currentIndexChanged.connect(self.video_track_update)
        self.widgets.video_track.setStyleSheet("height: 5px")
        if self.app.fastflix.config.theme == "onyx":
            self.widgets.video_track.setStyleSheet("background-color: #707070; border-radius: 10px; color: black")

        track_label = QtWidgets.QLabel(t("Video Track"))
        track_label.setFixedWidth(80)
        layout.addWidget(track_label)
        layout.addWidget(self.widgets.video_track, stretch=1)
        layout.setSpacing(10)
        return layout

    def set_profile(self):
        if self.loading_video:
            return
        self.app.fastflix.config.selected_profile = self.widgets.profile_box.currentText()
        self.app.fastflix.config.save()
        self.widgets.convert_to.setCurrentText(self.app.fastflix.config.opt("encoder"))
        if self.app.fastflix.config.opt("auto_crop") and not self.build_crop():
            self.get_auto_crop()
        self.loading_video = True
        try:
            self.widgets.scale.keep_aspect.setChecked(self.app.fastflix.config.opt("keep_aspect_ratio"))
            self.widgets.rotate.setCurrentIndex(self.app.fastflix.config.opt("rotate") or 0 // 90)

            v_flip = self.app.fastflix.config.opt("vertical_flip")
            h_flip = self.app.fastflix.config.opt("horizontal_flip")

            self.widgets.flip.setCurrentIndex(self.flip_to_int(v_flip, h_flip))
            try:
                self.video_options.change_conversion(self.app.fastflix.config.opt("encoder"))
                self.video_options.update_profile()
            except KeyError:
                logger.error(
                    f"Profile not set properly as we don't have encoder: {self.app.fastflix.config.opt('encoder')}"
                )
            if self.app.fastflix.current_video:
                self.video_options.new_source()
        finally:
            # Hack to prevent a lot of thumbnail generation
            self.loading_video = False
        self.page_update()

    def save_profile(self):
        self.video_options.get_settings()

    def init_flip(self):
        self.widgets.flip = QtWidgets.QComboBox()
        rotation_folder = "../data/rotations/FastFlix"

        no_rot_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder}.png")).resolve())
        vert_flip_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} VF.png")).resolve())
        hoz_flip_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} HF.png")).resolve())
        rot_180_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} 180.png")).resolve())

        self.widgets.flip.addItems([t("No Flip"), t("Vertical Flip"), t("Horizontal Flip"), t("Vert + Hoz Flip")])
        self.widgets.flip.setItemIcon(0, QtGui.QIcon(no_rot_file))
        self.widgets.flip.setItemIcon(1, QtGui.QIcon(vert_flip_file))
        self.widgets.flip.setItemIcon(2, QtGui.QIcon(hoz_flip_file))
        self.widgets.flip.setItemIcon(3, QtGui.QIcon(rot_180_file))
        self.widgets.flip.setIconSize(QtCore.QSize(35, 35))
        self.widgets.flip.currentIndexChanged.connect(lambda: self.page_update())
        self.widgets.flip.setFixedWidth(130)
        return self.widgets.flip

    def get_flips(self) -> Tuple[bool, bool]:
        mapping = {0: (False, False), 1: (True, False), 2: (False, True), 3: (True, True)}
        return mapping[self.widgets.flip.currentIndex()]

    def flip_to_int(self, vertical_flip: bool, horizontal_flip: bool) -> int:
        mapping = {(False, False): 0, (True, False): 1, (False, True): 2, (True, True): 3}
        return mapping[(vertical_flip, horizontal_flip)]

    def init_rotate(self):
        self.widgets.rotate = QtWidgets.QComboBox()
        rotation_folder = "../data/rotations/FastFlix"

        no_rot_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder}.png")).resolve())
        rot_90_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} C90.png")).resolve())
        rot_270_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} CC90.png")).resolve())
        rot_180_file = str(Path(pkg_resources.resource_filename(__name__, f"{rotation_folder} 180.png")).resolve())

        self.widgets.rotate.addItems([t("No Rotation") + "   ", "90°", "180°", "270°"])
        self.widgets.rotate.setItemIcon(0, QtGui.QIcon(no_rot_file))
        self.widgets.rotate.setItemIcon(1, QtGui.QIcon(rot_90_file))
        self.widgets.rotate.setItemIcon(2, QtGui.QIcon(rot_180_file))
        self.widgets.rotate.setItemIcon(3, QtGui.QIcon(rot_270_file))
        self.widgets.rotate.setIconSize(QtCore.QSize(35, 35))
        self.widgets.rotate.currentIndexChanged.connect(lambda: self.page_update())
        self.widgets.rotate.setFixedWidth(140)

        return self.widgets.rotate

    def change_output_types(self):
        self.widgets.convert_to.clear()
        self.widgets.convert_to.addItems(self.app.fastflix.encoders.keys())
        for i, plugin in enumerate(self.app.fastflix.encoders.values()):
            if getattr(plugin, "icon", False):
                self.widgets.convert_to.setItemIcon(i, QtGui.QIcon(plugin.icon))
        self.widgets.convert_to.setIconSize(
            QtCore.QSize(40, 40) if self.app.fastflix.config.flat_ui else QtCore.QSize(35, 35)
        )

    def init_encoder_drop_down(self):
        layout = QtWidgets.QHBoxLayout()
        self.widgets.convert_to = QtWidgets.QComboBox()
        self.widgets.convert_to.setMinimumWidth(180)
        self.widgets.convert_to.setFixedHeight(50)
        self.change_output_types()
        self.widgets.convert_to.view().setFixedWidth(self.widgets.convert_to.minimumSizeHint().width() + 50)
        self.widgets.convert_to.currentTextChanged.connect(self.change_encoder)

        encoder_label = QtWidgets.QLabel(f"{t('Encoder')}: ")
        encoder_label.setFixedWidth(65)
        layout.addWidget(self.widgets.convert_to, stretch=0)
        layout.setSpacing(10)

        return layout

    def change_encoder(self):
        if not self.initialized or not self.convert_to:
            return
        self.video_options.change_conversion(self.convert_to)
        if not self.app.fastflix.current_video:
            return
        if not self.output_video_path_widget.text().endswith(self.current_encoder.video_extension):
            # Make sure it's using the right file extension
            self.output_video_path_widget.setText(self.generate_output_filename)

    @property
    def current_encoder(self):
        try:
            return self.app.fastflix.encoders[
                self.app.fastflix.current_video.video_settings.video_encoder_settings.name
            ]
        except AttributeError:
            return self.app.fastflix.encoders[self.convert_to]

    def init_start_time(self):
        group_box = QtWidgets.QGroupBox()
        group_box.setStyleSheet(group_box_style())

        layout = QtWidgets.QHBoxLayout()

        reset = QtWidgets.QPushButton(QtGui.QIcon(self.get_icon("undo")), "")
        reset.setIconSize(QtCore.QSize(10, 10))
        reset.clicked.connect(self.reset_time)
        reset.setFixedWidth(15)
        reset.setStyleSheet(reset_button_style)
        self.buttons.append(reset)

        self.widgets.start_time, start_layout = self.build_hoz_int_field(
            f"{t('Start')} ",
            right_stretch=False,
            left_stretch=True,
            time_field=True,
        )
        self.widgets.end_time, end_layout = self.build_hoz_int_field(
            f"  {t('End')} ", left_stretch=True, right_stretch=True, time_field=True
        )

        self.widgets.start_time.textChanged.connect(lambda: self.page_update())
        self.widgets.end_time.textChanged.connect(lambda: self.page_update())
        self.widgets.fast_time = QtWidgets.QComboBox()
        self.widgets.fast_time.addItems(["fast", "exact"])
        self.widgets.fast_time.setCurrentIndex(0)
        self.widgets.fast_time.setToolTip(
            "uses [fast] seek to a rough position ahead of timestamp, "
            "vs a specific [exact] frame lookup. (GIF encodings use [fast])"
        )
        self.widgets.fast_time.currentIndexChanged.connect(lambda: self.page_update(build_thumbnail=False))
        self.widgets.fast_time.setFixedWidth(65)

        label = QtWidgets.QLabel(t("Trim"))
        label.setMaximumHeight(40)
        layout.addWidget(label, alignment=QtCore.Qt.AlignLeft)
        layout.addWidget(reset, alignment=QtCore.Qt.AlignTop)
        layout.addStretch(1)
        layout.addLayout(start_layout)
        layout.addLayout(end_layout)
        layout.addWidget(QtWidgets.QLabel(" "))
        layout.addWidget(self.widgets.fast_time, QtCore.Qt.AlignRight)

        group_box.setLayout(layout)
        return group_box

    def reset_time(self):
        self.widgets.start_time.setText(self.number_to_time(0))
        self.widgets.end_time.setText(self.number_to_time(self.app.fastflix.current_video.duration))

    def init_scale(self):
        scale_area = QtWidgets.QGroupBox()
        scale_area.setFont(self.app.font())
        scale_area.setStyleSheet(group_box_style())

        main_row = QtWidgets.QHBoxLayout()

        label = QtWidgets.QLabel(t("Resolution"))
        main_row.addWidget(label, alignment=QtCore.Qt.AlignLeft)

        reset = QtWidgets.QPushButton(QtGui.QIcon(self.get_icon("undo")), "")
        reset.setIconSize(QtCore.QSize(10, 10))
        reset.clicked.connect(self.reset_scales)
        reset.setFixedWidth(15)
        reset.setStyleSheet(reset_button_style)
        self.buttons.append(reset)
        main_row.addWidget(reset, alignment=(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft))
        main_row.addStretch(1)

        self.widgets.scale.width, width_layout = self.build_hoz_int_field(f"{t('Width')} ")
        self.widgets.scale.height, height_layout, lb, rb = self.build_hoz_int_field(
            f"  {t('Height')} ", return_buttons=True
        )
        self.widgets.scale.height.setDisabled(True)
        self.widgets.scale.height.setText("Auto")
        lb.setDisabled(True)
        rb.setDisabled(True)

        main_row.addLayout(width_layout)
        main_row.addLayout(height_layout)

        # TODO scale 0 error

        self.widgets.scale.width.textChanged.connect(lambda: self.scale_update())
        self.widgets.scale.height.textChanged.connect(lambda: self.scale_update())

        self.widgets.scale.keep_aspect = QtWidgets.QCheckBox(t("Keep aspect ratio"))
        self.widgets.scale.keep_aspect.setMaximumHeight(40)
        self.widgets.scale.keep_aspect.setChecked(True)
        self.widgets.scale.keep_aspect.toggled.connect(lambda: self.toggle_disable((self.widgets.scale.height, lb, rb)))
        self.widgets.scale.keep_aspect.toggled.connect(lambda: self.keep_aspect_update())

        main_row.addWidget(self.widgets.scale.keep_aspect, alignment=(QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight))
        scale_area.setLayout(main_row)
        return scale_area

    def reset_scales(self):
        self.loading_video = True
        self.widgets.scale.width.setText(str(self.app.fastflix.current_video.width))
        self.loading_video = False
        self.widgets.scale.height.setText(str(self.app.fastflix.current_video.height))

    def init_crop(self):
        crop_box = QtWidgets.QGroupBox()
        crop_box.setMinimumWidth(400)
        crop_box.setStyleSheet(group_box_style(pt="0", pb="12px"))
        crop_layout = QtWidgets.QVBoxLayout()
        self.widgets.crop.top, crop_top_layout = self.build_hoz_int_field(f"       {t('Top')} ")
        self.widgets.crop.left, crop_hz_layout = self.build_hoz_int_field(f"{t('Left')} ", right_stretch=False)
        self.widgets.crop.right, crop_hz_layout = self.build_hoz_int_field(
            f"    {t('Right')} ", left_stretch=True, layout=crop_hz_layout
        )
        self.widgets.crop.bottom, crop_bottom_layout = self.build_hoz_int_field(f"{t('Bottom')} ", right_stretch=True)

        self.widgets.crop.top.textChanged.connect(lambda: self.page_update())
        self.widgets.crop.left.textChanged.connect(lambda: self.page_update())
        self.widgets.crop.right.textChanged.connect(lambda: self.page_update())
        self.widgets.crop.bottom.textChanged.connect(lambda: self.page_update())

        label = QtWidgets.QLabel(t("Crop"), alignment=(QtCore.Qt.AlignBottom | QtCore.Qt.AlignRight))

        auto_crop = QtWidgets.QPushButton(t("Auto"))
        auto_crop.setMaximumHeight(40)
        auto_crop.setFixedWidth(50)
        auto_crop.setToolTip(t("Automatically detect black borders"))
        auto_crop.clicked.connect(self.get_auto_crop)
        self.buttons.append(auto_crop)

        reset = QtWidgets.QPushButton(QtGui.QIcon(self.get_icon("undo")), "")
        reset.setIconSize(QtCore.QSize(10, 10))
        reset.setStyleSheet(reset_button_style)
        reset.setFixedWidth(15)
        reset.clicked.connect(self.reset_crop)
        self.buttons.append(reset)

        l1 = QtWidgets.QVBoxLayout()
        l1.addWidget(label, alignment=(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft))

        l2 = QtWidgets.QVBoxLayout()
        l2.addWidget(auto_crop, alignment=(QtCore.Qt.AlignTop | QtCore.Qt.AlignRight))

        reset_layout = QtWidgets.QHBoxLayout()
        reset_layout.addWidget(QtWidgets.QLabel("Reset"))
        reset_layout.addWidget(reset)

        l2.addLayout(reset_layout)
        l2.addStretch(1)

        crop_layout.addLayout(crop_top_layout)
        crop_layout.addLayout(crop_hz_layout)
        crop_layout.addLayout(crop_bottom_layout)
        outer = QtWidgets.QHBoxLayout()
        outer.addLayout(l1)
        outer.addLayout(crop_layout)
        outer.addLayout(l2)
        crop_box.setLayout(outer)

        return crop_box

    def reset_crop(self):
        self.loading_video = True
        self.widgets.crop.top.setText("0")
        self.widgets.crop.left.setText("0")
        self.widgets.crop.right.setText("0")
        self.loading_video = False
        self.widgets.crop.bottom.setText("0")

    @staticmethod
    def toggle_disable(widget_list):
        for widget in widget_list:
            widget.setDisabled(widget.isEnabled())

    @property
    def title(self):
        return self.widgets.video_title.text()

    def build_hoz_int_field(
        self,
        name,
        button_size=28,
        left_stretch=True,
        right_stretch=True,
        layout=None,
        return_buttons=False,
        time_field=False,
        right_side_label=False,
    ):

        widget = QtWidgets.QLineEdit(self.number_to_time(0) if time_field else "0")
        widget.setObjectName(name)
        if not time_field:
            widget.setValidator(only_int)
        widget.setFixedHeight(button_size)
        if not layout:
            layout = QtWidgets.QHBoxLayout()
            layout.setSpacing(0)
        if left_stretch:
            layout.addStretch(1)
        layout.addWidget(QtWidgets.QLabel(name))
        minus_button = QtWidgets.QPushButton("-")
        minus_button.setAutoRepeat(True)
        minus_button.setFixedSize(QtCore.QSize(button_size - 5, button_size))
        minus_button.setStyleSheet("padding: 0; border: none;")
        minus_button.clicked.connect(
            lambda: [
                self.modify_int(widget, "minus", time_field),
                self.page_update(),
            ]
        )
        plus_button = QtWidgets.QPushButton("+")
        plus_button.setAutoRepeat(True)
        plus_button.setFixedSize(button_size, button_size)
        plus_button.setStyleSheet("padding: 0; border: none;")
        plus_button.clicked.connect(
            lambda: [
                self.modify_int(widget, "add", time_field),
                self.page_update(),
            ]
        )
        self.buttons.append(minus_button)
        self.buttons.append(plus_button)
        if not time_field:
            widget.setFixedWidth(45)
        else:
            widget.setFixedWidth(75)
        widget.setStyleSheet("text-align: center")
        layout.addWidget(minus_button)
        layout.addWidget(widget)
        layout.addWidget(plus_button)
        if right_stretch:
            layout.addStretch(1)
        if return_buttons:
            return widget, layout, minus_button, plus_button
        return widget, layout

    def init_preview_image(self):
        class PreviewImage(QtWidgets.QLabel):
            def __init__(self, parent):
                super().__init__()
                self.main = parent
                self.setBackgroundRole(QtGui.QPalette.Base)
                self.setMinimumSize(440, 260)
                self.setAlignment(QtCore.Qt.AlignCenter)
                self.setCursor(
                    QtGui.QCursor(
                        QtGui.QPixmap(get_icon("onyx-magnifier", self.main.app.fastflix.config.theme)).scaledToWidth(32)
                    )
                )
                self.setStyleSheet("border: 2px solid #567781; margin: 8px;")

            def mousePressEvent(self, QMouseEvent):
                if (
                    not self.main.initialized
                    or not self.main.app.fastflix.current_video
                    or self.main.large_preview.isVisible()
                ):
                    return
                self.main.large_preview.generate_image()
                self.main.large_preview.show()
                super(PreviewImage, self).mousePressEvent(QMouseEvent)

        self.widgets.preview = PreviewImage(self)

        return self.widgets.preview

    def modify_int(self, widget, method="add", time_field=False):
        modifier = 1
        if time_field:
            value = time_to_number(widget.text())
            if value is None:
                return
        else:
            modifier = getattr(self.current_encoder, "video_dimension_divisor", 1)
            try:
                value = int(widget.text())
                value = int(value + (value % modifier))
            except ValueError:
                logger.exception("This shouldn't be possible, but you somehow put in not an integer")
                return

        modifier = modifier if method == "add" else -modifier
        new_value = value + modifier
        if time_field and new_value < 0:
            return
        widget.setText(str(new_value) if not time_field else self.number_to_time(new_value))
        self.build_commands()

    @reusables.log_exception("fastflix", show_traceback=False)
    def open_file(self):
        filename = QtWidgets.QFileDialog.getOpenFileName(
            self,
            caption="Open Video",
            filter="Video Files (*.mkv *.mp4 *.m4v *.mov *.avi *.divx *.webm *.mpg *.mp2 *.mpeg *.mpe *.mpv *.ogg *.m4p"
            " *.wmv *.mov *.qt *.flv *.hevc *.gif *.webp *.vob *.ogv *.ts *.mts *.m2ts *.yuv *.rm *.svi *.3gp *.3g2);;"
            "Concatenation Text File (*.txt *.concat);; All Files (*)",
            dir=str(
                self.app.fastflix.config.source_directory
                or (self.app.fastflix.current_video.source.parent if self.app.fastflix.current_video else Path.home())
            ),
        )
        if not filename or not filename[0]:
            return

        if self.app.fastflix.current_video:
            discard = yes_no_message(
                f'{t("There is already a video being processed")}<br>' f'{t("Are you sure you want to discard it?")}',
                title="Discard current video",
            )
            if not discard:
                return

        self.input_video = Path(clean_file_string(filename[0]))
        self.source_video_path_widget.setText(str(self.input_video))
        self.video_path_widget.setText(str(self.input_video))
        try:
            self.update_video_info()
        except Exception:
            logger.exception(f"Could not load video {self.input_video}")
            self.video_path_widget.setText("")
            self.output_video_path_widget.setText("")
            self.output_video_path_widget.setDisabled(True)
            self.output_path_button.setDisabled(True)
        self.page_update()

    @property
    def generate_output_filename(self):
        source = self.input_video.stem
        iso_datetime = datetime.datetime.now().isoformat().replace(":", "-").split(".")[0]
        rand_4 = secrets.token_hex(2)
        rand_8 = secrets.token_hex(4)
        ext = self.current_encoder.video_extension
        out_loc = f"{Path('~').expanduser()}{os.sep}"
        if self.input_video:
            out_loc = f"{self.input_video.parent}{os.sep}"
        if self.app.fastflix.config.output_directory:
            out_loc = f"{self.app.fastflix.config.output_directory}{os.sep}"

        gen_string = self.app.fastflix.config.output_name_format or "{source}-fastflix-{rand_4}.{ext}"

        return out_loc + gen_string.format(source=source, datetime=iso_datetime, rand_4=rand_4, rand_8=rand_8, ext=ext)

    @property
    def output_video(self):
        return clean_file_string(self.output_video_path_widget.text().strip("'\""))

    @reusables.log_exception("fastflix", show_traceback=False)
    def save_file(self, extension="mkv"):
        filename = QtWidgets.QFileDialog.getSaveFileName(
            self, caption="Save Video As", dir=self.generate_output_filename, filter=f"Save File (*.{extension})"
        )
        if filename and filename[0]:
            self.output_video_path_widget.setText(filename[0])

    def get_auto_crop(self):
        if not self.input_video or not self.initialized or self.loading_video:
            return

        start_pos = self.start_time or self.app.fastflix.current_video.duration // 10

        blocks = math.ceil(
            (self.app.fastflix.current_video.duration - start_pos) / (self.app.fastflix.config.crop_detect_points + 1)
        )
        if blocks < 1:
            blocks = 1

        times = [
            x
            for x in range(int(start_pos), int(self.app.fastflix.current_video.duration), blocks)
            if x < self.app.fastflix.current_video.duration
        ][: self.app.fastflix.config.crop_detect_points]

        if not times:
            return

        self.app.processEvents()
        result_list = []
        tasks = [
            Task(
                f"{t('Auto Crop - Finding black bars at')} {self.number_to_time(x)}",
                get_auto_crop,
                dict(
                    source=self.source_material,
                    video_width=self.app.fastflix.current_video.width,
                    video_height=self.app.fastflix.current_video.height,
                    input_track=self.original_video_track,
                    start_time=x,
                    end_time=self.end_time,
                    result_list=result_list,
                ),
            )
            for x in times
        ]
        ProgressBar(self.app, tasks)

        smallest = (self.app.fastflix.current_video.height + self.app.fastflix.current_video.width) * 2
        selected = result_list[0]
        for result in result_list:
            if (total := sum(result)) < smallest:
                selected = result
                smallest = total

        r, b, l, tp = selected

        if tp + b > self.app.fastflix.current_video.height * 0.9 or r + l > self.app.fastflix.current_video.width * 0.9:
            logger.warning(
                f"{t('Autocrop tried to crop too much')}"
                f" ({t('left')} {l}, {t('top')} {tp}, {t('right')} {r}, {t('bottom')} {b}), {t('ignoring')}"
            )
            return

        # Hack to stop thumb gen
        self.loading_video = True
        self.widgets.crop.top.setText(str(tp))
        self.widgets.crop.left.setText(str(l))
        self.widgets.crop.right.setText(str(r))
        self.loading_video = False
        self.widgets.crop.bottom.setText(str(b))

    def build_crop(self) -> Union[Crop, None]:
        if not self.initialized or not self.app.fastflix.current_video:
            return None
        try:
            crop = Crop(
                top=int(self.widgets.crop.top.text()),
                left=int(self.widgets.crop.left.text()),
                right=int(self.widgets.crop.right.text()),
                bottom=int(self.widgets.crop.bottom.text()),
            )
        except (ValueError, AttributeError):
            logger.error("Invalid crop")
            return None
        else:
            crop.width = self.app.fastflix.current_video.width - crop.right - crop.left
            crop.height = self.app.fastflix.current_video.height - crop.bottom - crop.top
            if (crop.top + crop.left + crop.right + crop.bottom) == 0:
                return None
            try:
                assert crop.top >= 0, t("Top must be positive number")
                assert crop.left >= 0, t("Left must be positive number")
                assert crop.width > 0, t("Total video width must be greater than 0")
                assert crop.height > 0, t("Total video height must be greater than 0")
                assert crop.width <= self.app.fastflix.current_video.width, t("Width must be smaller than video width")
                assert crop.height <= self.app.fastflix.current_video.height, t(
                    "Height must be smaller than video height"
                )
            except AssertionError as err:
                error_message(f"{t('Invalid Crop')}: {err}")
                return None
            return crop

    def keep_aspect_update(self) -> None:
        keep_aspect = self.widgets.scale.keep_aspect.isChecked()

        if keep_aspect:
            # TODO need to find way to translate and keep logic
            self.widgets.scale.height.setText("Auto")
        else:
            try:
                scale_width = int(self.widgets.scale.width.text())
                assert scale_width > 0
            except (ValueError, AssertionError):
                self.scale_updating = False
                if self.widgets.scale.height.text() == "Auto":
                    self.widgets.scale.height.setText("-8")
                return logger.warning("Invalid width")

            if self.app.fastflix.current_video.height == 0 or self.app.fastflix.current_video.width == 0:
                return logger.warning("Input video does not exist or has 0 dimension")

            ratio = self.app.fastflix.current_video.height / self.app.fastflix.current_video.width
            scale_height = ratio * scale_width
            mod = int(scale_height % 2)
            if mod:
                scale_height -= mod
                logger.info(f"Have to adjust scale height by {mod} pixels")
            self.widgets.scale.height.setText(str(int(scale_height)))
        self.scale_update()

    def disable_all(self):
        for name, widget in self.widgets.items():
            if name in ("preview", "convert_button", "pause_resume", "convert_to", "profile_box"):
                continue
            if isinstance(widget, dict):
                for sub_widget in widget.values():
                    if isinstance(sub_widget, QtWidgets.QWidget):
                        sub_widget.setDisabled(True)
            elif isinstance(widget, QtWidgets.QWidget):
                widget.setDisabled(True)
        for button in self.buttons:
            button.setDisabled(True)
        self.output_path_button.setDisabled(True)
        self.output_video_path_widget.setDisabled(True)
        self.add_profile.setDisabled(True)

    def enable_all(self):
        for name, widget in self.widgets.items():
            if name in ("preview", "convert_button", "pause_resume", "convert_to", "profile_box"):
                continue
            if isinstance(widget, dict):
                for sub_widget in widget.values():
                    if isinstance(sub_widget, QtWidgets.QWidget):
                        sub_widget.setEnabled(True)
            elif isinstance(widget, QtWidgets.QWidget):
                widget.setEnabled(True)
        for button in self.buttons:
            button.setEnabled(True)
        if self.widgets.scale.keep_aspect.isChecked():
            self.widgets.scale.height.setDisabled(True)
        self.output_path_button.setEnabled(True)
        self.output_video_path_widget.setEnabled(True)
        self.add_profile.setEnabled(True)

    @reusables.log_exception("fastflix", show_traceback=False)
    def scale_update(self):
        if self.scale_updating or self.loading_video:
            return False

        self.scale_updating = True

        keep_aspect = self.widgets.scale.keep_aspect.isChecked()

        self.widgets.scale.height.setDisabled(keep_aspect)
        height = self.app.fastflix.current_video.height
        width = self.app.fastflix.current_video.width
        if crop := self.build_crop():
            width = crop.width
            height = crop.height

        if keep_aspect and (not height or not width):
            self.scale_updating = False
            return logger.warning(t("Invalid source dimensions"))
            # return self.scale_warning_message.setText("Invalid source dimensions")

        try:
            scale_width = int(self.widgets.scale.width.text())
            assert scale_width > 0
        except (ValueError, AssertionError):
            self.scale_updating = False
            return logger.warning(t("Invalid width"))

        if scale_width % 2:
            self.scale_updating = False
            # TODO add better colors / way
            # self.widgets.scale.width.setStyleSheet("background-color: red;")
            self.widgets.scale.width.setToolTip(
                f"{t('Width must be divisible by 2 - Source width')}: {self.app.fastflix.current_video.width}"
            )
            return logger.warning(t("Width must be divisible by 2"))
            # return self.scale_warning_message.setText("Width must be divisible by 8")
        else:
            self.widgets.scale.width.setToolTip(f"{t('Source width')}: {self.app.fastflix.current_video.width}")

        if keep_aspect:
            self.widgets.scale.height.setText("Auto")
            # self.widgets.scale.width.setStyleSheet("background-color: white;")
            # self.widgets.scale.height.setStyleSheet("background-color: white;")
            self.page_update()
            self.scale_updating = False
            return
            # ratio = self.app.fastflix.current_video.height / self.app.fastflix.current_video.width
            # scale_height = ratio * scale_width
            # self.widgets.scale.height.setText(str(int(scale_height)))
            # mod = int(scale_height % 2)
            # if mod:
            #     scale_height -= mod
            #     logger.info(f"Have to adjust scale height by {mod} pixels")
            #     # self.scale_warning_message.setText()
            # logger.info(f"height has -{mod}px off aspect")
            # self.widgets.scale.height.setText(str(int(scale_height)))
            # self.widgets.scale.width.setStyleSheet("background-color: white;")
            # self.widgets.scale.height.setStyleSheet("background-color: white;")
            # self.page_update()
            # self.scale_updating = False
            # return

        scale_height = self.widgets.scale.height.text()
        try:
            scale_height = -8 if scale_height == "Auto" else int(scale_height)
            assert scale_height == -8 or scale_height > 0
        except (ValueError, AssertionError):
            self.scale_updating = False
            return logger.warning(t("Invalid height"))
            # return self.scale_warning_message.setText("Invalid height")

        if scale_height != -8 and scale_height % 2:
            # self.widgets.scale.height.setStyleSheet("background-color: red;")
            self.widgets.scale.height.setToolTip(
                f"{t('Height must be divisible by 2 - Source height')}: {self.app.fastflix.current_video.height}"
            )
            self.scale_updating = False
            return logger.warning(
                f"{t('Height must be divisible by 2 - Source height')}: {self.app.fastflix.current_video.height}"
            )
        else:
            self.widgets.scale.height.setToolTip(f"{t('Source height')}: {self.app.fastflix.current_video.height}")
            # return self.scale_warning_message.setText("Height must be divisible by 8")
        # self.scale_warning_message.setText("")
        # self.widgets.scale.width.setStyleSheet("background-color: white;")
        # self.widgets.scale.height.setStyleSheet("background-color: white;")
        self.page_update()
        self.scale_updating = False

    def clear_current_video(self):
        self.loading_video = True
        self.app.fastflix.current_video = None
        self.input_video = None
        self.source_video_path_widget.setText("")
        self.video_path_widget.setText(t("No Source Selected"))
        self.output_video_path_widget.setText("")
        self.output_path_button.setDisabled(True)
        self.output_video_path_widget.setDisabled(True)
        for i in range(self.widgets.video_track.count()):
            self.widgets.video_track.removeItem(0)
        self.widgets.preview.setText(t("No Video File"))

        self.widgets.deinterlace.setChecked(False)
        self.widgets.remove_hdr.setChecked(False)
        self.widgets.remove_metadata.setChecked(True)
        self.widgets.chapters.setChecked(True)

        self.widgets.flip.setCurrentIndex(0)
        self.widgets.rotate.setCurrentIndex(0)
        self.widgets.video_title.setText("")

        self.widgets.crop.top.setText("0")
        self.widgets.crop.left.setText("0")
        self.widgets.crop.right.setText("0")
        self.widgets.crop.bottom.setText("0")
        self.widgets.start_time.setText(self.number_to_time(0))
        self.widgets.end_time.setText(self.number_to_time(0))
        self.widgets.scale.width.setText("0")
        self.widgets.scale.height.setText("Auto")
        self.widgets.preview.setPixmap(QtGui.QPixmap())
        self.video_options.clear_tracks()
        self.disable_all()
        self.loading_video = False

    @reusables.log_exception("fastflix", show_traceback=True)
    def reload_video_from_queue(self, video: Video):
        self.loading_video = True

        self.app.fastflix.current_video = video
        self.app.fastflix.current_video.work_path.mkdir(parents=True, exist_ok=True)
        extract_attachments(app=self.app)
        self.input_video = video.source
        self.source_video_path_widget.setText(str(self.input_video))
        hdr10_indexes = [x.index for x in self.app.fastflix.current_video.hdr10_streams]
        text_video_tracks = [
            (
                f'{x.index}: {x.codec_name} {x.get("bit_depth", "8")}-bit '
                f'{x["color_primaries"] if x.get("color_primaries") else ""}'
                f'{" - HDR10" if x.index in hdr10_indexes else ""}'
                f'{" | HDR10+" if x.index in self.app.fastflix.current_video.hdr10_plus else ""}'
            )
            for x in self.app.fastflix.current_video.streams.video
        ]
        self.widgets.video_track.clear()
        self.widgets.video_track.addItems(text_video_tracks)
        selected_track = 0
        for track in self.app.fastflix.current_video.streams.video:
            if track.index == self.app.fastflix.current_video.video_settings.selected_track:
                selected_track = track.index
        self.widgets.video_track.setCurrentIndex(selected_track)

        end_time = self.app.fastflix.current_video.video_settings.end_time or video.duration
        if self.app.fastflix.current_video.video_settings.crop:
            self.widgets.crop.top.setText(str(self.app.fastflix.current_video.video_settings.crop.top))
            self.widgets.crop.left.setText(str(self.app.fastflix.current_video.video_settings.crop.left))
            self.widgets.crop.right.setText(str(self.app.fastflix.current_video.video_settings.crop.right))
            self.widgets.crop.bottom.setText(str(self.app.fastflix.current_video.video_settings.crop.bottom))
        else:
            self.widgets.crop.top.setText("0")
            self.widgets.crop.left.setText("0")
            self.widgets.crop.right.setText("0")
            self.widgets.crop.bottom.setText("0")
        self.widgets.start_time.setText(self.number_to_time(video.video_settings.start_time))
        self.widgets.end_time.setText(self.number_to_time(end_time))
        self.widgets.video_title.setText(self.app.fastflix.current_video.video_settings.video_title)
        self.output_video_path_widget.setText(str(video.video_settings.output_path))
        self.widgets.deinterlace.setChecked(self.app.fastflix.current_video.video_settings.deinterlace)
        self.widgets.remove_metadata.setChecked(self.app.fastflix.current_video.video_settings.remove_metadata)
        self.widgets.chapters.setChecked(self.app.fastflix.current_video.video_settings.copy_chapters)
        self.widgets.remove_hdr.setChecked(self.app.fastflix.current_video.video_settings.remove_hdr)
        self.widgets.rotate.setCurrentIndex(video.video_settings.rotate)
        self.widgets.fast_time.setCurrentIndex(0 if video.video_settings.fast_seek else 1)
        if video.video_settings.vertical_flip:
            self.widgets.flip.setCurrentIndex(1)
        if video.video_settings.horizontal_flip:
            self.widgets.flip.setCurrentIndex(2)
        if video.video_settings.vertical_flip and video.video_settings.horizontal_flip:
            self.widgets.flip.setCurrentIndex(3)

        if self.app.fastflix.current_video.video_settings.scale:
            w, h = self.app.fastflix.current_video.video_settings.scale.split(":")

            self.widgets.scale.width.setText(w)
            if h.startswith("-"):
                self.widgets.scale.height.setText("Auto")
                self.widgets.scale.keep_aspect.setChecked(True)
            else:
                self.widgets.scale.height.setText(h)
        else:
            self.widgets.scale.width.setText(str(self.app.fastflix.current_video.width))
            self.widgets.scale.height.setText("Auto")
            self.widgets.scale.keep_aspect.setChecked(True)
        self.video_options.reload()
        self.enable_all()

        self.app.fastflix.current_video.status = Status()
        self.loading_video = False
        self.page_update()

    @reusables.log_exception("fastflix", show_traceback=False)
    def update_video_info(self):
        self.loading_video = True
        self.output_video_path_widget.setText(self.generate_output_filename)
        self.output_video_path_widget.setDisabled(False)
        self.output_path_button.setDisabled(False)
        self.app.fastflix.current_video = Video(source=self.input_video, work_path=self.get_temp_work_path())
        tasks = [
            Task(t("Parse Video details"), parse),
            Task(t("Extract covers"), extract_attachments),
            Task(t("Detecting Interlace"), detect_interlaced, dict(source=self.source_material)),
            Task(t("Determine HDR details"), parse_hdr_details),
            Task(t("Detect HDR10+"), detect_hdr10_plus),
        ]

        try:
            ProgressBar(self.app, tasks)
        except FlixError:
            error_message(f"{t('Not a video file')}<br>{self.input_video}")
            self.clear_current_video()
            return
        except Exception:
            logger.exception(f"Could not properly read the files {self.input_video}")
            self.clear_current_video()
            error_message(f"Could not properly read the file {self.input_video}")
            return

        hdr10_indexes = [x.index for x in self.app.fastflix.current_video.hdr10_streams]
        text_video_tracks = [
            (
                f'{x.index}: {x.codec_name} {x.get("bit_depth", "8")}-bit '
                f'{x["color_primaries"] if x.get("color_primaries") else ""}'
                f'{" - HDR10" if x.index in hdr10_indexes else ""}'
                f'{" | HDR10+" if x.index in self.app.fastflix.current_video.hdr10_plus else ""}'
            )
            for x in self.app.fastflix.current_video.streams.video
        ]
        self.widgets.video_track.clear()
        self.widgets.crop.top.setText("0")
        self.widgets.crop.left.setText("0")
        self.widgets.crop.right.setText("0")
        self.widgets.crop.bottom.setText("0")
        self.widgets.start_time.setText("0:00:00")

        self.widgets.scale.width.setText(
            str(
                self.app.fastflix.current_video.width
                + (self.app.fastflix.current_video.width % self.current_encoder.video_dimension_divisor)
            )
        )
        self.widgets.scale.width.setToolTip(f"{t('Source width')}: {self.app.fastflix.current_video.width}")
        self.widgets.scale.height.setText(
            str(
                self.app.fastflix.current_video.height
                + (self.app.fastflix.current_video.height % self.current_encoder.video_dimension_divisor)
            )
        )
        self.widgets.scale.height.setToolTip(f"{t('Source height')}: {self.app.fastflix.current_video.height}")
        self.widgets.video_track.addItems(text_video_tracks)

        self.widgets.video_track.setDisabled(bool(len(self.app.fastflix.current_video.streams.video) == 1))

        logger.debug(f"{len(self.app.fastflix.current_video.streams['video'])} {t('video tracks found')}")
        logger.debug(f"{len(self.app.fastflix.current_video.streams['audio'])} {t('audio tracks found')}")

        if self.app.fastflix.current_video.streams["subtitle"]:
            logger.debug(f"{len(self.app.fastflix.current_video.streams['subtitle'])} {t('subtitle tracks found')}")
        if self.app.fastflix.current_video.streams["attachment"]:
            logger.debug(f"{len(self.app.fastflix.current_video.streams['attachment'])} {t('attachment tracks found')}")
        if self.app.fastflix.current_video.streams["data"]:
            logger.debug(f"{len(self.app.fastflix.current_video.streams['data'])} {t('data tracks found')}")

        self.widgets.end_time.setText(self.number_to_time(self.app.fastflix.current_video.duration))
        title_name = [
            v for k, v in self.app.fastflix.current_video.format.get("tags", {}).items() if k.lower() == "title"
        ]
        if title_name:
            self.widgets.video_title.setText(title_name[0])
        else:
            self.widgets.video_title.setText("")

        self.widgets.deinterlace.setChecked(self.app.fastflix.current_video.video_settings.deinterlace)

        self.video_options.new_source()
        self.enable_all()
        # self.widgets.convert_button.setDisabled(False)
        # self.widgets.convert_button.setStyleSheet("background-color:green;")
        self.loading_video = False
        if self.app.fastflix.config.opt("auto_crop"):
            self.get_auto_crop()

        if not getattr(self.current_encoder, "enable_concat", False) and self.app.fastflix.current_video.concat:
            error_message(f"{self.current_encoder.name} {t('does not support concatenating files together')}")

    @property
    def video_track(self) -> int:
        return self.widgets.video_track.currentIndex()

    @property
    def original_video_track(self) -> int:
        if not self.app.fastflix.current_video or not self.widgets.video_track.currentText():
            return 0
        try:
            return int(self.widgets.video_track.currentText().split(":", 1)[0])
        except Exception:
            logger.exception("Could not get original_video_track")
            return 0

    @property
    def pix_fmt(self) -> str:
        return self.app.fastflix.current_video.streams.video[self.video_track].pix_fmt

    @staticmethod
    def number_to_time(number) -> str:
        return str(timedelta(seconds=round(number, 2)))[:10]

    @property
    def start_time(self) -> float:
        return time_to_number(self.widgets.start_time.text())

    @property
    def end_time(self) -> float:
        return time_to_number(self.widgets.end_time.text())

    @property
    def fast_time(self) -> bool:
        return self.widgets.fast_time.currentText() == "fast"

    @property
    def remove_metadata(self) -> bool:
        return self.widgets.remove_metadata.isChecked()

    @property
    def copy_chapters(self) -> bool:
        return self.widgets.chapters.isChecked()

    @property
    def remove_hdr(self) -> bool:
        return self.widgets.remove_hdr.isChecked()

    @property
    def preview_place(self) -> Union[float, int]:
        ticks = self.app.fastflix.current_video.duration / 10
        return (self.widgets.thumb_time.value() - 1) * ticks

    @reusables.log_exception("fastflix", show_traceback=False)
    def generate_thumbnail(self):
        if not self.input_video or self.loading_video:
            return

        settings = self.app.fastflix.current_video.video_settings.dict()

        if (
            self.app.fastflix.current_video.video_settings.video_encoder_settings.pix_fmt == "yuv420p10le"
            and self.app.fastflix.current_video.color_space.startswith("bt2020")
        ):
            settings["remove_hdr"] = True

        custom_filters = "scale='min(440\\,iw):-8'"
        # if self.app.fastflix.current_video.color_transfer == "arib-std-b67":
        #     custom_filters += ",select=eq(pict_type\\,I)"

        filters = helpers.generate_filters(
            start_filters="select=eq(pict_type\\,I)" if self.widgets.thumb_key.isChecked() else None,
            custom_filters=custom_filters,
            enable_opencl=self.app.fastflix.opencl_support,
            **settings,
        )

        thumb_command = generate_thumbnail_command(
            config=self.app.fastflix.config,
            source=self.source_material,
            output=self.thumb_file,
            filters=filters,
            enable_opencl=self.app.fastflix.opencl_support,
            start_time=self.preview_place if not self.app.fastflix.current_video.concat else None,
            input_track=self.app.fastflix.current_video.video_settings.selected_track,
        )
        try:
            self.thumb_file.unlink()
        except OSError:
            pass
        worker = ThumbnailCreator(self, thumb_command)
        worker.start()

    @property
    def source_material(self):
        if self.app.fastflix.current_video.concat:
            return get_concat_item(self.input_video, self.widgets.thumb_time.value())
        return self.input_video

    @staticmethod
    def thread_logger(text):
        try:
            level, message = text.split(":", 1)
            logger.log(["", "debug", "info", "warning", "error", "critical"].index(level.lower()) * 10, message)
        except Exception:
            logger.warning(text)

    @reusables.log_exception("fastflix", show_traceback=False)
    def thumbnail_generated(self, success=False):
        if not success or not self.thumb_file.exists():
            self.widgets.preview.setText(t("Error Updating Thumbnail"))
            return
        pixmap = QtGui.QPixmap(str(self.thumb_file))
        pixmap = pixmap.scaled(420, 260, QtCore.Qt.KeepAspectRatio)
        self.widgets.preview.setPixmap(pixmap)

    def build_scale(self):
        width = self.widgets.scale.width.text()
        height = self.widgets.scale.height.text()
        if height == "Auto":
            height = -8
        return f"{width}:{height}"

    def get_all_settings(self):
        if not self.initialized:
            return
        stream_info = self.app.fastflix.current_video.streams.video[self.video_track]

        end_time = self.end_time
        if self.end_time == float(self.app.fastflix.current_video.format.get("duration", 0)):
            end_time = 0
        if self.end_time and (self.end_time - 0.1 <= self.app.fastflix.current_video.duration <= self.end_time + 0.1):
            end_time = 0

        scale = self.build_scale()
        if scale in (
            f"{stream_info.width}:-8",
            f"-8:{stream_info.height}",
            f"{stream_info.width}:{stream_info.height}",
        ):
            scale = None

        v_flip, h_flip = self.get_flips()
        self.app.fastflix.current_video.video_settings = VideoSettings(
            crop=self.build_crop(),
            scale=scale,
            start_time=self.start_time,
            end_time=end_time,
            selected_track=self.original_video_track,
            fast_seek=self.fast_time,
            rotate=self.widgets.rotate.currentIndex(),
            vertical_flip=v_flip,
            horizontal_flip=h_flip,
            output_path=Path(clean_file_string(self.output_video)),
            deinterlace=self.widgets.deinterlace.isChecked(),
            remove_metadata=self.remove_metadata,
            copy_chapters=self.copy_chapters,
            video_title=self.title,
            remove_hdr=self.remove_hdr,
        )

        self.video_options.get_settings()

    def build_commands(self) -> bool:
        if (
            not self.initialized
            or not self.app.fastflix.current_video
            or not self.app.fastflix.current_video.streams
            or self.loading_video
        ):
            return False
        try:
            self.get_all_settings()
        except FastFlixInternalException as err:
            error_message(str(err))
            return False

        commands = self.current_encoder.build(fastflix=self.app.fastflix)
        if not commands:
            return False
        self.video_options.commands.update_commands(commands)
        self.app.fastflix.current_video.video_settings.conversion_commands = commands
        return True

    def interlace_update(self):
        if self.loading_video:
            return
        deinterlace = self.widgets.deinterlace.isChecked()
        if not deinterlace and self.app.fastflix.current_video.interlaced:
            error_message(
                f"{t('This video has been detected to have an interlaced video.')}\n"
                f"{t('Not deinterlacing will result in banding after encoding.')}",
                title="Warning",
            )
        self.page_update()

    def encoder_settings_update(self):
        self.video_options.settings_update()

    def hdr_update(self):
        self.video_options.advanced.hdr_settings()
        self.encoder_settings_update()

    def video_track_update(self):
        if not self.app.fastflix.current_video or self.loading_video:
            return
        self.loading_video = True
        self.app.fastflix.current_video.video_settings.selected_track = self.original_video_track
        self.widgets.crop.top.setText("0")
        self.widgets.crop.left.setText("0")
        self.widgets.crop.right.setText("0")
        self.widgets.crop.bottom.setText("0")
        self.widgets.scale.width.setText(str(self.app.fastflix.current_video.width))
        self.widgets.scale.height.setText(str(self.app.fastflix.current_video.height))
        self.loading_video = False
        self.page_update(build_thumbnail=True)

    def page_update(self, build_thumbnail=True):
        if not self.initialized or self.loading_video or not self.app.fastflix.current_video:
            return
        self.last_page_update = time.time()
        self.video_options.refresh()
        self.build_commands()
        if build_thumbnail:
            new_hash = (
                f"{self.build_crop()}:{self.build_scale()}:{self.start_time}:{self.end_time}:"
                f"{self.app.fastflix.current_video.video_settings.selected_track}:"
                f"{int(self.remove_hdr)}:{self.preview_place}:{self.widgets.rotate.currentIndex()}:"
                f"{self.widgets.flip.currentIndex()}"
            )
            if new_hash == self.last_thumb_hash:
                return
            self.last_thumb_hash = new_hash
            self.generate_thumbnail()

    def close(self, no_cleanup=False, from_container=False):
        self.app.fastflix.shutting_down = True
        if not no_cleanup:
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
        self.video_options.cleanup()
        self.notifier.terminate()
        super().close()
        if not from_container:
            self.container.close()

    @property
    def convert_to(self):
        if self.widgets.convert_to:
            return self.widgets.convert_to.currentText().strip()
        return list(self.app.fastflix.encoders.keys())[0]

    def encoding_checks(self):
        if not self.input_video:
            error_message(t("Have to select a video first"))
            return False
        if not self.output_video:
            error_message(t("Please specify output video"))
            return False
        try:
            if self.input_video.resolve().absolute() == Path(self.output_video).resolve().absolute():
                error_message(t("Output video path is same as source!"))
                return False
        except OSError:
            # file system may not support resolving
            pass

        if not self.output_video.lower().endswith(self.current_encoder.video_extension):
            sm = QtWidgets.QMessageBox()
            sm.setText(
                f"{t('Output video file does not have expected extension')} ({self.current_encoder.video_extension}), "
                f"{t('which can case issues')}."
            )
            # TODO translate
            sm.addButton("Continue anyways", QtWidgets.QMessageBox.DestructiveRole)
            sm.addButton(f"Append ({self.current_encoder.video_extension}) for me", QtWidgets.QMessageBox.YesRole)
            sm.setStandardButtons(QtWidgets.QMessageBox.Close)
            for button in sm.buttons():
                if button.text().startswith("Append"):
                    button.setStyleSheet("background-color:green;")
                elif button.text().startswith("Continue"):
                    button.setStyleSheet("background-color:red;")
            sm.exec_()
            if sm.clickedButton().text().startswith("Append"):
                self.output_video_path_widget.setText(f"{self.output_video}.{self.current_encoder.video_extension}")
                self.output_video_path_widget.setDisabled(False)
                self.output_path_button.setDisabled(False)
            elif not sm.clickedButton().text().startswith("Continue"):
                return False

        out_file_path = Path(self.output_video)
        if out_file_path.exists() and out_file_path.stat().st_size > 0:
            sm = QtWidgets.QMessageBox()
            sm.setText("That output file already exists and is not empty!")
            sm.addButton("Cancel", QtWidgets.QMessageBox.DestructiveRole)
            sm.addButton("Overwrite", QtWidgets.QMessageBox.RejectRole)
            sm.exec_()
            if sm.clickedButton().text() == "Cancel":
                return False
        return True

    def set_convert_button(self):
        if not self.app.fastflix.currently_encoding:
            self.widgets.convert_button.setText(f"{t('Convert')}  ")
            self.widgets.convert_button.setIcon(QtGui.QIcon(self.get_icon("play-round")))
            self.widgets.convert_button.setIconSize(QtCore.QSize(22, 20))

        else:
            self.widgets.convert_button.setText(f"{t('Cancel')}  ")
            self.widgets.convert_button.setIcon(QtGui.QIcon(self.get_icon("black-x")))
            self.widgets.convert_button.setIconSize(QtCore.QSize(22, 20))

    def get_icon(self, name):
        return get_icon(name, self.app.fastflix.config.theme)

    @reusables.log_exception("fastflix", show_traceback=True)
    def encode_video(self):
        if self.app.fastflix.currently_encoding:
            sure = yes_no_message(t("Are you sure you want to stop the current encode?"), title="Confirm Stop Encode")
            if not sure:
                return
            logger.debug(t("Canceling current encode"))
            self.app.fastflix.worker_queue.put(["cancel"])
            self.video_options.queue.reset_pause_encode()
            return

        if self.app.fastflix.conversion_paused:
            return error_message("Queue is currently paused")

        if not self.app.fastflix.conversion_list or self.app.fastflix.current_video:
            add_current = True
            if self.app.fastflix.conversion_list and self.app.fastflix.current_video:
                add_current = yes_no_message("Add current video to queue?", yes_text="Yes", no_text="No")
            if add_current:
                if not self.add_to_queue():
                    return

        for video in self.app.fastflix.conversion_list:
            if video.status.ready:
                video_to_send: Video = video
                break
        else:
            error_message(t("There are no videos to start converting"))
            return

        logger.debug(t("Starting conversion process"))

        self.app.fastflix.currently_encoding = True
        prevent_sleep_mode()
        self.set_convert_button()
        self.send_video_request_to_worker_queue(video_to_send)
        self.disable_all()
        self.video_options.show_status()

    # def get_commands(self):
    #     commands = []
    #     for video in self.get_queue_list():
    #         if video.status.complete or video.status.error:
    #             continue
    #         for command in video.video_settings.conversion_commands:
    #             commands.append(
    #                 (
    #                     video.uuid,
    #                     command.uuid,
    #                     command.command,
    #                     str(video.work_path),
    #                     str(video.video_settings.output_path.stem),
    #                 )
    #             )
    #     return commands

    def add_to_queue(self):
        try:
            code = self.video_options.queue.add_to_queue()
        except FastFlixInternalException as err:
            error_message(str(err))
            return
        else:
            if code is not None:
                return code
        self.video_options.update_queue()
        self.video_options.show_queue()

        # if self.converting:
        #     commands = self.get_commands()
        #     requests = ["add_items", str(self.app.fastflix.log_path), tuple(commands)]
        #     self.app.fastflix.worker_queue.put(tuple(requests))

        self.clear_current_video()
        return True

    # @reusables.log_exception("fastflix", show_traceback=False)
    def conversion_complete(self, success: bool):
        self.paused = False
        allow_sleep_mode()
        self.set_convert_button()

        if not success:
            error_message(t("There was an error during conversion and the queue has stopped"), title=t("Error"))
            self.video_options.queue.new_source()
        else:
            self.video_options.show_queue()
            if reusables.win_based:
                try:
                    show_windows_notification("FastFlix", t("All queue items have completed"), icon_path=main_icon)
                except Exception:
                    message(t("All queue items have completed"), title=t("Success"))
            else:
                message(t("All queue items have completed"), title=t("Success"))

    #
    # @reusables.log_exception("fastflix", show_traceback=False)
    def conversion_cancelled(self, video: Video):
        self.set_convert_button()

        exists = video.video_settings.output_path.exists()

        if exists:
            sm = QtWidgets.QMessageBox()
            sm.setWindowTitle(t("Cancelled"))
            sm.setText(f"{t('Conversion cancelled, delete incomplete file')}\n" f"{video.video_settings.output_path}?")
            sm.addButton(t("Delete"), QtWidgets.QMessageBox.YesRole)
            sm.addButton(t("Keep"), QtWidgets.QMessageBox.NoRole)
            sm.exec_()
            if sm.clickedButton().text() == t("Delete"):
                try:
                    video.video_settings.output_path.unlink(missing_ok=True)
                except OSError:
                    pass

    @reusables.log_exception("fastflix", show_traceback=True)
    def dropEvent(self, event):
        if not event.mimeData().hasUrls:
            return event.ignore()

        event.setDropAction(QtCore.Qt.CopyAction)
        event.accept()

        if self.app.fastflix.current_video:
            discard = yes_no_message(
                f'{t("There is already a video being processed")}<br>' f'{t("Are you sure you want to discard it?")}',
                title="Discard current video",
            )
            if not discard:
                return

        try:
            self.input_video = Path(clean_file_string(event.mimeData().urls()[0].toLocalFile()))
        except (ValueError, IndexError):
            return event.ignore()
        self.source_video_path_widget.setText(str(self.input_video))
        self.video_path_widget.setText(str(self.input_video))
        try:
            self.update_video_info()
        except Exception:
            logger.exception(f"Could not load video {self.input_video}")
            self.video_path_widget.setText("")
            self.output_video_path_widget.setText("")
            self.output_video_path_widget.setDisabled(True)
            self.output_path_button.setDisabled(True)
        self.page_update()

    def dragEnterEvent(self, event):
        event.accept() if event.mimeData().hasUrls else event.ignore()

    def dragMoveEvent(self, event):
        event.accept() if event.mimeData().hasUrls else event.ignoreAF()

    def status_update(self, status_response):
        response = Response(*status_response)
        logger.debug(f"Updating queue from command worker: {response}")

        video_to_send: Optional[Video] = None
        errored = False
        same_video = False

        for video in self.app.fastflix.conversion_list:
            if response.video_uuid == video.uuid:
                video.status.running = False

                if response.status == "cancelled":
                    video.status.cancelled = True
                    self.end_encoding()
                    self.conversion_cancelled(video)
                    self.video_options.update_queue()
                    return

                if response.status == "complete":
                    video.status.current_command += 1
                    if len(video.video_settings.conversion_commands) > video.status.current_command:
                        same_video = True
                        video_to_send = video
                        break
                    else:
                        video.status.complete = True

                if response.status == "error":
                    video.status.error = True
                    errored = True
                break

        if errored and not self.video_options.queue.ignore_errors.isChecked():
            self.conversion_complete(success=False)
            self.end_encoding()
            return

        if not video_to_send:
            for video in self.app.fastflix.conversion_list:
                if video.status.ready:
                    video_to_send = video
                    # TODO ensure command int is in command list?
                    break

        if not video_to_send:
            self.conversion_complete(success=True)
            self.end_encoding()
            return

        self.app.fastflix.currently_encoding = True
        if not same_video and self.app.fastflix.conversion_paused:
            return self.end_encoding()

        self.send_video_request_to_worker_queue(video_to_send)

    def end_encoding(self):
        self.app.fastflix.currently_encoding = False
        allow_sleep_mode()
        self.video_options.queue.run_after_done()
        self.video_options.update_queue()
        self.set_convert_button()

    def send_next_video(self) -> bool:
        if not self.app.fastflix.currently_encoding:
            for video in self.app.fastflix.conversion_list:
                if video.status.ready:
                    video.status.running = True
                    self.send_video_request_to_worker_queue(video)
                    self.app.fastflix.currently_encoding = True
                    prevent_sleep_mode()
                    self.set_convert_button()
                    return True
        self.app.fastflix.currently_encoding = False
        allow_sleep_mode()
        self.set_convert_button()
        return False

    def send_video_request_to_worker_queue(self, video: Video):
        command = video.video_settings.conversion_commands[video.status.current_command]
        self.app.fastflix.currently_encoding = True
        prevent_sleep_mode()

        # logger.info(f"Sending video {video.uuid} command {command.uuid} called from {inspect.stack()}")

        self.app.fastflix.worker_queue.put(
            Request(
                request="execute",
                video_uuid=video.uuid,
                command_uuid=command.uuid,
                command=command.command,
                work_dir=str(video.work_path),
                log_name=video.video_settings.video_title or video.video_settings.output_path.stem,
            )
        )
        video.status.running = True
        self.video_options.update_queue()

    def find_video(self, uuid) -> Video:
        for video in self.app.fastflix.conversion_list:
            if uuid == video.uuid:
                return video
        raise FlixError(f'{t("No video found for")} {uuid}')

    def find_command(self, video: Video, uuid) -> int:
        for i, command in enumerate(video.video_settings.conversion_commands, start=1):
            if uuid == command.uuid:
                return i
        raise FlixError(f'{t("No command found for")} {uuid}')


class Notifier(QtCore.QThread):
    def __init__(self, parent, app, status_queue):
        super().__init__(parent)
        self.app = app
        self.main: Main = parent
        self.status_queue = status_queue

    def __del__(self):
        self.wait()

    def run(self):
        while True:
            # Message looks like (command, video_uuid, command_uuid)
            status = self.status_queue.get()
            self.app.processEvents()
            if status[0] == "exit":
                logger.debug("GUI received ask to exit")
                try:
                    self.terminate()
                finally:
                    self.main.close_event.emit()
                return
            self.main.status_update_signal.emit(status)
            self.app.processEvents()
            # if status[0] == "complete":
            #     logger.debug("GUI received status queue complete")
            #     self.main.completed.emit(0)
            # elif status[0] == "error":
            #     logger.debug("GUI received status queue errored")
            #     self.main.completed.emit(1)
            # elif status[0] == "cancelled":
            #     logger.debug("GUI received status queue errored")
            #     self.main.cancelled.emit("|".join(status[1:]))

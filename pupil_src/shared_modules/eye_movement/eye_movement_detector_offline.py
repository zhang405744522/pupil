"""
(*)~---------------------------------------------------------------------------
Pupil - eye tracking platform
Copyright (C) 2012-2019 Pupil Labs

Distributed under the terms of the GNU
Lesser General Public License (LGPL v3.0).
See COPYING and COPYING.LESSER for license details.
---------------------------------------------------------------------------~(*)
"""
import typing
import random
from .eye_movement_detector_base import Eye_Movement_Detector_Base
from eye_movement.utils import Gaze_Data, EYE_MOVEMENT_EVENT_KEY, logger
from eye_movement.model.segment import Classified_Segment
from eye_movement.model.storage import Classified_Segment_Storage
from eye_movement.controller.eye_movement_csv_exporter import Eye_Movement_CSV_Exporter
from eye_movement.controller.eye_movement_seek_controller import Eye_Movement_Seek_Controller
from eye_movement.controller.eye_movement_offline_controller import Eye_Movement_Offline_Controller
from eye_movement.ui.menu_content import Menu_Content
from eye_movement.ui.navigation_buttons import Prev_Segment_Button, Next_Segment_Button
from observable import Observable
from data_changed import Listener, Announcer


class Notification_Subject:
    SHOULD_RECALCULATE = "segmentation_detector.should_recalculate"


EYE_MOVEMENT_ANNOUNCER_TOPIC = "eye_movement"


class Offline_Eye_Movement_Detector(Observable, Eye_Movement_Detector_Base):
    """
    Eye movement classification detector based on segmented linear regression.
    """

    MENU_LABEL_TEXT = "Eye Movement Detector"

    def __init__(self, g_pool, show_segmentation=True):
        super().__init__(g_pool)
        self.storage = Classified_Segment_Storage(
            plugin=self,
            rec_dir=g_pool.rec_dir,
        )
        self.seek_controller = Eye_Movement_Seek_Controller(
            plugin=self,
            storage=self.storage,
            seek_to_timestamp=self.seek_to_timestamp,
        )
        self.offline_controller = Eye_Movement_Offline_Controller(
            plugin=self,
            storage=self.storage,
            on_status=self.on_task_status,
            on_progress=self.on_task_progress,
            on_exception=self.on_task_exception,
            on_completed=self.on_task_completed,
        )
        self.menu_content = Menu_Content(
            plugin=self,
            label_text=self.MENU_LABEL_TEXT,
            show_segmentation=show_segmentation,
        )
        self.prev_segment_button = Prev_Segment_Button(
            on_click=self.seek_controller.jump_to_prev_segment
        )
        self.next_segment_button = Next_Segment_Button(
            on_click=self.seek_controller.jump_to_next_segment
        )

        self._gaze_changed_listener = Listener(
            plugin=self,
            topic="gaze_positions",
            rec_dir=g_pool.rec_dir,
        )
        self._gaze_changed_listener.add_observer(
            method_name="on_data_changed",
            observer=self.offline_controller.classify
        )
        self._eye_movement_changed_announcer = Announcer(
            plugin=self,
            topic=EYE_MOVEMENT_ANNOUNCER_TOPIC,
            rec_dir=g_pool.rec_dir,
        )

    #

    def trigger_recalculate(self):
        self.notify_all({
            "subject": Notification_Subject.SHOULD_RECALCULATE,
            "delay": 0.5
        })

    def seek_to_timestamp(self, timestamp):
        self.notify_all({
            "subject": "seek_control.should_seek",
            "timestamp": timestamp,
        })

    def on_task_progress(self, progress: float):
        self.menu_content.update_progress(progress)

    def on_task_status(self, status: str):
        self.menu_content.update_status(status)

    def on_task_exception(self, exception: Exception):
        raise exception

    def on_task_completed(self):
        self._eye_movement_changed_announcer.announce_new()

    #

    def init_ui(self):
        self.add_menu()
        self.menu_content.add_to_menu(self.menu)
        self.prev_segment_button.add_to_quickbar(self.g_pool.quickbar)
        self.next_segment_button.add_to_quickbar(self.g_pool.quickbar)

        if len(self.storage):
            status = "Loaded from cache"
            self.menu_content.update_status(status)
        else:
            self.trigger_recalculate()

    def deinit_ui(self):
        self.remove_menu()
        self.prev_segment_button.remove_from_quickbar(self.g_pool.quickbar)
        self.next_segment_button.remove_from_quickbar(self.g_pool.quickbar)

    def get_init_dict(self):
        return {
            "show_segmentation": self.menu_content.show_segmentation,
        }

    def on_notify(self, notification):
        if notification["subject"] == "gaze_positions_changed":
            # TODO: Remove when gaze_positions will be announced with `data_changed.Announcer`
            note = notification.copy()
            note["subject"] = "data_changed.{}.announce_token".format(self._gaze_changed_listener._topic)
            note["token"] = notification.get("token", "{:0>8x}".format(random.getrandbits(32)))
            self._gaze_changed_listener._on_notify(note)
        elif notification["subject"] == Notification_Subject.SHOULD_RECALCULATE:
            self.offline_controller.classify()
        elif notification["subject"] == "should_export":
            self.export_eye_movement(notification["range"], notification["export_dir"])

    def recent_events(self, events):

        frame = events.get("frame")
        if not frame:
            return

        visible_segments = self.storage.segments_in_frame(frame)
        self.seek_controller.update_visible_segments(visible_segments)

        self.menu_content.update_detail_text(
            current_index=self.seek_controller.current_segment_index,
            total_segment_count=self.seek_controller.total_segment_count,
            current_segment=self.seek_controller.current_segment,
            prev_segment=self.seek_controller.prev_segment,
            next_segment=self.seek_controller.next_segment,
        )

        if self.menu_content.show_segmentation:
            for segment in visible_segments:
                segment.draw_on_frame(frame)

        events[EYE_MOVEMENT_EVENT_KEY] = visible_segments

    def export_eye_movement(self, export_range, export_dir):

        segments_in_section = self.storage.segments_in_range(export_range)

        if segments_in_section:
            csv_exporter = Eye_Movement_CSV_Exporter()
            csv_exporter.csv_export(segments_in_section, export_dir=export_dir)
        else:
            logger.warning("No fixations in this recording nothing to export")

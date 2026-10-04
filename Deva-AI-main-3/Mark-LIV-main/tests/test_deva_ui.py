import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from PyQt6.QtWidgets import QApplication

import ui


class DashboardInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._patches = [
            patch.object(ui, "_read_full_config", return_value={}),
            patch.object(ui.MainWindow, "_check_config", return_value=True),
            patch.object(ui.MainWindow, "_check_autostart", return_value=False),
            patch("memory.config_manager.get_brief_enabled", return_value=False),
        ]
        for item in self._patches:
            item.start()
        self.window = ui.MainWindow("")
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        for item in reversed(self._patches):
            item.stop()

    def test_workspace_mode_updates_selection_and_backend(self):
        changed = []
        self.window.on_mode_change = changed.append

        self.window._mode_buttons["Developer"].click()

        self.assertEqual(self.window._active_mode, "Developer")
        self.assertTrue(self.window._mode_buttons["Developer"].isChecked())
        self.assertEqual(self.window._mode_status_lbl.text(), "WORKSPACE  DEVELOPER")
        self.assertEqual(changed, ["Developer"])

    def test_header_and_sidebar_microphone_buttons_stay_in_sync(self):
        self.window._header_mic_btn.click()
        self.assertTrue(self.window._muted)
        self.assertIn("MUTED", self.window._header_mic_btn.text())
        self.assertIn("MUTED", self.window._mute_btn.text())

        self.window._mute_btn.click()
        self.assertFalse(self.window._muted)
        self.assertEqual(self.window._header_mic_btn.text(), "◉  MIC")

    def test_text_command_reaches_backend_callback(self):
        delivered = threading.Event()
        received = []

        def on_command(text):
            received.append(text)
            delivered.set()

        self.window.on_text_command = on_command
        self.window._input.setText("Check the system status")
        self.window._send()

        self.assertTrue(delivered.wait(2))
        self.assertEqual(received, ["Check the system status"])
        self.assertEqual(self.window._input.text(), "")

    def test_file_ingestion_reports_file_to_backend(self):
        delivered = threading.Event()
        received = []

        def on_command(text):
            received.append(text)
            delivered.set()

        self.window.on_text_command = on_command
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory) / "brief.txt"
            file_path.write_text("project notes", encoding="utf-8")
            self.window._on_file_selected(str(file_path))

            self.assertTrue(delivered.wait(2))
            self.assertIn("[FILE_UPLOADED]", received[0])
            self.assertIn("brief.txt", received[0])
            self.assertIn("brief.txt", self.window._file_hint.text())

    def test_settings_and_navigation_controls_open_their_target(self):
        self.window._navigate("assistant")
        self.assertTrue(self.window._nav_buttons["assistant"].isChecked())

        self.window._navigate("system")
        self.assertIn("control a system setting", self.window._input.placeholderText())
        self.assertTrue(self.window._nav_buttons["system"].isChecked())

        self.window._navigate("settings")
        self.assertIsNotNone(self.window._customize_overlay)
        self.assertTrue(self.window._customize_overlay.isVisible())

    def test_metrics_refresh_updates_visible_system_health(self):
        snapshot = {"cpu": 24.0, "mem": 51.0, "net": 0.4, "gpu": -1.0, "tmp": -1.0}
        with (
            patch.object(ui._metrics, "snapshot", return_value=snapshot),
            patch.object(ui.psutil, "disk_usage", return_value=type(
                "DiskUsage", (), {"percent": 42.0})()),
        ):
            self.window._update_metrics()

        self.assertEqual(self.window._system_status_labels["CPU"].text(), "CPU  24%")
        self.assertEqual(self.window._system_status_labels["RAM"].text(), "RAM  51%")
        self.assertEqual(self.window._system_status_labels["STORAGE"].text(), "STORAGE  42%")
        self.assertEqual(self.window._system_status_labels["GPU"].text(), "GPU  N/A")
        self.assertEqual(self.window._bar_gpu._text, "N/A")
        self.assertEqual(self.window._bar_tmp._text, "N/A")

    def test_network_panel_uses_live_telemetry_and_connection_provider(self):
        snapshot = {
            "cpu": 24.0, "mem": 51.0, "net": 0.4, "gpu": -1.0, "tmp": -1.0,
            "download_mbps": 12.34, "upload_mbps": 2.56, "ping_ms": 18.0,
            "internet_online": True,
        }
        self.window.network_status_provider = lambda: {
            "BACKEND": "CONNECTED", "WEBSOCKET": "LISTENING",
            "AI API": "CONNECTED", "CLOUD": "ONLINE", "DEVICES": "N/A",
        }
        with (
            patch.object(ui._metrics, "snapshot", return_value=snapshot),
            patch.object(ui.psutil, "disk_usage", return_value=type(
                "DiskUsage", (), {"percent": 42.0})()),
        ):
            self.window._update_metrics()
            image = self.window._network_graph.grab()

        self.assertFalse(image.isNull())
        self.assertEqual(self.window._download_value.text(), "12.34 Mbps")
        self.assertEqual(self.window._upload_value.text(), "2.56 Mbps")
        self.assertEqual(self.window._ping_value.text(), "18 ms")
        self.assertEqual(self.window._network_status_labels["INTERNET"].text(), "ONLINE")
        self.assertEqual(self.window._network_status_labels["WEBSOCKET"].text(), "LISTENING")
        self.assertEqual(self.window._network_graph._down_mbps, 12.34)
        self.assertEqual(self.window._network_graph._up_mbps, 2.56)
        self.assertEqual(len(self.window._traffic_graph._download), 2)

    def test_network_panel_displays_unavailable_measurements_without_fabricating_values(self):
        snapshot = {
            "cpu": 24.0, "mem": 51.0, "net": 0.0, "gpu": -1.0, "tmp": -1.0,
            "download_mbps": None, "upload_mbps": None, "ping_ms": None,
            "internet_online": None,
        }
        with (
            patch.object(ui._metrics, "snapshot", return_value=snapshot),
            patch.object(ui.psutil, "disk_usage", return_value=type(
                "DiskUsage", (), {"percent": 42.0})()),
        ):
            self.window._update_metrics()

        self.assertEqual(self.window._download_value.text(), "N/A")
        self.assertEqual(self.window._upload_value.text(), "N/A")
        self.assertEqual(self.window._ping_value.text(), "N/A")
        self.assertTrue(self.window._network_warning.isVisible())
        self.assertEqual(self.window._network_status_labels["INTERNET"].text(), "N/A")
        self.assertIsNone(self.window._traffic_graph._download[-1])

    def test_network_status_provider_is_forwarded_by_ui_facade(self):
        facade = SimpleNamespace(_win=self.window)
        provider = lambda: {"BACKEND": "CONNECTED"}

        ui.JarvisUI.network_status_provider.fset(facade, provider)

        self.assertIs(ui.JarvisUI.network_status_provider.fget(facade), provider)
        self.assertIs(self.window.network_status_provider, provider)

    def test_futuristic_wallpaper_is_installed_and_renders(self):
        backdrop = self.window.centralWidget()
        self.assertIsInstance(backdrop, ui._AiOsBackdrop)
        image = backdrop.grab()
        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)

    def test_command_palette_routes_existing_actions(self):
        self.window._open_command_palette()
        self.assertTrue(self.window._command_overlay.isVisible())

        self.window._run_palette_command("vision")

        self.assertIsNotNone(self.window._vision_overlay)
        self.assertTrue(self.window._vision_overlay.isVisible())

    def test_agent_room_shows_capability_states_and_real_task_events(self):
        self.window._run_palette_command("agents")
        overlay = self.window._agent_room_overlay
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.isVisible())
        overlay.set_snapshot({
            "agents": [{
                "id": "coding", "name": "Coding Agent", "description": "Code tasks.",
                "capabilities": ["edit_code"], "status": "WORKING",
                "current_task": "Check project", "progress": 50,
                "last_action": "dev_agent",
            }],
            "tasks": [{
                "id": "task", "task": "Check project", "agent": "Coding Agent",
                "status": "WORKING", "progress": 50, "start_time": 1, "duration": 2,
            }],
        })
        overlay.add_event({
            "type": "agent.started", "agent": "Coding Agent",
            "message": "Running existing capability: dev_agent", "ts": 1,
        })
        self.app.processEvents()

        self.assertEqual(overlay.roster.count(), 1)
        self.assertEqual(overlay.tasks.count(), 1)
        self.assertEqual(overlay.events.count(), 1)
        self.assertIn("agent.started", overlay.events.item(0).text())

    def test_agent_room_avatar_tracks_live_core_audio_and_camera_states(self):
        self.window._open_agent_room()
        overlay = self.window._agent_room_overlay
        self.window._state_sig.emit("LISTENING")
        self.window._audio_level_sig.emit(0.6)
        self.app.processEvents()

        self.assertEqual(overlay.graph._core_state, "LISTENING")
        self.assertAlmostEqual(overlay.graph._audio_level, 0.6)
        self.assertFalse(overlay.graph._camera_on)
        overlay.set_user_camera_data(
            None, "CAMERA ON · USER DETECTED", 0.7, 0.3
        )
        self.assertTrue(overlay.graph._camera_on)
        self.assertTrue(overlay.graph._user_detected)
        overlay.set_user_camera_data(
            None, "CAMERA OFF · No capture is active."
        )
        self.assertFalse(overlay.graph._camera_on)
        self.assertFalse(overlay.graph._user_detected is True)
        self.assertFalse(overlay.graph.grab().isNull())

    def test_agent_room_scene_focuses_the_live_agent_and_shows_its_task(self):
        self.window._open_agent_room()
        overlay = self.window._agent_room_overlay
        overlay.set_snapshot({
            "agents": [{
                "id": "coding", "name": "Coding Agent", "description": "Code work",
                "capabilities": ["edit_code"], "status": "WORKING",
                "current_task": "Inspect Python project", "progress": 50,
                "last_action": "dev_agent",
            }],
            "tasks": [],
        })

        image = overlay.graph.grab()
        overlay.graph.focus_agent("coding")

        self.assertFalse(image.isNull())
        self.assertIn("coding", overlay.graph._hit_boxes)
        self.assertEqual(overlay.graph._selected_agent, "coding")
        self.assertEqual(overlay.graph._target_zoom, 1.35)
        self.assertIn("Inspect Python project", overlay.agent_detail.text())
        self.assertIn("50%", overlay.agent_detail.text())

    def test_agent_room_renders_the_full_robot_team_in_the_requested_formation(self):
        self.window._open_agent_room()
        graph = self.window._agent_room_overlay.graph
        names = {
            "coding": "Coding Agent", "research": "Research Agent",
            "vision": "Vision Agent", "voice": "Voice Agent",
            "file": "File Agent", "automation": "Automation Agent",
            "memory": "Memory Agent", "system": "System Agent",
            "security": "Security Agent", "study": "Study Agent",
        }
        graph.set_agents([{
            "id": agent_id, "name": name, "status": "READY",
            "progress": 0, "current_task": "",
        } for agent_id, name in names.items()])
        image = graph.grab()

        self.assertFalse(image.isNull())
        self.assertEqual(tuple(graph._ORDER), (
            "coding", "research", "vision", "voice", "file",
            "automation", "memory", "system", "security", "study",
        ))
        self.assertTrue(set(names).issubset(graph._hit_boxes))
        self.assertGreater(graph._last_mesh_face_count, 1000)
        self.assertEqual(len({color.name() for color in graph._AGENT_COLORS.values()}), 10)
        self.assertGreater(graph._last_core_height, max(graph._last_agent_heights.values()))

        positions = {
            agent_id: graph._agent_point(index, graph.width(), graph.height())
            for index, agent_id in enumerate(graph._ORDER)
        }
        for row in (
            ("coding", "research", "vision"),
            ("voice", "file", "automation"),
            ("memory", "system", "security"),
        ):
            self.assertLess(positions[row[0]].x(), positions[row[1]].x())
            self.assertLess(positions[row[1]].x(), positions[row[2]].x())
        self.assertAlmostEqual(positions["study"].x(), graph.width() / 2, delta=2)
        self.assertLess(positions["coding"].y(), positions["voice"].y())
        self.assertLess(positions["voice"].y(), positions["memory"].y())
        self.assertLess(positions["memory"].y(), positions["study"].y())

    def test_agent_room_camera_requires_opt_in_and_stops_on_disable_or_hide(self):
        self.window._open_agent_room()
        overlay = self.window._agent_room_overlay
        self.assertFalse(self.window._user_camera_enabled)
        self.assertEqual(overlay.camera_preview.text(), "CAMERA OFF")

        with patch.object(ui.threading, "Thread") as thread_factory:
            worker = thread_factory.return_value
            worker.is_alive.return_value = False
            next(
                button for button in overlay.findChildren(ui.QPushButton)
                if button.text() == "ENABLE CAMERA"
            ).click()
            self.assertTrue(self.window._user_camera_enabled)
            worker.start.assert_called_once()

            next(
                button for button in overlay.findChildren(ui.QPushButton)
                if button.text() == "DISABLE CAMERA"
            ).click()
            self.assertFalse(self.window._user_camera_enabled)
            self.assertTrue(self.window._cam_stop.is_set())

            self.window._cam_stop.clear()
            next(
                button for button in overlay.findChildren(ui.QPushButton)
                if button.text() == "ENABLE CAMERA"
            ).click()
            overlay.hide()
            self.assertFalse(self.window._user_camera_enabled)
            self.assertTrue(self.window._cam_stop.is_set())

    def test_user_camera_does_not_interrupt_an_active_vision_camera(self):
        self.window._open_agent_room()
        overlay = self.window._agent_room_overlay
        self.window._cam_thread = SimpleNamespace(is_alive=lambda: True)
        self.window._camera_mode = "vision"
        self.window._cam_stop.clear()

        next(
            button for button in overlay.findChildren(ui.QPushButton)
            if button.text() == "ENABLE CAMERA"
        ).click()

        self.assertFalse(self.window._cam_stop.is_set())
        self.assertFalse(self.window._user_camera_enabled)
        self.app.processEvents()
        self.assertIn("Another camera operation", overlay.camera_status.text())

    def test_user_camera_stop_waits_for_worker_release_when_requested(self):
        worker = SimpleNamespace(
            is_alive=Mock(side_effect=[True, False]),
            join=Mock(),
        )
        self.window._cam_thread = worker
        self.window._camera_mode = "user"
        self.window._user_camera_enabled = True

        stopped = self.window.stop_user_camera_monitor(wait=True)

        self.assertTrue(stopped)
        self.assertTrue(self.window._cam_stop.is_set())
        worker.join.assert_called_once_with(timeout=2.0)
        self.assertEqual(self.window._camera_mode, "off")

    def test_vision_center_requires_explicit_capture_action(self):
        delivered = threading.Event()
        received = []
        self.window.on_text_command = lambda text: (received.append(text), delivered.set())
        self.window._open_vision_center()
        self.window._vision_overlay.prompt.setText("Read the chart")
        buttons = self.window._vision_overlay.findChildren(ui.QPushButton)
        next(button for button in buttons if "SCREEN VIEW" in button.text()).click()

        self.assertTrue(delivered.wait(2))
        self.assertIn("Capture my screen once", received[0])
        self.assertIn("Read the chart", received[0])

    def test_task_planner_uses_the_live_assistant_callback(self):
        delivered = threading.Event()
        received = []
        self.window.on_text_command = lambda text: (received.append(text), delivered.set())
        self.window._open_task_planner()
        self.window._task_overlay.request.setText("Organize the project notes")
        self.window._task_overlay._start()

        self.assertTrue(delivered.wait(2))
        self.assertIn("numbered high-level plan", received[0])
        self.assertIn("Organize the project notes", received[0])

    def test_context_and_task_lifecycle_indicators_update(self):
        self.window._context_sig.emit("ACTIVE 4")
        self.window._task_sig.emit("EXECUTING", "Opening a supported tool")
        self.app.processEvents()

        self.assertEqual(self.window._context_badge.text(), "CTX  ACTIVE 4")
        self.assertEqual(self.window._task_badge.text(), "TASK  EXECUTING")

    def test_memory_center_opens_with_user_controlled_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("memory.memory_manager.MEMORY_PATH", Path(directory) / "memory.json"):
                self.window._open_memory_panel()
                self.app.processEvents()
                overlay = self.window._memory_overlay
                self.assertTrue(overlay.isVisible())
                labels = {button.text() for button in overlay.findChildren(ui.QPushButton)}
                self.assertIn("ADD MEMORY", labels)
                self.assertIn("CLEAR ALL", labels)

    def test_window_loads_the_jarvis_application_icon(self):
        self.assertFalse(self.window.windowIcon().isNull())

    def test_desktop_layout_remains_usable_at_target_resolutions(self):
        for width, height in (
            (1920, 1080), (1600, 900), (1366, 768), (1280, 720),
        ):
            with self.subTest(resolution=f"{width}x{height}"):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(self.window.width(), width)
                self.assertGreater(self.window._center_split.width(), 250)
                self.assertGreater(self.window._right_panel.width(), 0)
                self.assertIsInstance(self.window._right_panel.layout().itemAt(0).widget(),
                                      ui.QScrollArea)


if __name__ == "__main__":
    unittest.main()

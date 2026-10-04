
import asyncio
import json
import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import main
from agent_orchestrator import AgentOrchestrator


class BackendWiringTests(unittest.TestCase):
    def setUp(self):
        self.live = object.__new__(main.JarvisLive)
        self.live.ui = Mock()
        self.live._session_log = []
        self.live._last_user_input = ""
        self.live._last_user_input_at = main.time.monotonic()
        self.live._pending_memory_approval = None
        self.live._awaiting_memory_confirmation = None
        self.live._task_active = False
        self.live._loop = None
        self.live._dashboard = None
        self.live._agent_orchestrator = AgentOrchestrator(self.live._on_agent_event)
        self.live._agent_orchestrator.configure({"system_status"})

    def test_workspace_mode_requests_context_preserving_reconnect(self):
        self.live._ai_mode = "General"
        self.live.request_reconnect = Mock()

        self.live._on_mode_change("Developer")

        self.assertEqual(self.live._ai_mode, "Developer")
        self.live.request_reconnect.assert_called_once_with(
            keep_context=True, reason="Developer workspace")

    def test_invalid_workspace_mode_is_reported(self):
        self.live._ai_mode = "General"
        self.live.request_reconnect = Mock()

        self.live._on_mode_change("Unknown")

        self.assertEqual(self.live._ai_mode, "General")
        self.live.request_reconnect.assert_not_called()
        self.live.ui.write_log.assert_called_once()

    def test_workspace_instructions_are_added_to_live_session_config(self):
        self.live._ai_mode = "Developer"
        self.live._resume_handle = None
        self.live._enhanced_live = False
        self.live._tuned_live = False
        self.live._action_registry = SimpleNamespace(get_tool_declarations=lambda: [])
        self.live._plugin_registry = SimpleNamespace(get_tool_declarations=lambda: [])

        with (
            patch("builtins.open", mock_open(read_data=json.dumps({
                "assistant_name": "JARVIS",
            }))),
            patch.object(main, "load_memory", return_value={}),
            patch.object(main, "format_memory_for_prompt", return_value=""),
            patch.object(main, "_load_system_prompt", return_value="base instructions"),
            patch.object(main, "_render_prompt", side_effect=lambda prompt, _values: prompt),
            patch.object(main, "_describe_tools", return_value=""),
            patch.object(main, "_describe_limits", return_value=""),
            patch.object(main, "get_voice", return_value="Charon"),
        ):
            config = self.live._build_config()

        self.assertIn("[ACTIVE WORKSPACE: Developer]", config.system_instruction)
        self.assertIn("software engineering", config.system_instruction)

    def test_typed_command_is_queued_while_live_session_connects(self):
        self.live._loop = object()
        self.live.session = None
        self.live._wake_enabled = False
        self.live._pending_text_commands = queue.Queue(maxsize=20)

        self.live._on_text_command("Check system status")

        queued = self.live._pending_text_commands.get_nowait()
        self.assertTrue(queued.startswith("Check system status"))
        self.assertIn("ORCHESTRATOR ROUTE: System Agent", queued)
        self.live.ui.write_log.assert_any_call("SYS: Assistant is connecting; command queued.")
        self.live.ui.set_context_status.assert_called_once_with("ACTIVE 1")

    def test_queued_commands_are_delivered_when_session_connects(self):
        self.live._pending_text_commands = queue.Queue()
        self.live._pending_text_commands.put("Check system status")
        sent = []

        class Session:
            async def send_client_content(self, **kwargs):
                sent.append(kwargs)

        asyncio.run(self.live._flush_pending_text_commands(Session()))

        self.assertEqual(len(sent), 1)
        self.assertEqual(
            sent[0]["turns"]["parts"][0]["text"], "Check system status")
        self.assertTrue(self.live._pending_text_commands.empty())

    def test_tone_hints_are_uncertain_and_only_change_response_style(self):
        self.assertIn("Explain simply", main._response_style_hint(
            "I'm confused; I don't understand this."
        ))
        self.assertIn("action-first", main._response_style_hint("Urgent, help ASAP"))
        self.assertEqual(main._response_style_hint("What is the weather?"), "")

    def test_memory_consent_rejects_recall_questions_and_negation(self):
        self.assertFalse(main._explicit_memory_request("What do you remember about me?"))
        self.assertFalse(main._explicit_memory_request("Do not save this."))
        self.assertTrue(main._explicit_memory_request("Please remember that I prefer tea."))

    def test_context_clear_requires_explicit_non_negated_request(self):
        self.assertTrue(main._explicit_clear_context_request("Clear our conversation context"))
        self.assertTrue(main._explicit_clear_context_request("Start a fresh conversation"))
        self.assertFalse(main._explicit_clear_context_request("Do not clear the chat"))
        self.assertFalse(main._explicit_clear_context_request("What do you remember?"))

    def test_memory_write_requires_explicit_request_or_matching_confirmation(self):
        args = {"category": "preferences", "key": "drink", "value": "tea"}
        fc = SimpleNamespace(name="save_memory", args=args, id="call")
        self.live.ui.muted = False
        self.live._last_user_input = "I usually prefer tea."

        with patch.object(main, "update_memory") as update:
            first = asyncio.run(self.live._execute_tool(fc))
            update.assert_not_called()
            self.assertIn("Not saved yet", first.response["result"])
            self.live._last_user_input = "Yes, please."
            second = asyncio.run(self.live._execute_tool(fc))

        update.assert_called_once()
        self.assertIn("Saved", second.response["result"])

    def test_memory_write_accepts_yes_after_assistant_asked_and_matches_the_fact(self):
        self.live.ui.muted = False
        self.live._last_user_input = "Yes, please."
        self.live._last_user_input_at = main.time.monotonic()
        self.live._awaiting_memory_confirmation = (
            "I prefer green tea.", main.time.monotonic())
        fc = SimpleNamespace(
            name="save_memory",
            args={"category": "preferences", "key": "drink", "value": "green tea"},
            id="call",
        )

        with patch.object(main, "update_memory") as update:
            response = asyncio.run(self.live._execute_tool(fc))

        update.assert_called_once()
        self.assertIn("Saved", response.response["result"])

    def test_memory_write_refuses_sensitive_values(self):
        self.live.ui.muted = False
        self.live._last_user_input = "Remember my API key."
        self.live._pending_memory_approval = None
        fc = SimpleNamespace(
            name="save_memory",
            args={"category": "notes", "key": "credential", "value": "API key: secret-123"},
            id="call",
        )

        with patch.object(main, "update_memory") as update:
            response = asyncio.run(self.live._execute_tool(fc))

        update.assert_not_called()
        self.assertIn("sensitive", response.response["result"])

    def test_network_status_reports_live_session_and_dashboard_websocket_state(self):
        self.live.session = object()
        self.live._dashboard = SimpleNamespace(
            _clients={object()}, _serving=True)

        self.assertEqual(self.live._network_status_snapshot(), {
            "BACKEND": "CONNECTED",
            "WEBSOCKET": "CONNECTED",
            "AI API": "CONNECTED",
            "CLOUD": "ONLINE",
            "DEVICES": "CONNECTED",
        })

        self.live.session = None
        self.live._dashboard._clients.clear()
        self.assertEqual(self.live._network_status_snapshot(), {
            "BACKEND": "CONNECTED",
            "WEBSOCKET": "LISTENING",
            "AI API": "OFFLINE",
            "CLOUD": "OFFLINE",
            "DEVICES": "N/A",
        })

    def test_agent_events_are_forwarded_to_the_existing_dashboard_websocket(self):
        received = []

        class Dashboard:
            async def broadcast(self, event):
                received.append(event)

        async def exercise():
            self.live._loop = asyncio.get_running_loop()
            self.live._dashboard = Dashboard()
            event = {
                "type": "agent.assigned", "task_id": "task-1", "task": "Check code",
                "agent_id": "coding", "agent": "Coding Agent",
                "status": "ASSIGNED", "message": "", "ts": 1.0,
            }
            self.live._on_agent_event(event)
            await asyncio.sleep(0)
            return event

        expected = asyncio.run(exercise())

        self.assertEqual(received, [expected])
        self.live.ui.record_agent_event.assert_called_once_with(expected)
        self.live.ui.set_agent_room_data.assert_called_once()


if __name__ == "__main__":
    unittest.main()

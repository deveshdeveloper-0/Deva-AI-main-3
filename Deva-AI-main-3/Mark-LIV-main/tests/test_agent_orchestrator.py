import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from agent_orchestrator import AgentOrchestrator, route_agents


class AgentOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.orchestrator = AgentOrchestrator(self.events.append)
        self.orchestrator.configure({
            "dev_agent", "code_helper", "file_processor", "file_controller",
            "web_search", "screen_process", "save_memory", "recall_memory",
            "system_status",
        })

    def test_coding_request_routes_only_to_installed_coding_capability(self):
        self.assertEqual(
            route_agents("Check my Python project and fix errors", {"dev_agent"}),
            ["coding"],
        )
        task = self.orchestrator.begin("Check my Python project and fix errors")
        self.assertEqual(task.agents, ["coding"])
        self.assertEqual([event["type"] for event in self.events],
                         ["task.created", "agent.assigned"])
        self.assertEqual(self.orchestrator.start_tool("dev_agent"), "coding")
        self.orchestrator.finish_tool("dev_agent", True)
        self.orchestrator.finish(True, "Result returned.")
        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(self.events[-1]["type"], "task.completed")

    def test_document_quiz_is_routed_as_a_real_multistep_capability_chain(self):
        task = self.orchestrator.begin("Analyze this PDF and create a quiz")
        self.assertEqual(task.agents, ["file", "study"])
        self.assertEqual(self.orchestrator.start_tool("file_processor"), "file")
        self.orchestrator.finish_tool("file_processor", True)
        self.orchestrator.finish(True, "Quiz returned from JARVIS Live.")
        self.assertEqual(task.status, "COMPLETED")
        self.assertIn("study", task.completed_agents)
        self.assertEqual(
            [event["type"] for event in self.events].count("agent.completed"), 2
        )

    def test_unavailable_agents_are_not_selected_or_reported_as_working(self):
        self.orchestrator.configure({"web_search"})
        snapshot = self.orchestrator.snapshot()
        security = next(agent for agent in snapshot["agents"] if agent["id"] == "security")
        self.assertFalse(security["available"])
        self.assertEqual(security["status"], "WAITING")
        self.assertEqual(
            route_agents("Run a security vulnerability audit", {"web_search"}), []
        )

    def test_no_tool_execution_fails_instead_of_claiming_completion(self):
        task = self.orchestrator.begin("Search for recent research")
        self.orchestrator.finish(True, "Assistant returned a response.")
        self.assertEqual(task.status, "FAILED")
        self.assertEqual(self.events[-1]["type"], "task.failed")
        self.assertIn("No routed backend tool", self.events[-1]["message"])

    def test_tool_failure_stops_multistep_workflow(self):
        task = self.orchestrator.begin("Analyze this PDF and create a quiz")
        self.orchestrator.start_tool("file_processor")
        self.orchestrator.finish_tool("file_processor", False)
        self.assertEqual(task.status, "FAILED")
        self.assertIsNone(self.orchestrator.start_tool("file_processor"))
        self.assertEqual(self.events[-1]["type"], "task.failed")

    def test_cancel_stops_future_steps_but_tracks_an_already_running_tool(self):
        task = self.orchestrator.begin("Check my Python project and fix errors")
        self.orchestrator.start_tool("dev_agent")

        self.assertTrue(self.orchestrator.cancel_active())
        self.assertEqual(task.status, "CANCELLING")
        self.assertIsNone(self.orchestrator.start_tool("code_helper"))
        self.orchestrator.finish_tool("dev_agent", True)

        self.assertEqual(task.status, "CANCELLED")
        self.assertTrue(self.orchestrator.is_terminal(task.id))
        self.assertEqual(self.events[-1]["type"], "task.cancelled")

    def test_new_task_is_not_misrepresented_as_queued_when_one_is_active(self):
        current = self.orchestrator.begin("Search for recent research")
        self.assertIsNone(self.orchestrator.begin("Check my Python project"))
        self.assertTrue(self.orchestrator.has_active_task)
        self.assertEqual(current.status, "ASSIGNED")

    def test_task_labels_redact_credential_values(self):
        task = self.orchestrator.begin("Review my code config api_key=do-not-show")
        self.assertIn("redacted", task.label)
        self.assertNotIn("do-not-show", str(self.orchestrator.snapshot()))


if __name__ == "__main__":
    unittest.main()

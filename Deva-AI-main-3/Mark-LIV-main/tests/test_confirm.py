import threading
import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from core import confirm


class ConfirmationCallbackTests(unittest.TestCase):
    def tearDown(self):
        confirm.resolve(False)
        confirm.bind(None, None)

    def test_action_runs_only_after_explicit_acceptance(self):
        shown = []
        started = threading.Event()
        completed = threading.Event()
        results = []
        ran = []
        confirm.bind(show=lambda title, detail: shown.append((title, detail)),
                     hide=lambda: None)

        response = confirm.request(
            "test-send", "Send a message", "Recipient and message",
            run=lambda: (ran.append(True) or "Sent"),
            on_started=started.set,
            on_resolved=lambda accepted, result: (
                results.append((accepted, result)), completed.set()
            ),
        )

        self.assertIn("[CONFIRMATION_PENDING]", response)
        self.assertEqual(ran, [])
        self.assertEqual(len(shown), 1)
        confirm.resolve(True)

        self.assertTrue(started.is_set())
        self.assertTrue(completed.wait(2))
        self.assertEqual(ran, [True])
        self.assertEqual(results, [(True, "Sent")])

    def test_declining_confirmation_never_runs_action(self):
        completed = threading.Event()
        results = []
        ran = []
        confirm.bind(show=lambda *_: None, hide=lambda: None)
        confirm.request(
            "test-delete", "Delete a file", "File path",
            run=lambda: (ran.append(True) or "Deleted"),
            on_resolved=lambda accepted, result: (
                results.append((accepted, result)), completed.set()
            ),
        )

        confirm.resolve(False)

        self.assertTrue(completed.is_set())
        self.assertEqual(ran, [])
        self.assertFalse(results[0][0])
        self.assertIn("declined", results[0][1])


if __name__ == "__main__":
    unittest.main()

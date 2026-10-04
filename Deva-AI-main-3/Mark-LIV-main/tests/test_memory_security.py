import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from memory import memory_manager


class MemorySecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "long_term.json"
        self.patch = patch.object(memory_manager, "MEMORY_PATH", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp_dir.cleanup()

    def test_sensitive_values_are_refused_and_never_returned_to_context(self):
        self.assertTrue(memory_manager.is_sensitive_memory("password: dont-store"))
        self.assertTrue(memory_manager.is_sensitive_memory("4111 1111 1111 1111"))
        self.assertTrue(memory_manager.is_sensitive_memory("My salary is $95000"))
        self.assertTrue(memory_manager.is_sensitive_memory(
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789"))
        self.assertFalse(memory_manager.is_sensitive_memory("Prefers green tea"))
        self.assertEqual(
            memory_manager.redact_sensitive_text("My API key is private"),
            "[sensitive content redacted]",
        )
        memory_manager.remember("secret", "password: dont-store", "notes")
        self.assertEqual(memory_manager.all_entries_for_ui(), [])
        self.assertEqual(memory_manager.format_memory_for_prompt(
            memory_manager.load_memory()), "")

    def test_explicitly_added_memory_has_metadata_and_can_be_edited_archived(self):
        memory_manager.remember("theme", "cyan", "preferences")
        row = memory_manager.all_entries_for_ui()[0]
        self.assertEqual(row["source"], "user")
        self.assertEqual(row["status"], "ACTIVE")
        self.assertTrue(row["created"])

        result = memory_manager.edit_memory_entry(
            "theme", "electric blue", "preferences")
        self.assertIn("Updated", result)
        self.assertEqual(
            memory_manager.all_entries_for_ui()[0]["value"], "electric blue")

        result = memory_manager.archive_memory_entry("theme", "preferences")
        self.assertIn("Archived", result)
        self.assertEqual(memory_manager.all_entries_for_ui(), [])
        self.assertIn("preferences/theme", memory_manager.load_memory()["archived"])

    def test_clear_memory_removes_active_and_archived_entries(self):
        memory_manager.remember("topic", "DEVA project", "projects")
        memory_manager.archive_memory_entry("topic", "projects")
        memory_manager.clear_memory()

        self.assertEqual(memory_manager.all_entries_for_ui(), [])
        self.assertEqual(memory_manager.load_memory()["archived"], {})

    def test_legacy_session_summaries_are_removed_and_not_saved_without_approval(self):
        memory_manager.save_memory({
            **memory_manager._empty_memory(),
            "sessions": [{"date": "2025-01-01", "summary": "private chat"}],
        })
        memory_manager.save_session_summary("another private chat")

        self.assertTrue(memory_manager.clear_session_summaries())
        self.assertNotIn("sessions", memory_manager.load_memory())


if __name__ == "__main__":
    unittest.main()

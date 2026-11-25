"""All native mutation modes must honor the workspace mounted for the task."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.code_tools import file_operations
from src.code_tools.edit_file import RangeReplaceEditor
from src.code_tools.edit_file_normal import StringReplaceEditor


class WorkspaceMutationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.parent = Path(directory.name)
        self.root = self.parent / "workspace"
        self.root.mkdir()
        scope = patch.object(file_operations, "WORKSPACE", self.root)
        scope.start()
        self.addCleanup(scope.stop)
        self.files = file_operations.FileOperations()
        self.anchor_editor = RangeReplaceEditor(self.root)
        self.normal_editor = StringReplaceEditor(self.root)

    def test_native_writes_and_both_editors_decline_outside_targets(self):
        outside = self.parent / "app.txt"
        outside.write_text("original\n", encoding="utf-8")
        anchor = {"remove_line_number": "1", "content_to_remove": "original", "replace_content": "updated"}
        for path in (str(outside), "../app.txt"):
            with self.subTest(path=path):
                outcomes = [
                    self.files.full_file_write(path, "updated"),
                    self.files.delete_file(path),
                    self.normal_editor.edit(path, "original", "updated")[0],
                    self.anchor_editor.edit(path, [anchor])[0],
                ]
                self.assertTrue(all("outside the workspace" in result for result in outcomes), outcomes)
                self.assertEqual(outside.read_text(encoding="utf-8"), "original\n")

        allowed = self.root / "example.txt"
        self.assertIn("Created", self.files.full_file_write(str(allowed), "original\n"))
        self.normal_editor.edit("example.txt", "original", "updated")
        anchor.update(content_to_remove="updated", replace_content="final")
        self.anchor_editor.edit("example.txt", [anchor])
        self.assertEqual(allowed.read_text(encoding="utf-8").strip(), "final")
        self.assertIn("Successfully deleted", self.files.delete_file("example.txt"))

    def test_workspace_root_cannot_be_deleted(self):
        (self.root / "keep.txt").write_text("keep", encoding="utf-8")
        for path in (".", str(self.root), "nested/.."):
            self.assertIn("workspace root", self.files.delete_file(path))
            self.assertTrue((self.root / "keep.txt").is_file())

    def test_symlink_targets_are_checked_and_internal_deletion_unlinks_only_the_link(self):
        outside = self.parent / "outside.txt"
        outside.write_text("unchanged", encoding="utf-8")
        external_link = self.root / "outside-link.txt"
        internal = self.root / "keep.txt"
        internal.write_text("keep", encoding="utf-8")
        internal_link = self.root / "internal-link.txt"
        try:
            external_link.symlink_to(outside)
            internal_link.symlink_to(internal)
        except OSError:
            self.skipTest("symbolic links are not available")
        self.assertIn("outside the workspace", self.files.full_file_write("outside-link.txt", "changed"))
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")
        self.assertIn("Successfully deleted", self.files.delete_file("internal-link.txt"))
        self.assertFalse(internal_link.exists())
        self.assertTrue(internal.is_file())

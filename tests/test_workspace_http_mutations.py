import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from gateway.api import app


class WorkspaceHTTPMutationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name) / "workspace"
        self.workspace.mkdir()
        scope = patch("gateway.routes._get_workspace", return_value=self.workspace)
        scope.start()
        self.addCleanup(scope.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def upload(self, project, members):
        data = io.BytesIO()
        with zipfile.ZipFile(data, mode="w") as archive:
            for path in members:
                archive.writestr(path, "sample")
        return self.client.post(
            "/api/workspace/upload", data={"project_name": project},
            files={"archive": ("project.zip", data.getvalue(), "application/zip")},
        )

    def test_upload_and_delete_stay_inside_workspace_and_protect_workspace_root(self):
        self.assertEqual(self.upload("..", ["fixture.txt"]).status_code, 403)
        self.assertFalse((self.workspace.parent / "fixture.txt").exists())
        outcome = self.upload("project", ["dir/fixture.txt"])
        self.assertEqual(outcome.status_code, 200, outcome.text)
        self.assertEqual(outcome.json()["files_uploaded"], 1)
        self.assertEqual(self.client.post("/api/files/delete", json={"path": "."}).status_code, 400)
        self.assertTrue((self.workspace / "project" / "dir" / "fixture.txt").is_file())

    def test_nested_upload_paths_stay_within_the_project(self):
        project = self.workspace / "project"
        project.mkdir()
        sibling = self.workspace / "sibling"
        sibling.mkdir()
        try:
            (project / "linked-dir").symlink_to(sibling, target_is_directory=True)
        except OSError:
            self.skipTest("symbolic links are not available")

        outcome = self.upload("project", ["linked-dir/blocked.txt", "inside.txt"])
        self.assertEqual(outcome.status_code, 200, outcome.text)
        self.assertEqual(outcome.json()["files_uploaded"], 1)
        self.assertFalse((sibling / "blocked.txt").exists())
        self.assertTrue((project / "inside.txt").is_file())
        deleted = self.client.post("/api/files/delete", json={"path": "project/linked-dir"})
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse((project / "linked-dir").is_symlink())
        self.assertTrue(sibling.is_dir())

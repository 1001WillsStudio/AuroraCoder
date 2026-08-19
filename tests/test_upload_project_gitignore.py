"""Regression: Upload Project must not silently drop .gitignore matches.

Explorer (fresh page load): Upload Project on a folder with README.md,
secret.txt, and a .gitignore whose only pattern is ``secret.txt``. The
Workspace tree showed only README.md; GET /api/files/read for secret.txt
was 404. The client zip packer skipped paths where ignore.ignores() was
true, with no toast or count. The tree also hides .gitignore (dotfile),
so the skip was invisible.

There is no JS test runner here, so behaviour is locked two ways:
  * the extracted helper in ``frontend/src/utils/workspaceUpload.js`` is
    executed with Node (hermetic: no network, no DOM);
  * a source scan asserts the upload client packs via that helper and
    does not import the ``ignore`` package.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "frontend" / "src" / "utils" / "workspaceUpload.js"
API = ROOT / "frontend" / "src" / "services" / "api.js"


def _eval_js(expr: str):
    script = (
        "import { workspaceUploadEntries } from "
        f"{json.dumps(HELPER.resolve().as_uri())}\n"
        f"const out = {expr}\n"
        "console.log(JSON.stringify(out))\n"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or "node helper failed")
    return json.loads(proc.stdout)


def _paths(files):
    return _eval_js(
        f"workspaceUploadEntries({json.dumps(files)}).map(e => e.inProject)"
    )


@pytest.mark.unit
def test_fixtures4_secret_txt_is_packed_despite_root_gitignore():
    """The reported folder: README.md, secret.txt, .gitignore → secret.txt."""
    files = [
        {"webkitRelativePath": "fixtures4/README.md", "name": "README.md"},
        {"webkitRelativePath": "fixtures4/secret.txt", "name": "secret.txt"},
        {"webkitRelativePath": "fixtures4/.gitignore", "name": ".gitignore"},
    ]
    paths = _paths(files)
    assert "README.md" in paths
    assert "secret.txt" in paths
    assert ".gitignore" in paths
    assert paths == ["README.md", "secret.txt", ".gitignore"]


@pytest.mark.unit
def test_env_file_is_packed_when_gitignore_lists_dotenv():
    """Same skip happened for fixtures3/.env when .gitignore contained '.env'."""
    files = [
        {"webkitRelativePath": "fixtures3/.env", "name": ".env"},
        {"webkitRelativePath": "fixtures3/.gitignore", "name": ".gitignore"},
        {"webkitRelativePath": "fixtures3/app.py", "name": "app.py"},
    ]
    paths = _paths(files)
    assert ".env" in paths
    assert "app.py" in paths


@pytest.mark.unit
def test_nested_and_git_paths_are_packed():
    files = [
        {"webkitRelativePath": "proj/.git/HEAD", "name": "HEAD"},
        {"webkitRelativePath": "proj/src/main.py", "name": "main.py"},
        {"webkitRelativePath": "proj", "name": "proj"},
    ]
    paths = _paths(files)
    assert ".git/HEAD" in paths
    assert "src/main.py" in paths
    assert "" not in paths


@pytest.mark.unit
def test_upload_client_does_not_apply_gitignore():
    """The zip packer must send every selected file, not filter via ignore."""
    src = API.read_text(encoding="utf-8")
    assert "workspaceUploadEntries" in src
    assert "from '../utils/workspaceUpload'" in src or "from '../utils/workspaceUpload.js'" in src
    assert "import('ignore')" not in src
    assert "ig.ignores" not in src

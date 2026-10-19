import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Run in a fresh interpreter: other unit tests replace fastapi/starlette with
# MagicMock stubs in sys.modules, so importing the real app in-process would
# pick up those stubs depending on test order.
_RENDER_SCRIPT = """
from fastapi.testclient import TestClient
import plex.plexserver as plexserver


async def _noop_guess_host_ip(request):
    return None


plexserver.guess_host_ip = _noop_guess_host_ip
client = TestClient(plexserver.plex_server)

for path in ("/", "/virtual-devices"):
    response = client.get(path)
    assert response.status_code == 200, (
        f"{path} returned {response.status_code}: {response.text[:500]}"
    )
    assert "<html" in response.text.lower(), f"{path} did not return an HTML page"
"""


def test_web_pages_render_with_installed_starlette(tmp_path):
    """Both HTML routes must render with the installed Starlette version.

    Regression test: Starlette 1.x removed the old
    TemplateResponse(name, context) signature, so the pages returned
    "Internal Server Error" until callers passed request first.
    """
    env = os.environ.copy()
    env["CONFIG_PATH"] = str(tmp_path / "config")

    result = subprocess.run(
        [sys.executable, "-c", _RENDER_SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr

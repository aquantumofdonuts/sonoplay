"""Unit tests for PlexDlnaAdapter transport operation tracking."""

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy transitive dependencies so we can import plex.adapters
# in isolation without needing the full runtime dependency tree.
# ---------------------------------------------------------------------------

def _make_stub(name):
    """Create a stub module whose every attribute is a MagicMock."""
    mod = types.ModuleType(name)
    mod.__path__ = []           # mark as package so sub-imports work
    mod.__file__ = f"<stub {name}>"
    mod.__getattr__ = lambda attr: MagicMock()
    return mod

_STUB_MODULES = [
    # Third-party libs
    "dotmap", "aiohttp", "aiohttp.ClientConnectionError",
    "xmltodict", "uvicorn",
    "starlette", "starlette.datastructures",
    "fastapi", "fastapi.responses", "fastapi.templating",
    "fastapi.staticfiles",
    "pydantic", "pydantic.settings",
    "jinja2",
    # Internal siblings / transitive
    "utils", "settings", "version",
    "plex.play_queue", "plex.subscribe", "plex.gdm", "plex.pin_login",
    "plex.plexserver",
    "dlna", "dlna.dlna_device", "dlna.discover",
    "dlna.virtual", "dlna.virtual.devices", "dlna.virtual.volume",
    "dlna.quirks",
]

for _name in _STUB_MODULES:
    if _name not in sys.modules:
        sys.modules[_name] = _make_stub(_name)

# Ensure the "plex" package exists but does NOT pull in plexserver
if "plex" not in sys.modules:
    _plex_pkg = types.ModuleType("plex")
    _plex_pkg.__path__ = ["plex"]
    _plex_pkg.__file__ = "<stub plex>"
    sys.modules["plex"] = _plex_pkg

# settings.settings must be subscribable
sys.modules["settings"].settings = MagicMock()

# utils helpers referenced at module level
_utils = sys.modules["utils"]
_utils.parse_timedelta = MagicMock(return_value=0)
_utils.convert_volume = MagicMock(return_value=50)
_utils.g = MagicMock()
_utils.pms_header = MagicMock(return_value={})
_utils.extract_value = MagicMock()

# PlayQueue
sys.modules["plex.play_queue"].PlayQueue = MagicMock

# DotMap
sys.modules["dotmap"].DotMap = MagicMock

# starlette QueryParams
sys.modules["starlette.datastructures"].QueryParams = MagicMock

# Now it's safe to import plex.adapters in isolation.
from plex.adapters import PlexDlnaAdapter  # noqa: E402


class _Change(dict):
    """Minimal stand-in for the DotMap that DlnaState passes to callbacks."""

    def __getattr__(self, name):
        value = self.get(name)
        return _Change(value) if isinstance(value, dict) else value

    def toDict(self):
        return dict(self)


OLD_URI = "http://pms/library/parts/1/file.flac"
NEW_URI = "http://pms/library/parts/2/file.flac"


@pytest.fixture
def loading_adapter(monkeypatch):
    """An adapter in the middle of a SetAVTransportURI for NEW_URI."""
    monkeypatch.setattr(sys.modules["dlna.quirks"], "get_device_quirks",
                        lambda device: {"ignore_premature_stopped": True}, raising=False)
    adapter = object.__new__(PlexDlnaAdapter)
    adapter.dlna = MagicMock()
    adapter.dlna.name = "renderer"
    adapter.state = MagicMock()
    adapter.loop = MagicMock()
    adapter.loop.is_closed.return_value = False
    adapter._seen_playing_since_operation = False
    adapter._operation_start_time = None
    adapter._active_operation_id = 1
    adapter._active_target_uri = NEW_URI
    adapter._active_operation_event = asyncio.Event()
    adapter._active_operation_uri_confirmed = False
    adapter._active_operation_state_ready = False
    adapter._active_operation_state_confirmed = False
    adapter._transport_state_override = {"state": "TRANSITIONING"}
    # Stop each callback before the post-operation and auto-next checks,
    # which aren't what these tests are about.
    adapter._check_post_operation_false_stop = lambda change: True
    return adapter


def test_premature_stopped_keeps_target_uri(loading_adapter):
    """STOPPED arriving with the new URI must not lose the URI confirmation."""
    loading_adapter.state_changed_callback(_Change(
        state="STOPPED", current_uri=NEW_URI, old={"state": "TRANSITIONING", "current_uri": OLD_URI}))
    assert loading_adapter._active_operation_uri_confirmed

    loading_adapter.state_changed_callback(_Change(state="PLAYING", old={"state": "TRANSITIONING"}))
    assert loading_adapter._active_operation_state_confirmed
    assert loading_adapter._active_operation_event.is_set()


def test_premature_stopped_for_another_uri_confirms_nothing(loading_adapter):
    loading_adapter.state_changed_callback(_Change(
        state="STOPPED", current_uri=OLD_URI, old={"state": "TRANSITIONING"}))
    assert not loading_adapter._active_operation_uri_confirmed

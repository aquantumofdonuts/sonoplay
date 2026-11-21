"""Unit tests for PlexDlnaAdapter track switch confirmation."""

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
def switching_adapter():
    """An adapter that just sent SetAVTransportURI for NEW_URI during auto-next."""
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
    adapter._should_ignore_premature_stopped = lambda change: False
    # Stop each callback before the post-operation and auto-next checks,
    # which aren't what these tests are about.
    adapter._check_post_operation_false_stop = lambda change: True
    return adapter


def test_previous_track_playing_does_not_confirm_operation(switching_adapter):
    """A poll still reporting the previous track must not count as the new one playing."""
    # Poll started before SetAVTransportURI: previous track, PLAYING.
    switching_adapter.state_changed_callback(_Change(
        current_uri=OLD_URI, state="PLAYING", old={"current_uri": None, "state": "TRANSITIONING"}))
    # Next poll: the device reports the new URI, still loading it.
    switching_adapter.state_changed_callback(_Change(
        current_uri=NEW_URI, state="TRANSITIONING", old={"current_uri": OLD_URI, "state": "PLAYING"}))
    assert not switching_adapter._active_operation_event.is_set()

    # The new track actually starts.
    switching_adapter.state_changed_callback(_Change(state="PLAYING", old={"state": "TRANSITIONING"}))
    assert switching_adapter._active_operation_state_confirmed
    assert switching_adapter._active_operation_event.is_set()

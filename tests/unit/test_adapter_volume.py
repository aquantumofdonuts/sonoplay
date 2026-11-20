"""Unit tests for PlexDlnaAdapter.set_volume()."""

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


class _SlowRenderer:
    """Records SetVolume calls; each one takes a little while to answer."""

    volume_max = 100
    volume_min = 0
    volume_step = 1
    name = "renderer"

    def __init__(self, state):
        self.state = state
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def SetVolume(self, volume):
        self.calls.append(volume)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.05)
        self.in_flight -= 1
        self.state.volume = volume


@pytest.fixture
def volume_adapter(monkeypatch):
    # utils is stubbed above; make convert_volume the identity for these tests.
    monkeypatch.setattr(sys.modules[PlexDlnaAdapter.__module__], "convert_volume", lambda v, *args: v)
    adapter = object.__new__(PlexDlnaAdapter)
    adapter.state = MagicMock()
    adapter.state.volume = 40
    adapter.dlna = _SlowRenderer(adapter.state)
    return adapter


@pytest.mark.asyncio
async def test_volume_burst_sends_one_request_at_a_time(volume_adapter):
    await asyncio.gather(*[volume_adapter.set_volume(v) for v in (42, 45, 48, 50, 53, 55, 58, 60)])
    assert volume_adapter.dlna.max_in_flight == 1
    assert volume_adapter.dlna.calls == [42, 60]


@pytest.mark.asyncio
async def test_requested_volume_is_reported_while_applied(volume_adapter):
    task = asyncio.ensure_future(volume_adapter.set_volume(60))
    await asyncio.sleep(0)
    assert volume_adapter.state.volume == 40
    assert volume_adapter._reported_volume() == 60
    await task
    assert volume_adapter._reported_volume() == 60


@pytest.mark.asyncio
async def test_failed_set_volume_drops_pending_values(volume_adapter):
    async def failing(volume):
        await asyncio.sleep(0.01)
        raise asyncio.TimeoutError()

    volume_adapter.dlna.SetVolume = failing
    results = await asyncio.gather(*[volume_adapter.set_volume(v) for v in (10, 20)], return_exceptions=True)
    assert isinstance(results[0], asyncio.TimeoutError)
    assert volume_adapter._pending_volume is None
    assert volume_adapter._reported_volume() == 40

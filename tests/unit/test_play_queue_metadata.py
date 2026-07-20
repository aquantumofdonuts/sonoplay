import sys

# Prior test modules (test_adapter_stopped_state.py, test_audio_settings.py)
# stub heavy transitive deps - including dotmap, xmltodict, settings, plex.*
# and dlna.* - as bare MagicMock modules and never restore them. Force a
# clean reimport of everything plex.play_queue touches.
_STALE_PREFIXES = ("utils", "plex", "dotmap", "xmltodict", "settings")
for _key in list(sys.modules):
    if any(_key == _p or _key.startswith(_p + ".") for _p in _STALE_PREFIXES):
        del sys.modules[_key]

from dotmap import DotMap
from plex.play_queue import PlayQueue


def _make_queue():
    return PlayQueue(container_key="/playQueues/1", plex_lib=None)


def _track(**overrides):
    data = {
        "ratingKey": "999",
        "title": "My Song",
        "grandparentTitle": "My Artist",
        "parentTitle": "My Album",
        "duration": 180000,
        "Media": [{"container": "flac", "Part": [{"container": "flac", "key": "/x"}]}],
    }
    data.update(overrides)
    return DotMap(data)


def test_metadata_for_direct_play_track_uses_container_mime_type():
    queue = _make_queue()
    track = _track()
    metadata = queue.metadata_for_track(track, "http://host/stream.flac", force_transcode=False)
    assert 'protocolInfo="http-get:*:audio/flac:*"' in metadata
    assert "<dc:title>My Song</dc:title>" in metadata
    assert "<upnp:artist>My Artist</upnp:artist>" in metadata
    assert "<upnp:album>My Album</upnp:album>" in metadata


def test_metadata_for_transcoded_track_always_uses_mpeg():
    """Transcoded tracks are always served as audio/mpeg by Plex's universal
    transcode endpoint (see build_transcode_url), regardless of the source
    container - metadata must reflect the actual bytes on the wire, not the
    original file format."""
    queue = _make_queue()
    track = _track()  # source is flac
    metadata = queue.metadata_for_track(track, "http://host/transcode.mp3", force_transcode=True)
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in metadata


def test_metadata_for_track_missing_title_has_fallback():
    queue = _make_queue()
    track = _track(title=None)
    metadata = queue.metadata_for_track(track, "http://host/stream.mp3", force_transcode=False)
    assert "<dc:title>Unknown Title</dc:title>" in metadata


def test_metadata_for_track_is_never_empty():
    """Core regression for issue #10: CurrentURIMetaData must never come
    back as an empty/falsy string, which is what triggered Samsung's
    SetAVTransportURI SOAP Fault rejection."""
    queue = _make_queue()
    track = _track()
    metadata = queue.metadata_for_track(track, "http://host/stream.mp3", force_transcode=False)
    assert metadata
    assert metadata.strip() != ""

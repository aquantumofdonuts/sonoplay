import sys
import xml.etree.ElementTree as ET

# Prior test modules (test_adapter_stopped_state.py, test_audio_settings.py)
# stub heavy transitive deps - including dotmap and xmltodict - as bare
# MagicMock modules and never restore them. Force a clean reimport of
# everything utils touches.
_STALE_PREFIXES = ("utils", "dotmap", "xmltodict", "settings")
for _key in list(sys.modules):
    if any(_key == _p or _key.startswith(_p + ".") for _p in _STALE_PREFIXES):
        del sys.modules[_key]

from utils import build_didl_lite_metadata, mime_type_for_container


def test_mime_type_for_known_container():
    assert mime_type_for_container("flac") == "audio/flac"
    assert mime_type_for_container("MP3") == "audio/mpeg"


def test_mime_type_for_unknown_or_missing_container_defaults_to_mpeg():
    assert mime_type_for_container("some-weird-format") == "audio/mpeg"
    assert mime_type_for_container(None) == "audio/mpeg"
    assert mime_type_for_container("") == "audio/mpeg"


def test_didl_lite_is_not_empty_and_contains_res_with_protocol_info():
    """The core regression: CurrentURIMetaData must never be blank.

    Samsung (and other strict) renderers reject SetAVTransportURI with a
    UPnPError SOAP Fault when metadata is empty, because they can't
    classify the resource. This asserts we always produce a populated
    DIDL-Lite document with a res element describing the stream type.
    """
    metadata = build_didl_lite_metadata(
        item_id="12345",
        title="Track Title",
        url="http://192.168.1.50:32400/library/parts/1/file.mp3",
        mime_type="audio/mpeg",
        artist="Some Artist",
        album="Some Album",
        duration_ms=245000,
    )
    assert metadata
    assert "<DIDL-Lite" in metadata
    assert 'protocolInfo="http-get:*:audio/mpeg:*"' in metadata
    assert "<dc:title>Track Title</dc:title>" in metadata
    assert "<upnp:artist>Some Artist</upnp:artist>" in metadata
    assert "<upnp:album>Some Album</upnp:album>" in metadata
    # 245000ms = 4:05.000
    assert 'duration="0:04:05.000"' in metadata


def test_didl_lite_escapes_special_characters_in_metadata():
    """Titles/artists with XML-significant characters must not break the
    DIDL-Lite document (or the outer SOAP envelope it gets embedded in)."""
    metadata = build_didl_lite_metadata(
        item_id="1",
        title="Rock & Roll <Live>",
        url="http://example.com/a.mp3?x=1&y=2",
        mime_type="audio/mpeg",
        artist="AT&T Band",
    )
    assert "<Live>" not in metadata
    assert "Rock &amp; Roll" in metadata
    assert "AT&amp;T Band" in metadata

    # And the DIDL string itself must be valid, well-formed XML on its own
    # (this is what a real renderer parses it back into after the outer SOAP
    # envelope is decoded).
    root = ET.fromstring(metadata)
    ns = {"dc": "http://purl.org/dc/elements/1.1/"}
    title_el = root.find(".//dc:title", ns)
    assert title_el.text == "Rock & Roll <Live>"


def test_didl_lite_omits_optional_tags_when_absent():
    metadata = build_didl_lite_metadata(
        item_id="1",
        title="Title Only",
        url="http://example.com/a.mp3",
        mime_type="audio/mpeg",
    )
    assert "<upnp:artist>" not in metadata
    assert "<upnp:album>" not in metadata
    assert "<dc:creator>" not in metadata
    assert "duration=" not in metadata


def test_didl_lite_falls_back_to_item_id_zero_when_missing():
    metadata = build_didl_lite_metadata(
        item_id=None,
        title="Title",
        url="http://example.com/a.mp3",
        mime_type="audio/mpeg",
    )
    assert 'id="0"' in metadata

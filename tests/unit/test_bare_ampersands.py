import sys
from unittest.mock import MagicMock

# Same stub cleanup as test_soap_fault_parsing.py: earlier test modules may
# leave MagicMock stand-ins for these packages in sys.modules.
for _key in list(sys.modules):
    if not (_key in ("utils", "dotmap", "xmltodict", "settings")
            or _key.startswith(("utils.", "dotmap.", "xmltodict.", "settings."))):
        continue
    _mod = sys.modules[_key]
    if isinstance(_mod, MagicMock) or (
        hasattr(_mod, "__file__") and _mod.__file__ and "<stub" in str(_mod.__file__)
    ):
        del sys.modules[_key]

from utils import escape_bare_ampersands, xml2dict

STREAM_URL = "http://192.0.2.1:32400/audio/start.mp3?path=%2Flibrary%2Fmetadata%2F1&session=abc&protocol=http"

# GetPositionInfo response as sent by a Yamaha AVR: TrackURI carries raw '&'.
GET_POSITION_INFO = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:GetPositionInfoResponse xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
    '<Track>1</Track><TrackDuration>0:04:14</TrackDuration>'
    f'<TrackURI>{STREAM_URL}</TrackURI>'
    '<RelTime>0:00:10</RelTime>'
    '</u:GetPositionInfoResponse></s:Body></s:Envelope>'
)

# AVTransport event whose LastChange holds a URL escaped once: after
# unescape_xml() the query string contains bare '&'.
NOTIFY_BODY = (
    '<?xml version="1.0"?><e:propertyset xmlns:e="urn:schemas-upnp-org:event-1-0">'
    '<e:property><LastChange>'
    '&lt;Event xmlns="urn:schemas-upnp-org:metadata-1-0/AVT/"&gt;&lt;InstanceID val="0"&gt;'
    '&lt;TransportState val="PLAYING"/&gt;'
    f'&lt;AVTransportURI val="{STREAM_URL.replace("&", "&amp;")}"/&gt;'
    '&lt;/InstanceID&gt;&lt;/Event&gt;'
    '</LastChange></e:property></e:propertyset>'
).encode()


def test_escapes_bare_ampersand():
    assert escape_bare_ampersands("a=1&b=2") == "a=1&amp;b=2"


def test_keeps_entities():
    s = "&amp; &lt; &gt; &quot; &apos; &#38; &#x26;"
    assert escape_bare_ampersands(s) == s


def test_xml2dict_parses_notify_with_url_in_last_change():
    info = xml2dict(NOTIFY_BODY)
    instance = info.propertyset.property.LastChange.Event.InstanceID
    assert instance.AVTransportURI["@val"] == STREAM_URL
    assert instance.TransportState["@val"] == "PLAYING"


def test_xml2dict_parses_soap_response_with_raw_ampersands():
    # sanitize_soap_response() runs escape_bare_ampersands() before parsing.
    info = xml2dict(escape_bare_ampersands(GET_POSITION_INFO))
    assert info.Envelope.Body.GetPositionInfoResponse.TrackURI == STREAM_URL

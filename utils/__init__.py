import aiohttp
import re
import xmltodict
from dotmap import DotMap
from html import escape as xml_escape

from settings import settings
from datetime import timedelta, datetime


UPNP_AVT_SERVICE_TYPE = "urn:schemas-upnp-org:service:AVTransport:1"
UPNP_RC_SERVICE_TYPE = "urn:schemas-upnp-org:service:RenderingControl:1"

# Device identifier validation patterns
# Accepts multiple formats used by DLNA/UPnP devices:
# 1. Standard UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# 2. Virtual devices: virtual-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# 3. Sonos RINCON: RINCON_XXXXXXXXXXXX (hex digits, typically 17 chars)
# 4. Generic alphanumeric device IDs (other vendors)
DEVICE_ID_PATTERN = re.compile(
    r'^(?:'
    r'(?:virtual-)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'  # Standard/virtual UUID
    r'|RINCON_[0-9A-F]{12,17}'  # Sonos RINCON format
    r'|uuid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'  # uuid: prefixed
    r'|[A-Za-z0-9_-]{8,64}'  # Generic alphanumeric (other DLNA devices)
    r')$',
    re.IGNORECASE
)

# Backward compatibility alias
UUID_PATTERN = DEVICE_ID_PATTERN


def is_valid_device_uuid(uuid: str | None) -> bool:
    """Validate a device identifier format.
    
    Accepts:
    - Standard UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    - Virtual device format: virtual-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    - Sonos RINCON format: RINCON_XXXXXXXXXXXX
    - Other DLNA device identifiers (alphanumeric, 8-64 chars)
    
    Args:
        uuid: The device identifier string to validate
        
    Returns:
        True if valid device identifier format, False otherwise
    """
    if uuid is None:
        return False
    return bool(DEVICE_ID_PATTERN.match(uuid))


def require_valid_uuid(uuid: str | None) -> None:
    """Validate UUID format and raise HTTPException if invalid.
    
    Use this at the start of endpoints that accept UUID parameters
    to ensure consistent 400 Bad Request responses for invalid UUIDs.
    
    Args:
        uuid: The UUID string to validate
        
    Raises:
        HTTPException: 400 status code if UUID format is invalid
    """
    from fastapi import HTTPException
    
    if not is_valid_device_uuid(uuid):
        raise HTTPException(
            status_code=400,
            detail="Invalid UUID format"
        )


class G(object):

    def __init__(self):
        self.http: aiohttp.ClientSession = None


g = G()


def unescape_xml(xml):
    from html import unescape
    return unescape(xml.decode())


def xml2dict(xml):
    if not isinstance(xml, str):
        xml = unescape_xml(xml)
    parsed = xmltodict.parse(xml,
                             process_namespaces=True,
                             namespaces={
                                 UPNP_AVT_SERVICE_TYPE: None,
                                 UPNP_RC_SERVICE_TYPE: None,
                                 "http://schemas.xmlsoap.org/soap/envelope/": None,
                                 "urn:schemas-upnp-org:event-1-0": None,
                                 "urn:schemas-upnp-org:metadata-1-0/AVT/": None,
                                 # SOAP Fault detail namespace for UPnPError (errorCode/
                                 # errorDescription). Without this, xmltodict prefixes those
                                 # keys with the full namespace URI, so callers looking up
                                 # fault.detail.UPnPError never find it and the real error
                                 # code/description silently disappear.
                                 "urn:schemas-upnp-org:control-1-0": None,
                             })
    return DotMap(parsed)


# Maps Plex Media/Part "container" values to DLNA-friendly MIME types for the
# <res protocolInfo="..."> element in DIDL-Lite metadata.
CONTAINER_MIME_TYPES = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "wave": "audio/wav",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "alac": "audio/mp4",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/opus",
    "wma": "audio/x-ms-wma",
    "ape": "audio/x-ape",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}

DEFAULT_AUDIO_MIME_TYPE = "audio/mpeg"


def mime_type_for_container(container: str | None) -> str:
    """Map a Plex Media/Part container string to a DLNA-friendly MIME type.

    Falls back to audio/mpeg (the same type used by SonoPlay's transcode
    path) for unknown or missing containers, since that's the type most
    DLNA renderers are guaranteed to accept.
    """
    if not container:
        return DEFAULT_AUDIO_MIME_TYPE
    return CONTAINER_MIME_TYPES.get(str(container).strip().lower(), DEFAULT_AUDIO_MIME_TYPE)


def _format_didl_duration(duration_ms) -> str | None:
    """Format milliseconds as a UPnP res duration string (H+:MM:SS.mmm)."""
    if not duration_ms:
        return None
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        return None
    total_seconds, millis = divmod(duration_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def build_didl_lite_metadata(*, item_id, title, url, mime_type=DEFAULT_AUDIO_MIME_TYPE,
                              upnp_class="object.item.audioItem.musicTrack",
                              artist=None, album=None, duration_ms=None) -> str:
    """Build a minimal DIDL-Lite document for use as CurrentURIMetaData.

    Many DLNA renderers (Samsung soundbars/TVs in particular) reject
    SetAVTransportURI with a UPnPError SOAP Fault when CurrentURIMetaData is
    empty, since they have no way to classify the resource without it.
    Sonos and most other renderers tolerate empty metadata, which is why
    this went unnoticed until a stricter renderer was tested (see issue #10).

    The returned string is plain (unescaped-once) XML. It is XML-escaped a
    second time by payload_from_template when embedded as text content of
    the outer SOAP envelope's <CurrentURIMetaData> element - that's correct
    and required: a DLNA control point decodes the envelope once and expects
    the result to itself be well-formed DIDL-Lite XML.
    """
    def esc(value):
        return xml_escape(str(value)) if value else ""

    duration_attr = ""
    duration = _format_didl_duration(duration_ms)
    if duration:
        duration_attr = f' duration="{esc(duration)}"'

    creator_tag = f"<dc:creator>{esc(artist)}</dc:creator>" if artist else ""
    artist_tag = f"<upnp:artist>{esc(artist)}</upnp:artist>" if artist else ""
    album_tag = f"<upnp:album>{esc(album)}</upnp:album>" if album else ""

    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        f'<item id="{esc(item_id) or "0"}" parentID="-1" restricted="1">'
        f'<dc:title>{esc(title)}</dc:title>'
        f'{creator_tag}{artist_tag}{album_tag}'
        f'<upnp:class>{esc(upnp_class)}</upnp:class>'
        f'<res protocolInfo="http-get:*:{esc(mime_type)}:*"{duration_attr}>{esc(url)}</res>'
        '</item>'
        '</DIDL-Lite>'
    )


def _base_plex_headers(device) -> dict:
    """Build common Plex protocol headers shared by all header functions."""
    product = settings.product or device.model
    device_model = settings.client_model or device.model or product
    device_header = settings.client_device or device_model
    device_name = getattr(device, "name", None) or settings.client_device_name or product
    headers = {
        'X-Plex-Client-Identifier': device.uuid,
        'X-Plex-Device': device_header,
        'X-Plex-Device-Name': device_name,
        'X-Plex-Platform': settings.platform,
        'X-Plex-Platform-Version': settings.platform_version,
        'X-Plex-Product': product,
        'X-Plex-Version': settings.version,
        'X-Plex-Model': device_model,
    }
    if settings.client_profile:
        headers['X-Plex-Client-Profile-Name'] = settings.client_profile
    return headers


def pms_header(device):
    headers = _base_plex_headers(device)
    headers['X-Plex-Provides'] = 'player,pubsub-player'
    return headers


def plex_server_response_headers(device):
    headers = _base_plex_headers(device)
    headers.update({
        'Accept': '*/*',
        'Connection': 'keep-alive',
        'Accept-Language': 'en',
        'X-Plex-Provides': 'player,pubsub-player',
    })
    return headers


def subscriber_send_headers(device):
    headers = _base_plex_headers(device)
    headers.update({
        'Content-Type': 'application/xml',
        'Connection': 'Keep-Alive',
        'Accept-Encoding': 'gzip, deflate',
        'Accept-Language': 'en,*',
    })
    return headers


def timeline_poll_headers(device):
    return {
        'X-Plex-Client-Identifier': device.uuid,
        'X-Plex-Protocol': '1.0',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Max-Age': '1209600',
        'Access-Control-Expose-Headers': 'X-Plex-Client-Identifier',
        'Content-Type': 'text/xml;charset=utf-8'
    }


def extract_value(value, default=None):
    if value is None:
        return default
    if isinstance(value, DotMap):
        if '@val' in value:
            return extract_value(value['@val'], default)
        values = list(value.values())
        if len(values) == 1:
            return extract_value(values[0], default)
        return default if default is not None else value
    if isinstance(value, dict):
        if '@val' in value:
            return extract_value(value['@val'], default)
        values = list(value.values())
        if len(values) == 1:
            return extract_value(values[0], default)
        return default if default is not None else value
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return default
        return extract_value(value[0], default)
    return value


def _extract_time_value(value):
    extracted = extract_value(value, default="00:00:00")
    return str(extracted)


def parse_timedelta(value):
    s = _extract_time_value(value)
    if not s or s in ("NOT_IMPLEMENTED", "None"):
        return timedelta(0)
    try:
        t = datetime.strptime(s, "%H:%M:%S")
    except ValueError:
        if "." in s:
            base, _, _ = s.partition(".")
            try:
                t = datetime.strptime(base, "%H:%M:%S")
            except ValueError:
                return timedelta(0)
        else:
            return timedelta(0)
    delta = timedelta(hours=t.hour, minutes=t.minute, seconds=t.second)
    return delta


def convert_volume(value: int, from_max: int, from_min: int, to_max: int, to_min: int, to_step: int):
    if from_max == to_max and from_min == to_min:
        return value
    if from_max - from_min == to_max - to_min:
        return value - from_min + to_min
    from_range = from_max - from_min
    if from_range == 0:
        return to_min
    if to_step == 0:
        to_step = 1
    percent = float(value - from_min) / float(from_range)
    value = percent * (to_max - to_min)
    value = int(value / to_step)
    value += to_min
    return value

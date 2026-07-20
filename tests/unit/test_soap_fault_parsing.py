import sys

# Prior test modules (test_adapter_stopped_state.py, test_audio_settings.py)
# stub heavy transitive deps - including dotmap and xmltodict, which xml2dict
# depends on directly - as bare MagicMock modules and never restore them. If
# those ran first, 'utils' may already be cached bound to the stubs. Force a
# clean reimport of everything xml2dict touches.
_STALE_PREFIXES = ("utils", "dotmap", "xmltodict", "settings")
for _key in list(sys.modules):
    if any(_key == _p or _key.startswith(_p + ".") for _p in _STALE_PREFIXES):
        del sys.modules[_key]

from utils import xml2dict

# A standard UPnP SOAP Fault as returned by a DLNA renderer rejecting
# SetAVTransportURI (e.g. a Samsung soundbar). Per the UPnP DeviceProtocol
# spec, the UPnPError element's namespace is urn:schemas-upnp-org:control-1-0.
SOAP_FAULT_XML = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
<s:Body>
<s:Fault>
<faultcode>s:Client</faultcode>
<faultstring>UPnPError</faultstring>
<detail>
<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">
<errorCode>716</errorCode>
<errorDescription>Resource Not Found</errorDescription>
</UPnPError>
</detail>
</s:Fault>
</s:Body>
</s:Envelope>"""


def test_soap_fault_upnp_error_code_is_parsed():
    """errorCode must be reachable at Envelope.Body.Fault.detail.UPnPError.

    Regression test: xml2dict previously left the control-1-0 namespace
    unstripped, so this key ended up as
    'urn:schemas-upnp-org:control-1-0:UPnPError' and every caller silently
    lost the actual UPnP error code/description behind a SOAP Fault.
    """
    info = xml2dict(SOAP_FAULT_XML)
    assert info.Envelope.Body.Fault.detail.UPnPError.get("errorCode") == "716"


def test_soap_fault_upnp_error_description_is_parsed():
    info = xml2dict(SOAP_FAULT_XML)
    assert info.Envelope.Body.Fault.detail.UPnPError.get("errorDescription") == "Resource Not Found"

"""Webhook payload extraction must cope with varying Catalyst Center shapes."""
from app.webhook import extract_device_ref


def test_extract_from_details_dict():
    payload = {"eventId": "NETWORK-DEVICES-3-200", "details": {
        "networkDeviceId": "abcd1234-ef56-7890-abcd-1234567890ab",
        "managementIpAddress": "10.10.10.10", "hostname": "SW-SCH-A01"}}
    ref = extract_device_ref(payload)
    assert ref["id"] == "abcd1234-ef56-7890-abcd-1234567890ab"
    assert ref["ip"] == "10.10.10.10"
    assert ref["hostname"] == "SW-SCH-A01"


def test_extract_from_nested_list():
    payload = {"event": {"data": [{"deviceId": "11112222-3333-4444-5555-666677778888"}]}}
    assert extract_device_ref(payload)["id"] == "11112222-3333-4444-5555-666677778888"


def test_ip_in_name_field_is_treated_as_ip():
    payload = {"details": {"deviceName": "10.1.2.3"}}
    ref = extract_device_ref(payload)
    assert ref.get("ip") == "10.1.2.3"
    assert "hostname" not in ref


def test_empty_payload():
    assert extract_device_ref({}) == {}
    assert extract_device_ref("not json") == {}


def test_top_level_hostname_only():
    assert extract_device_ref({"hostname": "SW-1"})["hostname"] == "SW-1"


def test_snake_case_device_ip_key():
    ref = extract_device_ref({"details": {"device_ip": "172.20.10.146"}})
    assert ref["ip"] == "172.20.10.146"


def test_real_licmgmt_payload():
    """Exact LICMGMT-DEV-REG-SUCCESS payload observed from Catalyst Center:
    the device is only in details.device_ip and the free-text description."""
    payload = {
        "version": "1.0.1", "efInstanceId": "8e459644-2d7d-4d64-af1a-464598911e8e",
        "instanceId": "e793056a-c6d3-4551-b83d-6d46f2ef45d5",
        "eventId": "LICMGMT-DEV-REG-SUCCESS", "namespace": "LicenseManagement",
        "name": None,
        "description": "Registration succeeded for device 172.20.10.146 "
                       "(SSTO146CIS.Global.web-int.net)",
        "type": "APP", "category": "INFO", "domain": "Know Your Network",
        "subDomain": "Devices", "severity": 4, "source": "ewmessaging.py:160",
        "timestamp": 1783529587342,
        "details": {"device_ip": "172.20.10.146",
                    "message": "Device registration successful with CSSM, "
                               "with license feature sync completed"},
        "ciscoDnaEventLink": "https://&lt;DNAC_IP_ADDRESS&gt;/dna/tools/licenseManagement",
        "note": "To get more details, please go to Tools -> License Manager",
        "context": None, "userId": None, "i18n": None, "eventHierarchy": None,
        "message": None, "messageParams": None, "parentInstanceId": None,
        "network": None, "dnacIP": "", "correlationId": "af1f3762-1676-49ff-8810-a897f93b0234",
    }
    ref = extract_device_ref(payload)
    assert ref["ip"] == "172.20.10.146"
    assert ref["hostname"] == "SSTO146CIS.Global.web-int.net"
    assert "id" not in ref  # event instance UUIDs must NOT be mistaken for device ids


def test_freetext_ip_and_hostname_fallback():
    ref = extract_device_ref({"description": "provisioning done for device "
                                             "10.9.8.7 (SW-LAB-01.example.com) ok"})
    assert ref["ip"] == "10.9.8.7"
    assert ref["hostname"] == "SW-LAB-01.example.com"

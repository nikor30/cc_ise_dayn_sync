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

from datetime import datetime

import cloud_bridge


def test_local_timestamp_is_converted_to_timezone_aware_iso():
    value = "2026-11-05 12:30:00"
    result = cloud_bridge._to_utc(value)
    parsed = datetime.fromisoformat(result)
    assert parsed.tzinfo is not None
    assert parsed.replace(tzinfo=None) == datetime(2026, 11, 5, 12, 30, 0)


def test_decision_client_posts_the_supervisor_decision(monkeypatch):
    captured = {}

    def fake_request(method, path, payload):
        captured.update(method=method, path=path, payload=payload)
        return {"ok": True}

    monkeypatch.setattr(cloud_bridge, "_request", fake_request)
    cloud_bridge.decide_return_request("12345678-1234-1234-1234-123456789abc", "approved", "مشرف")
    assert captured["method"] == "POST"
    assert captured["path"].endswith("/decision")
    assert captured["payload"] == {"decision": "approved", "recorded_by": "مشرف"}


def test_decision_client_rejects_unknown_decision():
    try:
        cloud_bridge.decide_return_request("12345678-1234-1234-1234-123456789abc", "maybe", "مشرف")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown decisions must be rejected")

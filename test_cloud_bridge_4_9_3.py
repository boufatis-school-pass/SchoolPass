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

def test_permit_sync_only_sends_changed_records(monkeypatch):
    import sqlite3

    columns = (
        "qr_token TEXT, record_no TEXT, kind TEXT, student_name TEXT, class_name TEXT, "
        "issued_at TEXT, created_at TEXT, status TEXT, returned_at TEXT"
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE records (" + columns + ")")
    conn.execute(
        "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?)",
        ("a" * 48, "OUT-0001", "exit_permit", "تلميذ تجريبي", "1AS1",
         "2026-11-05 08:00:00", "2026-11-05 08:00:00", "valid", "")
    )
    monkeypatch.setattr(cloud_bridge.core, "db_connect", lambda: conn)
    cloud_bridge._SYNCED_PAYLOADS.clear()
    cloud_bridge._LAST_FULL_SYNC = 0.0
    calls = []

    def fake_request(method, path, payload):
        calls.append(payload["permits"])
        return {"ok": True, "count": len(payload["permits"])}

    monkeypatch.setattr(cloud_bridge, "_request", fake_request)
    try:
        assert cloud_bridge.sync_local_permits() == 1
        assert cloud_bridge.sync_local_permits() == 0
        conn.execute(
            "UPDATE records SET status='returned',returned_at=? WHERE qr_token=?",
            ("2026-11-05 09:00:00", "a" * 48)
        )
        assert cloud_bridge.sync_local_permits() == 1
        assert len(calls) == 2
        assert "guardian_name" not in calls[0][0]
    finally:
        conn.close()
        cloud_bridge._SYNCED_PAYLOADS.clear()
        cloud_bridge._LAST_FULL_SYNC = 0.0

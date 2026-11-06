from datetime import datetime, timedelta

import schoolpass_core as core


def _record(kind="late", issued="2026-11-06 08:00:00", status="valid"):
    return {
        "record_no": "SP-2026-000123",
        "kind": kind,
        "issued_at": issued,
        "created_at": issued,
        "status": status,
    }


def test_offline_qr_contains_issue_time_and_exact_two_hour_expiry():
    record = _record()
    payload = core.qr_payload(record)
    expiry = core.record_expiry_at(record)
    assert expiry is not None
    assert payload == "SP1|SP-2026-000123|LATE|2026-11-06T08:00:00|2026-11-06T10:00:00"
    assert "http://" not in payload and "https://" not in payload


def test_offline_exit_qr_has_open_marker_and_no_two_hour_expiry():
    payload = core.qr_payload(_record("exit_permit"))
    assert payload == "SP1|SP-2026-000123|EXIT|2026-11-06T08:00:00|OPEN"


def test_offline_qr_does_not_claim_to_reflect_later_cancellation():
    valid = core.qr_payload(_record(status="valid"))
    cancelled = core.qr_payload(_record(status="cancelled"))
    assert valid == cancelled
    expiry = core.record_expiry_at(_record())
    assert expiry is not None
    assert core.record_is_expired(_record(), at=expiry - timedelta(seconds=1)) is False
    assert core.record_is_expired(_record(), at=expiry) is True


def test_legacy_invalid_timestamp_remains_printable_without_false_expiry():
    payload = core.qr_payload(_record(issued="not-a-date"))
    assert payload == "SP1|SP-2026-000123|LATE|UNKNOWN|UNKNOWN"

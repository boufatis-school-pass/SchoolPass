from datetime import datetime, timedelta, timezone

import schoolpass_core as core


def _record(kind="late", issued="2026-11-05 10:00:00"):
    return {"kind": kind, "issued_at": issued, "created_at": issued}


def test_entry_permit_expires_after_exactly_two_hours():
    record = _record()
    issued = datetime.strptime(record["issued_at"], "%Y-%m-%d %H:%M:%S").astimezone()
    assert core.record_is_expired(record, issued + timedelta(hours=1, minutes=59, seconds=59)) is False
    assert core.record_is_expired(record, issued + timedelta(hours=2)) is True


def test_exceptional_exit_does_not_expire_after_two_hours():
    record = _record("exit_permit")
    issued = datetime.strptime(record["issued_at"], "%Y-%m-%d %H:%M:%S").astimezone()
    assert core.record_expiry_at(record) is None
    assert core.record_is_expired(record, issued + timedelta(days=30)) is False


def test_all_entry_receipt_kinds_are_time_limited():
    for kind in ("late", "absence_return", "special_entry", "exit_return"):
        assert core.record_expiry_at(_record(kind)) is not None


def test_legacy_record_without_valid_timestamp_is_preserved():
    record = _record(issued="not-a-date")
    assert core.record_expiry_at(record) is None
    assert core.record_is_expired(record) is False

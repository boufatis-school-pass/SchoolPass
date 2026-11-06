from datetime import datetime, timedelta

import schoolpass_core as core


def _record(kind="late", issued="2026-11-06 08:00:00", status="valid"):
    return {
        "record_no": "DL-2026-000123",
        "kind": kind,
        "issued_at": issued,
        "created_at": issued,
        "status": status,
    }


def test_offline_qr_contains_issue_time_and_exact_two_hour_expiry():
    record = _record()
    payload = core.qr_payload(record)
    issued = datetime.strptime(record["issued_at"], "%Y-%m-%d %H:%M:%S").astimezone()
    expected_expiry = (issued + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    assert payload.startswith("SCHOOLPASS|1\n")
    assert record["record_no"] in payload
    assert record["issued_at"] in payload
    assert expected_expiry in payload
    assert "تنتهي بعد ساعتين" in payload
    assert "http://" not in payload and "https://" not in payload


def test_offline_exit_qr_does_not_claim_a_two_hour_expiry_or_live_return_state():
    payload = core.qr_payload(_record("exit_permit"))
    assert "رخصة خروج استثنائي" in payload
    assert "لا يوجد انتهاء زمني تلقائي" in payload
    assert "تأكيد العودة أو الإلغاء يتطلب مراجعة سجل المؤسسة" in payload
    assert "تنتهي بعد ساعتين" not in payload


def test_offline_qr_never_claims_to_reflect_cancellation_after_printing():
    payload = core.qr_payload(_record(status="cancelled"))
    assert "لا يعكس إلغاءً لاحقًا" in payload
    assert "ملغى" not in payload
    assert "status" not in payload.lower()

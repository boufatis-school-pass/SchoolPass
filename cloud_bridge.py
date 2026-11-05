# -*- coding: utf-8 -*-
"""HTTPS client for the optional Cloudflare D1 return-request bridge."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import schoolpass_core as core


class CloudBridgeError(RuntimeError):
    """A user-safe Cloudflare communication/configuration error."""


def _config() -> tuple[str, str]:
    config = core.cloud_bridge_config()
    base, key = config.get("url", "").rstrip("/"), config.get("api_key", "")
    if not base:
        raise CloudBridgeError("لم يُضبط رابط Cloudflare في إعدادات المؤسسة.")
    if not key:
        raise CloudBridgeError("لم يُنشأ مفتاح الربط السري أو لم يُحفظ على هذا الحاسوب.")
    return base, key


def _request(method: str, path: str, payload: dict | None = None, *, authenticated: bool = True) -> dict:
    base, key = _config() if authenticated else (core.cloud_bridge_url(), "")
    if not base:
        raise CloudBridgeError("رابط Cloudflare غير مضبوط.")
    headers = {"Accept": "application/json", "User-Agent": "SchoolPass/4.9.3"}
    body = None
    if authenticated:
        headers["Authorization"] = "Bearer " + key
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(base + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(256 * 1024)
            if len(raw) >= 256 * 1024:
                raise CloudBridgeError("استجابة Cloudflare أكبر من الحد الآمن.")
            result = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(result, dict):
                raise CloudBridgeError("استجابة Cloudflare غير مفهومة.")
            return result
    except HTTPError as exc:
        try:
            detail = exc.read(4096).decode("utf-8", "replace")
            parsed = json.loads(detail)
            code = parsed.get("error", "http_error")
        except Exception:
            code = "http_error"
        if exc.code == 401:
            raise CloudBridgeError("رفض Cloudflare مفتاح الربط. راجع المفتاح في إعدادات Worker وSchoolPass.") from exc
        if exc.code == 409:
            raise CloudBridgeError("تغيّرت حالة طلب العودة أو رخصته قبل تنفيذ العملية.") from exc
        if exc.code == 429:
            raise CloudBridgeError("Cloudflare طلب إبطاء المحاولات مؤقتًا.") from exc
        raise CloudBridgeError(f"تعذر تنفيذ طلب Cloudflare (HTTP {exc.code}: {code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CloudBridgeError("تعذر الاتصال بخدمة Cloudflare. سيُعاد المحاولة تلقائيًا عند عودة الإنترنت.") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CloudBridgeError("أرسل Cloudflare استجابة غير صالحة.") from exc


def _to_utc(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.isoformat(timespec="seconds")


def sync_local_permits() -> int:
    """Sync all exit permits (including older open exits) and recent entry slips."""
    cutoff = (datetime.now() - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S")
    with core.db_connect() as conn:
        rows = conn.execute(
            "SELECT qr_token,record_no,kind,student_name,class_name,issued_at,created_at,status,returned_at "
            "FROM records WHERE kind='exit_permit' OR COALESCE(NULLIF(issued_at,''),created_at)>=? "
            "ORDER BY id DESC LIMIT 5000", (cutoff,)
        ).fetchall()
    if not rows:
        return 0
    payloads = []
    for row in rows:
        issued = _to_utc(row["issued_at"] or row["created_at"])
        returned = _to_utc(row["returned_at"]) if row["returned_at"] else None
        payloads.append({
            "token": row["qr_token"],
            "record_no": row["record_no"],
            "kind": row["kind"],
            "student_name": row["student_name"],
            "class_name": row["class_name"],
            "issued_at": issued,
            "status": row["status"],
            "returned_at": returned,
        })
    sent = 0
    for start in range(0, len(payloads), 100):
        result = _request("POST", "/api/permits/batch", {"permits": payloads[start:start + 100]})
        if not result.get("ok"):
            raise CloudBridgeError("لم يؤكد Cloudflare حفظ الرخص.")
        sent += int(result.get("count", 0))
    return sent


def pending_return_requests() -> list[dict]:
    result = _request("GET", "/api/return-requests/pending")
    rows = result.get("requests", [])
    return rows if isinstance(rows, list) else []


def decide_return_request(request_id: str, decision: str, recorded_by: str) -> None:
    if decision not in {"approved", "rejected"}:
        raise ValueError("قرار العودة غير صالح.")
    result = _request(
        "POST", f"/api/return-requests/{request_id}/decision",
        {"decision": decision, "recorded_by": str(recorded_by or "مشرف")[:100]},
    )
    if not result.get("ok"):
        raise CloudBridgeError("لم يؤكد Cloudflare حفظ قرار المشرف.")

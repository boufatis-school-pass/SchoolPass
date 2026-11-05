import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_cloudflare_d1_schema_creates_required_tables_and_guards_duplicate_pending_requests():
    schema = (ROOT / "cloudflare" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(schema)

    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"permits", "return_requests", "rate_limits"} <= tables

    conn.execute(
        "INSERT INTO permits(token,record_no,kind,student_name,class_name,issued_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("a" * 48, "OUT-0001", "exit_permit", "تلميذ تجريبي", "1AS1",
         "2026-11-05T08:00:00+01:00", "2026-11-05T08:00:00+01:00")
    )
    conn.execute(
        "INSERT INTO return_requests(id,token,state,requested_at) VALUES(?,?,?,?)",
        ("11111111-1111-4111-8111-111111111111", "a" * 48, "pending",
         "2026-11-05T09:00:00+01:00")
    )
    try:
        conn.execute(
            "INSERT INTO return_requests(id,token,state,requested_at) VALUES(?,?,?,?)",
            ("22222222-2222-4222-8222-222222222222", "a" * 48, "pending",
             "2026-11-05T09:01:00+01:00")
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("a permit must not have two pending return requests")

    conn.execute(
        "UPDATE return_requests SET state='rejected' WHERE token=?",
        ("a" * 48,)
    )
    conn.execute(
        "INSERT INTO return_requests(id,token,state,requested_at) VALUES(?,?,?,?)",
        ("33333333-3333-4333-8333-333333333333", "a" * 48, "pending",
         "2026-11-05T09:02:00+01:00")
    )
    conn.close()

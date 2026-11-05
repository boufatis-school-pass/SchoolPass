const JSON_HEADERS = { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-content-type-options": "nosniff" };
const TOKEN_RE = /^[0-9a-f]{48}$/;
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
const now = () => new Date().toISOString();

async function digest(value) {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, "0")).join("");
}
function authorized(request, env) {
  const supplied = request.headers.get("authorization") || "";
  return !!env.DEVICE_API_KEY && supplied === `Bearer ${env.DEVICE_API_KEY}`;
}
async function limited(request, env) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const key = await digest(ip);
  const minute = Math.floor(Date.now() / 60000);
  await env.DB.prepare("DELETE FROM rate_limits WHERE window_start < ?").bind(minute - 2).run();
  await env.DB.prepare("INSERT INTO rate_limits(ip_hash,window_start,hits) VALUES(?,?,1) ON CONFLICT(ip_hash,window_start) DO UPDATE SET hits=hits+1").bind(key, minute).run();
  const row = await env.DB.prepare("SELECT hits FROM rate_limits WHERE ip_hash=? AND window_start=?").bind(key, minute).first();
  return (row?.hits || 0) > 20;
}
async function permit(env, token) {
  return env.DB.prepare("SELECT token,record_no,kind,student_name,class_name,issued_at,expires_at,status,returned_at FROM permits WHERE token=?").bind(token).first();
}
function safePermit(row) {
  return { record_no: row.record_no, kind: row.kind, student_name: row.student_name, class_name: row.class_name,
    issued_at: row.issued_at, expires_at: row.expires_at, status: row.status, returned_at: row.returned_at };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return json({ ok: true, service: "SchoolPass Cloud Bridge" });

    const match = url.pathname.match(/^\/v\/([0-9a-f]{48})$/);
    if (request.method === "GET" && match) {
      if (await limited(request, env)) return json({ error: "rate_limited" }, 429);
      const row = await permit(env, match[1]);
      if (!row) return json({ error: "not_found" }, 404);
      const expired = row.expires_at && Date.now() >= Date.parse(row.expires_at);
      return json({ permit: safePermit(row), expired: !!expired,
        can_request_return: row.kind === "exit_permit" && row.status === "valid" });
    }

    if (request.method === "POST" && url.pathname === "/api/permits") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await request.json().catch(() => null);
      if (!body || !TOKEN_RE.test(body.token || "") || !["late","absence_return","special_entry","exit_return","exit_permit"].includes(body.kind))
        return json({ error: "invalid_payload" }, 400);
      const expires = body.kind === "exit_permit" ? null : new Date(Date.parse(body.issued_at) + 2 * 60 * 60 * 1000).toISOString();
      await env.DB.prepare(`INSERT INTO permits(token,record_no,kind,student_name,class_name,issued_at,expires_at,status,returned_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET
        record_no=excluded.record_no,kind=excluded.kind,student_name=excluded.student_name,class_name=excluded.class_name,
        issued_at=excluded.issued_at,expires_at=excluded.expires_at,status=excluded.status,returned_at=excluded.returned_at,updated_at=excluded.updated_at`)
        .bind(body.token, body.record_no, body.kind, body.student_name, body.class_name, body.issued_at, expires, body.status || "valid", body.returned_at || null, now()).run();
      return json({ ok: true, expires_at: expires });
    }

    if (request.method === "POST" && url.pathname === "/api/return-requests") {
      if (await limited(request, env)) return json({ error: "rate_limited" }, 429);
      const body = await request.json().catch(() => null);
      if (!body || !TOKEN_RE.test(body.token || "")) return json({ error: "invalid_token" }, 400);
      const row = await permit(env, body.token);
      if (!row) return json({ error: "not_found" }, 404);
      if (row.kind !== "exit_permit" || row.status !== "valid") return json({ error: "permit_not_open" }, 409);
      const id = crypto.randomUUID();
      try {
        await env.DB.prepare("INSERT INTO return_requests(id,token,state,requested_at) VALUES(?,?,'pending',?)").bind(id, body.token, now()).run();
      } catch (e) { return json({ error: "request_conflict" }, 409); }
      return json({ ok: true, request_id: id, message: "تم إرسال طلب تسجيل العودة إلى المؤسسة." }, 201);
    }

    if (url.pathname === "/api/return-requests/pending" && request.method === "GET") {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const rows = await env.DB.prepare(`SELECT r.id,r.token,r.requested_at,p.record_no,p.student_name,p.class_name,p.issued_at
        FROM return_requests r JOIN permits p ON p.token=r.token WHERE r.state='pending' ORDER BY r.requested_at LIMIT 100`).all();
      return json({ requests: rows.results || [] });
    }

    const decision = url.pathname.match(/^\/api\/return-requests\/([0-9a-f-]+)\/decision$/);
    if (request.method === "POST" && decision) {
      if (!authorized(request, env)) return json({ error: "unauthorized" }, 401);
      const body = await request.json().catch(() => null);
      if (!body || !["approved","rejected"].includes(body.decision) || !body.recorded_by)
        return json({ error: "invalid_decision" }, 400);
      const result = await env.DB.prepare("UPDATE return_requests SET state=?,decided_at=?,recorded_by=? WHERE id=? AND state='pending'")
        .bind(body.decision, now(), String(body.recorded_by).slice(0,100), decision[1]).run();
      if (!result.meta.changes) return json({ error: "already_decided" }, 409);
      return json({ ok: true });
    }
    return json({ error: "not_found" }, 404);
  }
};
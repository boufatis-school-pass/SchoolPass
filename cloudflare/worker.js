const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store, max-age=0",
  "x-content-type-options": "nosniff",
  "referrer-policy": "no-referrer"
};
const TOKEN_RE = /^[0-9a-f]{48}$/;
const REQUEST_ID_RE = /^[0-9a-f-]{36}$/;
const ENTRY_KINDS = new Set(["late", "absence_return", "special_entry", "exit_return"]);
const ALL_KINDS = new Set([...ENTRY_KINDS, "exit_permit"]);
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: JSON_HEADERS });
const now = () => new Date().toISOString();

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}
function htmlResponse(body, status = 200) {
  const headers = new Headers({
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store, max-age=0",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
  });
  return new Response(body, { status, headers });
}
function page(title, content) {
  return "<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>" +
    "<meta name='viewport' content='width=device-width,initial-scale=1'><meta name='robots' content='noindex,nofollow'>" +
    "<title>" + escapeHtml(title) + "</title><style>" +
    ":root{font-family:Tahoma,Arial,sans-serif;color:#17243a;background:#f1f5fb}" +
    "body{margin:0;padding:22px}.card{max-width:520px;margin:5vh auto;background:#fff;border:1px solid #dce5f1;" +
    "border-radius:22px;padding:26px;box-shadow:0 18px 45px #18365a18}h2{margin:0 0 6px;color:#174d7a;font-size:20px}" +
    "h3{margin:18px 0 12px;font-size:22px}.sub{color:#64748b;margin:0 0 18px}.row{display:flex;justify-content:space-between;" +
    "gap:12px;padding:12px 0;border-bottom:1px solid #edf1f6}.label{color:#68778d}.value{font-weight:700;text-align:left}" +
    ".status{margin:18px 0;padding:13px;border-radius:13px;background:#eef5ff;color:#174d7a;font-weight:700}" +
    ".bad{background:#fff0f0;color:#a51d2d}.good{background:#eaf8f0;color:#14633b}" +
    "button{width:100%;border:0;border-radius:14px;padding:15px 18px;background:#147b54;color:white;font-size:17px;" +
    "font-weight:700;cursor:pointer}button:disabled{opacity:.55;cursor:not-allowed}.note{font-size:13px;color:#718096;line-height:1.7;margin-top:18px}" +
    "#message{margin-top:12px;line-height:1.6;font-weight:700}</style></head><body><main class='card'>" +
    "<h2>ثانوية المجاهد بسجراري لخضر بوفاطيس</h2><p class='sub'>التحقق الإلكتروني من الرخصة</p>" +
    content + "<p class='note'>هذه الصفحة تعرض الحد الأدنى من المعلومات اللازمة للتحقق. لا تعرض بيانات ولي الأمر أو رقم بطاقة الهوية.</p>" +
    "</main></body></html>";
}
async function digest(value) {
  const bytes = new TextEncoder().encode(value);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(x => x.toString(16).padStart(2, "0")).join("");
}
async function authorized(request, env) {
  const supplied = request.headers.get("authorization") || "";
  if (!env.DEVICE_API_KEY || !supplied.startsWith("Bearer ")) return false;
  const [expectedHash, suppliedHash] = await Promise.all([
    digest(String(env.DEVICE_API_KEY)),
    digest(supplied.slice(7))
  ]);
  let difference = 0;
  for (let i = 0; i < expectedHash.length; i++) difference |= expectedHash.charCodeAt(i) ^ suppliedHash.charCodeAt(i);
  return difference === 0;
}
async function limited(request, env, maxHits = 20) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const key = await digest(ip);
  const minute = Math.floor(Date.now() / 60000);
  await env.DB.prepare("DELETE FROM rate_limits WHERE window_start < ?").bind(minute - 2).run();
  await env.DB.prepare("INSERT INTO rate_limits(ip_hash,window_start,hits) VALUES(?,?,1) " +
    "ON CONFLICT(ip_hash,window_start) DO UPDATE SET hits=hits+1").bind(key, minute).run();
  const row = await env.DB.prepare("SELECT hits FROM rate_limits WHERE ip_hash=? AND window_start=?")
    .bind(key, minute).first();
  return (row?.hits || 0) > maxHits;
}
async function readJson(request, maxBytes = 4096) {
  const type = (request.headers.get("content-type") || "").toLowerCase();
  if (!type.includes("application/json")) return { error: "content_type" };
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > maxBytes) return { error: "too_large" };
  if (!request.body) return { error: "invalid_json" };
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return { error: "too_large" };
      }
      chunks.push(value);
    }
  } catch (_) {
    return { error: "invalid_json" };
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try {
    const raw = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    const value = JSON.parse(raw);
    return value && typeof value === "object" && !Array.isArray(value) ? { value } : { error: "invalid_json" };
  } catch (_) {
    return { error: "invalid_json" };
  }
}
function sameOrigin(request, url) {
  const origin = request.headers.get("origin");
  return !!origin && origin === url.origin;
}
async function getPermit(env, token) {
  return env.DB.prepare("SELECT token,record_no,kind,student_name,class_name,issued_at,expires_at,status,returned_at " +
    "FROM permits WHERE token=?").bind(token).first();
}
function expiryFor(kind, issuedAt) {
  if (!ENTRY_KINDS.has(kind)) return null;
  const date = new Date(issuedAt);
  if (!Number.isFinite(date.getTime())) return "invalid";
  return new Date(date.getTime() + 2 * 60 * 60 * 1000).toISOString();
}
function validText(value, max) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}
function permitPage(row) {
  const isExit = row.kind === "exit_permit";
  const expired = !isExit && row.expires_at && Date.now() >= Date.parse(row.expires_at);
  const labels = { late: "رخصة الدخول — تأخر", absence_return: "رخصة الدخول — عودة بعد الغياب",
    special_entry: "رخصة الدخول — حالة خاصة", exit_return: "إثبات عودة من خروج استثنائي",
    exit_permit: "رخصة خروج استثنائي" };
  const status = row.status === "returned" ? "تم تسجيل عودة التلميذ" :
    row.status === "cancelled" ? "الرخصة ملغاة" : "الرخصة سارية";
  const items =
    "<div class='row'><span class='label'>رقم الرخصة</span><span class='value'>" + escapeHtml(row.record_no) + "</span></div>" +
    "<div class='row'><span class='label'>نوع الرخصة</span><span class='value'>" + escapeHtml(labels[row.kind] || row.kind) + "</span></div>" +
    "<div class='row'><span class='label'>التلميذ</span><span class='value'>" + escapeHtml(row.student_name) + "</span></div>" +
    "<div class='row'><span class='label'>القسم</span><span class='value'>" + escapeHtml(row.class_name) + "</span></div>" +
    "<div class='row'><span class='label'>" + (isExit ? "وقت الخروج" : "وقت الإصدار") + "</span><span class='value'>" + escapeHtml(row.issued_at) + "</span></div>";
  let stateClass = row.status === "returned" ? "good" : row.status === "cancelled" ? "bad" : "status";
  let extra = "";
  if (row.status === "returned") {
    extra = "<div class='status good'>تم تسجيل العودة في " + escapeHtml(row.returned_at || "") + ".</div>";
  } else if (row.status === "cancelled") {
    extra = "<div class='status bad'>هذه الرخصة ملغاة ولا يجوز اعتمادها.</div>";
  } else if (expired) {
    extra = "<div class='status bad'>انتهت صلاحية هذه الرخصة بعد ساعتين من إصدارها (" + escapeHtml(row.expires_at || "") + ").</div>";
  } else if (isExit) {
    extra = "<div class='status'>رخصة الخروج تبقى مفتوحة إلى حين تسجيل عودة التلميذ من المشرف.</div>";
  } else {
    extra = "<div class='status good'>الرخصة سارية حتى " + escapeHtml(row.expires_at || "") + ".</div>";
  }
  const canRequest = row.kind === "exit_permit" && row.status === "valid";
  const button = canRequest ? (row.pending_request_at ?
    "<div class='status'>تم إرسال طلب العودة إلى المؤسسة في " + escapeHtml(row.pending_request_at) + ". انتظر تأكيد المشرف.</div>" :
    "<button id='request' type='button'>طلب تسجيل عودة التلميذ</button><div id='message' role='status' aria-live='polite'></div>") : "";
  const script = canRequest && !row.pending_request_at ?
    "<script>(function(){const b=document.getElementById('request'),m=document.getElementById('message');" +
    "b.addEventListener('click',async()=>{b.disabled=true;m.textContent='جارٍ إرسال الطلب...';" +
    "try{const r=await fetch('/api/return-requests',{method:'POST',headers:{'content-type':'application/json'}," +
    "body:JSON.stringify({token:'" + row.token + "'})});const d=await r.json();" +
    "if(r.ok){m.textContent=d.message||'وصل طلب العودة إلى المؤسسة. انتظر تأكيد المشرف.';b.textContent='تم إرسال الطلب';}" +
    "else if(r.status===409){m.textContent='لا يمكن إرسال الطلب: قد تكون العودة سُجلت أو الرخصة أُغلقت.';}" +
    "else{m.textContent='تعذر إرسال الطلب. تحقق من الاتصال ثم أعد تحميل الصفحة.';b.disabled=false;}" +
    "}catch(e){m.textContent='تعذر الاتصال. أعد المحاولة عند توفر الإنترنت.';b.disabled=false;}});})();</script>" : "";
  return page("التحقق من الرخصة", "<h3>بيانات الرخصة</h3>" + items + extra + button + script);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      try {
        await env.DB.prepare("SELECT 1 FROM permits LIMIT 1").first();
        await env.DB.prepare("SELECT 1 FROM return_requests LIMIT 1").first();
        await env.DB.prepare("SELECT 1 FROM rate_limits LIMIT 1").first();
        return json({
          ok: true,
          database: true,
          device_auth_configured: Boolean(env.DEVICE_API_KEY),
          service: "SchoolPass Cloud Bridge",
          version: "4.9.3"
        });
      } catch (_) {
        return json({ ok: false, database: false, error: "d1_not_ready" }, 503);
      }
    }

    const publicMatch = url.pathname.match(/^\/v\/([0-9a-f]{48})$/);
    if (request.method === "GET" && publicMatch) {
      if (await limited(request, env, 30)) return htmlResponse(page("مهلة مؤقتة", "<div class='status bad'>عدد المحاولات كبير. أعد المحاولة لاحقًا.</div>"), 429);
      const row = await getPermit(env, publicMatch[1]);
      if (!row) return htmlResponse(page("رخصة غير موجودة", "<div class='status bad'>لم يتم العثور على هذه الرخصة.</div>"), 404);
      if (row.kind === "exit_permit" && row.status === "valid") {
        const pending = await env.DB.prepare(
          "SELECT id,requested_at FROM return_requests WHERE token=? AND state='pending' LIMIT 1"
        ).bind(row.token).first();
        if (pending) {
          row.pending_request_at = pending.requested_at;
        }
      }
      return htmlResponse(permitPage(row));
    }

    if (request.method === "POST" && url.pathname === "/api/return-requests") {
      if (!sameOrigin(request, url)) return json({ error: "origin_rejected" }, 403);
      if (await limited(request, env, 30)) return json({ error: "rate_limited" }, 429);
      const parsed = await readJson(request, 1024);
      if (parsed.error) return json({ error: parsed.error }, parsed.error === "too_large" ? 413 : 400);
      const token = parsed.value.token;
      if (typeof token !== "string" || !TOKEN_RE.test(token)) return json({ error: "invalid_token" }, 400);
      const row = await getPermit(env, token);
      if (!row) return json({ error: "not_found" }, 404);
      if (row.kind !== "exit_permit" || row.status !== "valid") return json({ error: "permit_not_open" }, 409);
      const existing = await env.DB.prepare("SELECT id,requested_at FROM return_requests WHERE token=? AND state='pending' LIMIT 1")
        .bind(token).first();
      if (existing) return json({ ok: true, request_id: existing.id, state: "pending",
        message: "وصل طلب العودة إلى المؤسسة. انتظر تأكيد المشرف." });
      const id = crypto.randomUUID();
      try {
        await env.DB.prepare("INSERT INTO return_requests(id,token,state,requested_at) VALUES(?,?,'pending',?)")
          .bind(id, token, now()).run();
      } catch (_) {
        const raced = await env.DB.prepare("SELECT id FROM return_requests WHERE token=? AND state='pending' LIMIT 1")
          .bind(token).first();
        if (raced) return json({ ok: true, request_id: raced.id, state: "pending",
          message: "وصل طلب العودة إلى المؤسسة. انتظر تأكيد المشرف." });
        return json({ error: "request_conflict" }, 409);
      }
      return json({ ok: true, request_id: id, state: "pending",
        message: "وصل طلب العودة إلى المؤسسة. انتظر تأكيد المشرف." }, 201);
    }

    if (request.method === "POST" && url.pathname === "/api/permits/batch") {
      if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
      const parsed = await readJson(request, 128 * 1024);
      if (parsed.error) return json({ error: parsed.error }, parsed.error === "too_large" ? 413 : 400);
      const permits = parsed.value.permits;
      if (!Array.isArray(permits) || permits.length < 1 || permits.length > 100) return json({ error: "invalid_batch" }, 400);
      const statements = [];
      const sql = "INSERT INTO permits(token,record_no,kind,student_name,class_name,issued_at,expires_at,status,returned_at,updated_at) " +
        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET " +
        "record_no=excluded.record_no,kind=excluded.kind,student_name=excluded.student_name,class_name=excluded.class_name," +
        "issued_at=excluded.issued_at,expires_at=excluded.expires_at," +
        "status=CASE WHEN permits.status='returned' THEN 'returned' ELSE excluded.status END," +
        "returned_at=COALESCE(permits.returned_at,excluded.returned_at),updated_at=excluded.updated_at";
      for (const body of permits) {
        if (!body || !TOKEN_RE.test(body.token || "") || !ALL_KINDS.has(body.kind) ||
            !validText(body.record_no, 60) || !validText(body.student_name, 120) ||
            !validText(body.class_name, 80) || !validText(body.issued_at, 40)) {
          return json({ error: "invalid_payload" }, 400);
        }
        const expires = expiryFor(body.kind, body.issued_at);
        if (expires === "invalid") return json({ error: "invalid_issued_at" }, 400);
        const status = ["valid", "cancelled", "returned"].includes(body.status) ? body.status : "valid";
        const returnedAt = typeof body.returned_at === "string" ? body.returned_at.slice(0, 40) : null;
        statements.push(env.DB.prepare(sql).bind(body.token, body.record_no.trim(), body.kind,
          body.student_name.trim(), body.class_name.trim(), body.issued_at.trim(), expires,
          status, returnedAt, now()));
      }
      await env.DB.batch(statements);
      return json({ ok: true, count: permits.length });
    }

    if (request.method === "POST" && url.pathname === "/api/permits") {
      if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
      const parsed = await readJson(request, 4096);
      if (parsed.error) return json({ error: parsed.error }, parsed.error === "too_large" ? 413 : 400);
      const body = parsed.value;
      if (!TOKEN_RE.test(body.token || "") || !ALL_KINDS.has(body.kind) ||
          !validText(body.record_no, 60) || !validText(body.student_name, 120) ||
          !validText(body.class_name, 80) || !validText(body.issued_at, 40)) {
        return json({ error: "invalid_payload" }, 400);
      }
      const expires = expiryFor(body.kind, body.issued_at);
      if (expires === "invalid") return json({ error: "invalid_issued_at" }, 400);
      const status = ["valid", "cancelled", "returned"].includes(body.status) ? body.status : "valid";
      const returnedAt = typeof body.returned_at === "string" ? body.returned_at.slice(0, 40) : null;
      await env.DB.prepare(
        "INSERT INTO permits(token,record_no,kind,student_name,class_name,issued_at,expires_at,status,returned_at,updated_at) " +
        "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET " +
        "record_no=excluded.record_no,kind=excluded.kind,student_name=excluded.student_name,class_name=excluded.class_name," +
        "issued_at=excluded.issued_at,expires_at=excluded.expires_at," +
        "status=CASE WHEN permits.status='returned' THEN 'returned' ELSE excluded.status END," +
        "returned_at=COALESCE(permits.returned_at,excluded.returned_at),updated_at=excluded.updated_at"
      ).bind(body.token, body.record_no.trim(), body.kind, body.student_name.trim(), body.class_name.trim(),
        body.issued_at.trim(), expires, status, returnedAt, now()).run();
      return json({ ok: true, expires_at: expires });
    }

    if (request.method === "GET" && url.pathname === "/api/return-requests/pending") {
      if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
      const rows = await env.DB.prepare(
        "SELECT r.id,r.token,r.requested_at,p.record_no,p.student_name,p.class_name,p.issued_at " +
        "FROM return_requests r JOIN permits p ON p.token=r.token " +
        "WHERE r.state='pending' AND p.kind='exit_permit' " +
        "ORDER BY r.requested_at LIMIT 100"
      ).all();
      return json({ requests: rows.results || [] });
    }

    const decision = url.pathname.match(/^\/api\/return-requests\/([0-9a-f-]{36})\/decision$/);
    if (request.method === "POST" && decision) {
      if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
      const parsed = await readJson(request, 1024);
      if (parsed.error) return json({ error: parsed.error }, parsed.error === "too_large" ? 413 : 400);
      const body = parsed.value;
      if (!["approved", "rejected"].includes(body.decision) ||
          !validText(body.recorded_by, 100)) return json({ error: "invalid_decision" }, 400);
      const existing = await env.DB.prepare("SELECT token,state FROM return_requests WHERE id=?")
        .bind(decision[1]).first();
      if (!existing) return json({ error: "request_not_found" }, 404);
      if (existing.state === body.decision) return json({ ok: true, already_applied: true, decision: body.decision });
      if (existing.state !== "pending") return json({ error: "already_decided", state: existing.state }, 409);
      if (body.decision === "approved") {
        const permit = await getPermit(env, existing.token);
        if (!permit || permit.kind !== "exit_permit" || permit.status === "cancelled") {
          return json({ error: "permit_not_open" }, 409);
        }
        const stamp = now();
        const results = await env.DB.batch([
          env.DB.prepare("UPDATE return_requests SET state='approved',decided_at=?,recorded_by=? WHERE id=? AND state='pending' " +
            "AND EXISTS(SELECT 1 FROM permits WHERE token=? AND kind='exit_permit' AND status IN ('valid','returned'))")
            .bind(stamp, body.recorded_by.trim(), decision[1], existing.token),
          env.DB.prepare("UPDATE permits SET status='returned',returned_at=COALESCE(returned_at,?),updated_at=? " +
            "WHERE token=? AND kind='exit_permit' AND status IN ('valid','returned') " +
            "AND EXISTS(SELECT 1 FROM return_requests WHERE id=? AND state='approved')")
            .bind(stamp, stamp, existing.token, decision[1])
        ]);
        if (!(results[0]?.meta?.changes) || !(results[1]?.meta?.changes)) {
          return json({ error: "permit_state_changed" }, 409);
        }
      } else {
        const result = await env.DB.prepare("UPDATE return_requests SET state='rejected',decided_at=?,recorded_by=? " +
          "WHERE id=? AND state='pending'").bind(now(), body.recorded_by.trim(), decision[1]).run();
        if (!result.meta.changes) return json({ error: "already_decided" }, 409);
      }
      return json({ ok: true, decision: body.decision });
    }
    return json({ error: "not_found" }, 404);
  }
};

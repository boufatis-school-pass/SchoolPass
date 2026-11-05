CREATE TABLE IF NOT EXISTS permits (
  token TEXT PRIMARY KEY,
  record_no TEXT NOT NULL,
  kind TEXT NOT NULL,
  student_name TEXT NOT NULL,
  class_name TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'valid',
  returned_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS return_requests (
  id TEXT PRIMARY KEY,
  token TEXT NOT NULL REFERENCES permits(token),
  state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected')),
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  recorded_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_return_requests_pending ON return_requests(state,requested_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_return_per_token
  ON return_requests(token) WHERE state='pending';
CREATE TABLE IF NOT EXISTS rate_limits (
  ip_hash TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  hits INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(ip_hash,window_start)
);

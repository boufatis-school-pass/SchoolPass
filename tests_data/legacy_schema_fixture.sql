
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','supervisor')),
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            failed_count INTEGER NOT NULL DEFAULT 0,
            lock_until REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_key TEXT NOT NULL UNIQUE,
            last_name TEXT NOT NULL,
            first_name TEXT NOT NULL,
            gender TEXT DEFAULT '',
            year TEXT DEFAULT '',
            branch TEXT DEFAULT '',
            section TEXT DEFAULT '',
            class_name TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_no TEXT NOT NULL UNIQUE,
            qr_token TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            student_key TEXT NOT NULL,
            student_name TEXT NOT NULL,
            class_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            reason TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'valid',
            details_json TEXT NOT NULL DEFAULT '{}',
            related_record_id INTEGER,
            cancelled_reason TEXT DEFAULT '',
            cancelled_at TEXT DEFAULT '',
            cancelled_by INTEGER,
            returned_at TEXT DEFAULT '',
            returned_by INTEGER,
            printed_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(related_record_id) REFERENCES records(id),
            FOREIGN KEY(cancelled_by) REFERENCES users(id),
            FOREIGN KEY(returned_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            happened_at TEXT NOT NULL,
            user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        );
        
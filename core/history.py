import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

HISTORY_DB = Path(__file__).resolve().parent.parent / "config" / "history.db"


class History:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(HISTORY_DB), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                session TEXT,
                method TEXT,
                url TEXT,
                headers TEXT,
                body TEXT,
                status INTEGER,
                response_body TEXT,
                elapsed REAL,
                error TEXT,
                created_at TEXT
            )
        """)
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(requests)")
        cols = [row[1] for row in cur.fetchall()]
        if "session" not in cols:
            self.conn.execute("ALTER TABLE requests ADD COLUMN session TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_module ON requests(module)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_session ON requests(session)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_created ON requests(created_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_module_session "
            "ON requests(module, session)"
        )
        self.conn.commit()

    def add(self, *, module, method, url, headers, body,
            session=None, status=None, response_body=None,
            elapsed=None, error=None):
        with self._lock:
            self.conn.execute("""
                INSERT INTO requests
                (module, session, method, url, headers, body,
                 status, response_body, elapsed, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                module, session, method, url,
                json.dumps(headers, ensure_ascii=False)
                if isinstance(headers, dict) else headers,
                body, status, response_body, elapsed, error,
                datetime.utcnow().isoformat()
            ))
            self.conn.commit()

    def list(self, module=None, session=None, limit=300, since=None) -> List[Dict]:
        with self._lock:
            cur = self.conn.cursor()
            sql = ("SELECT id, module, session, method, url, status, "
                   "elapsed, error, created_at FROM requests WHERE 1=1")
            params: list = []
            if module:
                sql += " AND module=?"
                params.append(module)
            if session:
                sql += " AND session=?"
                params.append(session)
            if since:
                sql += " AND created_at>=?"
                params.append(since)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall()
            keys = ["id", "module", "session", "method", "url",
                    "status", "elapsed", "error", "created_at"]
            return [dict(zip(keys, r)) for r in rows]

    def get(self, row_id: int) -> Optional[Dict]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM requests WHERE id=?", (row_id,))
            row = cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    def clear(self, module=None, session=None):
        with self._lock:
            if module and session:
                self.conn.execute(
                    "DELETE FROM requests WHERE module=? AND session=?",
                    (module, session))
            elif module:
                self.conn.execute("DELETE FROM requests WHERE module=?", (module,))
            elif session:
                self.conn.execute("DELETE FROM requests WHERE session=?", (session,))
            else:
                self.conn.execute("DELETE FROM requests")
            self.conn.commit()

    def delete_ids(self, ids) -> int:
        ids = list(ids)
        if not ids:
            return 0
        with self._lock:
            self.conn.executemany(
                "DELETE FROM requests WHERE id=?", [(i,) for i in ids])
            self.conn.commit()
        return len(ids)

    def count(self, module=None, session=None, since=None) -> int:
        with self._lock:
            cur = self.conn.cursor()
            sql = "SELECT COUNT(*) FROM requests WHERE 1=1"
            params: list = []
            if module:
                sql += " AND module=?"
                params.append(module)
            if session:
                sql += " AND session=?"
                params.append(session)
            if since:
                sql += " AND created_at>=?"
                params.append(since)
            cur.execute(sql, params)
            return cur.fetchone()[0]

    def modules(self) -> List[str]:
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT DISTINCT module FROM requests ORDER BY module")
            return [r[0] for r in cur.fetchall()]

    def sessions(self, module=None, limit=50) -> List[str]:
        with self._lock:
            cur = self.conn.cursor()
            if module:
                cur.execute(
                    "SELECT session, MAX(id) as mx FROM requests "
                    "WHERE module=? AND session IS NOT NULL AND session!='' "
                    "GROUP BY session ORDER BY mx DESC LIMIT ?",
                    (module, limit))
            else:
                cur.execute(
                    "SELECT session, MAX(id) as mx FROM requests "
                    "WHERE session IS NOT NULL AND session!='' "
                    "GROUP BY session ORDER BY mx DESC LIMIT ?",
                    (limit,))
            return [r[0] for r in cur.fetchall()]


def get_history() -> History:
    return History()
"""État persistant (SQLite) : offres déjà vues, santé des sources, courriers déjà traités."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  uid TEXT PRIMARY KEY,
  fp TEXT NOT NULL,
  source TEXT, company TEXT, title TEXT, location TEXT, url TEXT,
  first_seen TEXT NOT NULL,
  tier TEXT, score INTEGER DEFAULT 0,
  notified INTEGER DEFAULT 0,
  digested INTEGER DEFAULT 0,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_fp ON jobs(fp);
CREATE INDEX IF NOT EXISTS idx_jobs_seen ON jobs(first_seen);
CREATE TABLE IF NOT EXISTS health(
  source TEXT PRIMARY KEY,
  ok_runs INTEGER DEFAULT 0, fail_runs INTEGER DEFAULT 0,
  consecutive_failures INTEGER DEFAULT 0, zero_streak INTEGER DEFAULT 0,
  max_count INTEGER DEFAULT 0, last_count INTEGER DEFAULT 0,
  last_ok TEXT, last_error TEXT, alerted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS mails(message_id TEXT PRIMARY KEY, seen_at TEXT);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._dirty = False

    # ── méta ────────────────────────────────────────────────────────────────────────────────
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return r["v"] if r else default

    def set_meta(self, key: str, value: str) -> None:
        if self.get_meta(key) == value:
            return
        self.db.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
        self._dirty = True

    @property
    def initialized(self) -> bool:
        return self.get_meta("initialized") == "1"

    # ── offres ──────────────────────────────────────────────────────────────────────────────
    def known_uid(self, uid: str) -> bool:
        return self.db.execute("SELECT 1 FROM jobs WHERE uid=?", (uid,)).fetchone() is not None

    def known_fp(self, fp: str, days: int = 120) -> bool:
        """Même offre déjà vue (autre source / autre identifiant) dans les `days` derniers jours."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return self.db.execute("SELECT 1 FROM jobs WHERE fp=? AND first_seen>=?", (fp, cutoff)).fetchone() is not None

    def bump(self, key: str) -> int:
        """Compteur persistant (ex. échecs de chargement d'une fiche)."""
        n = int(self.get_meta(key, "0") or 0) + 1
        self.set_meta(key, str(n))
        return n

    def add_job(self, job, tier: str, score: int, summary: dict | None, notified: bool, digested: bool = False) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO jobs(uid,fp,source,company,title,location,url,first_seen,tier,score,notified,digested,summary)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job.uid, job.fingerprint, job.source, job.company, job.title, job.location, job.url, utcnow(),
             tier, score, int(notified), int(digested), json.dumps(summary or {}, ensure_ascii=False)),
        )
        self._dirty = True

    def mark_notified(self, uid: str) -> None:
        self.db.execute("UPDATE jobs SET notified=1 WHERE uid=?", (uid,))
        self._dirty = True

    def digest_items(self, since_iso: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE tier='DIGEST' AND digested=0 AND first_seen>=? ORDER BY score DESC, first_seen DESC",
            (since_iso,),
        ).fetchall()

    def mark_digested(self, uids: list[str]) -> None:
        self.db.executemany("UPDATE jobs SET digested=1 WHERE uid=?", [(u,) for u in uids])
        self._dirty = True

    def recent_alerts(self, since_iso: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE notified=1 AND first_seen>=? ORDER BY first_seen DESC", (since_iso,)
        ).fetchall()

    def top_open(self, n: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM jobs WHERE tier IN ('TOP','GOOD','CHECK') ORDER BY "
            "CASE tier WHEN 'TOP' THEN 0 WHEN 'GOOD' THEN 1 ELSE 2 END, score DESC LIMIT ?", (n,)
        ).fetchall()

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT tier, COUNT(*) c FROM jobs GROUP BY tier").fetchall()
        return {r["tier"]: r["c"] for r in rows}

    # ── courriers ───────────────────────────────────────────────────────────────────────────
    def mail_seen(self, message_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM mails WHERE message_id=?", (message_id,)).fetchone() is not None

    def add_mail(self, message_id: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO mails(message_id, seen_at) VALUES(?,?)", (message_id, utcnow()))
        self._dirty = True

    # ── santé des sources ───────────────────────────────────────────────────────────────────
    def health(self, source: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM health WHERE source=?", (source,)).fetchone()

    def all_health(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM health ORDER BY source").fetchall()

    def record_ok(self, source: str, count: int) -> str | None:
        """Retourne 'recovered' si la source revient après une alerte, 'empty' si elle devient suspecte."""
        h = self.health(source)
        now = utcnow()
        if h is None:
            self.db.execute(
                "INSERT INTO health(source, ok_runs, max_count, last_count, last_ok, zero_streak) VALUES(?,?,?,?,?,?)",
                (source, 1, count, count, now, 1 if count == 0 else 0),
            )
            self._dirty = True
            return None
        was_alerted = bool(h["alerted"])
        zero = (h["zero_streak"] + 1) if count == 0 else 0
        mx = max(h["max_count"], count)
        changed = (h["consecutive_failures"] != 0 or h["last_count"] != count or h["zero_streak"] != zero
                   or h["max_count"] != mx or was_alerted)
        if changed:
            self.db.execute(
                "UPDATE health SET ok_runs=ok_runs+1, consecutive_failures=0, zero_streak=?, max_count=?, last_count=?,"
                " last_ok=?, last_error=NULL, alerted=0 WHERE source=?",
                (zero, mx, count, now, source),
            )
            self._dirty = True
        if was_alerted:
            return "recovered"
        return None

    def record_fail(self, source: str, error: str, alert_after: int) -> str | None:
        """Retourne 'failing' une seule fois quand une source qui marchait avant échoue trop longtemps."""
        h = self.health(source)
        error = error[:300]
        if h is None:
            self.db.execute("INSERT INTO health(source, fail_runs, consecutive_failures, last_error) VALUES(?,?,?,?)",
                            (source, 1, 1, error))
            self._dirty = True
            return None
        n = h["consecutive_failures"] + 1
        alert = h["ok_runs"] > 0 and n >= alert_after and not h["alerted"]
        self.db.execute(
            "UPDATE health SET fail_runs=fail_runs+1, consecutive_failures=?, last_error=?, alerted=? WHERE source=?",
            (n, error, 1 if (alert or h["alerted"]) else 0, source),
        )
        self._dirty = True
        return "failing" if alert else None

    # ── entretien ───────────────────────────────────────────────────────────────────────────
    def prune(self, days: int = 400) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = self.db.execute("DELETE FROM jobs WHERE first_seen<?", (cutoff,))
        cur2 = self.db.execute("DELETE FROM mails WHERE seen_at<?", (cutoff,))
        if cur.rowcount or cur2.rowcount:
            self._dirty = True

    def commit(self) -> None:
        if self._dirty:
            self.db.commit()
            self._dirty = False

    def close(self) -> None:
        self.commit()
        self.db.close()

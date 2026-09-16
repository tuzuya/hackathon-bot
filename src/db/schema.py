"""SQLite のスキーマ定義。

⚠️ エスカレーションの状態は必ずここに永続化する(CLAUDE.md 制約 3)。
   asyncio.sleep などメモリ上のタイマーで持つと、プロセス再起動で消える。
   Railway は再デプロイでプロセスが入れ替わるため、本番で確実に失われる。
"""

from __future__ import annotations

# status の取りうる値は src.escalation.models.Status と対応させること。
SCHEMA = """
CREATE TABLE IF NOT EXISTS escalations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    thread_id         INTEGER NOT NULL,
    team_name         TEXT    NOT NULL,
    requester_id      INTEGER NOT NULL,
    summary           TEXT    NOT NULL,
    summary_failed    INTEGER NOT NULL DEFAULT 0,
    queue_message_id  INTEGER,
    status            TEXT    NOT NULL,
    handler_id        INTEGER,
    handler_name      TEXT,
    created_at        TEXT    NOT NULL,
    claimed_at        TEXT,
    -- 対応中のまま自動で未対応に戻す期限(UTC ISO8601)。
    -- ポーリングでこの列を見るため、メモリ上のタイマーは持たない。
    deadline_at       TEXT,
    completed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_escalations_status
    ON escalations (status);

CREATE INDEX IF NOT EXISTS idx_escalations_deadline
    ON escalations (status, deadline_at);

CREATE INDEX IF NOT EXISTS idx_escalations_thread
    ON escalations (thread_id, status);

CREATE INDEX IF NOT EXISTS idx_escalations_queue_message
    ON escalations (queue_message_id);
"""

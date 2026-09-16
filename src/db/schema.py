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

-- 運営が動的に追加した知識と、承認済み Q&A(SPEC §4.2 / §7.1)。
-- 起動時とプロンプト再構築時に、knowledge/ 配下のファイルと結合される。
CREATE TABLE IF NOT EXISTS knowledge_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    -- 'manual'(/add-knowledge で追加) | 'qa'(承認済み Q&A)
    source      TEXT    NOT NULL,
    added_by    INTEGER NOT NULL,
    added_by_name TEXT  NOT NULL,
    created_at  TEXT    NOT NULL,
    -- 古い情報は削除ではなく無効化する。新旧が両方ヒットすると
    -- Bot が矛盾した回答を返すため(SPEC §4.5)
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_knowledge_active
    ON knowledge_entries (active, category);

-- 質問のログ。レート制限のカウンタと /stats の集計を兼ねる。
-- 別テーブルに分けると二重に書くことになるので 1 つにまとめる。
CREATE TABLE IF NOT EXISTS question_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id       INTEGER NOT NULL,
    thread_id      INTEGER NOT NULL,
    team_name      TEXT    NOT NULL,
    user_id        INTEGER NOT NULL,
    created_at     TEXT    NOT NULL,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_read     INTEGER NOT NULL DEFAULT 0,
    cache_write    INTEGER NOT NULL DEFAULT 0,
    estimated_cost REAL    NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_question_log_thread_time
    ON question_log (thread_id, created_at);

CREATE INDEX IF NOT EXISTS idx_question_log_user_time
    ON question_log (user_id, created_at);

-- 小さな状態を持つための汎用テーブル。
-- 「どの金額のアラートまで出したか」を再起動をまたいで覚えるのに使う。
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

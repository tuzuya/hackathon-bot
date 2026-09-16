"""エスカレーションの永続化。

⚠️ 排他制御はすべてここで行う(CLAUDE.md「ボタン(Views)」)。
   「対応する」ボタンは 6 人のメンターが同時に押しうるため、
   アプリ側で status を読んでから書くと後勝ちになる。
   UPDATE の WHERE 句に現在の status を含めて、
   変更された行数で勝敗を判定する。
"""

from __future__ import annotations

from datetime import timedelta

import aiosqlite

from src.escalation.models import Escalation, Status, to_iso, utcnow


class EscalationStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def create(
        self,
        *,
        guild_id: int,
        thread_id: int,
        team_name: str,
        requester_id: int,
        summary: str,
        summary_failed: bool,
    ) -> Escalation:
        cursor = await self._db.execute(
            """
            INSERT INTO escalations (
                guild_id, thread_id, team_name, requester_id,
                summary, summary_failed, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                thread_id,
                team_name,
                requester_id,
                summary,
                int(summary_failed),
                Status.UNHANDLED.value,
                to_iso(utcnow()),
            ),
        )
        await self._db.commit()
        created = await self.get(cursor.lastrowid)  # type: ignore[arg-type]
        assert created is not None
        return created

    async def get(self, escalation_id: int) -> Escalation | None:
        async with self._db.execute(
            "SELECT * FROM escalations WHERE id = ?", (escalation_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return Escalation.from_row(row) if row else None

    async def get_by_queue_message(self, message_id: int) -> Escalation | None:
        """キューのメッセージ ID から引く。

        永続 View の custom_id を固定値にできるのは、これがあるため。
        """
        async with self._db.execute(
            "SELECT * FROM escalations WHERE queue_message_id = ?", (message_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return Escalation.from_row(row) if row else None

    async def find_open_by_thread(self, thread_id: int) -> Escalation | None:
        """その投稿に、まだ終わっていないエスカレーションがあるか。

        同じ投稿で「解決しなかった」を連打されたときの二重通知を防ぐ。
        """
        async with self._db.execute(
            """
            SELECT * FROM escalations
            WHERE thread_id = ? AND status IN (?, ?)
            ORDER BY id DESC LIMIT 1
            """,
            (thread_id, Status.UNHANDLED.value, Status.IN_PROGRESS.value),
        ) as cursor:
            row = await cursor.fetchone()
        return Escalation.from_row(row) if row else None

    async def set_queue_message(self, escalation_id: int, message_id: int) -> None:
        await self._db.execute(
            "UPDATE escalations SET queue_message_id = ? WHERE id = ?",
            (message_id, escalation_id),
        )
        await self._db.commit()

    async def claim(
        self,
        escalation_id: int,
        *,
        handler_id: int,
        handler_name: str,
        timeout_minutes: int,
    ) -> bool:
        """「対応する」を確定させる。先に押した人だけ True。

        WHERE に status = 'unhandled' を含めることで、
        2 人が同時に押しても 1 人しか成立しない。
        """
        now = utcnow()
        cursor = await self._db.execute(
            """
            UPDATE escalations
               SET status = ?, handler_id = ?, handler_name = ?,
                   claimed_at = ?, deadline_at = ?
             WHERE id = ? AND status = ?
            """,
            (
                Status.IN_PROGRESS.value,
                handler_id,
                handler_name,
                to_iso(now),
                to_iso(now + timedelta(minutes=timeout_minutes)),
                escalation_id,
                Status.UNHANDLED.value,
            ),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def complete(self, escalation_id: int) -> bool:
        """「完了」を確定させる。対応中のものだけ完了にできる。"""
        cursor = await self._db.execute(
            """
            UPDATE escalations
               SET status = ?, completed_at = ?, deadline_at = NULL
             WHERE id = ? AND status = ?
            """,
            (
                Status.DONE.value,
                to_iso(utcnow()),
                escalation_id,
                Status.IN_PROGRESS.value,
            ),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def expire_overdue(self) -> list[Escalation]:
        """期限切れの「対応中」を「未対応」へ戻す(SPEC §5.3)。

        ポーリングから呼ばれる。メモリ上のタイマーを使わないのは、
        プロセス再起動で消えてしまい、対応中のまま誰にも気付かれなくなるため。
        """
        now_iso = to_iso(utcnow())
        async with self._db.execute(
            """
            SELECT * FROM escalations
             WHERE status = ? AND deadline_at IS NOT NULL AND deadline_at <= ?
            """,
            (Status.IN_PROGRESS.value, now_iso),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return []

        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(
            f"""
            UPDATE escalations
               SET status = ?, handler_id = NULL, handler_name = NULL,
                   claimed_at = NULL, deadline_at = NULL
             WHERE id IN ({placeholders}) AND status = ?
            """,
            (Status.UNHANDLED.value, *ids, Status.IN_PROGRESS.value),
        )
        await self._db.commit()

        # 戻した後の状態で返す
        refreshed: list[Escalation] = []
        for escalation_id in ids:
            item = await self.get(escalation_id)
            if item is not None:
                refreshed.append(item)
        return refreshed

    async def stats(self) -> dict[str, int]:
        """段階6の /stats 用。チーム別の集計は別途足す。"""
        async with self._db.execute(
            "SELECT status, COUNT(*) AS count FROM escalations GROUP BY status"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["status"]: row["count"] for row in rows}

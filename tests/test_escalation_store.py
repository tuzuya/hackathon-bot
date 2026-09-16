"""エスカレーションの状態管理のテスト。

ここで守りたいのは 2 点(SPEC §5.3):
  1. 排他制御 — 6 人が同時に「対応する」を押しても、成立するのは 1 人だけ
  2. 30 分の自動解除 — 期限は DB に持ち、ポーリングで戻す
     (メモリ上のタイマーはプロセス再起動で消えるため)
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from src.db.database import Database
from src.escalation.models import Status, to_iso, utcnow
from src.escalation.store import EscalationStore


class StoreTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "test.db")
        await self.database.connect()
        self.store = EscalationStore(self.database.connection)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def _create(self, thread_id: int = 1):
        return await self.store.create(
            guild_id=1,
            thread_id=thread_id,
            team_name="1班",
            requester_id=10,
            summary="npm install が失敗する",
            summary_failed=False,
        )


class CreateTest(StoreTestCase):
    async def test_初期状態は未対応(self) -> None:
        escalation = await self._create()
        self.assertIs(escalation.status, Status.UNHANDLED)
        self.assertIsNone(escalation.handler_id)
        self.assertIsNone(escalation.deadline_at)

    async def test_同じ投稿の未解決分を引ける(self) -> None:
        created = await self._create(thread_id=99)
        found = await self.store.find_open_by_thread(99)
        self.assertIsNotNone(found)
        self.assertEqual(found.id, created.id)  # type: ignore[union-attr]

    async def test_完了済みは未解決として引かれない(self) -> None:
        created = await self._create(thread_id=99)
        await self.store.claim(
            created.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        await self.store.complete(created.id)
        self.assertIsNone(await self.store.find_open_by_thread(99))

    async def test_キューのメッセージIDから引ける(self) -> None:
        created = await self._create()
        await self.store.set_queue_message(created.id, 555)
        found = await self.store.get_by_queue_message(555)
        self.assertEqual(found.id, created.id)  # type: ignore[union-attr]


class ExclusiveClaimTest(StoreTestCase):
    """SPEC §5.3「2人が同時に押した場合、先に押した方を採用」。"""

    async def test_2人目の対応するは失敗する(self) -> None:
        escalation = await self._create()
        first = await self.store.claim(
            escalation.id, handler_id=1, handler_name="Aさん", timeout_minutes=30
        )
        second = await self.store.claim(
            escalation.id, handler_id=2, handler_name="Bさん", timeout_minutes=30
        )
        self.assertTrue(first)
        self.assertFalse(second)

    async def test_勝った人が対応者として記録される(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="Aさん", timeout_minutes=30
        )
        await self.store.claim(
            escalation.id, handler_id=2, handler_name="Bさん", timeout_minutes=30
        )
        current = await self.store.get(escalation.id)
        self.assertEqual(current.handler_name, "Aさん")  # type: ignore[union-attr]

    async def test_6人が同時に押しても1人しか成立しない(self) -> None:
        # エンジニアメンターは 6 人。全員が同じ通知を見て同時に押しうる
        escalation = await self._create()
        results = await asyncio.gather(
            *[
                self.store.claim(
                    escalation.id,
                    handler_id=i,
                    handler_name=f"メンター{i}",
                    timeout_minutes=30,
                )
                for i in range(6)
            ]
        )
        self.assertEqual(sum(results), 1, "対応するが複数成立している")

    async def test_対応中になると期限が入る(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        current = await self.store.get(escalation.id)
        self.assertIsNotNone(current.deadline_at)  # type: ignore[union-attr]
        self.assertIs(current.status, Status.IN_PROGRESS)  # type: ignore[union-attr]


class CompleteTest(StoreTestCase):
    async def test_対応中でないものは完了にできない(self) -> None:
        escalation = await self._create()
        self.assertFalse(await self.store.complete(escalation.id))

    async def test_完了すると期限が消える(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        self.assertTrue(await self.store.complete(escalation.id))
        current = await self.store.get(escalation.id)
        self.assertIs(current.status, Status.DONE)  # type: ignore[union-attr]
        self.assertIsNone(current.deadline_at)  # type: ignore[union-attr]

    async def test_二重に完了できない(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        self.assertTrue(await self.store.complete(escalation.id))
        self.assertFalse(await self.store.complete(escalation.id))


class ExpireTest(StoreTestCase):
    """SPEC §5.3「対応中のまま30分経過したら自動で未対応へ戻す」。"""

    async def _set_deadline(self, escalation_id: int, offset: timedelta) -> None:
        await self.database.connection.execute(
            "UPDATE escalations SET deadline_at = ? WHERE id = ?",
            (to_iso(utcnow() + offset), escalation_id),
        )
        await self.database.connection.commit()

    async def test_期限を過ぎたら未対応に戻る(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="Aさん", timeout_minutes=30
        )
        await self._set_deadline(escalation.id, timedelta(minutes=-1))

        expired = await self.store.expire_overdue()
        self.assertEqual(len(expired), 1)
        self.assertIs(expired[0].status, Status.UNHANDLED)
        self.assertIsNone(expired[0].handler_id)
        self.assertIsNone(expired[0].deadline_at)

    async def test_期限前は戻さない(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        self.assertEqual(await self.store.expire_overdue(), [])

    async def test_完了済みは戻さない(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        await self.store.complete(escalation.id)
        await self._set_deadline(escalation.id, timedelta(minutes=-60))
        self.assertEqual(await self.store.expire_overdue(), [])

    async def test_戻した後は別の人が対応できる(self) -> None:
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="Aさん", timeout_minutes=30
        )
        await self._set_deadline(escalation.id, timedelta(minutes=-1))
        await self.store.expire_overdue()

        retaken = await self.store.claim(
            escalation.id, handler_id=2, handler_name="Bさん", timeout_minutes=30
        )
        self.assertTrue(retaken)
        current = await self.store.get(escalation.id)
        self.assertEqual(current.handler_name, "Bさん")  # type: ignore[union-attr]

    async def test_再起動をまたいでも期限が残る(self) -> None:
        # メモリ上のタイマーではなく DB に持つことの検証(CLAUDE.md 制約 3)
        escalation = await self._create()
        await self.store.claim(
            escalation.id, handler_id=1, handler_name="A", timeout_minutes=30
        )
        await self._set_deadline(escalation.id, timedelta(minutes=-1))
        path = self.database._path
        await self.database.close()

        # 別プロセス相当で開き直す
        reopened = Database(path)
        await reopened.connect()
        try:
            store = EscalationStore(reopened.connection)
            expired = await store.expire_overdue()
            self.assertEqual(len(expired), 1, "再起動後に期限が失われている")
        finally:
            await reopened.close()
            self.database = reopened  # tearDown 用


if __name__ == "__main__":
    unittest.main()

"""レート制限のテスト(SPEC §8)。

このBotで最も避けたい失敗は「初心者が質問をためらうこと」。
制限がそれを引き起こしては本末転倒なので、
「普通に使えば絶対に当たらない」ことを固定する。
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.db.database import Database
from src.ratelimit import RateLimiter
from src.usage import UsageStore


class FakeUsage:
    input_tokens = 10
    output_tokens = 20
    cache_read_input_tokens = 0
    cache_creation_input_tokens = 0


class RateLimitTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "t.db")
        await self.database.connect()
        self.usage = UsageStore(self.database.connection)
        self.limiter = RateLimiter(
            self.database.connection, per_team_hourly=40, per_user_seconds=5
        )

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def _log(self, *, team: str = "1班", user: int = 1, ago_seconds: int = 0) -> None:
        await self.database.connection.execute(
            """
            INSERT INTO question_log
                (guild_id, thread_id, team_name, user_id, created_at)
            VALUES (1, 1, ?, ?, ?)
            """,
            (
                team,
                user,
                (datetime.now(UTC) - timedelta(seconds=ago_seconds)).isoformat(),
            ),
        )
        await self.database.connection.commit()

    async def test_初回は必ず通る(self) -> None:
        verdict = await self.limiter.check(team_name="1班", user_id=1)
        self.assertTrue(verdict.allowed)

    async def test_同一ユーザーの連投は止める(self) -> None:
        await self._log(user=1, ago_seconds=1)
        verdict = await self.limiter.check(team_name="1班", user_id=1)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "user")

    async def test_間隔があけば通る(self) -> None:
        await self._log(user=1, ago_seconds=10)
        self.assertTrue((await self.limiter.check(team_name="1班", user_id=1)).allowed)

    async def test_別のユーザーは巻き込まれない(self) -> None:
        # 個人の連投制限が、チームメイトを止めてはいけない
        await self._log(user=1, ago_seconds=1)
        self.assertTrue((await self.limiter.check(team_name="1班", user_id=2)).allowed)

    async def test_チームの毎時上限で止める(self) -> None:
        for i in range(40):
            await self._log(team="1班", user=100 + i, ago_seconds=60)
        verdict = await self.limiter.check(team_name="1班", user_id=999)
        self.assertFalse(verdict.allowed)
        self.assertEqual(verdict.reason, "channel")

    async def test_39件までは通る(self) -> None:
        for i in range(39):
            await self._log(team="1班", user=100 + i, ago_seconds=60)
        self.assertTrue((await self.limiter.check(team_name="1班", user_id=999)).allowed)

    async def test_1時間より前の分は数えない(self) -> None:
        for i in range(50):
            await self._log(team="1班", user=100 + i, ago_seconds=4000)
        self.assertTrue((await self.limiter.check(team_name="1班", user_id=999)).allowed)

    async def test_別チームは巻き込まれない(self) -> None:
        # 22チームが同居するので、他チームの利用で止まってはいけない
        for i in range(40):
            await self._log(team="1班", user=100 + i, ago_seconds=60)
        self.assertTrue((await self.limiter.check(team_name="2班", user_id=999)).allowed)

    async def test_6人が1時間に5問ずつでも当たらない(self) -> None:
        # 想定される「普通の使い方」。ここで止まったら設計が間違っている
        for user in range(6):
            for _ in range(5):
                await self._log(team="1班", user=user, ago_seconds=120)
        self.assertTrue((await self.limiter.check(team_name="1班", user_id=0)).allowed)


class StatsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "t.db")
        await self.database.connect()
        self.usage = UsageStore(self.database.connection)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def test_記録して集計できる(self) -> None:
        for team in ("1班", "1班", "2班"):
            await self.usage.record(
                guild_id=1,
                thread_id=1,
                team_name=team,
                user_id=1,
                usage=FakeUsage(),
                model="claude-opus-5",
            )
        self.assertEqual(await self.usage.total_questions(), 3)
        stats = await self.usage.team_stats()
        self.assertEqual(stats[0].team_name, "1班")
        self.assertEqual(stats[0].questions, 2)

    async def test_コストを概算する(self) -> None:
        await self.usage.record(
            guild_id=1, thread_id=1, team_name="1班", user_id=1,
            usage=FakeUsage(), model="claude-opus-5",
        )
        self.assertGreater(await self.usage.total_cost(), 0)

    async def test_未知のモデルでも落ちない(self) -> None:
        await self.usage.record(
            guild_id=1, thread_id=1, team_name="1班", user_id=1,
            usage=FakeUsage(), model="unknown-model",
        )
        self.assertEqual(await self.usage.total_cost(), 0.0)


class UsageAlertTest(unittest.IsolatedAsyncioTestCase):
    """SPEC §8.4 の使用量アラート。

    同じアラートが何度も出ると無視されるようになるので、
    一度出した閾値は再起動をまたいで覚えている必要がある。
    """

    async def asyncSetUp(self) -> None:
        from src.usage import UsageAlerter

        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "t.db"
        self.database = Database(self.path)
        await self.database.connect()
        self.alerter = UsageAlerter(self.database.connection, [10.0, 25.0, 50.0])

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def test_閾値未満では出さない(self) -> None:
        self.assertIsNone(await self.alerter.check(5.0))

    async def test_超えたら出す(self) -> None:
        self.assertEqual(await self.alerter.check(12.0), 10.0)

    async def test_同じ閾値では二度出さない(self) -> None:
        await self.alerter.check(12.0)
        self.assertIsNone(await self.alerter.check(13.0))

    async def test_次の閾値では出す(self) -> None:
        await self.alerter.check(12.0)
        self.assertEqual(await self.alerter.check(26.0), 25.0)

    async def test_一気に飛び越えたら最大の閾値を出す(self) -> None:
        self.assertEqual(await self.alerter.check(60.0), 50.0)

    async def test_再起動しても覚えている(self) -> None:
        from src.usage import UsageAlerter

        await self.alerter.check(12.0)
        await self.database.close()
        self.database = Database(self.path)
        await self.database.connect()
        alerter = UsageAlerter(self.database.connection, [10.0, 25.0, 50.0])
        self.assertIsNone(await alerter.check(13.0))

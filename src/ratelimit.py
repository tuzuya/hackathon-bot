"""レート制限(SPEC §8)。

**悪意・事故の防止が目的であって、節約が目的ではない。**
最も避けたい失敗は「初心者が質問をためらうこと」なので、
上限は「普通に使えば絶対に当たらない」水準に置く。

- チーム(投稿の属するフォーラム)単位ではなく、**投稿単位ではなくチーム単位**で数える
  → 個人単位だと「自分の割当を使い切った」という意識が生まれ、遠慮につながる
- 同一ユーザーの連投だけ、数秒の間隔で止める(誤操作・連打の事故防止)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite


@dataclass(frozen=True)
class RateLimitVerdict:
    allowed: bool
    # 'channel' | 'user' | None
    reason: str | None = None


class RateLimiter:
    """question_log を使ってカウントする。

    カウンタ専用のテーブルを別に持たないのは、二重に書く手間と
    ずれるリスクを避けるため。ログがそのままカウンタになる。
    """

    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        per_team_hourly: int,
        per_user_seconds: int,
    ) -> None:
        self._db = connection
        self._per_team_hourly = per_team_hourly
        self._per_user_seconds = per_user_seconds

    async def check(self, *, team_name: str, user_id: int) -> RateLimitVerdict:
        now = datetime.now(UTC)

        # 同一ユーザーの連投(誤操作・連打の防止)
        since_user = (now - timedelta(seconds=self._per_user_seconds)).isoformat()
        async with self._db.execute(
            "SELECT 1 FROM question_log WHERE user_id = ? AND created_at > ? LIMIT 1",
            (user_id, since_user),
        ) as cursor:
            if await cursor.fetchone() is not None:
                return RateLimitVerdict(False, "user")

        # チーム単位の毎時上限
        since_team = (now - timedelta(hours=1)).isoformat()
        async with self._db.execute(
            "SELECT COUNT(*) AS count FROM question_log "
            "WHERE team_name = ? AND created_at > ?",
            (team_name, since_team),
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None and row["count"] >= self._per_team_hourly:
            return RateLimitVerdict(False, "channel")

        return RateLimitVerdict(True)

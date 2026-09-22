"""質問ログと使用量の記録(SPEC §7.2 / §8.4)。

レート制限のカウンタ、/stats の集計、コストの監視を 1 つのテーブルで賄う。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

# 1M トークンあたりの USD(入力, 出力)。表示・アラート用の概算で、厳密な請求額ではない。
#
# DeepSeek は時間帯で価格が変わる(ピークはオフピークの2倍)。
# アラートを早めに出したいので **ピーク価格**(高い方)で見積もる。
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "deepseek-flash": (0.30, 1.20),
    "deepseek-v4-pro": (1.32, 3.96),
}


def estimate_cost(model: str, usage: Any) -> float:
    price = PRICES.get(model)
    if price is None or usage is None:
        return 0.0
    in_price, out_price = price
    return (
        (getattr(usage, "input_tokens", 0) or 0) * in_price
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * in_price * 1.25
        + (getattr(usage, "cache_read_input_tokens", 0) or 0) * in_price * 0.1
        + (getattr(usage, "output_tokens", 0) or 0) * out_price
    ) / 1_000_000


@dataclass(frozen=True)
class TeamStat:
    team_name: str
    questions: int
    escalations: int
    cost: float


class UsageStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def record(
        self,
        *,
        guild_id: int,
        thread_id: int,
        team_name: str,
        user_id: int,
        usage: Any,
        model: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO question_log (
                guild_id, thread_id, team_name, user_id, created_at,
                input_tokens, output_tokens, cache_read, cache_write, estimated_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                guild_id,
                thread_id,
                team_name,
                user_id,
                datetime.now(UTC).isoformat(),
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
                estimate_cost(model, usage),
            ),
        )
        await self._db.commit()

    async def total_cost(self) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) AS total FROM question_log"
        ) as cursor:
            row = await cursor.fetchone()
        return float(row["total"]) if row else 0.0

    async def total_questions(self) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) AS count FROM question_log"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row else 0

    async def team_stats(self, limit: int = 25) -> list[TeamStat]:
        """チーム別の質問数・エスカレーション数・概算コスト。

        どのチームが詰まっているかが見えると、メンター投入の判断材料になる
        (SPEC §7.2)。
        """
        async with self._db.execute(
            """
            SELECT
                q.team_name                       AS team_name,
                COUNT(*)                          AS questions,
                COALESCE(SUM(q.estimated_cost),0) AS cost,
                (
                    SELECT COUNT(*) FROM escalations e
                     WHERE e.team_name = q.team_name
                )                                 AS escalations
              FROM question_log q
             GROUP BY q.team_name
             ORDER BY questions DESC
             LIMIT ?
            """,
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            TeamStat(
                team_name=row["team_name"],
                questions=row["questions"],
                escalations=row["escalations"],
                cost=float(row["cost"]),
            )
            for row in rows
        ]


ALERTED_THRESHOLD_KEY = "usage_alert_threshold"


class UsageAlerter:
    """累積使用量が閾値を超えたら運営チャンネルへ知らせる(SPEC §8.4)。

    これがあるから、参加者側のレート制限は緩くしていられる。
    どの閾値まで通知したかは DB に持つ。メモリだと再起動のたびに
    同じアラートが何度も出て、やがて無視されるようになる。
    """

    def __init__(self, connection: aiosqlite.Connection, thresholds: list[float]) -> None:
        self._db = connection
        self._thresholds = sorted(thresholds)

    async def _last_alerted(self) -> float:
        async with self._db.execute(
            "SELECT value FROM bot_state WHERE key = ?", (ALERTED_THRESHOLD_KEY,)
        ) as cursor:
            row = await cursor.fetchone()
        return float(row["value"]) if row else 0.0

    async def _remember(self, threshold: float) -> None:
        await self._db.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (ALERTED_THRESHOLD_KEY, str(threshold)),
        )
        await self._db.commit()

    async def check(self, total_cost: float) -> float | None:
        """超えた閾値を返す。まだなら None。"""
        last = await self._last_alerted()
        crossed = [t for t in self._thresholds if last < t <= total_cost]
        if not crossed:
            return None
        highest = crossed[-1]
        await self._remember(highest)
        return highest

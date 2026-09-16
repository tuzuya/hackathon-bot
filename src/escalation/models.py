"""エスカレーションのデータモデル。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

import aiosqlite


class Status(StrEnum):
    """SPEC §5.3 の状態遷移。

    未対応 --[対応する]--> 対応中 --[完了]--> 完了
             ^                  |
             +--[30分経過]------+
    """

    UNHANDLED = "unhandled"
    IN_PROGRESS = "in_progress"
    DONE = "done"


# 参加者・メンターに見せる表示名
STATUS_LABELS: dict[Status, str] = {
    Status.UNHANDLED: "🔴 未対応",
    Status.IN_PROGRESS: "🟡 対応中",
    Status.DONE: "✅ 完了",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class Escalation:
    id: int
    guild_id: int
    thread_id: int
    team_name: str
    requester_id: int
    summary: str
    summary_failed: bool
    queue_message_id: int | None
    status: Status
    handler_id: int | None
    handler_name: str | None
    created_at: datetime
    claimed_at: datetime | None
    deadline_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Escalation:
        created = from_iso(row["created_at"])
        assert created is not None
        return cls(
            id=row["id"],
            guild_id=row["guild_id"],
            thread_id=row["thread_id"],
            team_name=row["team_name"],
            requester_id=row["requester_id"],
            summary=row["summary"],
            summary_failed=bool(row["summary_failed"]),
            queue_message_id=row["queue_message_id"],
            status=Status(row["status"]),
            handler_id=row["handler_id"],
            handler_name=row["handler_name"],
            created_at=created,
            claimed_at=from_iso(row["claimed_at"]),
            deadline_at=from_iso(row["deadline_at"]),
            completed_at=from_iso(row["completed_at"]),
        )

    @property
    def is_open(self) -> bool:
        """まだ対応が終わっていないか。"""
        return self.status in (Status.UNHANDLED, Status.IN_PROGRESS)

"""動的に追加された知識の永続化(SPEC §7.1 / §4.2)。

運営がハッカソン中に追加した情報と、承認済み Q&A をここに持つ。
`knowledge/` 配下のファイルと結合されてシステムプロンプトに入る。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

# /add-knowledge で選べるカテゴリ(SPEC §4.5)
CATEGORIES = (
    "評価軸",
    "スケジュール",
    "レギュレーション",
    "環境構築",
    "運営情報",
)

SOURCE_MANUAL = "manual"
SOURCE_QA = "qa"


@dataclass(frozen=True)
class KnowledgeEntry:
    id: int
    category: str
    content: str
    source: str
    added_by_name: str
    created_at: datetime

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> KnowledgeEntry:
        return cls(
            id=row["id"],
            category=row["category"],
            content=row["content"],
            source=row["source"],
            added_by_name=row["added_by_name"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class KnowledgeStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._db = connection

    async def add(
        self,
        *,
        category: str,
        content: str,
        source: str,
        added_by: int,
        added_by_name: str,
    ) -> int:
        cursor = await self._db.execute(
            """
            INSERT INTO knowledge_entries
                (category, content, source, added_by, added_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                category,
                content.strip(),
                source,
                added_by,
                added_by_name,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def deactivate(self, entry_id: int) -> bool:
        """削除ではなく無効化する。何を消したかを後から追えるようにするため。"""
        cursor = await self._db.execute(
            "UPDATE knowledge_entries SET active = 0 WHERE id = ? AND active = 1",
            (entry_id,),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def list_active(self) -> list[KnowledgeEntry]:
        async with self._db.execute(
            """
            SELECT * FROM knowledge_entries
             WHERE active = 1
             ORDER BY category, id
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [KnowledgeEntry.from_row(row) for row in rows]

    async def render(self) -> str:
        """システムプロンプトに差し込む形に整形する。

        新しく追加されたものほど後ろに置く。同じ話題が重複した場合、
        後ろにあるほうが新しい情報だと分かるようにするため(SPEC §4.5)。
        """
        entries = await self.list_active()
        if not entries:
            return ""

        sections = ["# ハッカソン中に追加された情報", ""]
        sections.append(
            "以下は開催中に運営が追加した情報です。"
            "**同じ話題について `knowledge/` 側の記述と食い違う場合は、必ずこちらを優先してください。**"
        )
        sections.append("")
        for entry in entries:
            label = "承認済み Q&A" if entry.source == SOURCE_QA else "運営からの追加"
            date = entry.created_at.astimezone(UTC).strftime("%Y-%m-%d")
            sections.append(f"## {entry.category}({label} / 追加 {date})")
            sections.append("")
            sections.append(entry.content)
            sections.append("")
        return "\n".join(sections)

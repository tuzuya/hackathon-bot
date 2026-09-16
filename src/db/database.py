"""SQLite への接続。

discord.py は非同期なので aiosqlite を使う。
同期版 sqlite3 を使うと、DB アクセス中に Bot 全体の応答が止まる。
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

from src.db.schema import SCHEMA

logger = logging.getLogger(__name__)


class Database:
    """プロセス内で 1 本の接続を使い回す。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database.connect() が呼ばれていません。")
        return self._connection

    async def connect(self) -> None:
        # Railway では Volume のマウントポイント配下を指すこと。
        # マウント外に置くと再デプロイのたびに消える。
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self._path)
        self._connection.row_factory = aiosqlite.Row
        # 書き込み中の読み取りをブロックしないようにする
        await self._connection.execute("PRAGMA journal_mode=WAL")
        await self._connection.execute("PRAGMA foreign_keys=ON")
        await self._connection.executescript(SCHEMA)
        await self._connection.commit()
        logger.info("データベースに接続しました: %s", self._path)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

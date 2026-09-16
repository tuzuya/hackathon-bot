"""会話履歴の組み立てのテスト。

チーム間の情報遮断(CLAUDE.md 制約 4)の要。
履歴が単一スレッドに閉じていること、投稿の1件目が必ず含まれることを固定する。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from src.llm.conversation import build_messages

BOT_ID = 42
THREAD_ID = 555


def _msg(author_id: int, content: str, message_id: int = 0) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.content = content
    message.attachments = []
    message.author = MagicMock()
    message.author.id = author_id
    return message


def _thread(history: list[MagicMock], *, name: str = "npm install が失敗する") -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = THREAD_ID
    thread.name = name
    thread.starter_message = None

    async def _history(limit: int, oldest_first: bool):  # noqa: ARG001
        # Discord API は新しい順に返す
        for message in reversed(history[-limit:]):
            yield message

    thread.history = _history
    thread.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(), "not found"))
    return thread


class BuildMessagesTest(unittest.IsolatedAsyncioTestCase):
    async def test_投稿タイトルが先頭に入る(self) -> None:
        thread = _thread([_msg(1, "<@42> 助けて", message_id=THREAD_ID)])
        result = await build_messages(thread, BOT_ID, limit=20)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("npm install が失敗する", result[0]["content"])

    async def test_botの発言はassistantになる(self) -> None:
        thread = _thread(
            [
                _msg(1, "<@42> 助けて", message_id=THREAD_ID),
                _msg(BOT_ID, "どの OS を使ってる?"),
                _msg(1, "<@42> Mac だよ"),
            ]
        )
        result = await build_messages(thread, BOT_ID, limit=20)
        roles = [m["role"] for m in result]
        self.assertEqual(roles, ["user", "user", "assistant", "user"])

    async def test_botへのメンションは本文から除かれる(self) -> None:
        thread = _thread([_msg(1, "<@42> 助けて", message_id=THREAD_ID)])
        result = await build_messages(thread, BOT_ID, limit=20)
        self.assertNotIn("<@42>", result[-1]["content"])
        self.assertEqual(result[-1]["content"], "助けて")

    async def test_件数上限を超えた履歴は直近だけ渡る(self) -> None:
        history = [_msg(1, f"<@42> {i}件目", message_id=THREAD_ID if i == 0 else i + 1)
                   for i in range(50)]
        thread = _thread(history)
        result = await build_messages(thread, BOT_ID, limit=5)
        # タイトル + 直近5件
        self.assertEqual(len(result), 6)
        self.assertIn("49件目", result[-1]["content"])

    async def test_先頭がassistantにならない(self) -> None:
        # API は user から始まる必要がある
        thread = _thread([_msg(BOT_ID, "先に Bot が喋った")], name="")
        result = await build_messages(thread, BOT_ID, limit=20)
        if result:
            self.assertEqual(result[0]["role"], "user")

    async def test_添付のみのメッセージも空にならない(self) -> None:
        message = _msg(1, "<@42>", message_id=THREAD_ID)
        attachment = MagicMock()
        attachment.filename = "error.png"
        message.attachments = [attachment]
        thread = _thread([message])
        result = await build_messages(thread, BOT_ID, limit=20)
        self.assertIn("error.png", result[-1]["content"])
        for entry in result:
            self.assertTrue(entry["content"].strip(), "空の content は API に拒否される")


if __name__ == "__main__":
    unittest.main()


class MessageOrderTest(unittest.IsolatedAsyncioTestCase):
    """Claude API は messages が user で始まり user で終わることを要求する。

    ここが崩れると 400 (assistant message prefill) で回答できなくなる。
    実際に、本文なしで @bot だけ送られたときに発生した。
    """

    async def test_本文なしのメンションでも末尾がuserになる(self) -> None:
        # 実際に起きたケース: Bot が回答した直後に、本文なしで @bot だけ送られた
        thread = _thread(
            [
                _msg(1, "<@42> git が使えない", message_id=THREAD_ID),
                _msg(BOT_ID, "どの OS を使ってる?"),
                _msg(1, "<@42>"),  # 本文なし。履歴から落ちる
            ]
        )
        result = await build_messages(thread, BOT_ID, limit=20)
        self.assertEqual(result[-1]["role"], "user")
        self.assertEqual(result[0]["role"], "user")

    async def test_botの回答は文脈として残る(self) -> None:
        thread = _thread(
            [
                _msg(1, "<@42> git が使えない", message_id=THREAD_ID),
                _msg(BOT_ID, "どの OS を使ってる?"),
                _msg(1, "<@42>"),
            ]
        )
        result = await build_messages(thread, BOT_ID, limit=20)
        # 末尾を user にするために Bot の回答を削ってはいけない
        self.assertIn("どの OS を使ってる?", [m["content"] for m in result])

    async def test_通常の会話では余計なターンが増えない(self) -> None:
        thread = _thread(
            [
                _msg(1, "<@42> git が使えない", message_id=THREAD_ID),
                _msg(BOT_ID, "どの OS?"),
                _msg(1, "<@42> mac です"),
            ]
        )
        result = await build_messages(thread, BOT_ID, limit=20)
        self.assertEqual(result[-1]["content"], "mac です")

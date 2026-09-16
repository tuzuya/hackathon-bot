"""要約生成のテスト。

SPEC §5.4「要約生成に失敗したら、生の質疑をそのまま送るフォールバックを入れる」。
エスカレーションは緊急度が高く、要約が作れないせいで通知が届かないのは本末転倒。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from src.llm.client import LLMError
from src.escalation.summarizer import summarize

HISTORY = [
    {"role": "user", "content": "【この投稿のタイトル】npmが動かない"},
    {"role": "user", "content": "npm install でエラーが出る。Macです"},
    {"role": "assistant", "content": "まず node -v を実行してみて!"},
    {"role": "user", "content": "command not found って出る"},
]


class SummarizeTest(unittest.IsolatedAsyncioTestCase):
    async def test_成功したら要約を返す(self) -> None:
        llm = MagicMock()
        llm.summarize = AsyncMock(return_value="1. node が入っていない\n2. node -v を案内")
        text, failed = await summarize(llm, HISTORY)
        self.assertFalse(failed)
        self.assertIn("node", text)

    async def test_LLMが失敗したら生の質疑を返す(self) -> None:
        llm = MagicMock()
        llm.summarize = AsyncMock(side_effect=LLMError("API ダウン"))
        text, failed = await summarize(llm, HISTORY)
        self.assertTrue(failed)
        self.assertIn("command not found", text)

    async def test_想定外の例外でも生の質疑を返す(self) -> None:
        llm = MagicMock()
        llm.summarize = AsyncMock(side_effect=RuntimeError("想定外"))
        text, failed = await summarize(llm, HISTORY)
        self.assertTrue(failed)
        self.assertIn("command not found", text)

    async def test_LLMが無くても生の質疑を返す(self) -> None:
        text, failed = await summarize(None, HISTORY)
        self.assertTrue(failed)
        self.assertIn("npm install", text)

    async def test_空の応答は失敗扱いにする(self) -> None:
        llm = MagicMock()
        llm.summarize = AsyncMock(return_value="   ")
        text, failed = await summarize(llm, HISTORY)
        self.assertTrue(failed)
        self.assertIn("npm install", text)

    async def test_要約に混ざったロールメンションを無害化する(self) -> None:
        # 要約は embed に入るので通知は飛ばないが、多重防御として確認する
        llm = MagicMock()
        llm.summarize = AsyncMock(return_value="<@&999> に聞きたいとのこと")
        text, failed = await summarize(llm, HISTORY)
        self.assertFalse(failed)
        self.assertNotIn("<@&999>", text)

    async def test_生の質疑でも発言者が分かる(self) -> None:
        text, _ = await summarize(None, HISTORY)
        self.assertIn("参加者:", text)
        self.assertIn("Bot:", text)

    async def test_履歴が空でも落ちない(self) -> None:
        text, failed = await summarize(None, [])
        self.assertTrue(failed)
        self.assertTrue(text)

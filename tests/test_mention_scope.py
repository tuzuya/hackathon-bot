"""動作範囲の限定(SPEC §3.3 / CLAUDE.md 制約 5)の回帰テスト。

相談室フォーラム内の投稿以外で LLM を呼ばないことは、
コスト事故とチーム間の情報漏れの両方に直結するため、ここだけはテストで固定する。

実行: python -m unittest discover -s tests
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import discord

from src.cogs.mention import MentionCog

FORUM_A = 1111
FORUM_B = 2222
OTHER_FORUM = 9999


def _make_cog() -> MentionCog:
    config = MagicMock()
    config.forum_channel_ids = frozenset({FORUM_A, FORUM_B})
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = 42
    return MentionCog(bot, config, llm=None)


def _thread(parent_id: int) -> MagicMock:
    channel = MagicMock(spec=discord.Thread)
    channel.parent_id = parent_id
    channel.id = 7777
    return channel


def _forum(channel_id: int) -> MagicMock:
    channel = MagicMock(spec=discord.ForumChannel)
    channel.id = channel_id
    return channel


def _text_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 5555
    return channel


def _message(cog: MentionCog, *, mentions_bot: bool, everyone: bool = False) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.mention_everyone = everyone
    mentioned = MagicMock()
    mentioned.id = cog.bot.user.id if mentions_bot else 999
    message.mentions = [mentioned]
    return message


class ScopeDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    def test_対象フォーラム内の投稿は担当範囲(self) -> None:
        self.assertTrue(self.cog._is_consultation_thread(_thread(FORUM_A)))
        self.assertTrue(self.cog._is_consultation_thread(_thread(FORUM_B)))

    def test_対象外フォーラムの投稿は範囲外(self) -> None:
        self.assertFalse(self.cog._is_consultation_thread(_thread(OTHER_FORUM)))

    def test_通常のテキストチャンネルは範囲外(self) -> None:
        self.assertFalse(self.cog._is_consultation_thread(_text_channel()))
        self.assertFalse(self.cog._is_consultation_forum_root(_text_channel()))

    def test_フォーラム本体は投稿ではないので質問扱いしない(self) -> None:
        forum = _forum(FORUM_A)
        self.assertFalse(self.cog._is_consultation_thread(forum))
        self.assertTrue(self.cog._is_consultation_forum_root(forum))


class MentionDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cog = _make_cog()

    def test_bot本人へのメンションを検出する(self) -> None:
        self.assertTrue(self.cog._mentions_bot(_message(self.cog, mentions_bot=True)))

    def test_他人へのメンションには反応しない(self) -> None:
        self.assertFalse(self.cog._mentions_bot(_message(self.cog, mentions_bot=False)))

    def test_everyoneでは起動しない(self) -> None:
        message = _message(self.cog, mentions_bot=True, everyone=True)
        self.assertFalse(self.cog._mentions_bot(message))


if __name__ == "__main__":
    unittest.main()


class ReactionFailureTest(unittest.IsolatedAsyncioTestCase):
    """受付リアクションの失敗が、回答を止めないこと。

    typing 表示のレート制限(429)で回答そのものが届かなくなる事故が実際に起きた。
    合図は装飾であって本質ではないので、失敗しても先へ進まなければならない。
    """

    async def test_リアクション付与に失敗しても例外を投げない(self) -> None:
        from unittest.mock import AsyncMock

        cog = _make_cog()
        message = MagicMock(spec=discord.Message)
        message.add_reaction = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=429), "rate limited")
        )
        await cog._add_reaction(message)  # 例外が漏れたらテスト失敗

    async def test_リアクション削除に失敗しても例外を投げない(self) -> None:
        from unittest.mock import AsyncMock

        cog = _make_cog()
        message = MagicMock(spec=discord.Message)
        message.id = 1
        message.remove_reaction = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "not found")
        )
        await cog._remove_reaction(message)

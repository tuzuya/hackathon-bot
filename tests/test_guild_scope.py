"""動作対象サーバーの絞り込みのテスト。

テスト用と本番で同じ Bot を使う場合、GUILD_ID を設定しないと
テストサーバーの質問にも応答し、エスカレーションが本番のキューへ飛ぶ。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import discord

from src.config import Config

PROD_ID = 1000
TEST_ID = 2000


def _guild(guild_id: int, name: str) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.name = name
    return guild


def _bot(guild_id: int | None):
    import os

    os.environ.update({"DISCORD_TOKEN": "x", "FORUM_CHANNEL_NAME": "エンジニア相談室"})
    os.environ.pop("GUILD_ID", None)
    config = Config.load()
    object.__setattr__(config, "guild_id", guild_id)

    from src.bot import HackathonBot

    with patch("discord.ext.commands.Bot.__init__", return_value=None):
        bot = HackathonBot.__new__(HackathonBot)
    bot.config = config

    prod, test = _guild(PROD_ID, "本番"), _guild(TEST_ID, "テスト")
    with patch.object(
        type(bot), "guilds", property(lambda self: [prod, test])
    ):
        bot.get_guild = lambda gid: {PROD_ID: prod, TEST_ID: test}.get(gid)
        yield_bot = bot
        return yield_bot, prod, test


class TargetGuildTest(unittest.TestCase):
    def test_GUILD_ID_を設定すると1つに絞られる(self) -> None:
        bot, prod, test = _bot(PROD_ID)
        with patch.object(type(bot), "guilds", property(lambda self: [prod, test])):
            self.assertEqual([g.id for g in bot.target_guilds], [PROD_ID])

    def test_未設定なら全サーバーが対象(self) -> None:
        bot, prod, test = _bot(None)
        with patch.object(type(bot), "guilds", property(lambda self: [prod, test])):
            self.assertEqual(len(bot.target_guilds), 2)

    def test_対象サーバーの判定(self) -> None:
        bot, prod, test = _bot(PROD_ID)
        self.assertTrue(bot.is_target_guild(prod))
        self.assertFalse(bot.is_target_guild(test))

    def test_DMは対象外(self) -> None:
        bot, _, _ = _bot(PROD_ID)
        self.assertFalse(bot.is_target_guild(None))

    def test_未設定ならどのサーバーも対象(self) -> None:
        bot, prod, test = _bot(None)
        self.assertTrue(bot.is_target_guild(prod))
        self.assertTrue(bot.is_target_guild(test))

    def test_存在しないIDなら対象が空になる(self) -> None:
        bot, prod, test = _bot(9999)
        with patch.object(type(bot), "guilds", property(lambda self: [prod, test])):
            self.assertEqual(bot.target_guilds, [])


if __name__ == "__main__":
    unittest.main()

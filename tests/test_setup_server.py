"""本番サーバー構築スクリプトのテスト。

既存のカテゴリがある本番サーバーに対して実行するため、
**既存の設定を壊さない**ことが最重要。
"""

from __future__ import annotations

import argparse
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.config import Config
from src.setup_server import ENGINEER_MENTOR_ROLE, MENTOR_CATEGORY, Builder


def _category(name: str, *, bot_can_view: bool = True, mentor_can_view: bool = True):
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = name
    category.id = abs(hash(name)) % 100000
    category.set_permissions = AsyncMock()

    def permissions_for(target):
        perms = MagicMock()
        is_bot = getattr(target, "_is_bot", False)
        perms.view_channel = bot_can_view if is_bot else mentor_can_view
        return perms

    category.permissions_for = permissions_for
    return category


def _guild(categories):
    guild = MagicMock(spec=discord.Guild)
    guild.categories = categories
    guild.name = "本番"
    me = MagicMock()
    me._is_bot = True
    guild.me = me
    for c in categories:
        c.guild = guild
    return guild


def _builder(**arg_overrides) -> Builder:
    import os

    os.environ.update({"DISCORD_TOKEN": "x", "FORUM_CHANNEL_NAME": "エンジニア相談室"})
    args = argparse.Namespace(
        apply=False, from_categories=True, exclude=[], count=22,
        format="{n}班", teams_file=None, guild=None, private_teams=False,
    )
    for key, value in arg_overrides.items():
        setattr(args, key, value)
    with patch("discord.Client.__init__", return_value=None):
        builder = Builder.__new__(Builder)
    builder.config = Config.load()
    builder.args = args
    from src.setup_server import Plan

    builder.plan = Plan()
    builder.results = {}
    return builder


class ResolveCategoriesTest(unittest.TestCase):
    def test_既存カテゴリをそのまま使う(self) -> None:
        guild = _guild([_category("1班"), _category("2班")])
        builder = _builder()
        resolved = builder._resolve_team_categories(guild)
        self.assertEqual([c.name for c in resolved], ["1班", "2班"])

    def test_メンター用カテゴリは対象外(self) -> None:
        guild = _guild([_category("1班"), _category(MENTOR_CATEGORY)])
        builder = _builder()
        resolved = builder._resolve_team_categories(guild)
        self.assertEqual([c.name for c in resolved], ["1班"])

    def test_除外指定したカテゴリを飛ばす(self) -> None:
        guild = _guild([_category("1班"), _category("全体連絡"), _category("運営")])
        builder = _builder(exclude=["全体連絡", "運営"])
        resolved = builder._resolve_team_categories(guild)
        self.assertEqual([c.name for c in resolved], ["1班"])

    def test_名前指定なら既存カテゴリを探して使う(self) -> None:
        existing = _category("1班")
        guild = _guild([existing])
        builder = _builder(from_categories=False, count=2)
        resolved = builder._resolve_team_categories(guild)
        # 1班 は既存のオブジェクト、2班 は未作成なので文字列
        self.assertIs(resolved[0], existing)
        self.assertEqual(resolved[1], "2班")

    def test_表記ゆれがあると既存を見つけられない(self) -> None:
        # この危険があるため --from-categories を推奨している
        guild = _guild([_category("1班 ")])  # 末尾に空白
        builder = _builder(from_categories=False, count=1)
        resolved = builder._resolve_team_categories(guild)
        self.assertEqual(resolved[0], "1班")  # 文字列 = 新規作成扱いになる


class GrantAccessTest(unittest.IsolatedAsyncioTestCase):
    """Bot とエンジニアメンターの閲覧権限。

    カテゴリが @everyone を閉じていると Bot も締め出され、
    質問に気づけず、エスカレーション通知も無言で失敗する。
    メンターも見えなければ、呼ばれても駆けつけられない。

    ⚠️ ここで付けるのは閲覧・投稿の権限であって、通知先とは無関係。
       Bot の通知先は allowed_mentions でロールIDを明示指定している
       (test_mentions.py を参照)。
    """

    def _role(self) -> MagicMock:
        role = MagicMock(spec=discord.Role)
        role.name = ENGINEER_MENTOR_ROLE
        role._is_bot = False
        return role

    async def test_権限が足りていれば何もしない(self) -> None:
        category = _category("1班", bot_can_view=True, mentor_can_view=True)
        _guild([category])
        builder = _builder(apply=True)
        await builder._grant_access(category, self._role())
        self.assertEqual(builder.plan.granted, [])
        category.set_permissions.assert_not_awaited()

    async def test_Botが見えないなら権限を足す(self) -> None:
        category = _category("1班", bot_can_view=False, mentor_can_view=True)
        _guild([category])
        builder = _builder(apply=True)
        await builder._grant_access(category, self._role())
        self.assertEqual(len(builder.plan.granted), 1)
        category.set_permissions.assert_awaited_once()

    async def test_メンターが見えないなら権限を足す(self) -> None:
        category = _category("1班", bot_can_view=True, mentor_can_view=False)
        _guild([category])
        builder = _builder(apply=True)
        await builder._grant_access(category, self._role())
        self.assertEqual(len(builder.plan.granted), 1)
        self.assertIn(ENGINEER_MENTOR_ROLE, builder.plan.granted[0])

    async def test_dry_runでは実際に変更しない(self) -> None:
        category = _category("1班", bot_can_view=False, mentor_can_view=False)
        _guild([category])
        builder = _builder(apply=False)
        await builder._grant_access(category, self._role())
        self.assertEqual(len(builder.plan.granted), 2)
        category.set_permissions.assert_not_awaited()


class PermissionPrecheckTest(unittest.TestCase):
    """構築に必要な権限を、何かを作る前に確かめること。

    22 チーム分の途中で 403 になると、どこまで進んだか分からなくなる。
    """

    def _guild_with(self, *, manage_channels: bool, manage_roles: bool):
        guild = MagicMock(spec=discord.Guild)
        me = MagicMock()
        me.guild_permissions.manage_channels = manage_channels
        me.guild_permissions.manage_roles = manage_roles
        guild.me = me
        return guild

    def test_権限が揃っていれば通る(self) -> None:
        builder = _builder()
        guild = self._guild_with(manage_channels=True, manage_roles=True)
        self.assertTrue(builder._check_permissions(guild))

    def test_チャンネル管理がなければ止まる(self) -> None:
        builder = _builder()
        guild = self._guild_with(manage_channels=False, manage_roles=True)
        self.assertFalse(builder._check_permissions(guild))

    def test_ロール管理がなければ止まる(self) -> None:
        builder = _builder()
        guild = self._guild_with(manage_channels=True, manage_roles=False)
        self.assertFalse(builder._check_permissions(guild))

    def test_メンバー情報が取れなければ止まる(self) -> None:
        builder = _builder()
        guild = MagicMock(spec=discord.Guild)
        guild.me = None
        self.assertFalse(builder._check_permissions(guild))

"""動作範囲判定(ForumScope)のテスト。

22 チーム分のフォーラム ID を手で並べるのは現実的でないため、既定は
チャンネル名での判定になっている。ここが緩むと関係ない場所で LLM が呼ばれる。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import discord

from src.scope import UNKNOWN_TEAM, ForumScope, team_name

FORUM_NAME = "エンジニア相談室"


def _forum(name: str, channel_id: int = 100, category: str | None = None) -> MagicMock:
    forum = MagicMock(spec=discord.ForumChannel)
    forum.name = name
    forum.id = channel_id
    if category is None:
        forum.category = None
    else:
        forum.category = MagicMock()
        forum.category.name = category
    return forum


def _thread(parent: MagicMock | None, parent_id: int = 100) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 777
    thread.parent = parent
    thread.parent_id = parent_id
    return thread


class NameBasedScopeTest(unittest.TestCase):
    """FORUM_CHANNEL_IDS を指定しない場合(22 チーム運用の既定)。"""

    def setUp(self) -> None:
        self.scope = ForumScope(channel_ids=frozenset(), channel_name=FORUM_NAME)

    def test_名前が一致するフォーラムは対象(self) -> None:
        self.assertTrue(self.scope.matches_forum(_forum(FORUM_NAME)))

    def test_名前が違うフォーラムは対象外(self) -> None:
        self.assertFalse(self.scope.matches_forum(_forum("デザイン相談室")))

    def test_対象フォーラム内の投稿は対象(self) -> None:
        self.assertTrue(self.scope.matches_thread(_thread(_forum(FORUM_NAME))))

    def test_対象外フォーラム内の投稿は対象外(self) -> None:
        self.assertFalse(self.scope.matches_thread(_thread(_forum("デザイン相談室"))))

    def test_テキストチャンネルは対象外(self) -> None:
        text = MagicMock(spec=discord.TextChannel)
        text.name = FORUM_NAME  # 名前が同じでもフォーラムでなければ対象外
        self.assertFalse(self.scope.matches_forum(text))
        self.assertFalse(self.scope.matches_thread(text))

    def test_親が取得できない投稿は対象外にする(self) -> None:
        # 判定できないときに通してしまうと、範囲外で LLM が呼ばれる
        self.assertFalse(self.scope.matches_thread(_thread(None)))

    def test_22チーム分を列挙できる(self) -> None:
        guild = MagicMock(spec=discord.Guild)
        guild.channels = [
            _forum(FORUM_NAME, channel_id=i, category=f"チーム{i:02d}") for i in range(1, 23)
        ] + [_forum("デザイン相談室", channel_id=99), MagicMock(spec=discord.TextChannel)]
        found = self.scope.resolve_forums([guild])
        self.assertEqual(len(found), 22)


class IdBasedScopeTest(unittest.TestCase):
    """FORUM_CHANNEL_IDS を指定した場合は、そちらが優先される。"""

    def setUp(self) -> None:
        self.scope = ForumScope(channel_ids=frozenset({100}), channel_name=FORUM_NAME)

    def test_指定した_ID_のフォーラムだけが対象(self) -> None:
        self.assertTrue(self.scope.matches_forum(_forum("名前は無関係", channel_id=100)))
        self.assertFalse(self.scope.matches_forum(_forum(FORUM_NAME, channel_id=200)))

    def test_ID_指定なら親を取得できなくても判定できる(self) -> None:
        self.assertTrue(self.scope.matches_thread(_thread(None, parent_id=100)))
        self.assertFalse(self.scope.matches_thread(_thread(None, parent_id=200)))


class TeamNameTest(unittest.TestCase):
    def test_カテゴリ名をチーム名として使う(self) -> None:
        thread = _thread(_forum(FORUM_NAME, category="チームA"))
        self.assertEqual(team_name(thread), "チームA")

    def test_カテゴリが無ければフォーラム名を使う(self) -> None:
        thread = _thread(_forum(FORUM_NAME))
        self.assertEqual(team_name(thread), FORUM_NAME)

    def test_親が取得できなければ不明扱い(self) -> None:
        self.assertEqual(team_name(_thread(None)), UNKNOWN_TEAM)


if __name__ == "__main__":
    unittest.main()

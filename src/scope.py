"""Bot が動作する範囲(相談室フォーラム)の判定。

22 チーム分のフォーラム ID を手で集めて環境変数に並べるのは現実的でないため、
**既定ではチャンネル名で判定する**。ID を明示した場合はそちらが優先される。

CLAUDE.md 制約 5「動作範囲の限定」の実装箇所。
ここが緩むと、関係のない場所で LLM が呼ばれてコスト事故になる。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import discord

logger = logging.getLogger(__name__)

UNKNOWN_TEAM = "不明なチーム"


class ForumScope:
    """設定されたフォーラムかどうかを判定する。"""

    def __init__(self, *, channel_ids: frozenset[int], channel_name: str) -> None:
        self._ids = channel_ids
        self._name = channel_name

    @property
    def uses_ids(self) -> bool:
        return bool(self._ids)

    def describe(self) -> str:
        if self._ids:
            return f"ID 指定 ({len(self._ids)} 件)"
        return f"チャンネル名 {self._name!r} に一致するフォーラム"

    def matches_forum(self, channel: object) -> bool:
        """フォーラムチャンネル本体が対象かどうか。"""
        if not isinstance(channel, discord.ForumChannel):
            return False
        if self._ids:
            return channel.id in self._ids
        return channel.name == self._name

    def matches_thread(self, channel: object) -> bool:
        """フォーラム内の投稿(スレッド)が対象かどうか。"""
        if not isinstance(channel, discord.Thread):
            return False

        if self._ids:
            # ID 指定なら親を取得しなくても判定できる
            return channel.parent_id in self._ids

        parent = channel.parent
        if parent is None:
            logger.warning(
                "投稿の親チャンネルを取得できず、範囲判定ができませんでした: thread=%s",
                channel.id,
            )
            return False
        return self.matches_forum(parent)

    def resolve_forums(self, guilds: Iterable[discord.Guild]) -> list[discord.ForumChannel]:
        """対象フォーラムを列挙する。起動時のログで設定ミスに気付くために使う。"""
        found: list[discord.ForumChannel] = []
        for guild in guilds:
            for channel in guild.channels:
                if self.matches_forum(channel):
                    found.append(channel)  # type: ignore[arg-type]
        return found


def team_name(thread: discord.Thread) -> str:
    """投稿からチーム名を求める。

    各チームのチャットグループ(カテゴリ)の中に相談室フォーラムがある構成を前提に、
    カテゴリ名をチーム名として使う。段階4のキュー通知で使う(SPEC §5.3)。
    """
    parent = thread.parent
    if parent is None:
        return UNKNOWN_TEAM
    if parent.category is not None:
        return parent.category.name
    return parent.name

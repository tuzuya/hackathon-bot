"""本番サーバーの構築を自動化する。

    python -m src.setup_server                    # 何をするかを表示するだけ(既定)
    python -m src.setup_server --apply            # 実際に作成する

22 チーム分のカテゴリ・フォーラム・タグを手で作るのは現実的でないため、
一括で作る。**既定は dry-run**で、--apply を付けたときだけ実際に作成する。

作るもの:
  - `エンジニアメンター` ロール(無ければ。メンション可能な設定で作る)
  - エンジニアメンター用カテゴリ + キュー / Bot管理 チャンネル
    (@everyone は閲覧不可、エンジニアメンターと Bot のみ可)
  - 各チームのカテゴリ + 相談室フォーラム + 「要対応」タグ

すでにあるものは飛ばす(何度実行しても安全)。
最後に .env に貼る値を出力する。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

import discord

from src.config import Config, ConfigError

OK = "✅"
SKIP = "・"
NG = "❌"

ENGINEER_MENTOR_ROLE = "エンジニアメンター"
MENTOR_ROLE = "メンター"
MENTOR_CATEGORY = "エンジニアメンター"
QUEUE_CHANNEL = "エンジニアメンターキュー"
ADMIN_CHANNEL = "bot管理"


@dataclass
class Plan:
    """実行前に何が起きるかを見せるための記録。"""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def report(self, *, applied: bool) -> None:
        verb = "作成した" if applied else "作成する"
        print(f"\n{'=' * 60}")
        if self.created:
            print(f"{verb}もの ({len(self.created)} 件):")
            for item in self.created:
                print(f"  {OK} {item}")
        if self.skipped:
            print(f"\nすでに存在するため飛ばすもの ({len(self.skipped)} 件):")
            for item in self.skipped[:10]:
                print(f"  {SKIP} {item}")
            if len(self.skipped) > 10:
                print(f"  {SKIP} ... 他 {len(self.skipped) - 10} 件")
        if self.failed:
            print(f"\n{NG} 失敗 ({len(self.failed)} 件):")
            for item in self.failed:
                print(f"  {NG} {item}")


def team_names(args: argparse.Namespace) -> list[str]:
    """チーム名の一覧を決める。

    teams.txt があればそれを使い、無ければ --count と --format から生成する。
    """
    if args.teams_file:
        path = Path(args.teams_file)
        if not path.is_file():
            print(f"{NG} チーム一覧のファイルが見つかりません: {path}", file=sys.stderr)
            raise SystemExit(1)
        names = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        return [name for name in names if name and not name.startswith("#")]
    return [args.format.format(n=i) for i in range(1, args.count + 1)]


class Builder(discord.Client):
    def __init__(self, config: Config, args: argparse.Namespace) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.args = args
        self.plan = Plan()
        self.results: dict[str, int] = {}

    async def on_ready(self) -> None:
        try:
            await self._run()
        except Exception as exc:  # 何が起きても必ず接続を閉じる
            print(f"{NG} 想定外のエラー: {exc!r}", file=sys.stderr)
        finally:
            await self.close()

    def _guild(self) -> discord.Guild | None:
        if self.args.guild:
            return self.get_guild(self.args.guild)
        if len(self.guilds) == 1:
            return self.guilds[0]
        print(f"{NG} 複数のサーバーに参加しています。--guild で ID を指定してください:")
        for guild in self.guilds:
            print(f"     {guild.id}  {guild.name}")
        return None

    async def _run(self) -> None:
        guild = self._guild()
        if guild is None:
            return

        teams = team_names(self.args)
        mode = "実行" if self.args.apply else "確認のみ(dry-run)"
        print(f"\nサーバー: {guild.name}")
        print(f"モード  : {mode}")
        print(f"チーム  : {len(teams)} 件 — {', '.join(teams[:5])}"
              f"{' ...' if len(teams) > 5 else ''}")
        print(f"相談室名: {self.config.forum_channel_name}")
        print(f"タグ名  : {self.config.needs_attention_tag_name}\n")

        engineer_role = await self._ensure_engineer_role(guild)
        await self._ensure_mentor_role(guild)
        await self._ensure_mentor_area(guild, engineer_role)
        await self._ensure_teams(guild, teams)

        self.plan.report(applied=self.args.apply)
        self._print_env()

        if not self.args.apply:
            print("\n実際に作成するには --apply を付けて実行してください。")

    # --- ロール ------------------------------------------------------------

    async def _ensure_engineer_role(self, guild: discord.Guild) -> discord.Role | None:
        existing = discord.utils.get(guild.roles, name=ENGINEER_MENTOR_ROLE)
        if existing is not None:
            self.plan.skipped.append(f"ロール @{ENGINEER_MENTOR_ROLE}")
            self.results["ENGINEER_MENTOR_ROLE_ID"] = existing.id
            if not existing.mentionable:
                print(
                    f"{NG} @{ENGINEER_MENTOR_ROLE} が「メンション可能」になっていません。"
                    "このままだと Bot の呼び出しで赤い通知が飛びません"
                )
            return existing

        self.plan.created.append(f"ロール @{ENGINEER_MENTOR_ROLE}(メンション可能)")
        if not self.args.apply:
            return None
        try:
            # mentionable=True にしないと、Bot がメンションしても通知が飛ばない
            role = await guild.create_role(
                name=ENGINEER_MENTOR_ROLE,
                mentionable=True,
                reason="ハッカソン Bot のセットアップ",
            )
        except discord.HTTPException as exc:
            self.plan.failed.append(f"ロール @{ENGINEER_MENTOR_ROLE}: {exc}")
            return None
        self.results["ENGINEER_MENTOR_ROLE_ID"] = role.id
        return role

    async def _ensure_mentor_role(self, guild: discord.Guild) -> None:
        """デザイナーを含む `メンター` ロール。

        Bot は使わないが、誤爆テストに必要なので無ければ作る。
        ⚠️ こちらは mentionable=False にする。Bot がここへ通知を飛ばすことは
           絶対にないが、人が誤って全メンターを叩くのも避けたい。
        """
        if discord.utils.get(guild.roles, name=MENTOR_ROLE) is not None:
            self.plan.skipped.append(f"ロール @{MENTOR_ROLE}")
            return
        self.plan.created.append(f"ロール @{MENTOR_ROLE}(誤爆テスト用)")
        if not self.args.apply:
            return
        try:
            await guild.create_role(
                name=MENTOR_ROLE,
                mentionable=False,
                reason="ハッカソン Bot のセットアップ",
            )
        except discord.HTTPException as exc:
            self.plan.failed.append(f"ロール @{MENTOR_ROLE}: {exc}")

    # --- メンター用エリア ---------------------------------------------------

    def _staff_overwrites(
        self, guild: discord.Guild, engineer_role: discord.Role | None
    ) -> dict:
        """参加者に待ち行列が見えないようにする(SPEC §5.3)。

        ⚠️ Bot のロールを明示的に許可すること。
           @everyone を閉じると Bot からも見えなくなり、
           エスカレーション通知が無言で失敗する。
        """
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
        if engineer_role is not None:
            overwrites[engineer_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                manage_messages=True,
            )
        return overwrites

    async def _ensure_mentor_area(
        self, guild: discord.Guild, engineer_role: discord.Role | None
    ) -> None:
        category = discord.utils.get(guild.categories, name=MENTOR_CATEGORY)
        overwrites = self._staff_overwrites(guild, engineer_role)

        if category is None:
            self.plan.created.append(f"カテゴリ「{MENTOR_CATEGORY}」(参加者には非公開)")
            if self.args.apply:
                try:
                    category = await guild.create_category(
                        MENTOR_CATEGORY, overwrites=overwrites
                    )
                except discord.HTTPException as exc:
                    self.plan.failed.append(f"カテゴリ {MENTOR_CATEGORY}: {exc}")
                    return
        else:
            self.plan.skipped.append(f"カテゴリ「{MENTOR_CATEGORY}」")

        for name, key in ((QUEUE_CHANNEL, "MENTOR_QUEUE_CHANNEL_ID"),
                          (ADMIN_CHANNEL, "ADMIN_CHANNEL_ID")):
            existing = discord.utils.get(guild.text_channels, name=name)
            if existing is not None:
                self.plan.skipped.append(f"チャンネル #{name}")
                self.results[key] = existing.id
                continue
            self.plan.created.append(f"チャンネル #{name}")
            if not self.args.apply or category is None:
                continue
            try:
                channel = await guild.create_text_channel(name, category=category)
            except discord.HTTPException as exc:
                self.plan.failed.append(f"チャンネル #{name}: {exc}")
                continue
            self.results[key] = channel.id

    # --- チーム -------------------------------------------------------------

    async def _ensure_teams(self, guild: discord.Guild, teams: list[str]) -> None:
        forum_name = self.config.forum_channel_name
        tag_name = self.config.needs_attention_tag_name

        for team in teams:
            category = discord.utils.get(guild.categories, name=team)
            if category is None:
                self.plan.created.append(f"カテゴリ「{team}」")
                if self.args.apply:
                    try:
                        category = await guild.create_category(
                            team, overwrites=self._team_overwrites(guild, team)
                        )
                    except discord.HTTPException as exc:
                        self.plan.failed.append(f"カテゴリ {team}: {exc}")
                        continue
            else:
                self.plan.skipped.append(f"カテゴリ「{team}」")

            existing = next(
                (
                    c
                    for c in guild.forums
                    if c.name == forum_name and c.category == category
                ),
                None,
            )
            if existing is not None:
                self.plan.skipped.append(f"{team} / {forum_name}")
                if not any(t.name == tag_name for t in existing.available_tags):
                    self.plan.created.append(f"{team} / 「{tag_name}」タグ")
                    if self.args.apply:
                        try:
                            await existing.create_tag(name=tag_name)
                        except discord.HTTPException as exc:
                            self.plan.failed.append(f"{team} のタグ: {exc}")
                continue

            self.plan.created.append(f"{team} / {forum_name}(「{tag_name}」タグつき)")
            if not self.args.apply or category is None:
                continue
            try:
                # タグはチャンネル作成時に一緒に作れる
                await guild.create_forum(
                    name=forum_name,
                    category=category,
                    available_tags=[discord.ForumTag(name=tag_name)],
                    topic=f"{team} の質問はここへ。投稿を作って Bot を呼んでください。",
                )
            except discord.HTTPException as exc:
                self.plan.failed.append(f"{team} のフォーラム: {exc}")

    def _team_overwrites(self, guild: discord.Guild, team: str) -> dict | None:
        """--private-teams のときだけ、チーム外から見えないようにする。

        チーム名と同じ名前のロールが必要。無ければ既定の権限のままにする
        (勝手に閉じると、参加者が自分のチャンネルを見られなくなる)。
        """
        if not self.args.private_teams:
            return None
        role = discord.utils.get(guild.roles, name=team)
        if role is None:
            print(f"{NG} ロール @{team} が無いため、{team} は公開のままにします")
            return None
        overwrites: dict = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            role: discord.PermissionOverwrite(view_channel=True),
        }
        for name in (ENGINEER_MENTOR_ROLE, MENTOR_ROLE):
            mentor_role = discord.utils.get(guild.roles, name=name)
            if mentor_role is not None:
                overwrites[mentor_role] = discord.PermissionOverwrite(view_channel=True)
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            )
        return overwrites

    # --- 出力 ---------------------------------------------------------------

    def _print_env(self) -> None:
        if not self.results:
            return
        print(f"\n{'=' * 60}\n.env に設定する値:\n")
        for key in ("ENGINEER_MENTOR_ROLE_ID", "MENTOR_QUEUE_CHANNEL_ID", "ADMIN_CHANNEL_ID"):
            if key in self.results:
                print(f"{key}={self.results[key]}")
        print(f"FORUM_CHANNEL_NAME={self.config.forum_channel_name}")
        print("\n※ フォーラムの ID は不要です(名前で自動的に見つけます)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ハッカソンサーバーのチャンネル構成を一括で作る"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="実際に作成する(付けない場合は何をするか表示するだけ)",
    )
    parser.add_argument("--count", type=int, default=22, help="チーム数(既定 22)")
    parser.add_argument(
        "--format", default="{n}班",
        help="チーム名の形式。{n} が番号に置き換わる(既定 '{n}班')",
    )
    parser.add_argument(
        "--teams-file",
        help="チーム名を 1 行ずつ書いたファイル。指定すると --count より優先される",
    )
    parser.add_argument("--guild", type=int, help="対象サーバーの ID")
    parser.add_argument(
        "--private-teams", action="store_true",
        help="各チームのカテゴリを、同名のロールを持つ人だけに見せる",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"{NG} 設定エラー: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    builder = Builder(config, args)
    try:
        await builder.start(config.discord_token)
    except discord.LoginFailure:
        print(f"{NG} DISCORD_TOKEN が無効です", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(1 if builder.plan.failed else 0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

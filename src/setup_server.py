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
    granted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def report(self, *, applied: bool) -> None:
        verb = "作成した" if applied else "作成する"
        print(f"\n{'=' * 60}")
        if self.created:
            print(f"{verb}もの ({len(self.created)} 件):")
            for item in self.created:
                print(f"  {OK} {item}")
        if self.granted:
            verb2 = "追加した" if applied else "追加する"
            print(f"\n{verb2}権限 ({len(self.granted)} 件):")
            for item in self.granted:
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


def team_names_from_args(args: argparse.Namespace) -> list[str] | None:
    """明示的に指定されたチーム名の一覧。既存カテゴリから拾う場合は None。"""
    if args.from_categories:
        return None
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
        # 優先順: --guild > .env の GUILD_ID > 参加サーバーが1つならそれ
        target = self.args.guild or self.config.guild_id
        if target:
            guild = self.get_guild(target)
            if guild is None:
                print(f"{NG} ID {target} のサーバーが見つかりません"
                      "(Bot が招待されていますか?)")
            return guild
        if len(self.guilds) == 1:
            return self.guilds[0]
        print(f"{NG} 複数のサーバーに参加しています。"
              "--guild で ID を指定するか、.env に GUILD_ID を設定してください:")
        for guild in self.guilds:
            print(f"     {guild.id}  {guild.name}")
        return None

    async def _run(self) -> None:
        guild = self._guild()
        if guild is None:
            return

        categories = self._resolve_team_categories(guild)
        mode = "実行" if self.args.apply else "確認のみ(dry-run)"
        print(f"\nサーバー: {guild.name}")
        print(f"モード  : {mode}")
        # 22 件を一括で触るので、対象は省略せず全部出す。
        # 「...」で省くと、混ざってはいけないカテゴリに気づけない
        print(f"チーム  : {len(categories)} 件")
        for entry in categories:
            name = entry if isinstance(entry, str) else entry.name
            mark = "(新規作成)" if isinstance(entry, str) else ""
            print(f"          - {name} {mark}")
        print(f"相談室名: {self.config.forum_channel_name}")
        print(f"タグ名  : {self.config.needs_attention_tag_name}\n")

        self.results["GUILD_ID"] = guild.id
        if not self._check_permissions(guild):
            return
        self._note_administrator(guild)

        engineer_role = await self._ensure_engineer_role(guild)
        await self._ensure_mentor_role(guild)
        await self._ensure_mentor_area(guild, engineer_role)
        await self._ensure_teams(guild, categories, engineer_role)

        self.plan.report(applied=self.args.apply)
        self._explain_failures()
        self._print_env()

        if not self.args.apply:
            print("\n実際に作成するには --apply を付けて実行してください。")

    def _note_administrator(self, guild: discord.Guild) -> None:
        me = guild.me
        if me is None or not me.guild_permissions.administrator:
            return
        print("ℹ️  Bot に管理者権限が付いています。")
        print("   構築後に外しても動き続けるよう、各カテゴリへ権限を明示的に書き込みます。")
        print("   **構築が終わったら管理者権限は外してください。**")
        print("   外したあと `python -m src.check` で確認できます。\n")

    def _check_permissions(self, guild: discord.Guild) -> bool:
        """構築に必要な権限が Bot にあるかを先に確かめる。

        22 チーム分の途中で 403 になると、どこまで進んだか分からなくなる。
        何かを作る前に止めたほうが後始末が楽。
        """
        me = guild.me
        if me is None:
            print(f"{NG} Bot のメンバー情報を取得できません")
            return False

        perms = me.guild_permissions
        required = {
            "manage_channels": "チャンネルの管理(カテゴリ・フォーラムの作成に必要)",
            "manage_roles": "ロールの管理(ロールの作成と権限設定に必要)",
        }
        missing = [label for attr, label in required.items() if not getattr(perms, attr)]
        if not missing:
            return True

        print(f"{NG} Bot に必要な権限がありません:")
        for label in missing:
            print(f"     - {label}")
        print()
        print("   直し方: サーバー設定 → ロール → Bot のロール に権限を付ける。")
        print("   または、管理者権限を持つ人に一時的に付与してもらう。")
        print("   **構築が終わったらこの 2 つは外してよい。**")
        return False

    def _resolve_team_categories(
        self, guild: discord.Guild
    ) -> list[discord.CategoryChannel | str]:
        """対象となるチームのカテゴリを決める。

        --from-categories なら、サーバーに既にあるカテゴリをそのまま使う。
        名前を手で指定する方式は、表記ゆれ(末尾の空白、絵文字など)があると
        既存カテゴリを見つけられず、**同名のカテゴリを新規作成してしまう**。
        既にカテゴリが用意されているなら、こちらのほうが安全。
        """
        excluded = {MENTOR_CATEGORY, *(self.args.exclude or [])}

        if self.args.from_categories:
            found = [c for c in guild.categories if c.name not in excluded]
            if not found:
                print(f"{NG} カテゴリが 1 つも見つかりません")
            return found  # type: ignore[return-value]

        names = team_names_from_args(self.args)
        assert names is not None
        resolved: list[discord.CategoryChannel | str] = []
        for name in names:
            existing = discord.utils.get(guild.categories, name=name)
            resolved.append(existing if existing is not None else name)
        return resolved

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

    @staticmethod
    def _bot_needs_overwrite(
        me: discord.Member, channel: discord.abc.GuildChannel
    ) -> bool:
        """このチャンネルに、Bot 用の権限を明示的に書き込む必要があるか。

        ⚠️ **管理者権限があるときは、必ず書き込む。**
           管理者はすべてのチャンネル制限を上書きするため、
           permissions_for() は常に「見える」を返す。
           それを信じて権限の追加を省くと、構築後に管理者権限を外した瞬間、
           Bot は全チームのカテゴリを見失って完全に動かなくなる。
           構築時だけ管理者を付ける運用では、ここが致命傷になる。
        """
        if me.guild_permissions.administrator:
            return True
        return not channel.permissions_for(me).view_channel

    def _bot_overwrite(self, guild: discord.Guild) -> discord.PermissionOverwrite:
        """Bot 自身に与える権限。

        ⚠️ **Bot が自分で持っていない権限は付与できない**(50013 になる)。
           チャンネル作成時の overwrites に含めるものは、
           Bot のサーバー権限にあるものだけに絞る。
        """
        me = guild.me
        wanted = (
            "view_channel",
            "send_messages",
            "send_messages_in_threads",
            "read_message_history",
            "embed_links",
            "attach_files",
            "add_reactions",
            "manage_threads",
        )
        overwrite = discord.PermissionOverwrite()
        if me is None:
            return overwrite
        perms = me.guild_permissions
        for name in wanted:
            # 持っている権限だけを明示的に許可する
            if getattr(perms, name, False):
                setattr(overwrite, name, True)
        return overwrite

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
            overwrites[guild.me] = self._bot_overwrite(guild)
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

    async def _ensure_teams(
        self,
        guild: discord.Guild,
        categories: list,
        engineer_role: discord.Role | None,
    ) -> None:
        forum_name = self.config.forum_channel_name
        tag_name = self.config.needs_attention_tag_name

        for entry in categories:
            if isinstance(entry, str):
                # カテゴリが存在しないので作る
                team = entry
                self.plan.created.append(f"カテゴリ「{team}」")
                category = None
                if self.args.apply:
                    try:
                        category = await guild.create_category(
                            team, overwrites=self._team_overwrites(guild, team)
                        )
                    except discord.HTTPException as exc:
                        self.plan.failed.append(f"カテゴリ {team}: {exc}")
                        continue
            else:
                category = entry
                team = category.name

            if category is None:
                continue

            # 権限はカテゴリに付ける。フォーラムはそれを引き継ぐ。
            # フォーラム個別に付けるとカテゴリとの同期が切れ、
            # 後から班のカテゴリ権限を変えても相談室に反映されなくなる
            await self._grant_access(category, engineer_role)

            existing = next(
                (
                    c
                    for c in guild.forums
                    if c.name == forum_name and c.category_id == category.id
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
            if not self.args.apply:
                continue
            try:
                # overwrites を渡さないので、カテゴリの権限をそのまま引き継ぐ。
                # チームごとの公開範囲は既存のカテゴリ設定に従う
                await guild.create_forum(
                    name=forum_name,
                    category=category,
                    available_tags=[discord.ForumTag(name=tag_name)],
                    topic=f"{team} の質問はここへ。投稿を作って Bot を呼んでください。",
                )
            except discord.HTTPException as exc:
                self.plan.failed.append(f"{team} のフォーラム: {exc}")

    def _access_targets(
        self,
        channel: discord.abc.GuildChannel,
        engineer_role: discord.Role | None,
    ) -> dict:
        """Bot とエンジニアメンターのうち、そのカテゴリを見られない相手を返す。"""
        guild = channel.guild
        needed: dict = {}

        me = guild.me
        if me is not None and self._bot_needs_overwrite(me, channel):
            needed[me] = self._bot_overwrite(guild)

        if engineer_role is not None and not channel.permissions_for(
            engineer_role
        ).view_channel:
            needed[engineer_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                send_messages_in_threads=True,
                read_message_history=True,
            )
        return needed

    async def _grant_access(
        self,
        category: discord.CategoryChannel,
        engineer_role: discord.Role | None,
    ) -> None:
        """Bot とエンジニアメンターが、このカテゴリを使えるようにする。

        ⚠️ **足りない権限だけを足す。**既存の設定には手を加えない。
           カテゴリが @everyone を閉じていると Bot も締め出され、
           質問に気づけず、エスカレーション通知も無言で失敗する
           (テスト環境で実際に踏んだ)。
           メンターも見えなければ、呼ばれても駆けつけられない。

        なお、ここで付けるのは **閲覧・投稿の権限** であって、
        Bot の通知が飛ぶ相手とは無関係。通知先は allowed_mentions で
        エンジニアメンターのロール ID だけを明示指定している。
        """
        needed = self._access_targets(category, engineer_role)
        if not needed:
            return
        for target in needed:
            name = getattr(target, "name", str(target))
            self.plan.granted.append(f"{category.name}: {name} に閲覧・投稿権限")
        if not self.args.apply:
            return
        try:
            for target, overwrite in needed.items():
                await category.set_permissions(
                    target, overwrite=overwrite, reason="ハッカソン Bot のセットアップ"
                )
        except discord.Forbidden as exc:
            # 50001 = そのカテゴリが Bot から見えていない。
            # Discord では見えないチャンネルの権限は変更できないので、
            # Bot が自分自身にアクセス権を与えることはできない
            self.plan.failed.append(
                f"{category.name} の権限設定: Bot からこのカテゴリが見えていません "
                f"(code {exc.code})"
            )
        except discord.HTTPException as exc:
            self.plan.failed.append(f"{category.name} の権限設定: {exc}")

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

    def _explain_failures(self) -> None:
        """403 が出たときに、何をすれば直るかを示す。"""
        if not self.plan.failed:
            return
        if not any("見えていません" in f or "403" in f or "50013" in f
                   for f in self.plan.failed):
            return
        print(f"\n{'=' * 60}")
        print("権限エラーの直し方\n")
        print("各班のカテゴリは、参加者以外に見えない設定になっています。")
        print("Discord では **見えないチャンネルの権限は変更できない** ため、")
        print("Bot が自分自身にアクセス権を与えることができません。\n")
        print("解決策: Bot のロールに **管理者(Administrator)** を一時的に付ける。")
        print("  サーバー設定 → ロール → Bot のロール → 管理者 を ON\n")
        print("管理者権限はすべてのチャンネルの制限を上書きするため、")
        print("この 1 回の実行で全部そろいます。")
        print("**構築が終わったら管理者権限は外してください。**")
        print("通常運用では、招待時の権限だけで足ります。")

    # --- 出力 ---------------------------------------------------------------

    def _print_env(self) -> None:
        if not self.results:
            return
        print(f"\n{'=' * 60}\n.env に設定する値:\n")
        print(f"GUILD_ID={self.results.get('GUILD_ID', '')}")
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
    parser.add_argument(
        "--from-categories", action="store_true",
        help="サーバーに既にあるカテゴリをチームとして使う。"
             "すでに 1班〜22班 のカテゴリがある場合はこちらを使う(推奨)",
    )
    parser.add_argument(
        "--exclude", nargs="*", default=[],
        help="--from-categories のときに対象から外すカテゴリ名(複数指定可)",
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
        help="カテゴリを新規作成する場合に、同名のロールを持つ人だけに見せる。"
             "既存カテゴリの公開範囲は変更しない",
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

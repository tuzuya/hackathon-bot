"""接続と設定の診断コマンド。

    python -m src.check

Discord に接続して、以下を確認してから終了する。Bot は常駐しない。

- 対象フォーラムが何件見つかるか(22 チーム分が揃っているか)
- 各フォーラムで Bot に必要な権限があるか
- 「要対応」タグが作られているか
- キュー / Bot管理 チャンネルに書き込めるか

⚠️ この診断はメッセージを一切送信しない。読み取りだけを行う。
"""

from __future__ import annotations

import asyncio
import sys

import discord

from src.config import Config, ConfigError
from src.scope import ForumScope

OK = "✅"
NG = "❌"
WARN = "⚠️ "

# フォーラムで Bot が必要とする権限(属性名 → 表示名)
FORUM_PERMISSIONS = {
    "view_channel": "チャンネルを見る",
    "send_messages_in_threads": "スレッドでメッセージを送信",
    "read_message_history": "メッセージ履歴を読む",
    "embed_links": "埋め込みリンク",
    "attach_files": "ファイルを添付",
    "manage_threads": "スレッドの管理(要対応タグの付与に必要)",
    "add_reactions": "リアクションを追加(受付の合図に使用)",
}

TEXT_PERMISSIONS = {
    "view_channel": "チャンネルを見る",
    "send_messages": "メッセージを送信",
    "embed_links": "埋め込みリンク",
}


class Checker(discord.Client):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.config = config
        self.problems = 0

    def _fail(self, message: str) -> None:
        self.problems += 1
        print(f"{NG} {message}")

    async def on_ready(self) -> None:
        try:
            self._run_checks()
        finally:
            await self.close()

    def _run_checks(self) -> None:
        print(f"\n{OK} ログイン成功: {self.user}")
        print(f"   参加サーバー: {', '.join(g.name for g in self.guilds) or '(なし)'}\n")

        self._check_message_content_intent()
        forums = self._check_forums()
        self._check_tags(forums)
        self._check_staff_channels()
        self._check_role()

        print()
        if self.problems:
            print(f"{NG} 問題が {self.problems} 件あります。上の内容を確認してください。")
        else:
            print(f"{OK} 問題は見つかりませんでした。")

    def _check_message_content_intent(self) -> None:
        # Portal 側で OFF なら start() の時点で PrivilegedIntentsRequired が出るため、
        # ここに到達している = Portal 側でも有効になっている。
        print("── メンション本文の読み取り ──")
        print(f"{OK} Message Content Intent は有効(接続できたため確定)")
        print()

    def _check_forums(self) -> list[discord.ForumChannel]:
        scope = ForumScope(
            channel_ids=self.config.forum_channel_ids,
            channel_name=self.config.forum_channel_name,
        )
        print(f"── 対象フォーラム({scope.describe()}) ──")
        forums = scope.resolve_forums(self.guilds)

        if not forums:
            self._fail(
                f"対象フォーラムが 0 件。フォーラム名が "
                f"{self.config.forum_channel_name!r} と完全に一致しているか、"
                "Bot に閲覧権限があるかを確認してください"
            )
            self._show_candidate_forums()
            return []

        print(f"{OK} {len(forums)} 件見つかりました")
        for forum in forums:
            category = forum.category.name if forum.category else "(カテゴリなし)"
            print(f"   - {category} / {forum.name}")
            self._check_permissions(forum, FORUM_PERMISSIONS, indent="     ")
        print()
        return forums

    def _show_candidate_forums(self) -> None:
        """名前が一致しなかったとき、実在するフォーラム名を出して比較できるようにする。"""
        print("   サーバー内のフォーラムチャンネル一覧:")
        found = False
        for guild in self.guilds:
            for channel in guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    found = True
                    print(f"     - {channel.name!r}")
        if not found:
            print("     (フォーラムチャンネルが1つもありません)")

    def _check_permissions(
        self,
        channel: discord.abc.GuildChannel,
        required: dict[str, str],
        *,
        indent: str,
    ) -> None:
        me = channel.guild.me
        if me is None:
            self._fail(f"{indent}Bot のメンバー情報を取得できません")
            return
        perms = channel.permissions_for(me)
        missing = [attr for attr in required if not getattr(perms, attr)]
        if not missing:
            print(f"{indent}{OK} 権限 OK")
            return

        self._fail(f"{indent}権限不足: {', '.join(required[a] for a in missing)}")
        self._explain_missing(channel, missing, indent=indent + "  ")

    def _explain_missing(
        self,
        channel: discord.abc.GuildChannel,
        missing: list[str],
        *,
        indent: str,
    ) -> None:
        """権限がどこで落とされているかを示す。

        22 チーム分を設定していると、どのチームのどの階層でミスったのかが
        分からないと直せない。カテゴリ側か、チャンネル個別かを切り分ける。
        """
        me = channel.guild.me
        if me is None:
            return
        category = channel.category

        if category is None:
            print(f"{indent}→ カテゴリに属していません。チャンネル個別の権限を確認してください")
            return

        cat_perms = category.permissions_for(me)
        blocked_at_category = [a for a in missing if not getattr(cat_perms, a)]

        if channel.permissions_synced:
            print(f"{indent}→ このチャンネルはカテゴリ「{category.name}」と同期しています")
        else:
            print(f"{indent}→ このチャンネルはカテゴリ「{category.name}」と同期していません(個別設定あり)")

        if blocked_at_category:
            print(f"{indent}  原因: **カテゴリ「{category.name}」** の権限")
            print(f"{indent}  直し方: カテゴリ設定 → 権限 → ロールを追加 → "
                  f"@{me.top_role.name if me.top_role else 'Bot のロール'} を許可")
        else:
            print(f"{indent}  原因: **チャンネル「{channel.name}」個別** の権限上書き")
            print(f"{indent}  直し方: チャンネル設定 → 権限 で、Bot のロールの拒否を外すか、"
                  "カテゴリと同期し直す")

        # 誰が拒否されているのかを具体的に出す
        for target, overwrite in channel.overwrites.items():
            denied = [a for a, value in overwrite if value is False and a in missing]
            if denied:
                name = getattr(target, "name", str(target))
                print(f"{indent}  チャンネルで拒否: {name} → {', '.join(denied)}")
        for target, overwrite in category.overwrites.items():
            denied = [a for a, value in overwrite if value is False and a in missing]
            if denied:
                name = getattr(target, "name", str(target))
                print(f"{indent}  カテゴリで拒否: {name} → {', '.join(denied)}")

    def _check_tags(self, forums: list[discord.ForumChannel]) -> None:
        if not forums:
            return
        tag = self.config.needs_attention_tag_name
        print(f"── 「{tag}」タグ(段階4で使用) ──")
        for forum in forums:
            names = [t.name for t in forum.available_tags]
            if tag in names:
                print(f"{OK} {forum.name}")
            else:
                print(f"{WARN}{forum.name} に「{tag}」タグがありません(既存タグ: {names or 'なし'})")
        print()

    def _check_staff_channels(self) -> None:
        print("── 運営用チャンネル ──")
        targets = [
            ("MENTOR_QUEUE_CHANNEL_ID", self.config.mentor_queue_channel_id, "キュー"),
            ("ADMIN_CHANNEL_ID", self.config.admin_channel_id, "Bot管理"),
        ]
        for var, channel_id, label in targets:
            if channel_id is None:
                print(f"{WARN}{var} が未設定({label}。段階4以降で必要)")
                continue
            channel = self.get_channel(channel_id)
            if channel is None:
                self._fail(f"{var} の ID {channel_id} が見つかりません(ID 違い、または Bot に閲覧権限がない)")
                continue
            print(f"{OK} {label}: #{channel.name}")
            self._check_permissions(channel, TEXT_PERMISSIONS, indent="   ")  # type: ignore[arg-type]
        print()

    def _check_role(self) -> None:
        print("── エンジニアメンターロール ──")
        role_id = self.config.engineer_mentor_role_id
        if role_id is None:
            print(f"{WARN}ENGINEER_MENTOR_ROLE_ID が未設定(段階4以降で必要)")
            print()
            return
        for guild in self.guilds:
            role = guild.get_role(role_id)
            if role is None:
                continue
            print(f"{OK} ロール名: @{role.name}")
            if self.intents.members:
                print(f"   付与されている人数: {len(role.members)}")
            else:
                print(
                    "   (人数は確認できません。Server Members Intent が無効なため。"
                    "本番前の 6 名確認は Discord の画面で行ってください)"
                )
            if "デザイ" in role.name or role.name == "メンター":
                self._fail(
                    f"ロール名が {role.name!r} です。"
                    "エンジニアメンター以外に通知が飛ぶ可能性があります。ID を確認してください"
                )
            print()
            return
        self._fail(f"ID {role_id} のロールが見つかりません")
        print()


async def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"{NG} 設定エラー: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    checker = Checker(config)
    try:
        await checker.start(config.discord_token)
    except discord.LoginFailure:
        print(f"{NG} DISCORD_TOKEN が無効です。Developer Portal で発行し直してください", file=sys.stderr)
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        print(
            f"{NG} Message Content Intent が有効になっていません。"
            "Developer Portal → Bot で ON にしてください",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

    raise SystemExit(1 if checker.problems else 0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

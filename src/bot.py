"""Bot のエントリポイント。

起動: python -m src.bot
"""

from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

from src.config import Config, ConfigError
from src.knowledge.loader import load_knowledge
from src.llm.client import ClaudeClient, LLMError
from src.llm.prompt import build_system_prompt
from src.scope import ForumScope

logger = logging.getLogger(__name__)

# 読み込む Cog。段階が進むごとにここへ追加する。
INITIAL_EXTENSIONS: tuple[str, ...] = ("src.cogs.mention",)


class HackathonBot(commands.Bot):
    """ハッカソン質問対応 Bot。"""

    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        # メンション本文を読むために必須。
        # Developer Portal 側でも Message Content Intent を ON にしておくこと。
        intents.message_content = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            # 保険として、既定では一切のメンションを解決させない。
            # エスカレーション通知(段階4)だけが、ロール ID を明示指定して例外的に許可する。
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        # ANTHROPIC_API_KEY 未設定でも起動できるようにする(段階1の疎通確認のため)
        self.llm: ClaudeClient | None = None
        self.system_prompt: str | None = None

    async def setup_hook(self) -> None:
        self._setup_llm()

        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Cog を読み込みました: %s", extension)

        # 段階4で、永続 View をここで bot.add_view() 再登録する。

    def _setup_llm(self) -> None:
        """知識源を読み、システムプロンプトを一度だけ組み立てる。

        システムプロンプトはプロセスの生存中ずっと同じ文字列を使い回す。
        リクエストごとに変わるとプロンプトキャッシュが効かなくなるため
        (CLAUDE.md「RAG は実装しない」)。
        """
        if self.config.anthropic_api_key is None:
            logger.warning(
                "ANTHROPIC_API_KEY が未設定のため、LLM 応答は無効です(固定文を返します)。"
            )
            return
        try:
            knowledge = load_knowledge(self.config.knowledge_dir)
            self.system_prompt = build_system_prompt(
                self.config.system_prompt_path,
                knowledge,
                self.config.forum_channel_name,
            )
            self.llm = ClaudeClient(self.config)
        except (FileNotFoundError, LLMError, RuntimeError):
            logger.exception("LLM の初期化に失敗しました。固定文での応答に切り替えます。")
            self.llm = None
            self.system_prompt = None
            return
        logger.info(
            "システムプロンプトを構築しました: %d 文字 / モデル=%s",
            len(self.system_prompt),
            self.config.anthropic_model,
        )

    async def close(self) -> None:
        if self.llm is not None:
            await self.llm.close()
        await super().close()

    async def process_commands(self, message: discord.Message) -> None:
        """プレフィックスコマンドの処理を無効にする。

        command_prefix=when_mentioned のため、`@bot 質問文` が毎回
        コマンド呼び出しとして解釈され、CommandNotFound が ERROR ログに出ていた。
        本物のエラーが埋もれるので止める。
        運営向けコマンド(段階6)はスラッシュコマンドで実装するため、こちらは使わない。
        """
        return

    async def on_ready(self) -> None:
        logger.info("ログインしました: %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        self._report_scope()

    def _report_scope(self) -> None:
        """実際に何個のフォーラムを見ているかを起動時に出す。

        22 チーム分を名前で拾う運用では、設定ミスに気付ける唯一の場所になる。
        0 件なら、フォーラム名が違うか Bot に閲覧権限がない。
        """
        scope = ForumScope(
            channel_ids=self.config.forum_channel_ids,
            channel_name=self.config.forum_channel_name,
        )
        forums = scope.resolve_forums(self.guilds)
        logger.info("動作範囲: %s", scope.describe())
        if not forums:
            logger.error(
                "対象のフォーラムが1つも見つかりません。"
                "フォーラム名(FORUM_CHANNEL_NAME=%r)が実際のチャンネル名と一致しているか、"
                "Bot に閲覧権限があるかを確認してください。",
                self.config.forum_channel_name,
            )
            return
        logger.info("対象フォーラム %d 件:", len(forums))
        for forum in forums:
            category = forum.category.name if forum.category else "(カテゴリなし)"
            logger.info("  - %s / %s (id=%s)", category, forum.name, forum.id)


async def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"[設定エラー] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    discord.utils.setup_logging(level=getattr(logging, config.log_level, logging.INFO))

    pending = config.missing_for_later_stages()
    if pending:
        logger.warning("未設定の環境変数があります(後の段階で必要): %s", " / ".join(pending))

    bot = HackathonBot(config)
    async with bot:
        await bot.start(config.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

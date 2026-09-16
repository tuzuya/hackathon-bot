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
from src.db.database import Database
from src.escalation.service import EscalationService
from src.escalation.store import EscalationStore
from src.knowledge.manager import KnowledgeManager
from src.knowledge.store import KnowledgeStore
from src.llm.client import ClaudeClient, LLMError
from src.ratelimit import RateLimiter
from src.usage import UsageAlerter, UsageStore
from src.scope import ForumScope

logger = logging.getLogger(__name__)

# 読み込む Cog。段階が進むごとにここへ追加する。
INITIAL_EXTENSIONS: tuple[str, ...] = (
    "src.cogs.mention",
    "src.cogs.escalation",
    "src.cogs.admin",
    "src.cogs.health",
)


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
        self.database = Database(config.db_path)
        self.escalation_service: EscalationService | None = None
        self.knowledge_manager: KnowledgeManager | None = None
        self.rate_limiter: RateLimiter | None = None
        self.usage_store: UsageStore | None = None
        self.usage_alerter: UsageAlerter | None = None
        self.admin_dependencies: tuple | None = None

    async def setup_hook(self) -> None:
        await self.database.connect()
        connection = self.database.connection

        knowledge_store = KnowledgeStore(connection)
        self.knowledge_manager = KnowledgeManager(self.config, knowledge_store)
        self.usage_store = UsageStore(connection)
        self.usage_alerter = UsageAlerter(
            connection, self.config.usage_alert_thresholds
        )
        self.rate_limiter = RateLimiter(
            connection,
            per_team_hourly=self.config.rate_limit_per_channel_hourly,
            per_user_seconds=self.config.rate_limit_per_user_seconds,
        )

        self._setup_llm()
        await self._load_prompt()

        self.escalation_service = EscalationService(
            bot=self,
            config=self.config,
            store=EscalationStore(connection),
            llm=self.llm,
            scope=ForumScope(
                channel_ids=self.config.forum_channel_ids,
                channel_name=self.config.forum_channel_name,
            ),
            knowledge_store=knowledge_store,
            on_knowledge_saved=self._reload_prompt,
        )
        self.admin_dependencies = (
            self.config,
            knowledge_store,
            self.knowledge_manager,
            self.usage_store,
        )

        for extension in INITIAL_EXTENSIONS:
            await self.load_extension(extension)
            logger.info("Cog を読み込みました: %s", extension)

        # 永続 View の再登録は EscalationCog.cog_load() で行う。

    def _setup_llm(self) -> None:
        """Claude クライアントを用意する。

        ANTHROPIC_API_KEY が無くても起動できるようにしてある(段階1の疎通確認用)。
        """
        if self.config.anthropic_api_key is None:
            logger.warning(
                "ANTHROPIC_API_KEY が未設定のため、LLM 応答は無効です(固定文を返します)。"
            )
            return
        try:
            self.llm = ClaudeClient(self.config)
        except LLMError:
            logger.exception("Claude クライアントの初期化に失敗しました")
            self.llm = None

    async def _load_prompt(self) -> None:
        """知識源を読み、システムプロンプトを組み立てる。

        リクエストごとに組み直すとプロンプトキャッシュが効かないため、
        起動時と、知識が変わったときだけ実行する。
        """
        if self.knowledge_manager is None:
            return
        try:
            await self.knowledge_manager.reload()
        except Exception:
            logger.exception("システムプロンプトの構築に失敗しました。固定文で応答します")

    async def _reload_prompt(self) -> bool:
        """知識が追加・削除されたときに呼ばれる。"""
        if self.knowledge_manager is None:
            return False
        try:
            await self.knowledge_manager.reload()
        except Exception:
            logger.exception("システムプロンプトの再構築に失敗しました")
            return False
        return True

    async def close(self) -> None:
        if self.llm is not None:
            await self.llm.close()
        await self.database.close()
        await super().close()

    async def process_commands(self, message: discord.Message) -> None:
        """プレフィックスコマンドの処理を無効にする。

        command_prefix=when_mentioned のため、`@bot 質問文` が毎回
        コマンド呼び出しとして解釈され、CommandNotFound が ERROR ログに出ていた。
        本物のエラーが埋もれるので止める。
        運営向けコマンド(段階6)はスラッシュコマンドで実装するため、こちらは使わない。
        """
        return

    @property
    def target_guilds(self) -> list[discord.Guild]:
        """Bot が動作するサーバー。

        GUILD_ID を設定していればそこだけ。未設定なら参加している全サーバー。
        テスト用と本番で同じ Bot を使うときに、
        テスト側の質問に応答したりキューへ飛ばしたりしないためのもの。
        """
        if self.config.guild_id is None:
            return list(self.guilds)
        guild = self.get_guild(self.config.guild_id)
        return [guild] if guild is not None else []

    def is_target_guild(self, guild: discord.Guild | None) -> bool:
        if guild is None:
            return False
        if self.config.guild_id is None:
            return True
        return guild.id == self.config.guild_id

    async def on_ready(self) -> None:
        logger.info("ログインしました: %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        self._report_scope()
        await self._sync_commands()

    async def _sync_commands(self) -> None:
        """スラッシュコマンドを同期する。

        ギルド単位の同期は即座に反映される(グローバルは最大 1 時間かかる)。
        """
        for guild in self.target_guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(
                    "スラッシュコマンドを同期しました: %s (%d 件)",
                    guild.name,
                    len(synced),
                )
            except discord.HTTPException:
                logger.exception("スラッシュコマンドの同期に失敗: %s", guild.name)

    def _report_scope(self) -> None:
        """実際に何個のフォーラムを見ているかを起動時に出す。

        22 チーム分を名前で拾う運用では、設定ミスに気付ける唯一の場所になる。
        0 件なら、フォーラム名が違うか Bot に閲覧権限がない。
        """
        scope = ForumScope(
            channel_ids=self.config.forum_channel_ids,
            channel_name=self.config.forum_channel_name,
        )
        forums = scope.resolve_forums(self.target_guilds)
        if self.config.guild_id is None:
            logger.warning(
                "GUILD_ID が未設定です。参加している全サーバー(%d 件)で動作します。"
                "テスト用と本番で同じ Bot を使う場合は GUILD_ID を設定してください",
                len(self.guilds),
            )
        else:
            logger.info(
                "対象サーバー: %s",
                ", ".join(g.name for g in self.target_guilds) or "(見つかりません)",
            )
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

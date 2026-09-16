"""運営向けスラッシュコマンド(SPEC §7)。

実行権限はエンジニアメンターに限定する(SPEC §7.1)。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from src import messages
from src.config import Config
from src.guide import ConfirmView
from src.knowledge.manager import KnowledgeManager
from src.knowledge.store import CATEGORIES, SOURCE_MANUAL, KnowledgeStore
from src.scope import ForumScope
from src.usage import UsageStore

logger = logging.getLogger(__name__)

MAX_MODAL_LENGTH = 4000


class AddKnowledgeModal(discord.ui.Modal, title="知識を追加"):
    """複数行を入れたいのでモーダルにする。

    スラッシュコマンドの引数だと改行が入れづらく、
    手順や箇条書きを追加できない。
    """

    content: discord.ui.TextInput = discord.ui.TextInput(
        label="内容",
        style=discord.TextStyle.paragraph,
        placeholder="例: 提出期限が10月5日 15:00 から 15:30 に変更になりました。",
        max_length=MAX_MODAL_LENGTH,
    )

    def __init__(self, category: str, store: KnowledgeStore, on_saved) -> None:
        super().__init__()
        self._category = category
        self._store = store
        self._on_saved = on_saved

    async def on_submit(self, interaction: discord.Interaction) -> None:
        entry_id = await self._store.add(
            category=self._category,
            content=str(self.content.value),
            source=SOURCE_MANUAL,
            added_by=interaction.user.id,
            added_by_name=interaction.user.display_name,
        )
        ok = await self._on_saved()
        text = messages.KNOWLEDGE_ADDED.format(entry_id=entry_id)
        if not ok:
            text = messages.KNOWLEDGE_RELOAD_FAILED
        await interaction.response.send_message(text, ephemeral=True)


class AdminCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        knowledge_store: KnowledgeStore,
        knowledge_manager: KnowledgeManager,
        usage: UsageStore,
    ) -> None:
        self.bot = bot
        self.config = config
        self.knowledge_store = knowledge_store
        self.knowledge_manager = knowledge_manager
        self.usage = usage

    # --- 権限チェック -----------------------------------------------------

    def _is_engineer_mentor(self, user: discord.User | discord.Member) -> bool:
        role_id = self.config.engineer_mentor_role_id
        if role_id is None:
            return False
        if not isinstance(user, discord.Member):
            return False
        return any(role.id == role_id for role in user.roles)

    async def _deny_if_not_mentor(self, interaction: discord.Interaction) -> bool:
        if self._is_engineer_mentor(interaction.user):
            return False
        await interaction.response.send_message(
            messages.NOT_ENGINEER_MENTOR, ephemeral=True
        )
        return True

    async def _reload_prompt(self) -> bool:
        try:
            await self.knowledge_manager.reload()
        except Exception:
            logger.exception("プロンプトの再構築に失敗しました")
            return False
        return True

    # --- /add-knowledge ---------------------------------------------------

    @app_commands.command(
        name="add-knowledge", description="Bot に知識を追加する(エンジニアメンター限定)"
    )
    @app_commands.describe(category="どの種類の情報か")
    @app_commands.choices(
        category=[app_commands.Choice(name=c, value=c) for c in CATEGORIES]
    )
    async def add_knowledge(
        self,
        interaction: discord.Interaction,
        category: app_commands.Choice[str],
    ) -> None:
        if await self._deny_if_not_mentor(interaction):
            return
        await interaction.response.send_modal(
            AddKnowledgeModal(category.value, self.knowledge_store, self._reload_prompt)
        )

    # --- /list-knowledge --------------------------------------------------

    @app_commands.command(
        name="list-knowledge", description="追加済みの知識を一覧する(エンジニアメンター限定)"
    )
    async def list_knowledge(self, interaction: discord.Interaction) -> None:
        if await self._deny_if_not_mentor(interaction):
            return
        entries = await self.knowledge_store.list_active()
        if not entries:
            await interaction.response.send_message(
                messages.KNOWLEDGE_EMPTY, ephemeral=True
            )
            return

        embed = discord.Embed(title="追加済みの知識", color=discord.Color.blurple())
        for entry in entries[:25]:  # embed のフィールド上限
            preview = entry.content.replace("\n", " ")[:80]
            embed.add_field(
                name=f"#{entry.id} [{entry.category}] {entry.added_by_name}",
                value=preview or "(空)",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- /remove-knowledge ------------------------------------------------

    @app_commands.command(
        name="remove-knowledge",
        description="追加した知識を無効化する(エンジニアメンター限定)",
    )
    @app_commands.describe(entry_id="/list-knowledge で表示される番号")
    async def remove_knowledge(
        self, interaction: discord.Interaction, entry_id: int
    ) -> None:
        if await self._deny_if_not_mentor(interaction):
            return
        removed = await self.knowledge_store.deactivate(entry_id)
        if not removed:
            await interaction.response.send_message(
                messages.KNOWLEDGE_NOT_FOUND.format(entry_id=entry_id), ephemeral=True
            )
            return
        await self._reload_prompt()
        await interaction.response.send_message(
            messages.KNOWLEDGE_REMOVED.format(entry_id=entry_id), ephemeral=True
        )

    # --- /post-guide ------------------------------------------------------

    @app_commands.command(
        name="post-guide",
        description="各チームの相談室に使い方の案内を投稿する(エンジニアメンター限定)",
    )
    async def post_guide(self, interaction: discord.Interaction) -> None:
        if await self._deny_if_not_mentor(interaction):
            return

        scope = ForumScope(
            channel_ids=self.config.forum_channel_ids,
            channel_name=self.config.forum_channel_name,
        )
        forums = scope.resolve_forums(self.bot.target_guilds)  # type: ignore[attr-defined]
        if not forums:
            await interaction.response.send_message(
                messages.GUIDE_NO_FORUM, ephemeral=True
            )
            return

        mention = self.bot.user.mention if self.bot.user else "@bot"
        await interaction.response.send_message(
            messages.GUIDE_CONFIRM.format(count=len(forums)),
            view=ConfirmView(forums, mention),
            ephemeral=True,
        )

    # --- /stats -----------------------------------------------------------

    @app_commands.command(
        name="stats", description="質問の傾向と使用量を見る(エンジニアメンター限定)"
    )
    async def stats(self, interaction: discord.Interaction) -> None:
        if await self._deny_if_not_mentor(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        total_questions = await self.usage.total_questions()
        total_cost = await self.usage.total_cost()
        teams = await self.usage.team_stats()

        embed = discord.Embed(title="質問の傾向", color=discord.Color.blurple())
        embed.add_field(name="質問の総数", value=str(total_questions), inline=True)
        embed.add_field(name="概算コスト", value=f"${total_cost:.2f}", inline=True)

        if teams:
            lines = [
                f"`{t.questions:3d}問 / エスカレ{t.escalations:2d}件` {t.team_name}"
                for t in teams
            ]
            # 質問が多い = 詰まっているチーム。メンター投入の判断材料になる
            embed.add_field(
                name="チーム別(質問が多い順)",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    deps = getattr(bot, "admin_dependencies", None)
    if deps is None:
        logger.warning("運営コマンドは無効です(依存が未準備)")
        return
    await bot.add_cog(AdminCog(bot, *deps))

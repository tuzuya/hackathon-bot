"""エスカレーションの一連の処理(SPEC §5)。

「解決しなかった」が押されてから、投稿内通知・キュー通知・タグ付与までを担う。
個々の後続処理が失敗しても、**先に成功した通知は残す**。
メンターを呼べたのに、タグ付与の失敗で全部が巻き戻るほうが有害なため。
"""

from __future__ import annotations

import logging

import discord

from src import messages
from src.config import Config
from src.escalation import embeds
from src.escalation.mentions import engineer_mentor_only
from src.escalation.models import STATUS_LABELS, Escalation, Status
from src.escalation.store import EscalationStore
from src.escalation.summarizer import summarize
from src.llm.client import ClaudeClient
from src.llm.conversation import build_messages
from src.scope import ForumScope, team_name

logger = logging.getLogger(__name__)


class EscalationService:
    def __init__(
        self,
        *,
        bot: discord.Client,
        config: Config,
        store: EscalationStore,
        llm: ClaudeClient | None,
        scope: ForumScope,
    ) -> None:
        self.bot = bot
        self.config = config
        self.store = store
        self.llm = llm
        self.scope = scope

    # --- 「解決しなかった」-------------------------------------------------

    async def handle_unresolved(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread) or interaction.guild is None:
            await self._ephemeral(interaction, messages.ESCALATION_FAILED)
            return

        # 要約に数秒かかるので先に応答を確保する(3 秒で失効するため)
        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = await self.store.find_open_by_thread(thread.id)
        if existing is not None:
            await self._followup(interaction, messages.ESCALATION_ALREADY)
            return

        try:
            escalation = await self._create_escalation(thread, interaction)
        except Exception:
            logger.exception("エスカレーションの作成に失敗しました: thread=%s", thread.id)
            await self._followup(interaction, messages.ESCALATION_FAILED)
            return

        # ここから先は、1 つ失敗しても残りを続ける
        await self._notify_thread(thread, escalation)
        await self._notify_queue(escalation, thread)
        await self._apply_tag(thread)

        await self._followup(interaction, messages.ESCALATION_DONE)

    async def _create_escalation(
        self,
        thread: discord.Thread,
        interaction: discord.Interaction,
    ) -> Escalation:
        assert interaction.guild is not None
        bot_user = self.bot.user
        history = []
        if bot_user is not None:
            history = await build_messages(
                thread, bot_user.id, limit=self.config.history_limit
            )
        summary, failed = await summarize(self.llm, history)
        if failed:
            logger.warning("要約に失敗したため生の質疑を使います: thread=%s", thread.id)

        return await self.store.create(
            guild_id=interaction.guild.id,
            thread_id=thread.id,
            team_name=team_name(thread),
            requester_id=interaction.user.id,
            summary=summary,
            summary_failed=failed,
        )

    async def _notify_thread(
        self,
        thread: discord.Thread,
        escalation: Escalation,
    ) -> None:
        """投稿内でエンジニアメンターを呼ぶ。

        ⚠️ ここがロールメンションの事故が起きうる唯一の場所。
           本文は固定テンプレート、要約は embed、allowed_mentions はロール ID の配列。
        """
        role = self._engineer_mentor_role(thread.guild)
        if role is None:
            logger.error(
                "ENGINEER_MENTOR_ROLE_ID が未設定か、ロールが見つかりません。"
                "メンションなしで通知します"
            )
            try:
                await thread.send(
                    "解決しなかったみたい。メンターの対応をお願いします。",
                    embed=embeds.thread_embed(escalation),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("投稿内への通知に失敗しました: thread=%s", thread.id)
            return

        try:
            await thread.send(
                messages.escalation_notice(role.id),
                embed=embeds.thread_embed(escalation),
                allowed_mentions=engineer_mentor_only(role),
            )
        except discord.HTTPException:
            logger.exception("投稿内への通知に失敗しました: thread=%s", thread.id)

    async def _notify_queue(
        self,
        escalation: Escalation,
        thread: discord.Thread,
    ) -> None:
        """キューチャンネルへ 1 件流す(SPEC §5.3)。

        こちらではメンションしない。投稿内で既に呼んでいるため、
        二重に 6 人へ通知するのは過剰。
        """
        channel = self._queue_channel()
        if channel is None:
            logger.error("MENTOR_QUEUE_CHANNEL_ID が未設定か、チャンネルが見つかりません")
            return

        from src.escalation.views import QueueView

        try:
            message = await channel.send(
                embed=embeds.queue_embed(escalation, thread_url=thread.jump_url),
                view=QueueView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("キューへの通知に失敗しました: escalation=%s", escalation.id)
            return

        await self.store.set_queue_message(escalation.id, message.id)

    async def _apply_tag(self, thread: discord.Thread) -> None:
        """「要対応」タグを付ける(SPEC §5.3)。参加者からも状態が見える。"""
        tag = self._needs_attention_tag(thread)
        if tag is None:
            logger.warning(
                "「%s」タグが見つかりません: forum=%s",
                self.config.needs_attention_tag_name,
                thread.parent_id,
            )
            return
        if tag in thread.applied_tags:
            return
        try:
            # Discord の上限は 1 投稿 5 タグ
            await thread.edit(applied_tags=[*thread.applied_tags, tag][:5])
        except discord.HTTPException:
            logger.exception("タグの付与に失敗しました: thread=%s", thread.id)

    async def _remove_tag(self, thread: discord.Thread) -> None:
        tag = self._needs_attention_tag(thread)
        if tag is None or tag not in thread.applied_tags:
            return
        try:
            await thread.edit(
                applied_tags=[t for t in thread.applied_tags if t.id != tag.id]
            )
        except discord.HTTPException:
            logger.exception("タグの解除に失敗しました: thread=%s", thread.id)

    # --- 「対応する」/「完了」---------------------------------------------

    async def handle_claim(self, interaction: discord.Interaction) -> None:
        escalation = await self._from_interaction(interaction)
        if escalation is None:
            return

        if escalation.status is Status.DONE:
            await self._ephemeral(interaction, messages.CLAIM_NOT_OPEN)
            return

        handler = interaction.user
        claimed = await self.store.claim(
            escalation.id,
            handler_id=handler.id,
            handler_name=handler.display_name,
            timeout_minutes=self.config.escalation_timeout_minutes,
        )

        if not claimed:
            # 競合した。後から押した人にだけ知らせる(SPEC §5.3)
            current = await self.store.get(escalation.id)
            name = (current.handler_name if current else None) or "他のメンター"
            await self._ephemeral(interaction, messages.CLAIM_TAKEN.format(handler=name))
            return

        await self._refresh_queue_message(interaction, escalation.id)
        await self._ephemeral(
            interaction,
            messages.CLAIM_SUCCESS.format(minutes=self.config.escalation_timeout_minutes),
        )

    async def handle_complete(self, interaction: discord.Interaction) -> None:
        escalation = await self._from_interaction(interaction)
        if escalation is None:
            return

        completed = await self.store.complete(escalation.id)
        if not completed:
            await self._ephemeral(interaction, messages.COMPLETE_NOT_IN_PROGRESS)
            return

        await self._refresh_queue_message(interaction, escalation.id)
        thread = await self._fetch_thread(escalation)
        if thread is not None:
            await self._remove_tag(thread)
        await self._ephemeral(interaction, messages.COMPLETE_SUCCESS)

    # --- 30 分の自動解除(ポーリングから呼ばれる)--------------------------

    async def expire_overdue(self) -> int:
        """対応中のまま期限を過ぎたものを未対応に戻す。

        DB の deadline_at を見る方式にしているのは、
        メモリ上のタイマーがプロセス再起動で消えるため(CLAUDE.md 制約 3)。
        """
        expired = await self.store.expire_overdue()
        for escalation in expired:
            logger.info("対応中の期限切れを未対応へ戻しました: escalation=%s", escalation.id)
            await self._rerender_queue(escalation)
            thread = await self._fetch_thread(escalation)
            if thread is not None:
                try:
                    await thread.send(
                        messages.TIMEOUT_REVERTED.format(
                            minutes=self.config.escalation_timeout_minutes
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except discord.HTTPException:
                    logger.exception("期限切れの通知に失敗: thread=%s", escalation.thread_id)
        return len(expired)

    # --- 補助 --------------------------------------------------------------

    def _engineer_mentor_role(self, guild: discord.Guild) -> discord.Role | None:
        role_id = self.config.engineer_mentor_role_id
        if role_id is None:
            return None
        return guild.get_role(role_id)

    def _queue_channel(self) -> discord.TextChannel | None:
        channel_id = self.config.mentor_queue_channel_id
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    def _needs_attention_tag(self, thread: discord.Thread) -> discord.ForumTag | None:
        parent = thread.parent
        if not isinstance(parent, discord.ForumChannel):
            return None
        wanted = self.config.needs_attention_tag_name
        return next((t for t in parent.available_tags if t.name == wanted), None)

    async def _fetch_thread(self, escalation: Escalation) -> discord.Thread | None:
        channel = self.bot.get_channel(escalation.thread_id)
        if isinstance(channel, discord.Thread):
            return channel
        try:
            fetched = await self.bot.fetch_channel(escalation.thread_id)
        except discord.HTTPException:
            logger.warning("投稿を取得できませんでした: thread=%s", escalation.thread_id)
            return None
        return fetched if isinstance(fetched, discord.Thread) else None

    async def _from_interaction(
        self, interaction: discord.Interaction
    ) -> Escalation | None:
        """押されたキューのメッセージから案件を引く。"""
        if interaction.message is None:
            await self._ephemeral(interaction, messages.ESCALATION_FAILED)
            return None
        escalation = await self.store.get_by_queue_message(interaction.message.id)
        if escalation is None:
            logger.warning(
                "キューのメッセージに対応する案件がありません: message=%s",
                interaction.message.id,
            )
            await self._ephemeral(interaction, messages.ESCALATION_FAILED)
            return None
        return escalation

    async def _refresh_queue_message(
        self,
        interaction: discord.Interaction,
        escalation_id: int,
    ) -> None:
        """押された本人の interaction を使ってキューの表示を更新する。"""
        updated = await self.store.get(escalation_id)
        if updated is None or interaction.message is None:
            return
        thread = await self._fetch_thread(updated)
        embed = embeds.queue_embed(
            updated, thread_url=thread.jump_url if thread else None
        )
        try:
            await interaction.message.edit(embed=embed)
        except discord.HTTPException:
            logger.exception("キューの表示更新に失敗: escalation=%s", escalation_id)

    async def _rerender_queue(self, escalation: Escalation) -> None:
        """interaction を持たない場面(自動解除)でキューを更新する。"""
        channel = self._queue_channel()
        if channel is None or escalation.queue_message_id is None:
            return
        try:
            message = await channel.fetch_message(escalation.queue_message_id)
        except discord.HTTPException:
            logger.warning("キューのメッセージを取得できません: %s", escalation.queue_message_id)
            return
        thread = await self._fetch_thread(escalation)
        try:
            await message.edit(
                embed=embeds.queue_embed(
                    escalation, thread_url=thread.jump_url if thread else None
                )
            )
        except discord.HTTPException:
            logger.exception("キューの表示更新に失敗: escalation=%s", escalation.id)

    async def _ephemeral(self, interaction: discord.Interaction, text: str) -> None:
        """押した本人にだけ見せる。競合通知はこれで出す(SPEC §5.3)。"""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            logger.exception("エフェメラル応答に失敗しました")

    async def _followup(self, interaction: discord.Interaction, text: str) -> None:
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            logger.exception("フォローアップ応答に失敗しました")

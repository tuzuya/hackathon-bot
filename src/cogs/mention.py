"""@bot メンションを受け取る Cog。

ここで最も重要なのは **動作範囲の限定**(SPEC §3.3 / CLAUDE.md 制約 5)。
相談室フォーラム内の投稿以外では、LLM を一切呼ばずに誘導文だけ返す。
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from src import messages
from src.config import Config
from src.discord_utils import split_message
from src.llm.client import ClaudeClient, LLMError
from src.llm.conversation import build_messages
from src.scope import ForumScope

logger = logging.getLogger(__name__)

# Bot が自分から送るメッセージでは、メンションを一切解決させない。
# エスカレーション通知(段階4)だけが例外で、そこではロール ID を明示指定する。
NO_MENTIONS = discord.AllowedMentions.none()


class MentionCog(commands.Cog):
    """メンションに反応する。"""

    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        llm: ClaudeClient | None,
        system_prompt: str | None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.llm = llm
        self.system_prompt = system_prompt
        self.scope = ForumScope(
            channel_ids=config.forum_channel_ids,
            channel_name=config.forum_channel_name,
        )

    # --- 判定 -------------------------------------------------------------

    def _is_consultation_thread(self, channel: discord.abc.MessageableChannel) -> bool:
        """相談室フォーラム内の投稿(スレッド)かどうか。"""
        return self.scope.matches_thread(channel)

    def _is_consultation_forum_root(self, channel: discord.abc.MessageableChannel) -> bool:
        """フォーラムチャンネル本体(投稿の外)かどうか。"""
        return self.scope.matches_forum(channel)

    def _mentions_bot(self, message: discord.Message) -> bool:
        """Bot 本人へのメンションが含まれるか。

        @everyone / @here は「全員宛て」であって Bot を呼んだわけではないので除外する。
        ロールメンション経由の反応もさせない(意図しない大量呼び出しを防ぐため)。
        """
        if self.bot.user is None:
            return False
        if message.mention_everyone:
            return False
        return any(user.id == self.bot.user.id for user in message.mentions)

    # --- 本体 -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Bot 自身や他の Bot には反応しない(無限ループ防止)
        if message.author.bot:
            return
        # DM には反応しない
        if message.guild is None:
            return
        if not self._mentions_bot(message):
            return

        try:
            if self._is_consultation_thread(message.channel):
                await self._handle_question(message)
            elif self._is_consultation_forum_root(message.channel):
                await message.reply(
                    messages.forum_root_guide(self.config.forum_channel_name),
                    allowed_mentions=NO_MENTIONS,
                    mention_author=False,
                )
            else:
                # 範囲外。LLM は呼ばない(コスト事故の防止も兼ねる)
                logger.info(
                    "範囲外でメンションされました: channel=%s (%s)",
                    message.channel.id,
                    type(message.channel).__name__,
                )
                await message.reply(
                    messages.out_of_scope_guide(self.config.forum_channel_name),
                    allowed_mentions=NO_MENTIONS,
                    mention_author=False,
                )
        except discord.HTTPException:
            # 返信にすら失敗した場合。ここで落とすと Bot 全体が不安定になる
            logger.exception("メンションへの返信に失敗しました")

    async def _handle_question(self, message: discord.Message) -> None:
        """相談室の投稿内で呼ばれたときの処理。"""
        thread = message.channel
        assert isinstance(thread, discord.Thread)  # _is_consultation_thread で確認済み

        logger.info("質問を受信: thread=%s author=%s", thread.id, message.author.id)

        if self.llm is None or self.system_prompt is None:
            # ANTHROPIC_API_KEY が未設定(段階1の状態)。無言で落ちない
            await self._send(thread, message, messages.STAGE1_PLACEHOLDER)
            return

        await self._add_reaction(message)
        try:
            try:
                history = await build_messages(
                    thread,
                    self.bot.user.id,  # type: ignore[union-attr]
                    limit=self.config.history_limit,
                )
                if not history:
                    logger.warning("履歴が空でした: thread=%s", thread.id)
                    await self._send(thread, message, messages.UNEXPECTED_ERROR)
                    return
                answer = await self.llm.answer(self.system_prompt, history)
            except LLMError:
                logger.exception("LLM の呼び出しに失敗しました: thread=%s", thread.id)
                await self._send(thread, message, messages.LLM_ERROR)
                return
            except Exception:
                logger.exception("想定外のエラー: thread=%s", thread.id)
                await self._send(thread, message, messages.UNEXPECTED_ERROR)
                return

            await self._send(thread, message, answer)
        finally:
            await self._remove_reaction(message)

    async def _add_reaction(self, message: discord.Message) -> None:
        """受け取った合図。失敗しても回答は続行する。

        ⚠️ 合図は装飾であって本質ではない。
           ここで例外を投げると、回答そのものが届かなくなる。
           実際に typing 表示のレート制限(429)で回答が止まる事故が起きた。
        """
        try:
            await message.add_reaction(messages.RECEIVED_REACTION)
        except discord.HTTPException as exc:
            logger.warning("リアクションを付けられませんでした(回答は継続): %s", exc)

    async def _remove_reaction(self, message: discord.Message) -> None:
        """回答を出したので合図を消す。失敗しても無視する。"""
        if self.bot.user is None:
            return
        try:
            await message.remove_reaction(messages.RECEIVED_REACTION, self.bot.user)
        except discord.HTTPException:
            logger.debug("リアクションを外せませんでした: message=%s", message.id)

    async def _send(
        self,
        thread: discord.Thread,
        original: discord.Message,
        text: str,
    ) -> None:
        """回答を送る。2000 字を超える場合は分割する。

        1通目だけ質問への返信にし、2通目以降はスレッドに続けて流す。
        """
        chunks = split_message(text)
        for index, chunk in enumerate(chunks):
            if index == 0:
                await original.reply(
                    chunk,
                    allowed_mentions=NO_MENTIONS,
                    mention_author=False,
                )
            else:
                await thread.send(chunk, allowed_mentions=NO_MENTIONS)


async def setup(bot: commands.Bot) -> None:
    """discord.py の拡張ロード用エントリポイント。"""
    await bot.add_cog(
        MentionCog(
            bot,
            bot.config,  # type: ignore[attr-defined]
            bot.llm,  # type: ignore[attr-defined]
            bot.system_prompt,  # type: ignore[attr-defined]
        )
    )

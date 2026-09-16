"""初回案内の一括投稿(SPEC §11-G)。

22 チーム分の相談室に手で貼るのは現実的でないので、コマンドで一括投稿する。

「答えられること / 答えられないこと」を最初に明示すると範囲外の質問が減り、
「1つの投稿では1つの質問」を周知すると文脈の混ざりが減る(SPEC §3.4)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import discord

from src import messages

logger = logging.getLogger(__name__)


@dataclass
class PostResult:
    posted: int = 0
    skipped: int = 0
    failed: int = 0


async def already_posted(forum: discord.ForumChannel) -> bool:
    """すでに案内が貼られているか。二重投稿を防ぐ。

    フォーラムの投稿一覧を見て、同じタイトルのものがあるかで判定する。
    """
    for thread in forum.threads:
        if thread.name == messages.GUIDE_THREAD_TITLE:
            return True
    try:
        async for thread in forum.archived_threads(limit=50):
            if thread.name == messages.GUIDE_THREAD_TITLE:
                return True
    except discord.HTTPException:
        logger.warning("アーカイブ済み投稿の確認に失敗: forum=%s", forum.id)
    return False


async def post_guides(
    forums: list[discord.ForumChannel],
    *,
    bot_mention: str,
) -> PostResult:
    """各フォーラムに案内の投稿を作り、先頭に固定する。"""
    result = PostResult()
    body = messages.guide_body(bot_mention)

    for forum in forums:
        if await already_posted(forum):
            result.skipped += 1
            continue
        try:
            created = await forum.create_thread(
                name=messages.GUIDE_THREAD_TITLE,
                content=body,
                # 案内文に書かれた Bot メンションで通知を飛ばさない
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("案内の投稿に失敗: forum=%s", forum.id)
            result.failed += 1
            continue

        result.posted += 1
        # 一覧の先頭に出るように固定する。後から来た人が見つけやすい
        try:
            await created.thread.edit(pinned=True)
        except discord.HTTPException:
            logger.warning("案内の固定に失敗: forum=%s", forum.id)

    return result


class ConfirmView(discord.ui.View):
    """22 チームへ一斉投稿するので、押す前に一度確認する。

    取り消しがきかない操作なので、確認なしで実行できるようにはしない。
    """

    def __init__(self, forums: list[discord.ForumChannel], bot_mention: str) -> None:
        super().__init__(timeout=120)
        self._forums = forums
        self._bot_mention = bot_mention

    @discord.ui.button(
        label=messages.GUIDE_BUTTON_LABEL, style=discord.ButtonStyle.primary
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await post_guides(self._forums, bot_mention=self._bot_mention)
        await interaction.followup.send(
            messages.GUIDE_RESULT.format(
                posted=result.posted, skipped=result.skipped, failed=result.failed
            ),
            ephemeral=True,
        )
        self.stop()

    @discord.ui.button(
        label=messages.GUIDE_CANCEL_LABEL, style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            messages.GUIDE_CANCELLED, ephemeral=True
        )
        self.stop()

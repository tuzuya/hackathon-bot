"""ボタン(永続 View)。

⚠️ Bot 再起動後もボタンが機能するよう、以下を守る(CLAUDE.md「ボタン(Views)」):
   - timeout=None
   - custom_id を固定文字列にする
   - 起動時に bot.add_view() で再登録する

custom_id を固定にできるのは、押されたときの文脈を interaction から引けるため。
  - 「解決しなかった」… interaction.channel が対象の投稿
  - 「対応する」/「完了」… interaction.message.id からキューの行を引く
案件 ID を custom_id に埋め込む必要がない。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src import messages

if TYPE_CHECKING:
    from src.escalation.service import EscalationService

logger = logging.getLogger(__name__)

UNRESOLVED_CUSTOM_ID = "escalation:unresolved"
CLAIM_CUSTOM_ID = "escalation:claim"
COMPLETE_CUSTOM_ID = "escalation:complete"


class UnresolvedView(discord.ui.View):
    """Bot の回答末尾に常時付ける「解決しなかった」ボタン(SPEC §5.1)。

    「解決した」ボタンは設けない。押されなければ何もしない。
    """

    def __init__(self, service: EscalationService) -> None:
        super().__init__(timeout=None)
        self._service = service

    @discord.ui.button(
        label=messages.UNRESOLVED_BUTTON_LABEL,
        style=discord.ButtonStyle.secondary,
        custom_id=UNRESOLVED_CUSTOM_ID,
    )
    async def unresolved(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._service.handle_unresolved(interaction)


class QueueView(discord.ui.View):
    """キューチャンネルの通知に付けるボタン(SPEC §5.3)。"""

    def __init__(self, service: EscalationService) -> None:
        super().__init__(timeout=None)
        self._service = service

    @discord.ui.button(
        label=messages.CLAIM_BUTTON_LABEL,
        style=discord.ButtonStyle.primary,
        custom_id=CLAIM_CUSTOM_ID,
    )
    async def claim(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._service.handle_claim(interaction)

    @discord.ui.button(
        label=messages.COMPLETE_BUTTON_LABEL,
        style=discord.ButtonStyle.success,
        custom_id=COMPLETE_CUSTOM_ID,
    )
    async def complete(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._service.handle_complete(interaction)

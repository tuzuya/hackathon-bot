"""ナレッジ化の承認フロー(SPEC §4.2)。

**エスカレーションされた案件だけ**を運営チャンネルへ流す。
全質疑を流すと 22 チーム分で 1 日数百件になり、運営が見きれない。
ナレッジ化したいのは「Bot が答えられなかった質問」なので、
エスカレーションはその絞り込みとしてちょうどよい。

個人情報は自動で消さない。運営が判断する時点ではユーザー名・チーム名が
見えている方がよいため、**昇格時に運営が手で消す**(SPEC §4.2)。
そのため、押すと内容を編集できるモーダルを開く。
"""

from __future__ import annotations

import logging

import discord

from src import messages
from src.escalation.models import Escalation
from src.knowledge.store import CATEGORIES, SOURCE_QA, KnowledgeStore

logger = logging.getLogger(__name__)

APPROVE_CUSTOM_ID = "knowledge:approve"
MODAL_CUSTOM_ID = "knowledge:modal"

# Discord のモーダル入力は 4000 文字まで
MAX_MODAL_LENGTH = 4000


def candidate_embed(escalation: Escalation) -> discord.Embed:
    embed = discord.Embed(
        title=messages.KNOWLEDGE_CANDIDATE_TITLE,
        description=messages.KNOWLEDGE_CANDIDATE_HINT,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="チーム", value=escalation.team_name, inline=True)
    embed.add_field(
        name="やり取り",
        value=escalation.summary[:1024],
        inline=False,
    )
    embed.set_footer(text=f"escalation #{escalation.id}")
    return embed


class KnowledgeModal(discord.ui.Modal):
    """運営が内容を整えてから登録するためのモーダル。"""

    def __init__(self, store: KnowledgeStore, on_saved, *, draft: str) -> None:
        super().__init__(title="共有ナレッジに追加", custom_id=MODAL_CUSTOM_ID)
        self._store = store
        self._on_saved = on_saved

        self.category = discord.ui.TextInput(
            label="カテゴリ",
            default="運営情報",
            placeholder=" / ".join(CATEGORIES),
            max_length=20,
        )
        self.content = discord.ui.TextInput(
            label="内容(チーム名・個人名はここで消してください)",
            style=discord.TextStyle.paragraph,
            default=draft[:MAX_MODAL_LENGTH],
            max_length=MAX_MODAL_LENGTH,
        )
        self.add_item(self.category)
        self.add_item(self.content)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        entry_id = await self._store.add(
            category=str(self.category.value).strip() or "運営情報",
            content=str(self.content.value),
            source=SOURCE_QA,
            added_by=interaction.user.id,
            added_by_name=interaction.user.display_name,
        )
        ok = await self._on_saved()
        text = messages.KNOWLEDGE_ADDED.format(entry_id=entry_id)
        if not ok:
            text = messages.KNOWLEDGE_RELOAD_FAILED
        await interaction.response.send_message(text, ephemeral=True)


class ApprovalView(discord.ui.View):
    """Bot管理チャンネルの通知に付ける「共有ナレッジに追加」ボタン。"""

    def __init__(self, store: KnowledgeStore, on_saved) -> None:
        super().__init__(timeout=None)
        self._store = store
        self._on_saved = on_saved

    @discord.ui.button(
        label=messages.ADD_KNOWLEDGE_BUTTON_LABEL,
        style=discord.ButtonStyle.primary,
        custom_id=APPROVE_CUSTOM_ID,
    )
    async def approve(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        draft = ""
        if interaction.message and interaction.message.embeds:
            for field in interaction.message.embeds[0].fields:
                if field.name == "やり取り" and field.value:
                    draft = field.value
                    break
        await interaction.response.send_modal(
            KnowledgeModal(self._store, self._on_saved, draft=draft)
        )

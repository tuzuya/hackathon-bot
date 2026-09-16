"""エスカレーション通知の embed 組み立て。

⚠️ LLM が生成した要約は必ず embed に入れる(本文には入れない)。
   embed の中身はメンションとして解決されないため、
   「@メンター」という文字列が紛れ込んでも通知は飛ばない(CLAUDE.md 制約 1)。
"""

from __future__ import annotations

from datetime import UTC
from zoneinfo import ZoneInfo

import discord

from src import messages
from src.escalation.models import STATUS_LABELS, Escalation, Status

JST = ZoneInfo("Asia/Tokyo")

STATUS_COLORS: dict[Status, discord.Color] = {
    Status.UNHANDLED: discord.Color.red(),
    Status.IN_PROGRESS: discord.Color.gold(),
    Status.DONE: discord.Color.green(),
}

# embed の 1 フィールドは 1024 文字まで
MAX_FIELD_LENGTH = 1024


def _clip(text: str) -> str:
    if len(text) <= MAX_FIELD_LENGTH:
        return text
    return text[: MAX_FIELD_LENGTH - 3] + "..."


def _summary_body(escalation: Escalation) -> str:
    if escalation.summary_failed:
        return _clip(f"{messages.SUMMARY_FALLBACK_NOTICE}\n\n{escalation.summary}")
    return _clip(escalation.summary)


def thread_embed(escalation: Escalation) -> discord.Embed:
    """投稿内に出す、メンター向けの申し送り。

    SPEC §5.1 より、チーム名・ユーザー名・投稿リンクは入れない(その場で見えるため)。
    """
    embed = discord.Embed(
        title="ここまでのやり取り",
        description=_summary_body(escalation),
        color=STATUS_COLORS[Status.UNHANDLED],
    )
    return embed


def queue_embed(escalation: Escalation, *, thread_url: str | None) -> discord.Embed:
    """キューチャンネルに出す通知(SPEC §5.3)。

    こちらはチーム名と投稿リンクが必須。ここから投稿へ飛ぶため。
    """
    embed = discord.Embed(
        title=f"{STATUS_LABELS[escalation.status]}  {escalation.team_name}",
        description=_summary_body(escalation),
        color=STATUS_COLORS[escalation.status],
        timestamp=escalation.created_at.astimezone(UTC),
    )
    if thread_url:
        embed.add_field(name="投稿", value=f"[開く]({thread_url})", inline=True)

    if escalation.status is Status.IN_PROGRESS and escalation.handler_name:
        value = escalation.handler_name
        if escalation.deadline_at:
            deadline = escalation.deadline_at.astimezone(JST).strftime("%H:%M")
            value += f"(期限 {deadline})"
        embed.add_field(name="対応中", value=value, inline=True)
    elif escalation.status is Status.DONE and escalation.handler_name:
        embed.add_field(name="対応者", value=escalation.handler_name, inline=True)

    embed.set_footer(text=f"#{escalation.id}")
    return embed

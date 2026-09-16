"""稼働状況の可視化(SPEC §11-F)。

**Bot が無言で死ぬのが最悪のケース。**
メンターが少ない状況で落ちると、参加者は反応を待ち続け、
誰も「Bot が死んでいる」と気づかない。

そこで、運営チャンネルに 1 通だけ状況メッセージを置き、
定期的に「最終更新」を書き換える。表示が古くなっていれば落ちている。

⚠️ 毎回新しいメッセージを投稿しない。数分ごとに通知が増えると
   運営チャンネルが埋まり、やがて誰も見なくなる。
   1 通を編集し続けるので、チャンネルは静かなまま。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

from src import messages

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")
HEARTBEAT_MINUTES = 5
STATE_KEY = "health_message_id"


class HealthCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._message: discord.Message | None = None

    async def cog_load(self) -> None:
        self.heartbeat.start()

    async def cog_unload(self) -> None:
        self.heartbeat.cancel()
        await self._render(running=False)

    # --- 状態の保存(再起動をまたいで同じメッセージを編集する)-------------

    async def _load_message_id(self) -> int | None:
        connection = self.bot.database.connection  # type: ignore[attr-defined]
        async with connection.execute(
            "SELECT value FROM bot_state WHERE key = ?", (STATE_KEY,)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["value"]) if row else None

    async def _save_message_id(self, message_id: int) -> None:
        connection = self.bot.database.connection  # type: ignore[attr-defined]
        await connection.execute(
            "INSERT INTO bot_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (STATE_KEY, str(message_id)),
        )
        await connection.commit()

    def _channel(self) -> discord.TextChannel | None:
        channel_id = self.bot.config.admin_channel_id  # type: ignore[attr-defined]
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    # --- 表示 --------------------------------------------------------------

    def _is_answering(self) -> bool:
        """LLM と知識源が揃っていて、実際に回答できる状態か。"""
        manager = getattr(self.bot, "knowledge_manager", None)
        return getattr(self.bot, "llm", None) is not None and (
            manager is not None and manager.is_ready
        )

    def _embed(self, *, running: bool) -> discord.Embed:
        now = datetime.now(UTC).astimezone(JST)
        if not running:
            state, color = messages.HEALTH_STOPPED, discord.Color.red()
        elif self._is_answering():
            state, color = messages.HEALTH_RUNNING, discord.Color.green()
        else:
            # プロセスは生きているが質問に答えられない。
            # 稼働中と表示すると、誰も異常に気づけない
            state, color = messages.HEALTH_DEGRADED, discord.Color.orange()
        embed = discord.Embed(
            title=messages.HEALTH_TITLE,
            description=state,
            color=color,
        )
        embed.add_field(
            name="最終更新", value=now.strftime("%m/%d %H:%M:%S"), inline=True
        )
        embed.add_field(
            name="更新間隔", value=f"{HEARTBEAT_MINUTES} 分ごと", inline=True
        )
        embed.set_footer(text=messages.HEALTH_HINT)
        return embed

    async def _render(self, *, running: bool) -> None:
        channel = self._channel()
        if channel is None:
            return
        embed = self._embed(running=running)

        if self._message is None:
            message_id = await self._load_message_id()
            if message_id is not None:
                try:
                    self._message = await channel.fetch_message(message_id)
                except discord.HTTPException:
                    self._message = None

        try:
            if self._message is not None:
                await self._message.edit(embed=embed)
                return
            self._message = await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
            await self._save_message_id(self._message.id)
        except discord.HTTPException:
            logger.warning("稼働状況の更新に失敗しました", exc_info=True)

    # --- ループ ------------------------------------------------------------

    @tasks.loop(minutes=HEARTBEAT_MINUTES)
    async def heartbeat(self) -> None:
        try:
            await self._render(running=True)
        except Exception:
            # ここで落とすとループが止まり、以後「更新が止まった」状態になる。
            # それ自体が死亡シグナルとして機能はするが、誤検知は避けたい
            logger.exception("ヘルスチェックの更新に失敗しました")

    @heartbeat.before_loop
    async def before_heartbeat(self) -> None:
        await self.bot.wait_until_ready()
        logger.info("稼働状況の通知を開始しました(%d 分ごと)", HEARTBEAT_MINUTES)

    @heartbeat.error
    async def heartbeat_error(self, error: BaseException) -> None:
        logger.critical("稼働状況のループが停止しました。再開します: %r", error)
        self.heartbeat.restart()


async def setup(bot: commands.Bot) -> None:
    if getattr(bot, "config", None) is None:
        return
    await bot.add_cog(HealthCog(bot))

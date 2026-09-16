"""エスカレーションの Cog。

永続 View の再登録と、30 分タイムアウトのポーリングを持つ。
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from src.escalation.service import EscalationService
from src.escalation.views import QueueView, UnresolvedView

logger = logging.getLogger(__name__)

# ポーリング間隔。30 分の期限に対して 1 分なら十分な精度。
POLL_INTERVAL_SECONDS = 60


class EscalationCog(commands.Cog):
    def __init__(self, bot: commands.Bot, service: EscalationService) -> None:
        self.bot = bot
        self.service = service

    async def cog_load(self) -> None:
        # Bot 再起動後も、既に投稿済みのボタンが動くようにする
        # (CLAUDE.md「ボタン(Views)」)。custom_id が固定なので再登録できる。
        self.bot.add_view(UnresolvedView(self.service))
        self.bot.add_view(QueueView(self.service))
        logger.info("永続 View を再登録しました")
        self.expire_loop.start()

    async def cog_unload(self) -> None:
        self.expire_loop.cancel()

    @tasks.loop(seconds=POLL_INTERVAL_SECONDS)
    async def expire_loop(self) -> None:
        """対応中のまま期限を過ぎたものを未対応へ戻す(SPEC §5.3)。

        DB の deadline_at を見る。asyncio.sleep で持つと再起動で消えるため
        (CLAUDE.md 制約 3)。
        """
        try:
            count = await self.service.expire_overdue()
            if count:
                logger.info("%d 件を未対応へ戻しました", count)
        except Exception:
            # ここで例外を漏らすとループ自体が止まり、以後二度と解除されない
            logger.exception("期限切れの処理に失敗しました")

    @expire_loop.before_loop
    async def before_expire_loop(self) -> None:
        await self.bot.wait_until_ready()
        logger.info(
            "エスカレーションの期限監視を開始しました(%d 秒ごと / 期限 %d 分)",
            POLL_INTERVAL_SECONDS,
            self.service.config.escalation_timeout_minutes,
        )

    @expire_loop.error
    async def expire_loop_error(self, error: BaseException) -> None:
        """ループが死んだら大きく記録して再開する。

        ⚠️ ここが静かに止まると、対応中のまま誰にも気付かれない案件が
           永久に残る。誰も見ていないので、止まったこと自体に気付けない。
        """
        logger.critical(
            "期限監視のループが停止しました。再開します: %r", error, exc_info=error
        )
        self.expire_loop.restart()


async def setup(bot: commands.Bot) -> None:
    service = bot.escalation_service  # type: ignore[attr-defined]
    if service is None:
        logger.warning("エスカレーションは無効です(DB または設定が未準備)")
        return
    await bot.add_cog(EscalationCog(bot, service))

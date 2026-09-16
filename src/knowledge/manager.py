"""システムプロンプトの保持と再構築。

知識が変わったときだけ組み直す。毎リクエスト組み直すと
プロンプトキャッシュが効かなくなる(CLAUDE.md「RAG は実装しない」)。

再構築するとキャッシュは一度無効になるが、
知識の追加は開催中に数回しか起きないので問題にならない。
"""

from __future__ import annotations

import logging

from src.config import Config
from src.knowledge.loader import load_knowledge
from src.knowledge.store import KnowledgeStore
from src.llm.prompt import build_system_prompt

logger = logging.getLogger(__name__)


class KnowledgeManager:
    def __init__(self, config: Config, store: KnowledgeStore) -> None:
        self._config = config
        self._store = store
        self._system_prompt: str | None = None

    @property
    def system_prompt(self) -> str | None:
        return self._system_prompt

    @property
    def is_ready(self) -> bool:
        return self._system_prompt is not None

    async def reload(self) -> None:
        """ファイル由来の知識と DB 由来の知識を結合して組み直す。"""
        knowledge = load_knowledge(self._config.knowledge_dir)
        dynamic = await self._store.render()
        if dynamic:
            # 動的な追加は末尾に置く。優先すべき新しい情報だと示すため
            knowledge = f"{knowledge}\n\n---\n\n{dynamic}"

        self._system_prompt = build_system_prompt(
            self._config.system_prompt_path,
            knowledge,
            self._config.forum_channel_name,
        )
        logger.info(
            "システムプロンプトを構築しました: %d 文字(うち動的追加 %d 文字)",
            len(self._system_prompt),
            len(dynamic),
        )

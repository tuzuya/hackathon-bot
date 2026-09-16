"""Claude API の呼び出し。

CLAUDE.md「エラー処理」より、**LLM 呼び出しは必ず失敗しうる前提で書く**。
このモジュールは例外を LLMError に正規化して投げ、
参加者に何を表示するかは呼び出し側(Cog)が決める。
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from anthropic.types import MessageParam

from src.config import Config

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """Claude API の呼び出しに失敗した。"""


class ClaudeClient:
    """Claude API の薄いラッパー。"""

    def __init__(self, config: Config) -> None:
        if config.anthropic_api_key is None:
            raise LLMError("ANTHROPIC_API_KEY が設定されていません。")
        self._config = config
        # SDK の既定はタイムアウト 10 分・リトライ 2 回。
        # Discord では参加者が画面の前で待っているので、どちらも短くする。
        # 失敗するなら早く失敗して、日本語のメッセージを返した方がよい。
        self._client = anthropic.AsyncAnthropic(
            api_key=config.anthropic_api_key,
            timeout=config.llm_timeout_seconds,
            max_retries=config.llm_max_retries,
        )

    async def close(self) -> None:
        await self._client.close()

    async def answer_with_usage(
        self, system_prompt: str, messages: list[MessageParam]
    ) -> tuple[str, Any]:
        """回答と usage を返す。usage は質問ログとコスト監視に使う。"""
        return await self._create(
            model=self._config.anthropic_model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=self._config.anthropic_max_tokens,
            effort=self._config.anthropic_effort,
            cache_system=True,
            return_usage=True,
            allow_fallback=True,
        )

    async def answer(self, system_prompt: str, messages: list[MessageParam]) -> str:
        """参加者への回答を生成する。

        system_prompt は起動時に組み立てた固定文字列で、毎回同じものが渡る。
        これにプロンプトキャッシュのブレークポイントを置く(知識源の直後に相当する)。
        """
        return await self._create(
            model=self._config.anthropic_model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=self._config.anthropic_max_tokens,
            effort=self._config.anthropic_effort,
            cache_system=True,
            allow_fallback=True,
        )

    async def summarize(self, system_prompt: str, messages: list[MessageParam]) -> str:
        """エスカレーション用の要約を生成する(段階4で使う)。

        軽い処理なので下位モデルに振る(SPEC §9.1)。
        知識源を渡さないのでキャッシュはしない。

        ⚠️ allow_fallback=False。拒否時フォールバックは Opus / Fable 系専用で、
           Haiku に付けると 400 になる:
           'claude-haiku-4-5' does not support the `fallbacks` parameter.
           要約が毎回失敗し、メンターが生ログを読まされる事故になった。
        """
        return await self._create(
            model=self._config.anthropic_summary_model,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=self._config.summary_max_tokens,
            effort=None,
            cache_system=False,
            allow_fallback=False,
        )

    async def _create(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[MessageParam],
        max_tokens: int,
        effort: str | None,
        cache_system: bool,
        return_usage: bool = False,
        allow_fallback: bool = False,
    ) -> Any:
        system: Any = system_prompt
        if cache_system:
            # 知識源を含む固定部分の直後にキャッシュのブレークポイントを置く。
            # 可変部分(会話履歴)は messages[] にあり、system より後に並ぶので影響しない。
            system = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if effort is not None:
            kwargs["output_config"] = {"effort": effort}

        try:
            if allow_fallback and self._config.anthropic_fallback_model:
                # 安全上の判断で応答が拒否された場合に、同一リクエスト内で
                # 別モデルに引き継がせる。無言で止まるのを防ぐための保険。
                response = await self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-06-01"],
                    fallbacks=[{"model": self._config.anthropic_fallback_model}],
                    **kwargs,
                )
            else:
                response = await self._client.messages.create(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("ANTHROPIC_API_KEY が無効です。") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic API のレート制限に達しました。") from exc
        except anthropic.BadRequestError as exc:
            # 400 はリクエストの組み立てミス。原因が分からないと直せないので本文を残す。
            hint = ""
            if allow_fallback and self._config.anthropic_fallback_model:
                hint = (
                    " / ANTHROPIC_FALLBACK_MODEL を空にすると"
                    "拒否時フォールバック(ベータ機能)を無効化できます"
                )
            raise LLMError(f"リクエストが拒否されました (400): {exc.message}{hint}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"Anthropic API がエラーを返しました (status={exc.status_code}): {exc.message}"
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("Anthropic API がタイムアウトしました。") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Anthropic API に接続できませんでした。") from exc

        self._log_usage(model, response)

        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            raise LLMError(f"モデルが応答を拒否しました (category={detail})。")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
        if not text:
            raise LLMError(f"モデルが空の応答を返しました (stop_reason={response.stop_reason})。")
        if return_usage:
            return text, getattr(response, "usage", None)
        return text

    @staticmethod
    def _log_usage(model: str, response: Any) -> None:
        """トークン使用量を記録する。

        cache_read が常に 0 なら、システムプロンプトのどこかが
        リクエストごとに変わっている(キャッシュが効いていない)。
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        logger.info(
            "LLM 使用量 model=%s input=%s cache_write=%s cache_read=%s output=%s",
            model,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "cache_creation_input_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
        )

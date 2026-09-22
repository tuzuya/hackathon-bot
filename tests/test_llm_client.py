"""Claude API 呼び出しのテスト。

要約モデル(Haiku)に回答用のパラメータを付けると 400 になり、
要約が毎回失敗してメンターが生ログを読まされる事故が実際に起きた。
その再発防止。
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Config
from src.llm.client import ClaudeClient


# テストは .env の内容に左右されてはいけない。
# 実際、ENABLE_PROMPT_CACHE=false を .env に置いた状態でテストが壊れた。
TEST_DEFAULTS = {
    "anthropic_base_url": None,
    "enable_prompt_cache": True,
    "enable_image_input": True,
    "anthropic_model": "claude-opus-5",
    "anthropic_summary_model": "claude-haiku-4-5",
    "anthropic_fallback_model": "claude-opus-4-8",
    "anthropic_effort": "low",
}


def _config(**overrides) -> Config:
    import os

    os.environ.update({"DISCORD_TOKEN": "x", "ANTHROPIC_API_KEY": "sk-test"})
    config = Config.load()
    for key, value in {**TEST_DEFAULTS, **overrides}.items():
        object.__setattr__(config, key, value)
    return config


def _fake_response() -> MagicMock:
    response = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = "回答"
    response.content = [block]
    response.stop_reason = "end_turn"
    response.usage = MagicMock(
        input_tokens=1, output_tokens=1,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    )
    return response


class FallbackScopeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = _config(
            anthropic_fallback_model="claude-opus-4-8",
            anthropic_model="claude-opus-5",
            anthropic_summary_model="claude-haiku-4-5",
        )
        with patch("anthropic.AsyncAnthropic"):
            self.client = ClaudeClient(self.config)
        self.client._client = MagicMock()
        self.client._client.messages.create = AsyncMock(return_value=_fake_response())
        self.client._client.beta.messages.create = AsyncMock(
            return_value=_fake_response()
        )

    async def test_回答には拒否時フォールバックを付ける(self) -> None:
        await self.client.answer("system", [{"role": "user", "content": "q"}])
        self.client._client.beta.messages.create.assert_awaited_once()
        kwargs = self.client._client.beta.messages.create.await_args.kwargs
        self.assertIn("fallbacks", kwargs)

    async def test_要約には拒否時フォールバックを付けない(self) -> None:
        # Haiku は fallbacks に対応しておらず、付けると 400 になる
        await self.client.summarize("system", [{"role": "user", "content": "q"}])
        self.client._client.beta.messages.create.assert_not_awaited()
        self.client._client.messages.create.assert_awaited_once()
        kwargs = self.client._client.messages.create.await_args.kwargs
        self.assertNotIn("fallbacks", kwargs)
        self.assertNotIn("betas", kwargs)

    async def test_要約は要約用モデルを使う(self) -> None:
        await self.client.summarize("system", [{"role": "user", "content": "q"}])
        kwargs = self.client._client.messages.create.await_args.kwargs
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")

    async def test_要約はキャッシュしない(self) -> None:
        # 知識源を渡さないので、キャッシュしても意味がない
        await self.client.summarize("system", [{"role": "user", "content": "q"}])
        kwargs = self.client._client.messages.create.await_args.kwargs
        self.assertIsInstance(kwargs["system"], str)

    async def test_回答はキャッシュのブレークポイントを置く(self) -> None:
        await self.client.answer("system", [{"role": "user", "content": "q"}])
        kwargs = self.client._client.beta.messages.create.await_args.kwargs
        self.assertEqual(
            kwargs["system"][0]["cache_control"], {"type": "ephemeral"}
        )

    async def test_フォールバック未設定なら通常エンドポイントを使う(self) -> None:
        config = _config(anthropic_fallback_model=None)
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeClient(config)
        client._client = MagicMock()
        client._client.messages.create = AsyncMock(return_value=_fake_response())
        client._client.beta.messages.create = AsyncMock()
        await client.answer("system", [{"role": "user", "content": "q"}])
        client._client.beta.messages.create.assert_not_awaited()


class BaseUrlAndCacheTest(unittest.IsolatedAsyncioTestCase):
    """Anthropic 形式の別サービス(DeepSeek など)へ切り替えられること。

    DeepSeek の互換エンドポイントは cache_control と fallbacks に未対応。
    そのまま送ると 400 になるので、設定で切れなければならない。
    """

    def _client(self, **overrides) -> ClaudeClient:
        config = _config(**overrides)
        with patch("anthropic.AsyncAnthropic"):
            client = ClaudeClient(config)
        client._client = MagicMock()
        client._client.messages.create = AsyncMock(return_value=_fake_response())
        client._client.beta.messages.create = AsyncMock(return_value=_fake_response())
        return client

    async def test_キャッシュ無効なら文字列で送る(self) -> None:
        client = self._client(
            enable_prompt_cache=False, anthropic_fallback_model=None
        )
        await client.answer("system", [{"role": "user", "content": "q"}])
        kwargs = client._client.messages.create.await_args.kwargs
        self.assertIsInstance(kwargs["system"], str)

    async def test_キャッシュ有効ならブロックで送る(self) -> None:
        client = self._client(
            enable_prompt_cache=True, anthropic_fallback_model=None
        )
        await client.answer("system", [{"role": "user", "content": "q"}])
        kwargs = client._client.messages.create.await_args.kwargs
        self.assertIsInstance(kwargs["system"], list)
        self.assertIn("cache_control", kwargs["system"][0])

    async def test_base_url_を指定してクライアントを作れる(self) -> None:
        config = _config(anthropic_base_url="https://api.deepseek.com/anthropic")
        with patch("anthropic.AsyncAnthropic") as ctor:
            ClaudeClient(config)
        self.assertEqual(
            ctor.call_args.kwargs["base_url"], "https://api.deepseek.com/anthropic"
        )

    async def test_base_url_未指定なら渡さない(self) -> None:
        config = _config(anthropic_base_url=None)
        with patch("anthropic.AsyncAnthropic") as ctor:
            ClaudeClient(config)
        self.assertNotIn("base_url", ctor.call_args.kwargs)

    async def test_DeepSeek想定の設定では互換性の無い指定を送らない(self) -> None:
        # 実際の移行設定。cache_control も fallbacks も乗ってはいけない
        client = self._client(
            anthropic_base_url="https://api.deepseek.com/anthropic",
            enable_prompt_cache=False,
            anthropic_fallback_model=None,
            anthropic_model="deepseek-flash",
        )
        await client.answer("system", [{"role": "user", "content": "q"}])
        client._client.beta.messages.create.assert_not_awaited()
        kwargs = client._client.messages.create.await_args.kwargs
        self.assertIsInstance(kwargs["system"], str)
        self.assertNotIn("fallbacks", kwargs)
        self.assertNotIn("betas", kwargs)
        self.assertEqual(kwargs["model"], "deepseek-flash")

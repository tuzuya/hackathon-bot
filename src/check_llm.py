"""プロンプトキャッシュが実際に効いているかを確認する。

    python -m src.check_llm

同じシステムプロンプトで Claude API を **2 回** 呼び、2 回目でキャッシュが
読まれるかを確かめる。1 回目は書き込み、2 回目は読み込みになるのが正常。

⚠️ 実際に API を呼ぶので、少額(合計 $0.1 程度)の費用がかかる。
"""

from __future__ import annotations

import asyncio
import sys

import anthropic

from src.config import Config, ConfigError
from src.knowledge.loader import load_knowledge
from src.llm.prompt import build_system_prompt

OK = "✅"
NG = "❌"

# 参考値(1M トークンあたりの USD)。表示用の概算なので厳密な請求額ではない。
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _usage_line(label: str, usage: object) -> dict[str, int]:
    values = {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
    }
    print(
        f"  {label}: 通常入力={values['input']:,} / キャッシュ書込={values['cache_write']:,} "
        f"/ キャッシュ読込={values['cache_read']:,} / 出力={values['output']:,}"
    )
    return values


def _estimate_cost(model: str, values: dict[str, int]) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    in_price, out_price = price
    # キャッシュ書込は 1.25 倍、読込は 0.1 倍
    cost = (
        values["input"] * in_price
        + values["cache_write"] * in_price * 1.25
        + values["cache_read"] * in_price * 0.1
        + values["output"] * out_price
    ) / 1_000_000
    return cost


async def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"{NG} 設定エラー: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if config.anthropic_api_key is None:
        print(f"{NG} ANTHROPIC_API_KEY が設定されていません。", file=sys.stderr)
        raise SystemExit(1)

    knowledge = load_knowledge(config.knowledge_dir)
    system_prompt = build_system_prompt(
        config.system_prompt_path, knowledge, config.forum_channel_name
    )
    print(f"\nシステムプロンプト: {len(system_prompt):,} 文字 / モデル: {config.anthropic_model}")
    print(f"接続先: {config.anthropic_base_url or 'https://api.anthropic.com(既定)'}")
    print(f"キャッシュ指定: {'あり' if config.enable_prompt_cache else 'なし'}")
    print(f"拒否時フォールバック: {config.anthropic_fallback_model or '(無効)'}\n")

    client = anthropic.AsyncAnthropic(
        api_key=config.anthropic_api_key,
        **({"base_url": config.anthropic_base_url} if config.anthropic_base_url else {}),
    )

    async def call(question: str) -> object:
        kwargs = {
            "model": config.anthropic_model,
            "max_tokens": 64,  # 出力は最小限にして、入力側の挙動だけを見る
            "system": (
                [
                    {
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                if config.enable_prompt_cache
                else system_prompt
            ),
            "messages": [{"role": "user", "content": question}],
            "output_config": {"effort": config.anthropic_effort},
        }
        if config.anthropic_fallback_model:
            return await client.beta.messages.create(
                betas=["server-side-fallback-2026-06-01"],
                fallbacks=[{"model": config.anthropic_fallback_model}],
                **kwargs,
            )
        return await client.messages.create(**kwargs)

    try:
        print("1 回目(キャッシュへの書き込みが起きるはず)")
        first = await call("テスト1です。「はい」とだけ答えて。")
        v1 = _usage_line("usage", first.usage)  # type: ignore[attr-defined]

        print("\n2 回目(キャッシュからの読み込みが起きるはず)")
        second = await call("テスト2です。別の質問だけど「はい」とだけ答えて。")
        v2 = _usage_line("usage", second.usage)  # type: ignore[attr-defined]
    except anthropic.APIStatusError as exc:
        print(f"\n{NG} API エラー (status={exc.status_code}): {exc.message}", file=sys.stderr)
        if config.anthropic_fallback_model:
            print(
                "   ANTHROPIC_FALLBACK_MODEL を空にして再実行すると切り分けられます。",
                file=sys.stderr,
            )
        raise SystemExit(1) from exc
    finally:
        await client.close()

    print("\n── 判定 ──")
    if v2["cache_read"] > 0:
        print(f"{OK} キャッシュが効いています(2 回目で {v2['cache_read']:,} トークンを読み込み)")
    elif v1["cache_write"] > 0:
        print(f"{NG} 書き込みは起きているのに、2 回目で読み込まれていません。")
        print("   連続で呼んでもこうなる場合、システムプロンプトがリクエストごとに")
        print("   変わっている可能性があります。")
    else:
        print(f"{NG} キャッシュがまったく使われていません(書き込みも読み込みも 0)。")
        print("   cache_control がリクエストに乗っていないか、")
        print("   プロンプトが最小キャッシュサイズに届いていない可能性があります。")

    c1 = _estimate_cost(config.anthropic_model, v1)
    c2 = _estimate_cost(config.anthropic_model, v2)
    if c1 is not None and c2 is not None:
        print(f"\n概算コスト: 1 回目 ${c1:.4f} / 2 回目 ${c2:.4f}")
        uncached = (v2["input"] + v2["cache_read"] + v2["cache_write"])
        price = PRICES.get(config.anthropic_model)
        if price and v2["cache_read"] > 0:
            full = (uncached * price[0] + v2["output"] * price[1]) / 1_000_000
            print(f"   キャッシュ無しなら 2 回目は ${full:.4f} → {(1 - c2 / full) * 100:.0f}% 削減")
    print(
        "\n注意: 実運用では出力が長くなるため、1 回あたりの費用はこれより大きくなります。"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

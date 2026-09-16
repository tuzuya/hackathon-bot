"""エスカレーション用の要約生成(SPEC §5.4)。

⚠️ 要約に失敗しても、必ず生の質疑を返すこと。
   エスカレーションは緊急度が高く、要約が作れないせいで通知が届かないのは本末転倒。
"""

from __future__ import annotations

import logging

from anthropic.types import MessageParam

from src.escalation.mentions import strip_mentions
from src.llm.client import ClaudeClient, LLMError

logger = logging.getLogger(__name__)

# 要約に含める 4 点(SPEC §5.4)。これがあればメンターは即座に状況を把握できる。
SUMMARY_SYSTEM_PROMPT = """\
あなたは、ハッカソンの質問対応 Bot のログを、メンターに引き継ぐための要約を書きます。

以下の 4 点を、この順で簡潔にまとめてください。

1. **何に詰まっているか**
2. **Bot が何を答えたか**
3. **なぜ解決しなかったか**(やり取りから読み取れる場合のみ。不明なら「不明」と書く)
4. **OS / 環境**(やり取りから分かる場合のみ。不明なら「不明」と書く)

制約:
- 全体で 400 文字以内
- メンターが数秒で読めること。前置きや挨拶は書かない
- やり取りに書かれていないことを補わない。推測しない
- 参加者への呼びかけではなく、メンターへの申し送りとして書く
"""

# 要約が長すぎるとキューが読みづらいので、保険で切る
MAX_SUMMARY_LENGTH = 1200
# フォールバック時に載せる生の質疑の長さ
MAX_FALLBACK_LENGTH = 1500


async def summarize(
    llm: ClaudeClient | None,
    history: list[MessageParam],
) -> tuple[str, bool]:
    """要約を返す。第 2 要素は「フォールバックしたか」。

    LLM が使えない / 失敗した場合は、生のやり取りをそのまま返す。
    """
    fallback = _raw_transcript(history)

    if llm is None or not history:
        return fallback, True

    try:
        text = await llm.summarize(SUMMARY_SYSTEM_PROMPT, _as_transcript_message(history))
    except LLMError:
        logger.exception("要約の生成に失敗しました。生の質疑を送ります")
        return fallback, True
    except Exception:
        logger.exception("要約の生成で想定外のエラー。生の質疑を送ります")
        return fallback, True

    text = strip_mentions(text.strip())
    if not text:
        return fallback, True
    return text[:MAX_SUMMARY_LENGTH], False


def _as_transcript_message(history: list[MessageParam]) -> list[MessageParam]:
    """会話履歴を 1 つの user メッセージに畳んで渡す。

    要約は「会話の続き」ではなく「会話についての作業」なので、
    role を保ったまま渡すとモデルが参加者への返答を書いてしまう。
    """
    return [
        {
            "role": "user",
            "content": (
                "次のやり取りを、指示どおりに要約してください。\n\n"
                "----\n" + _transcript_text(history) + "\n----"
            ),
        }
    ]


def _transcript_text(history: list[MessageParam]) -> str:
    lines: list[str] = []
    for message in history:
        speaker = "Bot" if message["role"] == "assistant" else "参加者"
        content = message["content"]
        if isinstance(content, str):
            lines.append(f"{speaker}: {content}")
    return "\n".join(lines)


def _raw_transcript(history: list[MessageParam]) -> str:
    """要約できなかったときに送る、生のやり取り。"""
    if not history:
        return "(やり取りを取得できませんでした)"
    text = strip_mentions(_transcript_text(history))
    if len(text) > MAX_FALLBACK_LENGTH:
        # 新しい方を残す。古い前置きより直近の詰まりのほうが重要
        text = "…(省略)\n" + text[-MAX_FALLBACK_LENGTH:]
    return text

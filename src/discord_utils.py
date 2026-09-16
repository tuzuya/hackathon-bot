"""Discord 固有の細かい処理。"""

from __future__ import annotations

import re

# Discord のメッセージ長上限
MESSAGE_LIMIT = 2000

_FENCE_RE = re.compile(r"^\s*```(\S*)")
_MENTION_RE = re.compile(r"<@!?(\d+)>")


def strip_mention(text: str, user_id: int) -> str:
    """本文から特定ユーザーへのメンションを取り除く。

    「@bot npm install が失敗する」の @bot 部分を落として質問文だけにする。
    """
    cleaned = re.sub(rf"<@!?{user_id}>", "", text)
    return cleaned.strip()


def has_mention(text: str, user_id: int) -> bool:
    return any(int(m) == user_id for m in _MENTION_RE.findall(text))


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Discord の文字数上限に収まるように分割する。

    コードブロックの途中で切れると、後半が地の文として表示されて読めなくなる。
    そのため分割点でフェンスを一度閉じ、次のチャンクで同じ言語指定で開き直す。
    """
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    lines = _split_long_lines(text.split("\n"), limit)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    fence_lang: str | None = None  # None = コードブロックの外

    for line in lines:
        # コードブロックの中なら、閉じフェンス("\n```")の分を先に確保しておく
        reserve = 4 if fence_lang is not None else 0
        if current and current_len + len(line) + 1 + reserve > limit:
            body = "\n".join(current)
            if fence_lang is not None:
                body += "\n```"
            chunks.append(body)
            current = []
            current_len = 0
            if fence_lang is not None:
                opener = f"```{fence_lang}"
                current.append(opener)
                current_len = len(opener) + 1

        current.append(line)
        current_len += len(line) + 1

        match = _FENCE_RE.match(line)
        if match:
            fence_lang = None if fence_lang is not None else match.group(1)

    tail = "\n".join(current)
    if tail.strip():
        chunks.append(tail)
    return chunks


def _split_long_lines(lines: list[str], limit: int) -> list[str]:
    """1行だけで上限を超える行を、上限内の断片に割る。

    長い URL やエラーログの1行が該当する。
    """
    usable = limit - 8  # フェンスの開閉に使う余地
    result: list[str] = []
    for line in lines:
        if len(line) <= usable:
            result.append(line)
            continue
        for i in range(0, len(line), usable):
            result.append(line[i : i + usable])
    return result

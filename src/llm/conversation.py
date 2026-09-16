"""投稿(スレッド)内の会話履歴を、Claude API の messages[] に変換する。

⚠️ チーム間の情報遮断(CLAUDE.md 制約 4 / SPEC §4.1)
   履歴の取得元は **常に単一の discord.Thread に限定する**。
   投稿をまたいで履歴を集める処理をここに足さないこと。
   他チームのアイデアや実装が、別チームへの回答に混入してはならない。
"""

from __future__ import annotations

import logging

import discord
from anthropic.types import MessageParam

from src.discord_utils import strip_mention

logger = logging.getLogger(__name__)

# 本文が空(添付のみ)のメッセージの代わりに入れる文字列。
# API は空文字の content を受け付けないため、かつ文脈としても
# 「何か貼られた」ことは伝えたい。
EMPTY_CONTENT_PLACEHOLDER = "(添付ファイルのみ)"

# 本文なしで @bot だけを送られたときに、末尾に補う user ターン。
# Claude API は messages が user で終わることを要求するため、
# これが無いと 400 (assistant message prefill) になる。
EMPTY_TRIGGER_PLACEHOLDER = "(@bot とだけ呼ばれました。本文はありません)"


def _message_text(message: discord.Message, bot_user_id: int) -> str:
    text = strip_mention(message.content, bot_user_id)
    if text:
        return text
    if message.attachments:
        # 画像の中身を読むのは段階5で対応する
        names = ", ".join(a.filename for a in message.attachments)
        return f"{EMPTY_CONTENT_PLACEHOLDER}: {names}"
    return ""


async def build_messages(
    thread: discord.Thread,
    bot_user_id: int,
    *,
    limit: int,
) -> list[MessageParam]:
    """投稿内の会話履歴を messages[] に組み立てる。

    - 直近 `limit` 件だけを渡す(長い投稿でコストが跳ね上がるのを防ぐ。SPEC §8.3)
    - 投稿の最初のメッセージ(= 質問本体)は、古くて打ち切られても必ず含める
    - Bot の発言は assistant、それ以外(参加者・メンター)は user として扱う
    """
    collected: list[discord.Message] = []
    async for message in thread.history(limit=limit, oldest_first=False):
        collected.append(message)
    collected.reverse()  # 古い順に並べ直す

    # フォーラム投稿の1件目には質問そのものが書かれている。
    # 履歴が limit で打ち切られてここが落ちると、Bot は何の話か分からなくなる。
    if not any(m.id == thread.id for m in collected):
        starter = await _fetch_starter_message(thread)
        if starter is not None:
            collected.insert(0, starter)

    messages: list[MessageParam] = []
    # 投稿のタイトルも文脈として渡す。
    # ⚠️ 投稿ごとに変わる情報なので、システムプロンプト側には絶対に入れない
    #    (入れるとプロンプトキャッシュが毎回無効になる)。
    if thread.name:
        messages.append({"role": "user", "content": f"【この投稿のタイトル】{thread.name}"})

    for message in collected:
        text = _message_text(message, bot_user_id)
        if not text:
            continue
        role = "assistant" if message.author.id == bot_user_id else "user"
        messages.append({"role": role, "content": text})

    # API は user で始まり、user で終わる必要がある。

    # 先頭: タイトルを入れているので通常は満たされるが、念のため。
    while messages and messages[0]["role"] == "assistant":
        messages.pop(0)

    # 末尾: 本文なしで @bot だけ送られると、その発言が履歴から落ちて
    # Bot 自身の返信が最後に残り、400 (assistant message prefill) になる。
    # Bot の回答は文脈として残したいので、消さずに user ターンを足す。
    if messages and messages[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": EMPTY_TRIGGER_PLACEHOLDER})

    return messages


async def _fetch_starter_message(thread: discord.Thread) -> discord.Message | None:
    """フォーラム投稿の1件目を取得する。スレッド ID と同じ ID を持つ。"""
    if thread.starter_message is not None:
        return thread.starter_message
    try:
        return await thread.fetch_message(thread.id)
    except (discord.NotFound, discord.HTTPException):
        logger.warning("投稿の最初のメッセージを取得できませんでした: thread=%s", thread.id)
        return None

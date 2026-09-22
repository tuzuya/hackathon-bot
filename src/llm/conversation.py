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

from src import attachments as attachments_module
from src.discord_utils import strip_mention

logger = logging.getLogger(__name__)

# 本文が空(添付のみ)のメッセージの代わりに入れる文字列。
# API は空文字の content を受け付けないため、かつ文脈としても
# 「何か貼られた」ことは伝えたい。
EMPTY_CONTENT_PLACEHOLDER = "(添付ファイルのみ)"

# 画像を実際に読み込むのは、直近この件数のメッセージまで。
# 古い画像まで毎回送るとコストが跳ね上がる(画像1枚で1,500〜3,000トークン)。
IMAGE_LOOKBACK = 2

# 本文なしで @bot だけを送られたときに、末尾に補う user ターン。
# Claude API は messages が user で終わることを要求するため、
# これが無いと 400 (assistant message prefill) になる。
EMPTY_TRIGGER_PLACEHOLDER = "(@bot とだけ呼ばれました。本文はありません)"


def _message_text(message: discord.Message, bot_user_id: int) -> str:
    text = strip_mention(message.content, bot_user_id)
    if text:
        return text
    if message.attachments:
        names = ", ".join(a.filename for a in message.attachments)
        return f"{EMPTY_CONTENT_PLACEHOLDER}: {names}"
    return ""


async def _build_content(
    message: discord.Message,
    bot_user_id: int,
    *,
    include_images: bool,
    images_supported: bool,
) -> str | list[dict]:
    """1 メッセージ分の content を組み立てる。

    添付が無ければ文字列、あればブロックの配列を返す。
    画像は本文より前に置く(そのほうがモデルが文脈を掴みやすい)。
    """
    text = _message_text(message, bot_user_id)
    if not message.attachments:
        return text

    result = await attachments_module.collect(
        message.attachments,
        include_images=include_images,
        images_supported=images_supported,
    )
    if not result.has_content and not result.notes and not result.omitted:
        return text

    blocks: list[dict] = list(result.blocks)
    parts: list[str] = []
    if text and not text.startswith(EMPTY_CONTENT_PLACEHOLDER):
        parts.append(text)
    if result.omitted:
        # 過去の画像。失敗ではないので、事実だけを中立に伝える
        parts.append(f"(この発言には画像が添付されていた: {', '.join(result.omitted)})")
    if result.notes:
        # 読めなかったことをモデルにも伝え、参加者に案内させる
        parts.append(
            "[システム注記: 次の添付は読み込めませんでした — "
            + " / ".join(result.notes)
            + "。参加者にそのことを伝えてください]"
        )
    if not parts and not blocks:
        return text
    if parts:
        blocks.append({"type": "text", "text": "\n\n".join(parts)})
    return blocks


async def build_messages(
    thread: discord.Thread,
    bot_user_id: int,
    *,
    limit: int,
    images_supported: bool = True,
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

    # 画像を読み込むのは直近のメッセージだけにする(コスト防御)
    image_ids = {
        m.id
        for m in [m for m in collected if m.attachments][-IMAGE_LOOKBACK:]
    }

    for message in collected:
        role = "assistant" if message.author.id == bot_user_id else "user"
        if role == "assistant":
            text = _message_text(message, bot_user_id)
            if text:
                messages.append({"role": role, "content": text})
            continue

        content = await _build_content(
            message,
            bot_user_id,
            include_images=message.id in image_ids,
            images_supported=images_supported,
        )
        if not content:
            continue
        messages.append({"role": role, "content": content})  # type: ignore[typeddict-item]

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

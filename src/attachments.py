"""添付ファイルの取り込み(SPEC §11-D)。

初心者はエラーをテキストでコピーせず、画面を撮って貼る。
Bot 自身が「分からなかったらスクショを送って」と案内しているので、
画像を読めることは必須(SPEC §6.4)。

⚠️ コスト暴走の防御はレート制限よりここが効く(SPEC §8.3)。
   画像 1 枚で 1,500〜3,000 トークン程度を消費するため、枚数とサイズを必ず制限する。
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import discord

logger = logging.getLogger(__name__)

# Claude が受け付ける画像形式
SUPPORTED_IMAGE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)

# 1 枚あたりの上限。Claude API の上限は 5MB
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# 1 メッセージあたりの枚数上限。コスト防御
MAX_IMAGES_PER_MESSAGE = 3

# テキスト系ファイルの上限
MAX_TEXT_BYTES = 100 * 1024
MAX_TEXT_LINES = 400

# 拡張子で判定するテキスト系ファイル(content_type が text/* にならないものが多い)
TEXT_EXTENSIONS = frozenset(
    {
        "txt", "md", "log", "csv",
        "py", "js", "jsx", "ts", "tsx", "html", "css", "scss",
        "json", "yml", "yaml", "toml", "ini", "cfg", "conf", "env",
        "java", "kt", "rb", "go", "rs", "c", "h", "cpp", "cs", "php", "swift",
        "sh", "bash", "zsh", "sql", "gitignore", "dockerfile", "lock",
    }
)


@dataclass
class AttachmentResult:
    """取り込み結果。blocks は Claude に渡す content ブロック。"""

    blocks: list[dict[str, Any]] = field(default_factory=list)
    # 読めなかったもの。参加者に伝える必要がある(形式違い、サイズ超過など)
    notes: list[str] = field(default_factory=list)
    # 意図的に送っていないもの。過去の画像など。
    # ⚠️ notes と混ぜないこと。混ぜるとモデルが「読めませんでした」と
    #    参加者に謝ってしまう。これは失敗ではなくコスト最適化。
    omitted: list[str] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.blocks)


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _is_image(attachment: discord.Attachment) -> bool:
    return (attachment.content_type or "").split(";")[0] in SUPPORTED_IMAGE_TYPES


def _is_text(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").split(";")[0]
    if content_type.startswith("text/"):
        return True
    return _extension(attachment.filename) in TEXT_EXTENSIONS


def _human_size(size: int) -> str:
    return f"{size / 1024 / 1024:.1f}MB" if size >= 1024 * 1024 else f"{size // 1024}KB"


async def collect(
    attachments: list[discord.Attachment],
    *,
    include_images: bool,
    images_supported: bool = True,
) -> AttachmentResult:
    """添付ファイルを API に渡せる形にする。

    include_images=False … 古いメッセージの画像を再送しない(コスト対策)。
    images_supported=False … モデルが画像に対応していない。
        この場合は「読めなかった」として参加者に伝える。
        黙って無視すると、参加者はスクショを送ったのに
        無関係な回答が返ってきたように見える。
    """
    result = AttachmentResult()
    image_count = 0

    for attachment in attachments:
        if _is_image(attachment):
            if not images_supported:
                result.notes.append(
                    f"{attachment.filename}(画像の読み取りには対応していません)"
                )
                continue
            if not include_images:
                # 失敗ではない。過去の画像を毎回送らないための省略
                result.omitted.append(attachment.filename)
                continue
            if image_count >= MAX_IMAGES_PER_MESSAGE:
                result.notes.append(
                    f"{attachment.filename}(画像は一度に {MAX_IMAGES_PER_MESSAGE} 枚まで)"
                )
                continue
            if attachment.size > MAX_IMAGE_BYTES:
                result.notes.append(
                    f"{attachment.filename}({_human_size(attachment.size)}。"
                    f"{_human_size(MAX_IMAGE_BYTES)} を超える画像は読めません)"
                )
                continue
            block = await _image_block(attachment)
            if block is None:
                result.notes.append(f"{attachment.filename}(読み込みに失敗しました)")
                continue
            result.blocks.append(block)
            image_count += 1
            continue

        if _is_text(attachment):
            if attachment.size > MAX_TEXT_BYTES:
                result.notes.append(
                    f"{attachment.filename}({_human_size(attachment.size)}。"
                    f"{_human_size(MAX_TEXT_BYTES)} を超えるファイルは読めません)"
                )
                continue
            block = await _text_block(attachment)
            if block is None:
                result.notes.append(f"{attachment.filename}(読み込みに失敗しました)")
                continue
            result.blocks.append(block)
            continue

        # zip、動画、実行ファイルなど
        result.notes.append(f"{attachment.filename}(対応していない形式です)")

    return result


async def _image_block(attachment: discord.Attachment) -> dict[str, Any] | None:
    try:
        data = await attachment.read()
    except discord.HTTPException:
        logger.exception("画像の取得に失敗しました: %s", attachment.filename)
        return None
    media_type = (attachment.content_type or "image/png").split(";")[0]
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
    }


async def _text_block(attachment: discord.Attachment) -> dict[str, Any] | None:
    try:
        raw = await attachment.read()
    except discord.HTTPException:
        logger.exception("ファイルの取得に失敗しました: %s", attachment.filename)
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("shift_jis")
        except UnicodeDecodeError:
            return None

    lines = text.splitlines()
    truncated = len(lines) > MAX_TEXT_LINES
    if truncated:
        # 先頭を残す。設定ファイルは冒頭に要点があることが多い
        lines = lines[:MAX_TEXT_LINES]

    body = "\n".join(lines)
    language = _extension(attachment.filename)
    notice = f"\n…(長いため先頭 {MAX_TEXT_LINES} 行のみ)" if truncated else ""
    return {
        "type": "text",
        "text": f"【添付ファイル: {attachment.filename}】\n```{language}\n{body}\n```{notice}",
    }

"""添付ファイルの取り込みのテスト(SPEC §11-D)。

コスト暴走の防御はレート制限よりここが効く(SPEC §8.3)。
画像 1 枚で 1,500〜3,000 トークン消費するため、枚数とサイズの制限を固定する。
"""

from __future__ import annotations

import base64
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from src.attachments import (
    MAX_IMAGES_PER_MESSAGE,
    MAX_TEXT_LINES,
    collect,
)


def _attachment(filename: str, content_type: str | None, size: int, data: bytes = b"") -> MagicMock:
    attachment = MagicMock(spec=discord.Attachment)
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = size
    attachment.read = AsyncMock(return_value=data)
    return attachment


def _png(size: int = 1024) -> MagicMock:
    return _attachment("error.png", "image/png", size, b"\x89PNG fake")


class ImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_画像をbase64ブロックにする(self) -> None:
        result = await collect([_png()], include_images=True)
        self.assertEqual(len(result.blocks), 1)
        block = result.blocks[0]
        self.assertEqual(block["type"], "image")
        self.assertEqual(block["source"]["media_type"], "image/png")
        self.assertEqual(
            base64.standard_b64decode(block["source"]["data"]), b"\x89PNG fake"
        )

    async def test_枚数の上限を超えたら読まない(self) -> None:
        result = await collect(
            [_png() for _ in range(MAX_IMAGES_PER_MESSAGE + 2)], include_images=True
        )
        self.assertEqual(len(result.blocks), MAX_IMAGES_PER_MESSAGE)
        self.assertEqual(len(result.notes), 2)

    async def test_大きすぎる画像は読まない(self) -> None:
        result = await collect([_png(size=10 * 1024 * 1024)], include_images=True)
        self.assertEqual(result.blocks, [])
        self.assertIn("error.png", result.notes[0])

    async def test_include_imagesがFalseなら読み込まない(self) -> None:
        # 古いメッセージの画像を毎回送るとコストが跳ね上がる
        result = await collect([_png()], include_images=False)
        self.assertEqual(result.blocks, [])
        self.assertEqual(result.notes, [])
        self.assertEqual(result.omitted, ["error.png"])

    async def test_取得に失敗しても落ちない(self) -> None:
        attachment = _png()
        attachment.read = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "gone")
        )
        result = await collect([attachment], include_images=True)
        self.assertEqual(result.blocks, [])
        self.assertIn("読み込みに失敗", result.notes[0])


class TextFileTest(unittest.IsolatedAsyncioTestCase):
    async def test_コードファイルをコードブロックにする(self) -> None:
        data = 'console.log("hi");'.encode()
        result = await collect(
            [_attachment("app.js", "application/javascript", len(data), data)],
            include_images=True,
        )
        self.assertEqual(len(result.blocks), 1)
        text = result.blocks[0]["text"]
        self.assertIn("app.js", text)
        self.assertIn("```js", text)
        self.assertIn("console.log", text)

    async def test_長すぎるファイルは先頭だけ読む(self) -> None:
        data = "\n".join(f"line {i}" for i in range(MAX_TEXT_LINES + 200)).encode()
        result = await collect(
            [_attachment("big.py", "text/x-python", len(data), data)],
            include_images=True,
        )
        text = result.blocks[0]["text"]
        self.assertIn(f"先頭 {MAX_TEXT_LINES} 行のみ", text)
        self.assertNotIn(f"line {MAX_TEXT_LINES + 100}", text)

    async def test_拡張子のない設定ファイルも読む(self) -> None:
        data = b"API_KEY=xxx"
        result = await collect(
            [_attachment(".env", None, len(data), data)], include_images=True
        )
        self.assertEqual(len(result.blocks), 1)

    async def test_巨大なテキストは読まない(self) -> None:
        result = await collect(
            [_attachment("huge.log", "text/plain", 5 * 1024 * 1024)],
            include_images=True,
        )
        self.assertEqual(result.blocks, [])
        self.assertIn("huge.log", result.notes[0])


class UnsupportedTest(unittest.IsolatedAsyncioTestCase):
    async def test_zipは拒否する(self) -> None:
        result = await collect(
            [_attachment("project.zip", "application/zip", 1024)], include_images=True
        )
        self.assertEqual(result.blocks, [])
        self.assertIn("対応していない形式", result.notes[0])

    async def test_動画は拒否する(self) -> None:
        result = await collect(
            [_attachment("demo.mp4", "video/mp4", 1024)], include_images=True
        )
        self.assertEqual(result.blocks, [])
        self.assertIn("対応していない形式", result.notes[0])

    async def test_拒否した理由がファイル名つきで残る(self) -> None:
        # 参加者に「なぜ読まれなかったか」を伝えられるようにする
        result = await collect(
            [_attachment("a.zip", "application/zip", 1)], include_images=True
        )
        self.assertIn("a.zip", result.notes[0])


if __name__ == "__main__":
    unittest.main()


class ImagesUnsupportedTest(unittest.IsolatedAsyncioTestCase):
    """画像非対応のモデル(deepseek-v4-pro など)へ切り替えたとき。

    黙って無視してはいけない。参加者はスクショを送ったのに、
    それに触れない回答が返ってきたように見える。
    """

    async def test_画像は送らない(self) -> None:
        result = await collect([_png()], include_images=True, images_supported=False)
        self.assertEqual(result.blocks, [])

    async def test_読めなかったことを伝える(self) -> None:
        result = await collect([_png()], include_images=True, images_supported=False)
        self.assertEqual(len(result.notes), 1)
        self.assertIn("error.png", result.notes[0])
        self.assertIn("対応していません", result.notes[0])

    async def test_テキストファイルは引き続き読める(self) -> None:
        data = b"API_KEY=xxx"
        result = await collect(
            [_attachment(".env", None, len(data), data)],
            include_images=True,
            images_supported=False,
        )
        self.assertEqual(len(result.blocks), 1)

    async def test_既定では画像を読む(self) -> None:
        result = await collect([_png()], include_images=True)
        self.assertEqual(len(result.blocks), 1)

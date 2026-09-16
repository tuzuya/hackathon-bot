"""メッセージ分割の回帰テスト。

Discord の 2000 字上限を超える回答は分割送信する必要がある(CLAUDE.md「応答体験」)。
特に、コードブロックの途中で切れると後半が読めなくなるため、そこを重点的に確認する。
"""

from __future__ import annotations

import unittest

from src.discord_utils import split_message, strip_mention


class SplitMessageTest(unittest.TestCase):
    def test_上限以下ならそのまま1つ(self) -> None:
        self.assertEqual(split_message("短い回答"), ["短い回答"])

    def test_空文字は空リスト(self) -> None:
        self.assertEqual(split_message(""), [])

    def test_全チャンクが上限以下(self) -> None:
        text = "\n".join(f"{i}行目のテキスト" for i in range(600))
        for chunk in split_message(text):
            self.assertLessEqual(len(chunk), 2000)

    def test_分割しても本文が失われない(self) -> None:
        lines = [f"{i}行目" for i in range(500)]
        chunks = split_message("\n".join(lines))
        self.assertGreater(len(chunks), 1)
        joined = "\n".join(chunks)
        for line in lines:
            self.assertIn(line, joined)

    def test_コードブロックの途中で切れたら閉じて開き直す(self) -> None:
        body = "\n".join(f"console.log({i});" for i in range(200))
        text = f"こうやって書くよ!\n\n```javascript\n{body}\n```"
        chunks = split_message(text)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)
            # 各チャンク内でフェンスの数が偶数 = 閉じ忘れがない
            self.assertEqual(chunk.count("```") % 2, 0, f"フェンスが閉じていない: {chunk[:80]}")
        # 2つ目以降のチャンクは言語指定付きで開き直されている
        self.assertTrue(chunks[1].lstrip().startswith("```javascript"))

    def test_1行が上限を超えても分割できる(self) -> None:
        chunks = split_message("あ" * 5000)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 2000)
        self.assertEqual("".join(chunks).count("あ"), 5000)


class StripMentionTest(unittest.TestCase):
    def test_メンションを取り除く(self) -> None:
        self.assertEqual(strip_mention("<@123> npm install が失敗する", 123), "npm install が失敗する")

    def test_ニックネーム形式のメンションも取り除く(self) -> None:
        self.assertEqual(strip_mention("<@!123> 助けて", 123), "助けて")

    def test_他人へのメンションは残す(self) -> None:
        self.assertEqual(strip_mention("<@999> ありがとう", 123), "<@999> ありがとう")


if __name__ == "__main__":
    unittest.main()

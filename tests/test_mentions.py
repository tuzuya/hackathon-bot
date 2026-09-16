"""メンション制御のテスト。

デザイナーメンターに通知が飛ぶ事故は、このプロジェクトで最も避けたい失敗
(CLAUDE.md 制約 1 / SPEC §5.2)。ここは念入りに固定する。
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import discord

from src.escalation.mentions import engineer_mentor_only, strip_mentions

ENGINEER_ROLE_ID = 111
MENTOR_ROLE_ID = 222  # デザイナーにも付いている。絶対に通知してはいけない


class AllowedMentionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.role = MagicMock(spec=discord.Role)
        self.role.id = ENGINEER_ROLE_ID

    def test_エンジニアメンターロールだけが許可される(self) -> None:
        allowed = engineer_mentor_only(self.role)
        self.assertEqual(allowed.roles, [self.role])

    def test_everyoneとhereは無効(self) -> None:
        allowed = engineer_mentor_only(self.role)
        self.assertFalse(allowed.everyone)

    def test_ユーザーメンションは無効(self) -> None:
        allowed = engineer_mentor_only(self.role)
        self.assertFalse(allowed.users)

    def test_rolesがTrueになっていない(self) -> None:
        # roles=True にすると本文中の全ロールが有効化され、
        # デザイナーメンターにも通知が飛ぶ
        allowed = engineer_mentor_only(self.role)
        self.assertIsNot(allowed.roles, True)
        self.assertIsInstance(allowed.roles, list)

    def test_許可リストに他のロールが混ざらない(self) -> None:
        allowed = engineer_mentor_only(self.role)
        ids = [r.id for r in allowed.roles]  # type: ignore[union-attr]
        self.assertNotIn(MENTOR_ROLE_ID, ids)


class StripMentionsTest(unittest.TestCase):
    def test_ロールメンションを無害化する(self) -> None:
        result = strip_mentions(f"詰まってるので <@&{MENTOR_ROLE_ID}> に聞きたい")
        self.assertNotIn(f"<@&{MENTOR_ROLE_ID}>", result)

    def test_everyoneを無害化する(self) -> None:
        result = strip_mentions("@everyone 助けて")
        self.assertNotIn("@everyone", result)

    def test_hereを無害化する(self) -> None:
        self.assertNotIn("@here", strip_mentions("@here 誰か"))

    def test_普通の文章は変えない(self) -> None:
        text = "npm install でエラーが出ています。Mac です。"
        self.assertEqual(strip_mentions(text), text)

    def test_メールアドレスは壊さない(self) -> None:
        text = "git config user.email me@example.com を設定した"
        self.assertEqual(strip_mentions(text), text)


if __name__ == "__main__":
    unittest.main()

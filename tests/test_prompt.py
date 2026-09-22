"""システムプロンプト構築のテスト。

プロンプトキャッシュはシステムプロンプトの前方一致で効く。
投稿ごとに変わる情報が混ざるとキャッシュが毎回無効になり、
CLAUDE.md の「プロンプトキャッシュを必ず使う」を満たせなくなる。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.knowledge.loader import load_knowledge
from src.llm.prompt import PromptError, build_system_prompt

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BuildSystemPromptTest(unittest.TestCase):
    def test_実際のテンプレートと知識源で組み立てられる(self) -> None:
        knowledge = load_knowledge(PROJECT_ROOT / "knowledge")
        prompt = build_system_prompt(
            PROJECT_ROOT / "prompts" / "system_prompt.md", knowledge, "エンジニア相談室"
        )
        self.assertNotIn("{{KNOWLEDGE}}", prompt)
        self.assertNotIn("{{FORUM_NAME}}", prompt)
        self.assertIn("環境構築 FAQ", prompt)
        self.assertIn("エンジニア相談室", prompt)
        self.assertGreater(len(prompt), 10000)

    def test_同じ入力なら毎回同じ文字列になる(self) -> None:
        # ここが揺れるとプロンプトキャッシュが効かない
        first = load_knowledge(PROJECT_ROOT / "knowledge")
        second = load_knowledge(PROJECT_ROOT / "knowledge")
        self.assertEqual(first, second)

    def test_会話履歴のプレースホルダが残っていたら失敗する(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system_prompt.md"
            path.write_text("{{KNOWLEDGE}}\n{{CONVERSATION_CONTEXT}}", encoding="utf-8")
            with self.assertRaises(PromptError) as ctx:
                build_system_prompt(path, "知識", "エンジニア相談室")
            self.assertIn("プロンプトキャッシュ", str(ctx.exception))

    def test_知識源のプレースホルダが無ければ失敗する(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "system_prompt.md"
            path.write_text("プレースホルダなし", encoding="utf-8")
            with self.assertRaises(PromptError):
                build_system_prompt(path, "知識", "エンジニア相談室")


if __name__ == "__main__":
    unittest.main()


class ParticipantMessageTest(unittest.TestCase):
    """参加者向けの誘導文が、実際のチャンネル名に追従すること。

    ここが固定文字列だと、チャンネル名を変えたときに
    参加者が存在しないチャンネルを探すことになる。
    """

    def test_誘導文にフォーラム名が入る(self) -> None:
        from src import messages

        text = messages.out_of_scope_guide("エンジニア相談室")
        self.assertIn("エンジニア相談室", text)
        self.assertNotIn("開発相談室", text)

    def test_フォーラム本体への案内にも名前が入る(self) -> None:
        from src import messages

        self.assertIn("エンジニア相談室", messages.forum_root_guide("エンジニア相談室"))


class KnowledgeCommentTest(unittest.TestCase):
    """運営向けの記入例が Bot に渡らないこと。

    テンプレートの HTML コメントには架空の配点や日付が入っている。
    これがプロンプトに混ざると、Bot が推測ではなく「知識源に書いてある」
    ものとして架空の締切を答えてしまう(SPEC §6.3 に真っ向から反する)。
    """

    def test_HTMLコメントは知識源に含まれない(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "10_test.md"
            path.write_text(
                "# 見出し\n\n<!-- 記入例: 提出期限は10月5日 15:30 -->\n\n本当の内容\n",
                encoding="utf-8",
            )
            knowledge = load_knowledge(Path(tmp))
            self.assertIn("本当の内容", knowledge)
            self.assertNotIn("10月5日", knowledge)
            self.assertNotIn("記入例", knowledge)

    def test_複数行のコメントも除去される(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "10_test.md"
            path.write_text(
                "見出し\n<!--\n複数行\nのコメント\n-->\n本文\n", encoding="utf-8"
            )
            knowledge = load_knowledge(Path(tmp))
            self.assertNotIn("複数行", knowledge)
            self.assertIn("本文", knowledge)

    def test_実際のテンプレートに架空データが残っていない(self) -> None:
        knowledge = load_knowledge(PROJECT_ROOT / "knowledge")
        for fake in ("課題設定 | 30点", "記入例"):
            self.assertNotIn(fake, knowledge, f"架空の例が混入しています: {fake}")


class NoScreenshotGuidanceTest(unittest.TestCase):
    """画像非対応のモデルへ切り替えたので、スクショを促してはいけない。

    読めない方法を案内すると、参加者は送っても無反応に見え、
    「Bot が壊れている」と受け取る。
    """

    def test_スクショを促していない(self) -> None:
        knowledge = load_knowledge(PROJECT_ROOT / "knowledge")
        prompt = build_system_prompt(
            PROJECT_ROOT / "prompts" / "system_prompt.md", knowledge, "エンジニア相談室"
        )
        self.assertIn("画像は読めません", prompt)
        self.assertNotIn("スクショで送って", prompt)
        self.assertNotIn("スクショは歓迎", prompt)

    def test_代わりの導線が示されている(self) -> None:
        knowledge = load_knowledge(PROJECT_ROOT / "knowledge")
        prompt = build_system_prompt(
            PROJECT_ROOT / "prompts" / "system_prompt.md", knowledge, "エンジニア相談室"
        )
        # OS が分からない人に、テキストで返せる手段を渡しているか
        self.assertIn("uname -a", prompt)

    def test_初回案内もスクショを促していない(self) -> None:
        from src import messages

        body = messages.guide_body("@bot")
        self.assertNotIn("スクショ", body)
        self.assertIn("コピーして", body)

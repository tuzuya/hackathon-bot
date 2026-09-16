"""動的に追加された知識のテスト(SPEC §7.1 / §4.5)。

期限変更などで新旧の情報が両方ヒットすると、Bot が矛盾した回答を返す。
新しい情報が優先されること、無効化できることを固定する。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.config import Config
from src.db.database import Database
from src.knowledge.manager import KnowledgeManager
from src.knowledge.store import SOURCE_MANUAL, SOURCE_QA, KnowledgeStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class KnowledgeStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "t.db")
        await self.database.connect()
        self.store = KnowledgeStore(self.database.connection)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def _add(self, content: str, category: str = "スケジュール") -> int:
        return await self.store.add(
            category=category,
            content=content,
            source=SOURCE_MANUAL,
            added_by=1,
            added_by_name="メンターA",
        )

    async def test_追加した内容が描画される(self) -> None:
        await self._add("提出期限が 15:30 に変更になりました")
        rendered = await self.store.render()
        self.assertIn("15:30", rendered)
        self.assertIn("スケジュール", rendered)

    async def test_新しい情報を優先せよと明示している(self) -> None:
        # これが無いと、ファイル側の古い記述と衝突したときに Bot が迷う
        await self._add("提出期限が 15:30 に変更")
        rendered = await self.store.render()
        self.assertIn("優先", rendered)

    async def test_無効化すると描画から消える(self) -> None:
        entry_id = await self._add("古い情報")
        self.assertTrue(await self.store.deactivate(entry_id))
        self.assertNotIn("古い情報", await self.store.render())

    async def test_二重に無効化はできない(self) -> None:
        entry_id = await self._add("x")
        self.assertTrue(await self.store.deactivate(entry_id))
        self.assertFalse(await self.store.deactivate(entry_id))

    async def test_存在しないIDの無効化は失敗する(self) -> None:
        self.assertFalse(await self.store.deactivate(9999))

    async def test_何も無ければ空文字(self) -> None:
        self.assertEqual(await self.store.render(), "")

    async def test_追加した順に並ぶ(self) -> None:
        # 後に追加したものが新しい情報だと分かる必要がある
        await self._add("古い方: 15:00")
        await self._add("新しい方: 15:30")
        rendered = await self.store.render()
        self.assertLess(rendered.index("古い方"), rendered.index("新しい方"))

    async def test_承認済みQAは出典が分かる(self) -> None:
        await self.store.add(
            category="環境構築",
            content="WSLでnpmが見つからない件",
            source=SOURCE_QA,
            added_by=1,
            added_by_name="運営",
        )
        self.assertIn("承認済み Q&A", await self.store.render())


class KnowledgeManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "t.db")
        await self.database.connect()
        self.store = KnowledgeStore(self.database.connection)

        import os
        os.environ.update({"DISCORD_TOKEN": "x", "FORUM_CHANNEL_NAME": "エンジニア相談室"})
        config = Config.load()
        object.__setattr__(config, "knowledge_dir", PROJECT_ROOT / "knowledge")
        object.__setattr__(
            config, "system_prompt_path", PROJECT_ROOT / "prompts" / "system_prompt.md"
        )
        self.manager = KnowledgeManager(config, self.store)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._tmp.cleanup()

    async def test_ファイル由来の知識で組み立てられる(self) -> None:
        await self.manager.reload()
        prompt = self.manager.system_prompt
        self.assertIsNotNone(prompt)
        self.assertIn("環境構築 FAQ", prompt)  # type: ignore[arg-type]
        self.assertNotIn("{{KNOWLEDGE}}", prompt)  # type: ignore[arg-type]

    async def test_追加した知識が反映される(self) -> None:
        await self.manager.reload()
        before = len(self.manager.system_prompt or "")

        await self.store.add(
            category="スケジュール",
            content="提出期限が 15:30 になりました",
            source=SOURCE_MANUAL,
            added_by=1,
            added_by_name="メンターA",
        )
        await self.manager.reload()

        self.assertIn("15:30", self.manager.system_prompt or "")
        self.assertGreater(len(self.manager.system_prompt or ""), before)

    async def test_再構築しなければ変わらない(self) -> None:
        # 毎リクエスト組み直すとプロンプトキャッシュが効かなくなる
        await self.manager.reload()
        first = self.manager.system_prompt
        await self.store.add(
            category="スケジュール", content="新情報", source=SOURCE_MANUAL,
            added_by=1, added_by_name="A",
        )
        self.assertEqual(self.manager.system_prompt, first)

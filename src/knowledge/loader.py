"""知識源の読み込み。

`knowledge/` 配下の Markdown を全文結合して、そのままシステムプロンプトに埋め込む。
検索も RAG も行わない(SPEC §9.2)。ファイルを分けているのは人間が管理しやすくするため。

⚠️ 結合順は必ず決定的にすること。順番が揺れるとシステムプロンプトのバイト列が変わり、
   プロンプトキャッシュが毎回無効になる。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 知識源そのものではなく編集手順を書いたファイルなので、プロンプトには含めない
EXCLUDED_FILENAMES = frozenset({"README.md"})


def load_knowledge(directory: Path) -> str:
    """`knowledge/` 配下の Markdown を結合して返す。

    ファイル名の昇順で結合する(`10_` `20_` の接頭辞はこのための並び順)。
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"知識源のディレクトリが見つかりません: {directory}")

    paths = sorted(
        (p for p in directory.glob("*.md") if p.name not in EXCLUDED_FILENAMES),
        key=lambda p: p.name,
    )
    if not paths:
        raise FileNotFoundError(f"知識源の Markdown が1つもありません: {directory}")

    sections: list[str] = []
    for path in paths:
        body = path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        sections.append(body)

    knowledge = "\n\n---\n\n".join(sections)
    logger.info(
        "知識源を読み込みました: %d ファイル / %d 文字",
        len(paths),
        len(knowledge),
    )
    return knowledge

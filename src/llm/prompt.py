"""システムプロンプトの構築。

システムプロンプトは **プロセス起動時に一度だけ組み立てて使い回す**。
リクエストごとに内容が変わるとプロンプトキャッシュが効かないため
(CLAUDE.md「RAG は実装しない」/ SPEC §9.2)。
"""

from __future__ import annotations

from pathlib import Path

KNOWLEDGE_PLACEHOLDER = "{{KNOWLEDGE}}"
FORUM_NAME_PLACEHOLDER = "{{FORUM_NAME}}"
# 旧テンプレートの名残。システムプロンプトに会話履歴を入れるとキャッシュが壊れるので、
# 残っていたら起動時に気付けるようにする。
FORBIDDEN_PLACEHOLDER = "{{CONVERSATION_CONTEXT}}"


class PromptError(RuntimeError):
    """システムプロンプトの組み立てに失敗した。"""


def build_system_prompt(template_path: Path, knowledge: str, forum_name: str) -> str:
    """テンプレートのプレースホルダを実際の値に差し替える。

    `forum_name` はプロセス起動時に確定する固定値なので、
    ここに入れてもプロンプトキャッシュは壊れない。
    """
    if not template_path.is_file():
        raise PromptError(f"システムプロンプトが見つかりません: {template_path}")

    template = template_path.read_text(encoding="utf-8")

    if KNOWLEDGE_PLACEHOLDER not in template:
        raise PromptError(
            f"{template_path} に {KNOWLEDGE_PLACEHOLDER} がありません。"
            "知識源の埋め込み位置を指定してください。"
        )
    if FORBIDDEN_PLACEHOLDER in template:
        raise PromptError(
            f"{template_path} に {FORBIDDEN_PLACEHOLDER} が残っています。"
            "会話履歴は messages[] で渡すため、システムプロンプトからは削除してください"
            "(残すとプロンプトキャッシュが毎回無効になります)。"
        )

    return template.replace(KNOWLEDGE_PLACEHOLDER, knowledge).replace(
        FORUM_NAME_PLACEHOLDER, forum_name
    )

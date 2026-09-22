"""環境変数の読み込みと検証。

シークレットやロール ID / チャンネル ID はすべてここで一元的に読む。
コード中に直接書かないこと(CLAUDE.md の制約 2)。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """必須の環境変数が欠けているときに送出する。"""


def _get(name: str, default: str | None = None) -> str | None:
    """環境変数を読む。空文字は「未設定」として扱う。"""
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or None


def _require(name: str) -> str:
    value = _get(name)
    if value is None:
        raise ConfigError(
            f"環境変数 {name} が設定されていません。"
            ".env.example を参考に .env を作成してください。"
        )
    return value


def _optional_int(name: str) -> int | None:
    value = _get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"環境変数 {name} は数値(ID)である必要があります: {value!r}") from exc


def _int_with_default(name: str, default: int) -> int:
    value = _optional_int(name)
    return default if value is None else value


def _bool(name: str, *, default: bool) -> bool:
    value = _get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float_list(name: str, default: list[float]) -> list[float]:
    """カンマ区切りの金額。使用量アラートの閾値に使う。"""
    raw = _get(name)
    if raw is None:
        return default
    try:
        return sorted(float(part) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ConfigError(f"環境変数 {name} は数値のカンマ区切りで指定してください。") from exc


def _optional_int_list(name: str) -> list[int]:
    """カンマ区切りの ID 列を読む。未設定なら空リスト。"""
    raw = _get(name)
    if raw is None:
        return []
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise ConfigError(
                f"環境変数 {name} に ID でない値が含まれています: {part!r}"
            ) from exc
    return ids


@dataclass(frozen=True)
class Config:
    """Bot 全体の設定。"""

    # --- 段階1で必須 ---
    discord_token: str
    # 動作対象のサーバー。未設定なら参加している全サーバーで動く。
    # ⚠️ テスト用と本番で同じ Bot を使う場合は必ず設定すること。
    #    設定しないと、テストサーバーの質問にも応答し、
    #    エスカレーションが本番のキューへ飛ぶ。
    guild_id: int | None
    # ID を明示した場合はそちらが優先される。未設定なら名前で判定する
    # (22 チーム分の ID を手で並べるのは現実的でないため)
    forum_channel_ids: frozenset[int]
    forum_channel_name: str

    # --- 段階2以降(未設定でも起動はできる) ---
    anthropic_api_key: str | None
    # Anthropic 形式の API を提供する別サービスへ向けられる。
    # DeepSeek: https://api.deepseek.com/anthropic
    anthropic_base_url: str | None
    # プロンプトキャッシュのブレークポイントを送るか。
    # DeepSeek の Anthropic 互換エンドポイントは cache_control 未対応なので
    # 切り替え時に false にする(サーバー側の自動キャッシュは効く)
    enable_prompt_cache: bool
    # 画像入力を使うか。画像非対応のモデル(deepseek-v4-pro など)では false。
    # false のとき、添付された画像は API に送らず、
    # 「読めなかった」と参加者に伝える
    enable_image_input: bool
    anthropic_model: str
    anthropic_summary_model: str
    anthropic_fallback_model: str | None
    anthropic_effort: str
    anthropic_max_tokens: int
    summary_max_tokens: int
    history_limit: int
    llm_timeout_seconds: float
    llm_max_retries: int
    knowledge_dir: Path
    system_prompt_path: Path

    # --- 段階4以降 ---
    engineer_mentor_role_id: int | None
    mentor_queue_channel_id: int | None
    needs_attention_tag_name: str
    escalation_timeout_minutes: int

    # --- 段階6以降 ---
    admin_channel_id: int | None
    rate_limit_per_channel_hourly: int
    rate_limit_per_user_seconds: int
    usage_alert_thresholds: list[float]

    # --- 永続化 ---
    db_path: Path

    log_level: str

    @classmethod
    def load(cls) -> Config:
        return cls(
            discord_token=_require("DISCORD_TOKEN"),
            guild_id=_optional_int("GUILD_ID"),
            forum_channel_ids=frozenset(_optional_int_list("FORUM_CHANNEL_IDS")),
            forum_channel_name=_get("FORUM_CHANNEL_NAME") or "エンジニア相談室",
            anthropic_api_key=_get("ANTHROPIC_API_KEY"),
            anthropic_base_url=_get("ANTHROPIC_BASE_URL"),
            enable_prompt_cache=_bool("ENABLE_PROMPT_CACHE", default=True),
            enable_image_input=_bool("ENABLE_IMAGE_INPUT", default=True),
            anthropic_model=_get("ANTHROPIC_MODEL") or "claude-opus-5",
            anthropic_summary_model=_get("ANTHROPIC_SUMMARY_MODEL") or "claude-haiku-4-5",
            anthropic_fallback_model=_get("ANTHROPIC_FALLBACK_MODEL"),
            anthropic_effort=_get("ANTHROPIC_EFFORT") or "low",
            anthropic_max_tokens=_int_with_default("ANTHROPIC_MAX_TOKENS", 2000),
            summary_max_tokens=_int_with_default("SUMMARY_MAX_TOKENS", 1000),
            history_limit=_int_with_default("HISTORY_LIMIT", 20),
            llm_timeout_seconds=float(_int_with_default("LLM_TIMEOUT_SECONDS", 90)),
            llm_max_retries=_int_with_default("LLM_MAX_RETRIES", 1),
            knowledge_dir=Path(_get("KNOWLEDGE_DIR") or "./knowledge"),
            system_prompt_path=Path(
                _get("SYSTEM_PROMPT_PATH") or "./prompts/system_prompt.md"
            ),
            engineer_mentor_role_id=_optional_int("ENGINEER_MENTOR_ROLE_ID"),
            mentor_queue_channel_id=_optional_int("MENTOR_QUEUE_CHANNEL_ID"),
            needs_attention_tag_name=_get("NEEDS_ATTENTION_TAG_NAME") or "要対応",
            escalation_timeout_minutes=_int_with_default("ESCALATION_TIMEOUT_MINUTES", 30),
            admin_channel_id=_optional_int("ADMIN_CHANNEL_ID"),
            rate_limit_per_channel_hourly=_int_with_default("RATE_LIMIT_PER_CHANNEL_HOURLY", 40),
            rate_limit_per_user_seconds=_int_with_default("RATE_LIMIT_PER_USER_SECONDS", 5),
            usage_alert_thresholds=_float_list(
                "USAGE_ALERT_THRESHOLDS", [10.0, 25.0, 50.0, 100.0, 200.0]
            ),
            db_path=Path(_get("DB_PATH") or "./data/bot.db"),
            log_level=(_get("LOG_LEVEL") or "INFO").upper(),
        )

    def missing_for_later_stages(self) -> list[str]:
        """まだ設定されていない、後の段階で必要になる変数を返す。

        起動を止めはせず、警告ログに出すだけにする。
        段階1の疎通確認を、全部の ID が揃う前に始められるようにするため。
        """
        pending: list[str] = []
        if self.anthropic_api_key is None:
            pending.append("ANTHROPIC_API_KEY(段階2)")
        if self.engineer_mentor_role_id is None:
            pending.append("ENGINEER_MENTOR_ROLE_ID(段階4)")
        if self.mentor_queue_channel_id is None:
            pending.append("MENTOR_QUEUE_CHANNEL_ID(段階4)")
        if self.admin_channel_id is None:
            pending.append("ADMIN_CHANNEL_ID(段階6)")
        return pending

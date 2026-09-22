"""モデルと effort の組み合わせを、同じ質問で比較する。

    python -m src.compare

同一のシステムプロンプト・同一の質問を、複数の設定で実行して
回答・トークン数・概算コストを Markdown に書き出す。品質の判断は人間が行う。

⚠️ 実際に API を呼ぶので費用がかかる(既定の4設定 × 5問で $1 程度)。
   キャッシュはモデルごとに別なので、モデルを増やすとその分の書き込みが増える。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anthropic

from src.config import Config, ConfigError
from src.knowledge.loader import load_knowledge
from src.llm.prompt import build_system_prompt

# 1M トークンあたりの USD(入力, 出力)
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# 実行中の設定(_system_param から参照する)
_CONFIG = None

# 既定の比較対象(Anthropic 本家に接続している場合)。
# 同じモデルを連続させて、キャッシュの書き込み回数を抑える。
DEFAULT_CONFIGURATIONS: list[tuple[str, str]] = [
    ("claude-opus-5", "medium"),
    ("claude-opus-5", "low"),
    ("claude-sonnet-5", "medium"),
    ("claude-sonnet-5", "low"),
]


def resolve_configurations(config: Config) -> list[tuple[str, str]]:
    """比較する (モデル, effort) の組を決める。

    COMPARE_MODELS で明示できる。例:
        COMPARE_MODELS=deepseek-flash:low,deepseek-flash:medium

    指定がなく、接続先が Anthropic 本家でない場合は、
    設定中のモデル1つだけを検証する。
    別サービスに Claude のモデル名を投げても意味がないため。
    """
    raw = os.environ.get("COMPARE_MODELS", "").strip()
    if raw:
        pairs: list[tuple[str, str]] = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            model, _, effort = part.partition(":")
            pairs.append((model.strip(), effort.strip() or config.anthropic_effort))
        return pairs
    if config.anthropic_base_url:
        return [(config.anthropic_model, config.anthropic_effort)]
    return DEFAULT_CONFIGURATIONS


@dataclass(frozen=True)
class Question:
    """検証したい振る舞いと、それを引き出す質問。

    turns が 2 つ以上なら複数ターンの会話として実行する
    (文脈保持が effort やモデルで劣化しないかを見るため)。
    """

    label: str
    checks: str
    title: str
    turns: list[str]
    # (説明, 正規表現) — 最後の回答に含まれていてほしいもの
    expect: list[tuple[str, str]]
    # (説明, 正規表現) — 含まれていたら疑わしいもの
    warn: list[tuple[str, str]]


# 日付・時刻の表現。運営情報を知識源なしで答えていたら事故。
DATETIME_PATTERN = r"\d{1,2}\s*[:時]\s*\d{0,2}|\d{1,2}\s*月\s*\d{1,2}\s*日"
# 環境を聞き返す表現
ASK_OS_PATTERN = r"どの環境|どのOS|どの os|Windows.{0,10}Mac|環境を教え|OS を教え|OSを教え"


# SPEC §6 で決めた振る舞いを、それぞれ狙って引き出す質問。
QUESTIONS: list[Question] = [
    Question(
        label="環境構築(知識源にある)",
        checks="PAT か SSH の話に着地するか。知識源の内容を正しく使えているか",
        title="githubにpushできない",
        turns=["githubにpushしようとしたらパスワードが違うって言われる"],
        expect=[("トークン or SSH に言及", r"トークン|Personal Access Token|PAT|SSH")],
        warn=[],
    ),
    Question(
        label="手順もの(段階的に出せるか)",
        checks="SPEC §6.2「手順は一度に全部出さない」を守れているか",
        title="コンフリクトした",
        turns=["git pullしたらCONFLICTって出た。どうしたらいい?"],
        expect=[("git status に言及", r"git status")],
        warn=[("手順を出しすぎの疑い(6ステップ以上)", r"(?s)6\.\s|ステップ6|手順6")],
    ),
    Question(
        label="担当外(プロンプト骨組み)",
        checks="担当外と伝えた上で、コピペできる雛形を出せるか(SPEC §2.4)",
        title="Reactの不具合",
        turns=["Reactでボタン押してもstateが更新されないんだけど、どこが悪いのかな"],
        expect=[
            ("プロンプトの雛形を提示", r"【前提】|【やりたいこと】|【エラー全文】"),
            ("コードブロックで渡している", r"```"),
        ],
        warn=[("担当外なのに実装を説明している疑い", r"useState|setState\(|useEffect")],
    ),
    Question(
        label="運営情報(知識源に無い)★最重要",
        checks="推測で答えていないか。メンターへの確認を促せているか(SPEC §6.3)",
        title="提出について",
        turns=["提出期限っていつだっけ?あと何を提出すればいいの?"],
        expect=[("メンター/運営への確認を促す", r"メンター|運営|確認")],
        warn=[("具体的な日時を答えている(ハルシネーションの疑い)", DATETIME_PATTERN)],
    ),
    Question(
        label="OS 不明(スクショ誘導)★重要",
        checks="1回だけ聞き返し、見分け方と『スクショを送って』の逃げ道を添えているか(SPEC §6.4)",
        title="npmが動かない",
        turns=["npm installしたらエラーが出る"],
        expect=[
            ("スクショ送付を促している", r"スクショ|スクリーンショット|画面.{0,6}(撮|送)"),
            ("見分け方を添えている", r"Windows|Mac|WSL"),
        ],
        warn=[],
    ),
    Question(
        label="OS 判明済み(聞き返さないか)",
        checks="SPEC §6.4「環境が既に分かっているなら絶対に聞き返さない」を守れているか",
        title="npmが動かない",
        turns=["Macを使ってるんだけど、npm installでエラーが出る"],
        expect=[],
        warn=[("OS が分かっているのに聞き返している", ASK_OS_PATTERN)],
    ),
    Question(
        label="複数ターン(文脈保持)",
        checks="2ターン目で話題を見失っていないか。effort を下げても保てるか",
        title="githubにpushできない",
        turns=[
            "githubにpushしようとしたらパスワードが違うって言われる",
            "Macです",
        ],
        expect=[("push の話を継続している", r"トークン|SSH|ssh-keygen|pbcopy|push|GitHub")],
        warn=[("話題を見失っている疑い", r"何について|どういったこと|もう一度.{0,6}教え")],
    ),
]


@dataclass
class Result:
    model: str
    effort: str
    question: Question
    # (質問, 回答) をターン順に
    exchanges: list[tuple[str, str]]
    seconds: float
    input_tokens: int
    cache_write: int
    cache_read: int
    output_tokens: int

    @property
    def final_answer(self) -> str:
        return self.exchanges[-1][1] if self.exchanges else ""

    @property
    def cost(self) -> float:
        in_price, out_price = PRICES.get(self.model, (0.0, 0.0))
        return (
            self.input_tokens * in_price
            + self.cache_write * in_price * 1.25
            + self.cache_read * in_price * 0.1
            + self.output_tokens * out_price
        ) / 1_000_000

    @property
    def steady_state_cost(self) -> float:
        """キャッシュが温まっている状態での 1 問あたりのコスト。

        書き込み分を読み込み価格に置き換えて見積もる。本番はほぼこの値になる。
        """
        in_price, out_price = PRICES.get(self.model, (0.0, 0.0))
        cached = self.cache_write + self.cache_read
        return (
            self.input_tokens * in_price
            + cached * in_price * 0.1
            + self.output_tokens * out_price
        ) / 1_000_000

    def evaluate(self) -> list[str]:
        """自動で拾える目印を返す。良し悪しの判定ではなく、人間が見る箇所の絞り込み。"""
        marks: list[str] = []
        answer = self.final_answer
        for description, pattern in self.question.expect:
            hit = re.search(pattern, answer, re.IGNORECASE) is not None
            marks.append(f"{'✅' if hit else '❌'} {description}")
        for description, pattern in self.question.warn:
            if re.search(pattern, answer, re.IGNORECASE):
                marks.append(f"⚠️ {description}")
        return marks


def _system_param(system_prompt: str):
    """接続先がキャッシュ指定に対応していなければ、ただの文字列で送る。"""
    from src.config import Config as _C

    if not _CONFIG.enable_prompt_cache:
        return system_prompt
    return [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]


async def run_one(
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    model: str,
    effort: str,
    question: Question,
) -> Result:
    """1 つの質問を、必要なターン数ぶん実行する。"""
    started = time.monotonic()
    messages: list[dict] = [
        {"role": "user", "content": f"【この投稿のタイトル】{question.title}"}
    ]
    exchanges: list[tuple[str, str]] = []
    totals = {"input": 0, "write": 0, "read": 0, "output": 0}

    for turn in question.turns:
        messages.append({"role": "user", "content": turn})
        response = await client.messages.create(
            model=model,
            max_tokens=2000,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={"effort": effort},
            messages=messages,  # type: ignore[arg-type]
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        exchanges.append((turn, text))
        # 次のターンのために、Bot の発言を履歴へ入れる(本番と同じ形)
        messages.append({"role": "assistant", "content": text})

        usage = response.usage
        totals["input"] += usage.input_tokens or 0
        totals["write"] += usage.cache_creation_input_tokens or 0
        totals["read"] += usage.cache_read_input_tokens or 0
        totals["output"] += usage.output_tokens or 0

    return Result(
        model=model,
        effort=effort,
        question=question,
        exchanges=exchanges,
        seconds=time.monotonic() - started,
        input_tokens=totals["input"],
        cache_write=totals["write"],
        cache_read=totals["read"],
        output_tokens=totals["output"],
    )


def write_report(results: list[Result], path: Path) -> None:
    lines: list[str] = [
        "# モデル / effort の比較結果",
        "",
        "同じシステムプロンプトと同じ質問を、設定を変えて実行したもの。",
        "**✅ / ❌ / ⚠️ は自動で拾った目印であって、品質の判定ではない。**",
        "見る箇所を絞るための手がかりとして使い、回答そのものは必ず目で読むこと。",
        "",
        "## 設定ごとの集計",
        "",
        "| モデル | effort | 平均秒数 | 平均出力トークン | 1問あたり(キャッシュ温時) | 期待を満たした数 | 要注意の数 |",
        "|---|---|---|---|---|---|---|",
    ]

    by_config: dict[tuple[str, str], list[Result]] = {}
    for r in results:
        by_config.setdefault((r.model, r.effort), []).append(r)

    for (model, effort), group in by_config.items():
        avg_sec = sum(r.seconds for r in group) / len(group)
        avg_out = sum(r.output_tokens for r in group) / len(group)
        avg_cost = sum(r.steady_state_cost for r in group) / len(group)
        marks = [m for r in group for m in r.evaluate()]
        passed = sum(1 for m in marks if m.startswith("✅"))
        expected = sum(1 for m in marks if m.startswith(("✅", "❌")))
        warned = sum(1 for m in marks if m.startswith("⚠️"))
        lines.append(
            f"| {model} | {effort} | {avg_sec:.1f}s | {avg_out:.0f} | "
            f"${avg_cost:.4f} | {passed}/{expected} | {warned} |"
        )

    lines += [
        "",
        "## 特に見てほしい箇所",
        "",
        "- **運営情報(知識源に無い)** — 具体的な日時を答えていたら、その設定は採用できない",
        "- **OS 不明(スクショ誘導)** — 「スクショ送って」の逃げ道が出ているか。"
        "初心者は自力で OS を判別できない",
        "- **複数ターン** — 2 ターン目で話題を見失っていないか",
        "",
        "## 質問ごとの回答",
        "",
    ]

    for question in QUESTIONS:
        lines += [
            f"### {question.label}",
            "",
            f"**見るべき点:** {question.checks}",
            "",
        ]
        for (model, effort), group in by_config.items():
            match = next((r for r in group if r.question.label == question.label), None)
            if match is None:
                continue
            lines += [
                f"#### {model} / effort={effort}",
                "",
                f"`{match.seconds:.1f}秒 / 出力{match.output_tokens}トークン "
                f"/ ${match.steady_state_cost:.4f}`",
                "",
            ]
            marks = match.evaluate()
            if marks:
                lines += [" ".join(marks), ""]
            for turn_text, answer in match.exchanges:
                lines += [f"> **質問:** {turn_text}", "", answer, ""]
            lines += ["---", ""]

    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if config.anthropic_api_key is None:
        print("ANTHROPIC_API_KEY が設定されていません。", file=sys.stderr)
        raise SystemExit(1)

    global _CONFIG
    _CONFIG = config
    knowledge = load_knowledge(config.knowledge_dir)
    system_prompt = build_system_prompt(
        config.system_prompt_path, knowledge, config.forum_channel_name
    )
    configurations = resolve_configurations(config)
    turns_total = sum(len(q.turns) for q in QUESTIONS)
    total_calls = len(configurations) * turns_total
    print(f"\nシステムプロンプト: {len(system_prompt):,} 文字")
    print(
        f"{len(configurations)} 設定 × {len(QUESTIONS)} 問({turns_total} ターン) "
        f"= {total_calls} 回の API 呼び出し\n"
    )

    client = anthropic.AsyncAnthropic(
        api_key=config.anthropic_api_key,
        timeout=180.0,
        **({"base_url": config.anthropic_base_url} if config.anthropic_base_url else {}),
    )
    results: list[Result] = []
    actual_cost = 0.0

    try:
        for model, effort in configurations:
            print(f"── {model} / effort={effort} ──")
            for question in QUESTIONS:
                result = await run_one(client, system_prompt, model, effort, question)
                results.append(result)
                actual_cost += result.cost
                marks = result.evaluate()
                flags = " ".join(m.split()[0] for m in marks) if marks else ""
                print(
                    f"   {question.label}: {result.seconds:.1f}秒 "
                    f"/ 出力{result.output_tokens} {flags}"
                )
    except anthropic.APIStatusError as exc:
        print(f"\nAPI エラー (status={exc.status_code}): {exc.message}", file=sys.stderr)
        if results:
            print("ここまでの結果を書き出します。", file=sys.stderr)
        else:
            raise SystemExit(1) from exc
    finally:
        await client.close()

    path = Path("compare_results.md")
    write_report(results, path)
    print(f"\n結果を {path} に書き出しました。")
    print(f"この検証の実費: 約 ${actual_cost:.2f}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

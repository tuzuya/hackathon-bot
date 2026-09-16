"""モデルと effort の組み合わせを、同じ質問で比較する。

    python -m src.compare

同一のシステムプロンプト・同一の質問を、複数の設定で実行して
回答・トークン数・概算コストを Markdown に書き出す。品質の判断は人間が行う。

⚠️ 実際に API を呼ぶので費用がかかる(既定の4設定 × 5問で $1 程度)。
   キャッシュはモデルごとに別なので、モデルを増やすとその分の書き込みが増える。
"""

from __future__ import annotations

import asyncio
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

# 比較する設定。同じモデルを連続させて、キャッシュの書き込み回数を抑える。
CONFIGURATIONS: list[tuple[str, str]] = [
    ("claude-opus-5", "medium"),
    ("claude-opus-5", "low"),
    ("claude-sonnet-5", "medium"),
    ("claude-sonnet-5", "low"),
]


@dataclass(frozen=True)
class Question:
    """検証したい振る舞いと、それを引き出す質問。"""

    label: str
    checks: str
    title: str
    text: str


# SPEC §6 で決めた振る舞いを、それぞれ狙って引き出す質問。
QUESTIONS: list[Question] = [
    Question(
        label="環境構築(知識源にある)",
        checks="PAT かSSH の話に着地するか。知識源の内容を正しく使えているか",
        title="githubにpushできない",
        text="githubにpushしようとしたらパスワードが違うって言われる",
    ),
    Question(
        label="手順もの(段階的に出せるか)",
        checks="SPEC §6.2「手順は一度に全部出さない」を守れているか。長すぎないか",
        title="コンフリクトした",
        text="git pullしたらCONFLICTって出た。どうしたらいい?",
    ),
    Question(
        label="担当外(プロンプト骨組み)",
        checks="担当外だと伝えた上で、コピペできる雛形を出せるか(SPEC §2.4)",
        title="Reactの不具合",
        text="Reactでボタン押してもstateが更新されないんだけど、どこが悪いのかな",
    ),
    Question(
        label="運営情報(知識源に無い)★最重要",
        checks="推測で答えていないか。メンターへの確認を促せているか(SPEC §6.3)",
        title="提出について",
        text="提出期限っていつだっけ?あと何を提出すればいいの?",
    ),
    Question(
        label="OS 不明(聞き返しの作法)",
        checks="1回だけ聞き返しているか。見分け方と逃げ道を添えているか(SPEC §6.4)",
        title="npmが動かない",
        text="npm installしたらエラーが出る",
    ),
]


@dataclass
class Result:
    model: str
    effort: str
    question: Question
    answer: str
    seconds: float
    input_tokens: int
    cache_write: int
    cache_read: int
    output_tokens: int

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


async def run_one(
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    model: str,
    effort: str,
    question: Question,
) -> Result:
    started = time.monotonic()
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
        messages=[
            {"role": "user", "content": f"【この投稿のタイトル】{question.title}"},
            {"role": "user", "content": question.text},
        ],
    )
    elapsed = time.monotonic() - started
    usage = response.usage
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return Result(
        model=model,
        effort=effort,
        question=question,
        answer=text,
        seconds=elapsed,
        input_tokens=usage.input_tokens or 0,
        cache_write=usage.cache_creation_input_tokens or 0,
        cache_read=usage.cache_read_input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
    )


def write_report(results: list[Result], path: Path) -> None:
    lines: list[str] = [
        "# モデル / effort の比較結果",
        "",
        "同じシステムプロンプトと同じ質問を、設定を変えて実行したもの。",
        "**回答の良し悪しは人間が判断すること。**",
        "",
        "## 設定ごとの集計",
        "",
        "| モデル | effort | 平均秒数 | 平均出力トークン | 1問あたり(キャッシュ温時) |",
        "|---|---|---|---|---|",
    ]

    by_config: dict[tuple[str, str], list[Result]] = {}
    for r in results:
        by_config.setdefault((r.model, r.effort), []).append(r)

    for (model, effort), group in by_config.items():
        avg_sec = sum(r.seconds for r in group) / len(group)
        avg_out = sum(r.output_tokens for r in group) / len(group)
        avg_cost = sum(r.steady_state_cost for r in group) / len(group)
        lines.append(
            f"| {model} | {effort} | {avg_sec:.1f}s | {avg_out:.0f} | ${avg_cost:.4f} |"
        )

    lines += ["", "## 質問ごとの回答", ""]
    for question in QUESTIONS:
        lines += [f"### {question.label}", "", f"> {question.text}", "",
                  f"**見るべき点:** {question.checks}", ""]
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
                match.answer,
                "",
                "---",
                "",
            ]
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

    knowledge = load_knowledge(config.knowledge_dir)
    system_prompt = build_system_prompt(
        config.system_prompt_path, knowledge, config.forum_channel_name
    )
    total_calls = len(CONFIGURATIONS) * len(QUESTIONS)
    print(f"\nシステムプロンプト: {len(system_prompt):,} 文字")
    print(f"{len(CONFIGURATIONS)} 設定 × {len(QUESTIONS)} 問 = {total_calls} 回の API 呼び出し\n")

    client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key, timeout=180.0)
    results: list[Result] = []
    actual_cost = 0.0

    try:
        for model, effort in CONFIGURATIONS:
            print(f"── {model} / effort={effort} ──")
            for question in QUESTIONS:
                result = await run_one(client, system_prompt, model, effort, question)
                results.append(result)
                actual_cost += result.cost
                print(
                    f"   {question.label}: {result.seconds:.1f}秒 "
                    f"/ 出力{result.output_tokens} / 読込{result.cache_read:,}"
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

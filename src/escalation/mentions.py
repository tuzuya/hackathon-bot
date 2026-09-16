"""メンション制御。このプロジェクトで最も事故が起きやすい箇所。

サーバーには 2 つのロールがある(SPEC §5.2):
  - `メンター`           … デザイナーとエンジニアの両方に付与されている
  - `エンジニアメンター` … エンジニア側の 6 名

**デザイナーメンターに Bot の通知が飛んではならない。**

そのため:
  - allowed_mentions にはロール ID を配列で明示指定する
  - parse=["roles"] / roles=True は使わない(本文中の全ロールが有効化される)
  - everyone / here は常に無効
  - LLM の生成文は本文に入れず embed に入れる。加えて、
    念のため本文へ入りうる文字列からロールメンションを除去する
"""

from __future__ import annotations

import re

import discord

# <@&123456789> 形式のロールメンション
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
# @everyone / @here
MASS_MENTION_RE = re.compile(r"@(everyone|here)")


def engineer_mentor_only(role: discord.Role | discord.Object) -> discord.AllowedMentions:
    """エンジニアメンターロールだけに通知が飛ぶ設定を作る。

    ロール ID を配列で明示するため、本文中に別のロールメンションが
    紛れ込んでいても、そちらは通知されない。
    """
    return discord.AllowedMentions(
        everyone=False,
        users=False,
        roles=[role],
        replied_user=False,
    )


def strip_mentions(text: str) -> str:
    """メンションとして解決されうる記法を、見た目を保ったまま無害化する。

    embed に入れる時点で通知は飛ばないが、多重防御として通しておく。
    将来この文字列が本文側へ移されたときに事故らないようにするため。
    """
    text = ROLE_MENTION_RE.sub("@(ロール)", text)
    text = MASS_MENTION_RE.sub(r"@​\1", text)  # ゼロ幅スペースを挟んで無効化
    return text

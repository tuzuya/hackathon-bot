# ハッカソン質問対応 Discord Bot

初心者エンジニア向けハッカソンの質問対応 Bot。
仕様は `SPEC.md`、開発上の制約は `CLAUDE.md` を参照。

## 現在の進捗

| 段階 | 内容 | 状態 |
|---|---|---|
| 1 | 最小の bot(接続確認・動作範囲の限定) | ✅ 実装済み |
| 2 | LLM 接続・文脈保持 | ✅ 実装済み |
| 3 | 知識源の投入 | ⚠️ 仕組みは実装済み。運営情報(A〜D)が未記入 |
| 4 | エスカレーション | ✅ 実装済み(実機確認まち) |
| 5 | 添付ファイル対応 | ✅ 実装済み(実機確認まち) |
| 6 | 動的追加・レート制限・ログ | ✅ 実装済み(実機確認まち) |

運営側の手作業は `SETUP_CHECKLIST.md` にまとめてある。

## セットアップ

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # 値を埋める
```

`.env` に最低限必要なのは `DISCORD_TOKEN` の1つ。
対象フォーラムは既定でチャンネル名(`FORUM_CHANNEL_NAME=エンジニア相談室`)で探すため、
22 チーム分の ID を集める必要はない。
他の変数は未設定でも起動でき、起動時に「まだ設定されていない」旨が警告ログに出る。

## 本番サーバーの構築

```bash
python -m src.setup_server --count 22 --format "{n}班"          # 確認のみ
python -m src.setup_server --count 22 --format "{n}班" --apply  # 実行
```

22 チーム分のカテゴリ・フォーラム・タグ・ロール・メンター用チャンネルを一括で作る。
**既定は dry-run**で、`--apply` を付けたときだけ作成する。既にあるものは飛ばす。

## 設定の診断(起動前に実行する)

```bash
python -m src.check
```

Discord に接続して、対象フォーラムの件数・権限・タグ・チャンネル ID を点検して終了する。
**メッセージは一切送信しない。**22 チームへ展開するときは、本番サーバーでもこれを最初に流す。

## 起動

```bash
python -m src.bot
```

## テスト

```bash
python -m unittest discover -s tests
```

次の3つは事故に直結するため、テストで固定している。

- **動作範囲の限定** — 「エンジニア相談室」フォーラム内の投稿以外で LLM を呼ばない(22 チーム分を名前で解決)
- **チーム間の情報遮断** — 履歴の取得元を単一スレッドに限定する
- **プロンプトキャッシュ** — システムプロンプトに投稿ごとに変わる情報を混ぜない
- **メンション制御** — エンジニアメンター以外のロールに通知が飛ばない
- **排他制御** — 6 人が同時に「対応する」を押しても 1 人しか成立しない
- **30 分の自動解除** — 期限は DB に持ち、プロセス再起動をまたいでも失われない

## Bot が落ちたときの検知

`Bot管理` チャンネルに稼働状況のメッセージを 1 通置き、5 分ごとに「最終更新」を書き換える。
**表示が数分以上古くなっていれば Bot が落ちている。**

毎回新しく投稿せず 1 通を編集し続けるので、チャンネルは静かなまま。
通知が増えると、やがて誰も見なくなるため。

## 運営向けコマンド(エンジニアメンター限定)

| コマンド | 内容 |
|---|---|
| `/add-knowledge` | 知識を追加する。カテゴリを選ぶとモーダルが開く |
| `/list-knowledge` | 追加済みの知識を一覧する |
| `/remove-knowledge` | 追加した知識を無効化する(削除ではない) |
| `/stats` | 質問数・エスカレーション数・概算コストをチーム別に見る |
| `/post-guide` | 全チームの相談室に使い方の案内を一括投稿する(確認ボタンあり) |

知識を追加・削除するとシステムプロンプトが組み直され、プロンプトキャッシュは
一度無効になる。開催中に数回しか起きないので問題にならない。

## プロンプトキャッシュについて

知識源(約 38,000 字)は毎リクエスト送られるが、システムプロンプトは
**起動時に一度だけ組み立てて使い回す**ため、2回目以降はキャッシュから読まれる。

効いているかはログで確認できる:

```
LLM 使用量 model=claude-opus-5 input=32 cache_write=0 cache_read=14200 output=380
```

`cache_read` が常に 0 なら、システムプロンプトのどこかがリクエストごとに変わっている。
**会話履歴・チーム名・時刻などを `prompts/system_prompt.md` に書かないこと。**

## ディレクトリ

```
prompts/system_prompt.md   Bot に与えるシステムプロンプト本文
knowledge/                 知識源。全文がシステムプロンプトに埋め込まれる
src/
  bot.py                   エントリポイント
  config.py                環境変数の読み込みと検証
  messages.py              参加者向けの文言(トーン調整はここに集約)
  discord_utils.py         メッセージ分割など Discord 固有の処理
  scope.py                 動作範囲(相談室フォーラム)の判定
  attachments.py           画像・コードファイルの取り込みと上限
  ratelimit.py             レート制限(チーム毎時 / 個人の連投)
  usage.py                 質問ログ・コスト集計・使用量アラート
  knowledge/manager.py     システムプロンプトの保持と再構築
  knowledge/store.py       動的追加された知識と承認済み Q&A
  escalation/approval.py   ナレッジ化の承認フロー
  cogs/admin.py            運営向けスラッシュコマンド
  cogs/health.py           稼働状況の可視化(無言で死んだときの検知)
  guide.py                 初回案内の一括投稿
  setup_server.py          本番サーバーの構築(python -m src.setup_server)
  check.py                 設定の診断コマンド(python -m src.check)
  cogs/mention.py          @bot メンションの受け口
  knowledge/loader.py      knowledge/ の読み込み
  llm/prompt.py            システムプロンプトの構築(起動時に一度だけ)
  llm/client.py            Claude API 呼び出しとエラーの正規化
  llm/conversation.py      投稿内の履歴を messages[] に変換
  db/                      SQLite のスキーマと接続
  escalation/
    mentions.py            ★ロールメンションの安全制御(最重要)
    models.py              状態(未対応/対応中/完了)の定義
    store.py               永続化と排他制御
    summarizer.py          要約生成とフォールバック
    embeds.py              通知の組み立て(LLM生成文はここに入れる)
    views.py               永続ボタン
    service.py             一連の処理のまとめ役
  cogs/escalation.py       View の再登録と 30 分ポーリング
tests/                     回帰テスト
```

## Railway へのデプロイ

`Procfile` で `worker: python -m src.bot` を指定している。

⚠️ **Volume を必ずマウントし、`DB_PATH` をそのマウントポイント配下にすること。**
Volume の外に SQLite を置くと、再デプロイのたびにデータが消える。

## よくあるつまずき

### `ModuleNotFoundError: No module named 'discord'`

仮想環境を有効にしていない。ターミナルを開き直すたびに必要。

```bash
source .venv/bin/activate
```

プロンプトの先頭に `(.venv)` が付いていれば有効になっている。

### `SSLCertVerificationError: unable to get local issuer certificate`(macOS のみ)

python.org のインストーラで入れた Python には、CA 証明書のバンドルが同梱されていない。
コードの問題ではなく、Mac のローカル環境だけで起きる(Railway の Linux では起きない)。

**恒久的な修正**(1回だけ。以後すべての Python プロジェクトで解決する):

```bash
"/Applications/Python 3.13/Install Certificates.command"
```

**その場しのぎ**(`pip install certifi` が必要):

```bash
SSL_CERT_FILE=$(python -m certifi) python -m src.check
```

## Python バージョン

`.python-version` で 3.13 に固定している。ローカルと Railway で揃えること。

# Railway へのデプロイ手順

Discord Bot は WebSocket の常時接続が必要なので、Vercel のようなサーバーレスでは動かない。
Railway は常時起動のプロセスを置けて、Volume でファイルを永続化できる。

---

## 1. GitHub にリポジトリを作る

⚠️ **必ず private にすること。**知識源に会場の Wi-Fi パスワードや
配布 API キーの案内が入る可能性がある。

```bash
# .env が追跡されていないことを確認(空なら OK)
git ls-files | grep -x "\.env" && echo "危険" || echo "OK"

gh repo create hackathon-bot --private --source=. --remote=origin --push
```

`gh` を使わない場合は、GitHub の画面で private リポジトリを作ってから:

```bash
git remote add origin https://github.com/<ユーザー名>/hackathon-bot.git
git push -u origin main
```

**push 後、GitHub の画面で `.env` が存在しないことを目視確認する。**

---

## 2. Railway のプロジェクトを作る

1. https://railway.com にアクセスし、GitHub アカウントでサインイン
2. **支払い方法を登録する**(恒常的な無料枠はない。Hobby プランで月 $5 相当から)
3. `New Project` → `Deploy from GitHub repo` → `hackathon-bot` を選ぶ
4. 初回のビルドが走る。**この時点では環境変数が無いので失敗してよい**

Railway は `.python-version`(3.13)と `requirements.txt` を読んで自動で構成する。
起動コマンドは `railway.json` の `startCommand` で `python -m src.bot` を指定済み。

---

## 3. Volume をマウントする ★最重要

⚠️ **これを忘れると、再デプロイのたびに SQLite が消える。**
エスカレーションの状態、追加した知識、質問ログがすべて失われる。

1. サービスを開く → `Variables` の隣の `Settings`(または右クリック)
2. `Volumes` → `Add Volume`
3. **Mount path** に `/data` を入力
4. 保存

---

## 4. 環境変数を設定する

サービス → `Variables` → `Raw Editor` に、ローカルの `.env` の中身を貼る。

**貼ったあとに 1 箇所だけ必ず変更する:**

```
DB_PATH=/data/bot.db
```

ローカルは `./data/bot.db` だが、Railway では **Volume のマウントパス配下**を指す必要がある。

**確認すべき変数:**

| 変数 | 値 |
|---|---|
| `DISCORD_TOKEN` | 本番 Bot のトークン |
| `GUILD_ID` | **本番サーバーの ID**(テストサーバーで動かないようにするため) |
| `ANTHROPIC_API_KEY` | Anthropic のキー |
| `DB_PATH` | **`/data/bot.db`**(ローカルと違う) |
| `ENGINEER_MENTOR_ROLE_ID` | 本番のロール ID |
| `MENTOR_QUEUE_CHANNEL_ID` | 本番のチャンネル ID |
| `ADMIN_CHANNEL_ID` | 本番のチャンネル ID |
| `FORUM_CHANNEL_NAME` | `エンジニア相談室` |

**あるとよい:**

```
TZ=Asia/Tokyo
```

Railway のコンテナは UTC で動くので、これを入れるとログの時刻が日本時間になり、
障害時に追いやすくなる。

---

## 5. デプロイして確認する

環境変数を保存すると自動で再デプロイされる。`Deployments` → 最新 → `View Logs`。

**正常な起動ログ:**

```
データベースに接続しました: /data/bot.db
システムプロンプトを構築しました: ..... 文字
Cog を読み込みました: src.cogs.mention / escalation / admin / health
永続 View を再登録しました
ログインしました: hackathon-bot#....
対象サーバー: <本番サーバー名>
対象フォーラム 22 件:
スラッシュコマンドを同期しました
エスカレーションの期限監視を開始しました
稼働状況の通知を開始しました
```

**「対象フォーラム 22 件」が出ていなければ、フォーラム名が揃っていない。**

さらに `Bot管理` チャンネルに「Bot の稼働状況 🟢 稼働中」が出て、
5 分ごとに最終更新が進んでいれば正常。

---

## 6. 運用中の注意

### ログの保持は 7 日程度

振り返りたいログは別途保存する。Bot 自身の重要な記録
(エスカレーション、質問ログ、使用量)は SQLite に入っているので、
Volume さえ守れば残る。

### 再デプロイすると Bot は一瞬落ちる

`git push` するたびに再起動が走る。**開催中の push は避ける。**
やむを得ず直す場合は、参加者の少ない時間帯に。

再起動しても以下は失われない:
- エスカレーションの状態と 30 分の期限(DB に保存)
- 過去のボタン(永続 View として再登録される)
- 追加した知識

### 落ちたときの気づき方

`Bot管理` の「Bot の稼働状況」の最終更新が**数分以上進んでいなければ落ちている**。
`restartPolicyType: ON_FAILURE` で最大 10 回まで自動再起動するが、
それでも復帰しない場合は Railway のログを見る。

### DB のバックアップ

Railway の Volume は自動バックアップされない。開催前後に手元へ落としておくと安心。

```bash
railway run cat /data/bot.db > backup.db
```

(Railway CLI が必要: `npm i -g @railway/cli` → `railway login` → `railway link`)

---

## よくある失敗

| 症状 | 原因 |
|---|---|
| 再デプロイのたびにデータが消える | Volume 未マウント、または `DB_PATH` が `/data` 配下でない |
| Bot がオンラインにならない | `DISCORD_TOKEN` の貼り間違い、Message Content Intent が OFF |
| 対象フォーラム 0 件 | `FORUM_CHANNEL_NAME` と実際のチャンネル名が不一致 |
| テストサーバーでも反応する | `GUILD_ID` が未設定 |
| 起動直後に落ちる | ログの「設定エラー」を見る。必須変数の未設定が多い |

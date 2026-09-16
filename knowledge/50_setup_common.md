# 環境構築 FAQ(OS 共通)

## Git の初期設定(コミット前に一度だけ必要)

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

Git を入れた直後は「誰がコミットしたか」の設定が空なので、最初のコミットで失敗する。
以下を一度だけ実行する(`--global` なので全プロジェクトに効く)。

```bash
git config --global user.name "あなたの名前"
git config --global user.email "GitHubに登録したメールアドレス"
```

確認:

```bash
git config --global --list
```

メールアドレスは GitHub アカウントに登録しているものと揃える。違っていてもコミットはできるが、
GitHub 上でそのコミットが自分のものとして表示されない。

---

## GitHub への push でパスワードを聞かれて弾かれる(最頻出)

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

**GitHub のパスワード認証は廃止済み。**GitHub アカウントのパスワードを入力しても必ず失敗する。
`remote: Support for password authentication was removed` というエラーが出る。

解決策は2つ。**初心者には方法1を勧める。**

### 方法1: Personal Access Token(PAT)を使う

1. GitHub → 右上のアイコン → Settings
2. 一番下の Developer settings → Personal access tokens → Tokens (classic)
3. Generate new token (classic)
4. Note に用途を書き、Expiration を設定、**スコープは `repo` にチェック**
5. Generate token → **表示されたトークンをコピー**(このページを離れると二度と見られない)
6. push 時に Username は GitHub のユーザー名、**Password の欄にこのトークンを貼る**

### 方法2: SSH 鍵を使う

一度設定すれば以降パスワード入力が不要になる。手順は OS ごとの FAQ を参照。

---

## clone / add / commit / push の基本サイクル

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

最初に一度だけ(リポジトリを手元に持ってくる):

```bash
git clone https://github.com/ユーザー名/リポジトリ名.git
cd リポジトリ名
```

以降、作業するたびに繰り返すのはこの3つ:

```bash
git add .                      # 変更をステージに載せる
git commit -m "何をしたか"      # スナップショットを作る
git push                       # GitHub に送る
```

他の人の変更を取り込むときは、作業を始める前に:

```bash
git pull
```

**「今どうなってるか分からなくなった」ときは、まずこれを実行する:**

```bash
git status
```

---

## conflict(コンフリクト)が起きたときの対処

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

同じファイルの同じ行を、2人が別々に変更して両方 push しようとすると起きる。
**壊れたわけではないし、誰のせいでもない。**チーム開発では日常的に起きる。

`git pull` したときにこう出る:

```
CONFLICT (content): Merge conflict in ファイル名
Automatic merge failed; fix conflicts and then commit the result.
```

対処:

1. `git status` で、どのファイルが conflict しているか確認する
2. そのファイルを VSCode で開く。こういう記号が入っている:

   ```
   <<<<<<< HEAD
   自分の変更
   =======
   相手の変更
   >>>>>>> ブランチ名
   ```

3. VSCode なら「Accept Current Change / Accept Incoming Change / Accept Both Changes」
   のボタンが出るので押す。**どちらを残すか分からないときは、勝手に決めずに相手に聞く**
4. `<<<<<<<` `=======` `>>>>>>>` の行が1つも残っていないことを確認する
5. 解決したら:

   ```bash
   git add .
   git commit -m "コンフリクトを解消"
   git push
   ```

**どうしても分からなくなったら、作業フォルダごとバックアップを取ってからメンターを呼ぶ。**

---

## ブランチを使ったチーム開発の基本

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

全員が `main` に直接 push すると、conflict が多発する。
**1人1ブランチ、または1機能1ブランチ**にすると事故が減る。

```bash
git switch -c feature/ログイン画面     # 新しいブランチを作って移動
# ... 作業 ...
git add .
git commit -m "ログイン画面を追加"
git push -u origin feature/ログイン画面   # 初回だけ -u が必要
```

GitHub 上で Pull Request を作って、`main` に取り込む。

ブランチの確認と移動:

```bash
git branch          # 今あるブランチ一覧。* が現在地
git switch main     # main に戻る
```

---

## .gitignore の役割

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

**Git に載せたくないファイルを書いておくファイル。**リポジトリの一番上に置く。

特に重要なのはこの2つ:

```
.env            # APIキーなどの秘密情報。絶対に push しない
node_modules/   # 容量が巨大。package.json があれば復元できるので不要
```

**一度 commit してしまったファイルは、.gitignore に書いても追跡され続ける。**
その場合は追跡だけを外す:

```bash
git rm --cached .env
git commit -m ".env を追跡対象から外す"
```

⚠️ **すでに GitHub に push してしまった API キーは、無効になっていない限り漏洩している。**
`.gitignore` に足すだけでは履歴に残る。**必ずそのキーを再発行し、古い方を無効化すること。**

---

## package.json の役割

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

Node.js プロジェクトの**設定ファイル兼、使っているライブラリの一覧**。

主な中身:

- `"name"` `"version"` — プロジェクトの名前とバージョン
- `"scripts"` — `npm run dev` のような短縮コマンドの定義
- `"dependencies"` — 本番で必要なライブラリ
- `"devDependencies"` — 開発中だけ必要なライブラリ

`npm install` は、この `dependencies` を見て `node_modules/` にライブラリを展開する。
だから **`node_modules/` は Git に載せず、`package.json` と `package-lock.json` を載せる。**
チームメンバーは clone した後に `npm install` するだけで同じ環境になる。

`package-lock.json` はライブラリの正確なバージョンを固定するファイル。**これも Git に載せる。**

---

## .env の書き方と読み込まれ方

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

API キーなど、**コードに直接書きたくない秘密の値**を入れるファイル。
プロジェクトの一番上に `.env` という名前で置く。

```
API_KEY=abcdef123456
DATABASE_URL=postgres://...
```

書き方の注意:

- `=` の前後にスペースを入れない
- 値をクォートで囲む必要は基本的にない
- コメントは `#` で始める

読み込み方はフレームワークによって違う:

- **Vite(React など)** — 変数名を `VITE_` で始める必要がある(例: `VITE_API_KEY`)。
  コードからは `import.meta.env.VITE_API_KEY` で読む
- **Next.js** — ブラウザ側で使う値は `NEXT_PUBLIC_` で始める
- **素の Node.js** — `dotenv` パッケージを入れて `process.env.API_KEY` で読む

**`.env` を編集したら、開発サーバーを再起動する。**起動中のサーバーは古い値を持ったままになる。

⚠️ `.env` は必ず `.gitignore` に入れる。代わりに、変数名だけを書いた `.env.example` を
Git に載せておくと、チームメンバーが何を設定すればいいか分かる。

---

## npm install でエラーが出たときの確認順

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

上から順に確認する。

1. **今いる場所が正しいか。**`package.json` があるフォルダで実行する必要がある

   ```bash
   ls package.json
   ```

   見つからないと言われたら、`cd` でプロジェクトのフォルダに移動する

2. **Node.js が入っているか**

   ```bash
   node -v
   npm -v
   ```

   `command not found` なら Node.js 自体が未インストール、またはターミナルの再起動が必要

3. **エラーの種類を見る**
   - `EACCES` — 権限エラー
   - `ENOENT` — ファイルが見つからない
   - `ERESOLVE` — ライブラリのバージョンが噛み合っていない

4. **それでも直らなければ、作り直す**

   ```bash
   rm -rf node_modules package-lock.json
   npm install
   ```

   (Windows のコマンドプロンプトでは `rmdir /s /q node_modules` と `del package-lock.json`)

---

## VSCode を入れたら最初にやること

> カテゴリ: 環境構築 | OS: 共通 | 最終更新: 2026-09-16

1. **日本語化** — 左の四角いアイコン(拡張機能)から `Japanese Language Pack` を検索して
   インストール。右下に出る「Restart」を押す
2. **フォルダを開く** — 「ファイル → フォルダーを開く」でプロジェクトのフォルダを開く。
   ファイル単体ではなく**フォルダごと開く**のが重要
3. **ターミナルを開く** — `Ctrl + @`(Mac は `Control + @`)。
   VSCode 内のターミナルは、開いているフォルダが最初からカレントディレクトリになる

おすすめの拡張:

- `Japanese Language Pack` — 日本語化
- `Prettier` — コードの整形
- `GitLens` — Git の履歴が見やすくなる

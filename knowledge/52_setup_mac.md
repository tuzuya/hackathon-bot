# 環境構築 FAQ(Mac)

## Git のインストール(Mac)

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

Mac には最初から簡易的な Git が入っていることが多い。まず確認する:

```bash
git --version
```

バージョンが出ればそのまま使える。
初回は「コマンドラインデベロッパツールをインストールしますか?」というダイアログが出るので、
**「インストール」を押して完了を待つ**(数分かかる)。

Homebrew を使って新しい Git を入れる場合:

```bash
brew install git
```

---

## Homebrew のインストール(Mac)

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

Mac で開発ツールを入れるときの定番パッケージマネージャ。

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Mac のログインパスワードを聞かれる。**入力しても画面には何も表示されないが、
ちゃんと入力されているので、そのまま打って Enter を押す。**

インストール後、画面の最後に「Next steps」として PATH を通すコマンドが表示される。
**これを必ず実行する。**実行しないと `brew` が見つからない。

Apple Silicon(M1 以降)の場合はこうなる:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

確認:

```bash
brew --version
```

---

## Node.js のインストール(Mac)

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

**方法1: 公式インストーラ(簡単)**

https://nodejs.org/ から LTS 版の `.pkg` をダウンロードして実行する。

**方法2: Homebrew**

```bash
brew install node
```

確認:

```bash
node -v
npm -v
```

⚠️ **インストール後はターミナルを開き直す。**開きっぱなしのターミナルには PATH が反映されない。

---

## ターミナルの開き方(Mac)

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

- **VSCode 内** — `Control + @`。フォルダを開いていれば、そこが最初から現在地になる
- **ターミナル.app** — `Command + Space` で Spotlight を開き、「ターミナル」と入力

フォルダの移動:

```bash
cd フォルダ名     # 中に入る
cd ..            # 一つ上に戻る
ls               # 今いる場所のファイル一覧
pwd              # 今どこにいるか
```

**便利:** Finder でフォルダをターミナルのウィンドウにドラッグ&ドロップすると、
そのパスが入力される。`cd ` と打ってからドロップすると確実。

---

## zsh: command not found と言われる

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

原因はだいたいこの3つ。上から確認する。

1. **ターミナルを開き直していない** — インストール直後は最もこれが多い。
   一度ターミナルを完全に閉じて開き直す(VSCode 内のターミナルなら VSCode ごと再起動)

2. **PATH が通っていない** — Homebrew のインストール後に「Next steps」のコマンドを
   実行し忘れているケース。上の Homebrew の項目を参照

3. **そもそも入っていない** — `brew list` や公式サイトの手順を確認する

今どこを探しているかの確認:

```bash
which node
echo $PATH
```

---

## SSH 鍵を作って GitHub に登録する(Mac)

> カテゴリ: 環境構築 | OS: Mac | 最終更新: 2026-09-16

PAT の入力が面倒なら、こちらを設定すると以降は何も聞かれなくなる。

1. 鍵を作る(メールアドレスは GitHub のもの):

   ```bash
   ssh-keygen -t ed25519 -C "your@email.com"
   ```

   保存先とパスフレーズを聞かれる。**すべて Enter でそのまま進んでよい**

2. 公開鍵をクリップボードにコピー:

   ```bash
   pbcopy < ~/.ssh/id_ed25519.pub
   ```

3. GitHub → Settings → SSH and GPG keys → New SSH key → 貼り付けて保存

4. 接続テスト:

   ```bash
   ssh -T git@github.com
   ```

   `Hi ユーザー名!` と出れば成功

5. リポジトリの URL を SSH 形式に切り替える:

   ```bash
   git remote set-url origin git@github.com:ユーザー名/リポジトリ名.git
   ```

⚠️ 末尾が `.pub` **ではない**方のファイル(`id_ed25519`)は秘密鍵。
**誰にも見せない。GitHub にも貼らない。**

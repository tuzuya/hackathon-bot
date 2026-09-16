# 環境構築 FAQ(Windows)

## Git のインストール(Windows)

> カテゴリ: 環境構築 | OS: Windows | 最終更新: 2026-09-16

1. https://git-scm.com/download/win からインストーラをダウンロード
2. 基本は「Next」を押していけばよい。以下だけ確認する
   - **Adjusting your PATH environment** → 真ん中の
     `Git from the command line and also from 3rd-party software` を選ぶ(既定値)
   - **Choosing the default editor** → 迷ったら `Use Visual Studio Code as Git's default editor`
   - **Configuring the line ending conversions** →
     `Checkout Windows-style, commit Unix-style` を選ぶ(既定値)
3. インストール後、**ターミナルを開き直してから**確認する:

   ```
   git --version
   ```

⚠️ **インストール直後は、開きっぱなしのターミナルでは `git` が見つからない。**
PATH は新しく開いたターミナルにしか反映されない。VSCode も一度閉じて開き直す。

---

## Node.js のインストール(Windows)

> カテゴリ: 環境構築 | OS: Windows | 最終更新: 2026-09-16

1. https://nodejs.org/ から **LTS** 版(左側の、偶数バージョンの方)をダウンロード
2. インストーラを実行。基本は「Next」でよい
3. **ターミナルを開き直してから**確認:

   ```
   node -v
   npm -v
   ```

両方でバージョン番号が出れば成功。

---

## ターミナルの開き方(Windows)

> カテゴリ: 環境構築 | OS: Windows | 最終更新: 2026-09-16

いくつか方法があるが、**VSCode の中のターミナルを使うのが一番ラク**。

- **VSCode 内** — `Ctrl + @`。フォルダを開いていれば、そこが最初から現在地になる
- **PowerShell** — スタートメニューで「PowerShell」と検索
- **Git Bash** — Git をインストールすると入る。Mac/Linux と同じコマンドが使える

フォルダの移動:

```
cd フォルダ名          # 中に入る
cd ..                 # 一つ上に戻る
dir                   # 今いる場所のファイル一覧(PowerShell / コマンドプロンプト)
ls                    # Git Bash ならこちら
pwd                   # 今どこにいるか
```

**パスにスペースが含まれる場合はクォートで囲む:**

```
cd "C:\Users\名前\My Project"
```

---

## PowerShell でスクリプトの実行が拒否される

> カテゴリ: 環境構築 | OS: Windows | 最終更新: 2026-09-16

`npm` などを実行したときにこう出ることがある:

```
このシステムではスクリプトの実行が無効になっているため...
UnauthorizedAccess
```

Windows の既定のセキュリティ設定が原因。PowerShell で以下を実行して許可する:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

確認を聞かれたら `Y` を入力。**`CurrentUser` を付けているので、管理者権限は不要で、
影響範囲も自分のユーザーだけ。**その後ターミナルを開き直す。

---

## SSH 鍵を作って GitHub に登録する(Windows)

> カテゴリ: 環境構築 | OS: Windows | 最終更新: 2026-09-16

PAT の入力が面倒なら、こちらを設定すると以降は何も聞かれなくなる。

1. PowerShell または Git Bash で鍵を作る(メールアドレスは GitHub のもの):

   ```
   ssh-keygen -t ed25519 -C "your@email.com"
   ```

   保存先とパスフレーズを聞かれる。**すべて Enter でそのまま進んでよい**

2. 公開鍵をコピーする:

   ```powershell
   Get-Content ~/.ssh/id_ed25519.pub | Set-Clipboard
   ```

   (Git Bash なら `cat ~/.ssh/id_ed25519.pub` の出力を手でコピー)

3. GitHub → Settings → SSH and GPG keys → New SSH key → 貼り付けて保存

4. 接続テスト:

   ```
   ssh -T git@github.com
   ```

   `Hi ユーザー名!` と出れば成功

5. リポジトリの URL を SSH 形式に切り替える:

   ```
   git remote set-url origin git@github.com:ユーザー名/リポジトリ名.git
   ```

⚠️ 末尾が `.pub` **ではない**方のファイル(`id_ed25519`)は秘密鍵。
**誰にも見せない。GitHub にも貼らない。**

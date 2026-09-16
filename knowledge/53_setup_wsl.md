# 環境構築 FAQ(WSL)

WSL は Windows の中で Linux を動かす仕組み。便利だが、**Windows 側と WSL 側で
環境が二重になる**ため、初心者が最も混乱しやすい。
WSL だと分かったら、まず以下を疑うこと。

---

## WSL で command not found になる(最頻出)

> カテゴリ: 環境構築 | OS: WSL | 最終更新: 2026-09-16

**Windows 側と WSL 側は別々の環境。**Windows に Node.js を入れても、
WSL のターミナルからは見えない(逆も同じ)。

「インストールしたはずなのに `command not found`」の大半はこれ。

今どちらにいるかの確認:

```bash
uname -a
```

`Linux` や `microsoft-standard-WSL2` と出れば WSL の中にいる。

**WSL の中で作業するなら、WSL の中にインストールし直す:**

```bash
sudo apt update
sudo apt install -y git

# Node.js は nvm 経由が扱いやすい
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
# インストール後、ターミナルを開き直してから
nvm install --lts
```

確認:

```bash
which node
which git
```

`/usr/bin/...` や `/home/ユーザー名/...` から始まっていれば WSL 側のもの。
`/mnt/c/...` から始まっていたら**それは Windows 側のものを見ている**ので、
動くこともあるが不安定。WSL 側に入れ直す。

---

## WSL で動作が極端に遅い

> カテゴリ: 環境構築 | OS: WSL | 最終更新: 2026-09-16

プロジェクトを `/mnt/c/` の下(= Windows のディスク)に置いていると、
ファイルアクセスが非常に遅くなる。`npm install` が何分も終わらない、
開発サーバーの起動が遅い、といった症状が出る。

今どこにいるかの確認:

```bash
pwd
```

`/mnt/c/Users/...` と出たら、それが原因。

**解決策: プロジェクトを WSL 側のホームディレクトリに移す。**

```bash
cd ~
mkdir -p projects
cd projects
git clone https://github.com/ユーザー名/リポジトリ名.git
```

`~`(`/home/ユーザー名`)配下に置くと劇的に速くなる。

---

## VSCode が WSL に接続されていない

> カテゴリ: 環境構築 | OS: WSL | 最終更新: 2026-09-16

VSCode を Windows 側で普通に開いていると、WSL の中のファイルを触れているようで
**ターミナルは Windows 側**になっていることがある。

確認方法: **VSCode の左下の角**を見る。`WSL: Ubuntu` のような表示があれば接続済み。
何も出ていなければ Windows 側で動いている。

接続する方法:

1. 拡張機能から `WSL`(Microsoft 製)をインストール
2. WSL のターミナルでプロジェクトのフォルダに移動し、以下を実行:

   ```bash
   code .
   ```

   これが一番確実。WSL に接続された状態で VSCode が開く

3. または VSCode で `Ctrl + Shift + P` → `WSL: Connect to WSL`

---

## 改行コード(CRLF / LF)で Git の差分が壊れる

> カテゴリ: 環境構築 | OS: WSL | 最終更新: 2026-09-16

Windows と Linux では改行コードが違う(CRLF と LF)。
混ざると **1行も変えていないのに全行が変更扱いになる**、
シェルスクリプトが `bad interpreter` で動かない、といった症状が出る。

WSL 側での推奨設定:

```bash
git config --global core.autocrlf input
```

これで「チェックアウト時はそのまま、コミット時に LF へ変換」になる。

すでに壊れてしまった場合、ファイルを LF に直す:

```bash
sudo apt install -y dos2unix
dos2unix ファイル名
```

VSCode なら**右下に `CRLF` / `LF` の表示**があり、クリックで切り替えられる。

---

## WSL で sudo のパスワードが分からない

> カテゴリ: 環境構築 | OS: WSL | 最終更新: 2026-09-16

`sudo` で聞かれるのは **WSL をセットアップしたときに自分で決めた Linux 用のパスワード**。
Windows のログインパスワードでも、Microsoft アカウントのパスワードでもない。

⚠️ **入力しても画面には何も表示されない**(`*` すら出ない)が、
ちゃんと入力されているのでそのまま打って Enter を押す。

どうしても思い出せない場合は、Windows 側の PowerShell からリセットできる:

```powershell
wsl -u root passwd あなたのLinuxユーザー名
```

新しいパスワードを2回入力する。

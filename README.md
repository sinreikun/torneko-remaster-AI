# Local Game Pilot 0.2.0

Windows のゲーム画面をローカル画像対応LLMへ送り、目標・攻略知識に沿って操作するツールです。

## 利用者の起動方法

**[LocalGamePilot.exe をダウンロード](https://github.com/sinreikun/torneko-remaster-AI/raw/refs/heads/main/release/LocalGamePilot.exe)して実行してください。** Python の手動導入やコマンド操作は不要です。

1. exe を実行するとセットアップUIが開きます。
2. Python の版、LLM実行環境、画像モデルを選択します。それぞれに用途・容量・ライセンスの説明があります。
3. 利用条件を確認し「自動セットアップして起動」を押します。
4. 必要な部品のダウンロード・検証・導入、Ollama の起動、モデル取得が自動で進み、準備完了後に本体が開きます。
5. 本体でゲームのウィンドウ名・攻略目標・攻略知識を設定して開始します。

本体exeは約71KBです。外部ランタイムやモデルを同梱せず、自前の起動処理・UI・アプリソースだけを埋め込んでいます。Windows 10/11 x64 の Windows PowerShell 5.1、.NET Framework、Universal CRT といった標準機能を使用します。独自の署名証明書は付与していません。

## 自動導入の選択肢

| 項目 | 選択肢 | 自動処理 |
|---|---|---|
| Python | 3.14 / 3.13 | 公式の最新安定版x64インストーラーを取得。Tcl/Tk・pipを含めユーザー用領域に導入 |
| 画面取得 | Pillow 12系 | Pythonへ自動導入・import検証 |
| LLM実行環境 | Ollama の自動導入 / 起動済みOllamaの再利用 | 公式ポータブル版を取得・展開・起動。既存環境ではランタイム取得を省略 |
| 画像モデル | qwen2.5vl:3b / 7b / 32b / gemma3:4b | Ollama APIでダウンロード。進捗表示・完了確認・画像対応能力の確認 |

モデル容量の目安は順に約3.2GB / 6GB / 21GB / 3.3GBです。必要なRAM/VRAMは容量より大きく、実行速度はCPU・GPU・画像サイズに依存します。Ollama の公式Windowsアーカイブにも数百MB〜数GBの空き容量が必要です。初回セットアップにはインターネット接続が必要です。

Python は公式発行元署名、Ollama は公式 GitHub API の SHA-256 と照合してから利用します。検証できない配布物は実行しません。Python の最新情報は python.org の公式Windows配布一覧から読み取ります。

保存先は `%LOCALAPPDATA%\LocalGamePilot`。管理者権限やPATHの編集は要求せず、導入したランタイムを明示的に使用します。選択内容・導入済み部品・モデルは次回起動で再利用します。OSが組織管理によりPowerShellやダウンロードを制限している場合は、その制限を解除する処理は行いません。

- `python-3.14/` または `python-3.13/`: 実行環境
- `ollama/`: ローカルLLM実行環境
- `models/`: 自動導入したOllamaのモデル
- `app-0.2.0/`: アプリ本体
- `data/settings.json`: 目標・知識・設定
- `data/runs/*.jsonl`: 判断・操作の記録
- `downloads/`: 検証対象のダウンロードファイル

自動導入したOllamaは `127.0.0.1:11435` で待ち受けます。モデルを再利用できるよう本体終了後もサーバーは残ります。既存Ollamaの標準ポート11434とは分離します。画像はローカルAPIへ送り、ディスクには保存しません。

## 攻略機能

- 毎回スクリーンショットから現在のシーンを判断し、固定の開始画面を仮定しません。
- 目標・攻略知識・直近12手の履歴をモデルへ渡します。
- 状況・小目標・観測根拠・理由・信頼度・操作を表示します。
- 許可キー、操作時間、手数の上限、不正JSONの拒否、低信頼度時の操作抑止があります。
- 推論中に画面が大きく変わった場合は操作せず再判断します。
- F8、停止ボタン、ゲームから前面が移ることで停止します。API要求が終了する前に停止しても、戻ってきた操作は実行しません。
- 「観察のみ」はキー送信なし。自動操作する場合はチェックを外します。
- 前面のウィンドウモードのゲームを対象にします。ゲーム本体の起動は利用者が行います。

本体はOpenAI互換ローカルAPIにも対応していますが、この版の起動UIが自動導入するLLM実行環境はOllamaです。llama.cpp / LM Studioの自動導入は実装していません。

## 検証状況

**開発版です。任意シーンからの実ゲーム完全攻略は実証していません。** 元の「ゲーム攻略くん」の考え方を参考にした独立実装であり、添付版の学習DB・OCR・ランチャーを流用していません。

Linux環境でWindows exeのクロスビルド、22件の自動テスト、PowerShell構文解析、PE形式・サイズ・外部ランタイム非同梱を検証しました。Windows用の自動CIでは本体テストに加え、Windows PowerShellによる展開・構文検査とセットアップUIの起動確認を行います。CIの実行結果は [Actions](https://github.com/sinreikun/torneko-remaster-AI/actions) で確認できます。CIを追加した事実と、実行成功は区別してください。

Windows実環境でのネットワーク取得・サイレント導入・実ゲーム操作と、実モデルによる攻略は未検証です。長期学習、永続的な地図、網羅的な攻略知識、危険なゲーム内操作の独立検証器は未実装です。開発中の人間によるゲームテストは依頼していません。

## 開発者向け

```sh
python -m unittest discover -s tests -v
python -m pip install ziglang==0.13.0
python tools/build_exe.py --zig <zig実行ファイルのパス>
python tools/package.py
```

`tools/build_exe.py` は自前ソースのZIPとPowerShell UIを埋め込んだWin32ランチャーを生成します。成果物は `dist/LocalGamePilot.exe` と SHA-256。`release/` は利用者が直接ダウンロードできる検証済みexeの配置先です。バージョンタグのpush時はWindows CI成功後にGitHub Releaseにも成果物を登録します。

ソースから起動する開発者は Python 3.12以上と `requirements.txt` を導入し、`python -m autoplayer` で実行できます。利用者向けにはこの作業は不要です。

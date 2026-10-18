# 0.2.0 検証記録

## 実行済み

- Linux上でZig 0.13.0を使用し、Windows x64用ネイティブexeをビルド。
- `python -m unittest discover -s tests -v`: 22テスト成功。
- PowerShellスクリプトをtree-sitter-powershellで構文解析し、構文エラーなし。
- PEヘッダーを検査: x64 / GUIサブシステム。約71KB。
- PEインポート検査: Windows標準DLLのみ。PythonやLLMランタイムのDLLを同梱していない。
- 埋込ZIPの整合性、自前ソースとの一致、依存バイナリ・履歴・キャッシュの除外を確認。

従来の20テストはAPI画像・知識送信、操作プロトコル、制御ループ、低信頼度・停止・画面変化・記録失敗時の抑止等を検証。追加の2テストは埋込配布物とWindows exe形式を検証します。

## 追加したWindows CI（結果の確認は別途必要）

- Windows x64 exeのビルド。
- 22テスト。
- Windows PowerShell 5.1で実際の本体ZIPを展開し、導入処理を構文解析。
- セットアップUIを表示してタイマーで閉じる自動スモークテスト。
- タグ付きリリースでは上記成功後にexe・SHA-256をGitHub Releaseへ登録。

## 未検証

Windows上での実ダウンロードとサイレントインストール、Ollamaの実モデル取得、ゲーム画面取得とキー入力、実LLMによる任意シーンからの攻略成功は未検証です。この実行環境にはWindowsと対象ゲームがなく、python.orgとGitHub APIへのHTTPアクセスも403でした（Gitの読み取り・pushは別経路で可能）。

したがってexe生成と静的検証の成功を、Windows導入経路やゲーム攻略の成功とは扱っていません。開発途中の人間によるゲームテストは依頼していません。

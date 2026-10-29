# Knowledge Access / Retrieval (v0.27)

既存nextgenの上に、用途ごとのContext Viewを置く。大型改修の進行・能力・観測試験・視覚基盤を置換しない。
実装順は証拠と予算→有限の追加要求→派生Book→領域知識アダプタ。

## 正本と責務

正本はworld-v2.sqlite。Progress/Capability/UI/Rules/Observerは既存テーブルが所有する。
KnowledgeAccessはそれらのbounded snapshotとID/対象ID指定検索を提供する。LLMワーカーはDB、ゲーム、可変Managerを直接読まない。
knowledge_claimsは共通の主張レコード用で、専門Managerの全データを複製しない。
profileで分離し、frame/session/context/時刻/evidence/counterevidenceを保持する。
sourceはOBSERVED/EXPERIENCED/INFERRED/EXTERNAL/UNKNOWN、statusはUNCONFIRMED/SUPPORTED/DISPUTED/UNKNOWN。
外部資料と観測を同じ主張へ合算しない。旧JSONの出所不明は控えめな分類で投影する。
直接観測や行動経験も真実保証ではない。観測主張は自動でSUPPORTEDへ昇格しない。
同じ参照資料の再取込でDISPUTEDと反証を消さない。値の異なる主張は別IDで保持する。
現在値の更新はWorld Modelが担当し、検索の過去値で現在画面を上書きしない。

## Hot/Warm/Cold

Hotは現在frame/時刻、数値/保持値の区別、目標/必要能力、現在のUIか周囲。
Warmは現在課題に関連する最大3件/トピックの経験・進行・反証。
ColdはDBに残す。各領域最大96最近レコードと現在対象ID照合から8候補へ絞り、明示ID/対象ID要求なら古い行も検索する。
件数索引が存在してもロード済みとはしない。available_not_loaded/partially_loaded/unknown、検索後のnot_found/not_in_snapshotを区別。
字句/現在対象/同じcontext/反証/時刻で順位付け。ベクトルDBや別LLMは追加しない。
索引はSQLiteのprofile/更新時刻/subject/entity_kind/predicate/sourceに付与。全履歴を毎ターン展開しない。

## 用途別Viewと予算

ui/progress/recovery/combat/item/explorationを選び、UIでも再計画要求がある場合は進行情報を加える。
現在の許可候補とキー対応、作戦指示、数値、現在目標、計画用IDは保護する。
候補ごとに繰り返していた同じキー説明は一つのuser_key_bindingsへまとめる。
任意情報はレコード単位で採否を決め、事実を途中で切らない。選択位置・現在文字を優先し、HotがWarmを全て圧迫しないよう枠を予約。
不足・未ロード情報があること自体を明示する。必須情報だけで予算を超えた場合は要求を送らず、理由を診断へ残し既存実行器を継続。

Settings.llm_context_tokens（既定8192）とllm_input_budget（既定5000）を使用する。
入力枠は出力1600、画像1536、system推定量、grammar/framing512、余白256を引いた値以下。
Ollamaのformat schemaはメッセージ本文の重複としては数えない。tokenizer非依存の推定はASCII/3、非ASCII×1.5。
実トークン数や画像トークン数の厳密保証ではない。既存通信はprompt_eval_countなど実測を残す。
コンテキスト拒否時には選択済みの任意レコードと履歴を外す。保護情報を切らず、それ以上縮まらなければ同一要求を再送しない。
v0.26.2の旧プロンプト用縮約は残し、新Knowledge Viewには適用しない。

## 追加情報要求

brief protocolにinformation_requests（最大2件、topicとtarget_id）とobservation_requestを追加。
最初の応答から、手元snapshot内に新情報があり、45秒未満の場合だけ追加推論を最大1回行う。
全体のLLM要求時間も90秒以内の残り時間へ制限。既にロードした同じ知識だけでは追加推論しない。
workerで未取得だったIDは次のowner-thread監督時に検索。未発見/未ロードを不存在と断定しない。
新しい制御revision/作戦/進行目標等で無効化された応答からは要求も引き継がない。
full_ocrは保存済み文字情報。fresh_full_ocrは次ターンの新規全体OCRであり別の観測課題。
fresh要求は16手に1回までで、状態/OCRキャッシュを1回だけ迂回する。入力許可や戦闘文保護を変更しない。
現在実装の追加要求は任意SQL/ファイル/ネット検索ではなく、型付きの限定インターフェース。

## Book/UI/診断

停止・再接続の制御器終了時、DB writerをflush/closeした後に別readerで
`data/knowledge/<profile>/Game_Knowledge.md`へストリーミング生成、一時ファイルから置換。
LLMの生成文をDBへ取り込まない。Bookを編集しても学習値は変わらない。
進行/能力/UI/規則/仮説/観測試験/アイテム/敵/所持品/外部資料を分け、出所・未確認・反証と条件を説明。
未記録の章は未確認。UIの「知識ブックを開く」で終了時の成果物を開く。
左の認識・知識枠に役割/予算/ロード内容/追加要求/生成結果を表示。診断ZIPには知識件数と最近8主張を追加しBook全体は添付しない。

## 領域統合と未完部分

現段階で、実画面のOCR/数値、明示的なinventory_observation、既存の戦闘メッセージ解析、既存GameGuide/利用者資料を共通知識へ接続。
戦闘メッセージ中の名称はOCR名称として記録し、外見との同一性や入力との因果は確定しない。
item/monster/Inventoryの型・対象ID検索と章は存在するが、nextgenで全アイテム/所持品を確実に読む認識器は未完成。
所持品observerが無い場面で、メニュー名や外見から所持情報を作らない。
対象外見と名前の継続対応、効果の対照実験、呪い/同名個体/在庫変化の汎用推定、再現ルールの信頼度校正は今後の大型改修に残る。
Bookは条件付き経験を説明する初期版で、完全な自動攻略本や自然言語による統合解説の完成を意味しない。
現状の再評価は主に既存Managerが担当。新claimのDISPUTEDを自動で解除する汎用再実験器は未実装。
Bookの分量・件数は報酬にしない。ゲーム外の学習成果は出所と反証付きで人間が観察できるが、攻略成功・因果実証とは別。

## 自動検査の境界

DB/出所/反証維持/予算/atomic選択/profile分離/Cold検索/追加要求上限/非同期接続/新規観測のcache迂回/Book派生を検査。
既存の入力・停止・再接続・視覚検査も継続する。架空ゲームは使わず、実ゲームでの未知UI操作、攻略向上、LLMの適切な追加要求は未実証。

## v0.27.1 課題別の最低限の証拠

診断で進行Viewのloadedが0件、復旧Viewに失敗/観測履歴が無い例を確認。巨大なmanager/証拠配列をそのまま1レコードとしたため、関連性が高くても丸ごと予算から落ちていた。Progressは前提・条件・反証条件・直近の支持/反証参照を保持し、復旧は方法/元目標/直近失敗、Observerは設定と直近のbaseline/measured/採否を投影する。保存正本は変更しない。支持/反証の保持件数も表示する。

現在文字の最小集合とUIの選択候補を保持し、長期意図・プロトコル拒否理由・同義な反証目標群・進行か復旧の主要レコードを追加詳細より先に確保。同義な反証条件を重複して全文再掲せずequivalent_idsに出所IDを残す。回復用途でもUI位置情報を落とさない。knowledge_contextにtask_evidence_not_loadedとhot_coverageを追加し、選択器自身の不足を診断可能にする。未知の知識を作る機能や、必須情報が予算を超えても無理に切る機能ではない。

## v0.27.2 UI再計画と応答修正

UIモードでも再計画要求がある場合、主要な進行レコードをblocked_progress一覧より先に採用する。長期意図と現在画面の最小観測は維持し、一般履歴を後にする。能力獲得課題のLLM向けラベルを日本語の作用検証として表し、内部capability/idと可視OCRは別だと明示。保存した目標や技能IDを変更しない。

brief protocolの検証に失敗した場合、45秒未満かつ知識取得の追加推論枠を未使用なら、任意レコードを外したViewと拒否理由/現在OCRで一度だけ修正要求する。追加要求は最大1回の共有枠で、全体90秒の残り時間、停止/改訂キャンセル、元の入力候補と検証器を維持する。修正も不正なら拒否し、架空の原文や目標を補完しない。最終応答の追加情報要求は次回のowner-thread検索へ引き継ぐ。

## v0.29.1 必須の成功条件索引

success_choicesは現在snapshotから作る有限の操作検証条件表で、Knowledge Storeの新しい事実ではない。Context Viewの必須制御情報として保存し、retry_viewでも削除しない。情報取得後も同じowner snapshotのID対応を使い、保存OCRから勝手に新しい成功条件を生成しない。予算を超えれば既存の必須情報超過として扱い、条件表の末尾を切ってSchemaと不一致にしない。

## v0.29.6 保留した局所課題の索引

goal_hierarchy.deferred_goalに最新の局所課題のid/label/parent/deferred_reasonを必須情報として渡す。全保留履歴をプロンプトへ載せず、現在の目標と過去に適用できなかった目標を分離する。保留した条件の成立や攻略の失敗を事実化しない。

## v0.29.7 Context Viewの内部投影と集計の予約

current_goalはid/label/parent/conditions/capability/origin/deadlineへ投影し、ui_scope_checkのフレーム一覧やstart_frame等の内部チェックポイントを通常監督へ送らない。正本のautonomyデータは削除しない。必須の入力許可・作戦指示・現在数値・成功条件候補・達成条件は維持する。

hot_coverageは初期必須フィールド検査前から予算に含め、loadedの桁数をavailableの上限桁数で予約する。任意情報の採用後に実際のloadedへ更新しても予約を超えない。以前は初期0で任意情報を予算いっぱいに採り、末尾更新で桁が増え、削除可能な知識レコードが無ければ失敗した。原文切断や設定予算の増加では解決しない。真に必須情報だけで超過する場合は既存の明示エラーを残す。

## v0.30.0 研究と知識の正本

現行ResearchLayerは専用readonly接続で知識検索し、現場の問い合わせ前検索を置き換える。追加検索も研究ワーカーで一回だけ行う。意味の抽象化はINFERRED/UNCONFIRMED、矛盾は候補としてDISPUTEDを付けた独立claimに保存し、根拠となる観測/外部資料の出所を維持。現在の画面や既存事実の書換えには使わない。派生ブックに専用節を設ける。旧Context View/短期監督の契約は互換性検査のため保持するが、通常の一手選択経路では使わない。

## v0.30.2 行動経験と研究仮説を再び検索する

研究用のaction_experience（transitions）とresearch_knowledge（knowledge_claimsの研究predicate）を追加。
行動経験はprofile別の最新8件を有限索引にし、before/実入力/OS送信/after/効果/自然変化を投影する。
現在の数値と保持値、OS送信とゲームの反応を分離し、文字列は完全なレコード単位で選ぶ。
追加要求のtransition:IDで過去の一件を検索できる。別profileのIDは返さない。

研究仮説は研究predicateで絞って最新96件から関連8件を選び、毎手の新しいOCRで押し出されないようにする。
値は全文のまま、根拠はID/版/フレーム等の索引へ投影し、重い作成時スナップショットは正本に保持する。
過去の意味仮説が保存されるだけで次の推論へ戻らなかった経路を補う。

現場は変更せず、研究ワーカーが検索する。行動経験には入力予算の最大70%までを使い、
研究仮説/反証/現段階のための余白を残す。入力履歴は観測データであり、LLMからキーを生成する経路ではない。
新規研究は未確認のままであり、検索されたことを正しい知識の認定やゲーム攻略成功としない。

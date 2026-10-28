# 次世代コアの調査・移行設計（0.24.0）

## 調査結果

全PythonモジュールのAST・定義・SQL表、既存設計書、接続/復旧/手動割込から実行開始するUI経路、診断ZIPと表示更新経路を調査した。以下は旧コードの存続ではなく実運用経路の整理。旧設計書は履歴であり新仕様を拘束しない。

現在の中心は1713行のControllerで、PolicyLearner、ActionEffects、ExperienceCycle、LearnedPredictions、NoveltyExplorer、ImprovementSupervisor、Supervisor/OperationalControlなどが別々に状態・行動・予測・反復・採点を保持する。過去Q値、既知ゲームの意味役割、ルート優先、監督の担当機能が候補を何度も上書きする。観測の欠測と状態キー変更により経験が分断され、メニュー内の未完了手順が再発する。汎用ゲームで同じ学習ループを成立させる中心モデルになっていない。

新コアは独立したnextgenパッケージとして接続し、UIの自動攻略入口を置換する。旧Controllerと旧学習表は互換試験・履歴資料として残すが、新コアの採点や事実へ流入させない。

## 新しい中心

単一の観測状態 → 索引付き経験検索 → 条件付き入力結果分布 → 不確実性/新奇性/局所目標/観測された損失の比較 → 単発入力 → 結果と遅延結果の照合 → 同一モデルの更新。

学習単位は前後フレーム/観測値、キー、時間、OS受理、自然変化、予測、誤差、観測信頼度。意味説明は別表の反証可能な仮説。予測の事前採点と更新後の採点を混ぜない。未観測は0ではなく欠測。保持値は別欄。

LLMは非同期の汎用監督。ゲーム固有skill一覧や入力マクロを選ばせず、目標に関係する可視条件、候補への一時的な価値仮説、解釈と検証対象を提案させる。応答は入力設定/作戦指示/画面文脈で検査し、古い画面のカーソル指示を別画面へ流用しない。未知・大きな予測誤差・反復・経験集約時に呼ぶ。既知行動・観測・保存は継続する。

## データ・移行

`world-v2.sqlite`（WAL）を新規作成し、ゲーム/キー割当に隔離する。旧DBを変換しない。現在観測、保持値、状態推定、LLM仮説、経験による規則の支持を分離。生の遷移、予測規則、予測照合、遅延効果、仮説、空間グラフ、調整試行を別に保存する。DB書込は単一ワーカー、有限キュー、まとめコミット。送信直前の入力意図は同期ジャーナル。停止/切断では未完了因果系列を切る。

実ミニマップは既存の画素センサーだけ再利用。地図がない場合は観測状態間のトポロジーを保持し、入力回数から座標を作らない。外見追跡は匿名候補と信頼度で持ち、類似だけで種類を確定しない。確認済み階変更は既存の複数観測確認を再利用する。

## 高速化と評価

同一フレーム解析を有限キャッシュへ統合。領域の無変化は認識時刻を保持して再利用。文字/戦闘/未知状況の精査を速度優先で落とさない。推論・入力・待機・OCR・状態構築・照合・選択・学習・DB・UIを別計測する。

学習件数ではなく事前予測一致率/誤差、反復率、既知状況の判断時間、非LLM処理割合、新規観測、仮説反証、可視目標条件の到達を別々に記録。可視条件はゲームクリア証明ではない。短期の調整試行は比較窓と撤回理由を保存する。

## 資産別の扱い

| ソース | 扱い | 主な定義 | 既存学習・状態表 |
|---|---|---|---|
| `autoplayer/__init__.py` | 起動・版情報を更新 | 定数・入口 | — |
| `autoplayer/__main__.py` | 起動・版情報を更新 | main | — |
| `autoplayer/action_effects.py` | 新コアへ統合・実行経路から退役 | ActionEffects | action_effects_v1, operational_experiments_v1 |
| `autoplayer/active_input.py` | 新コアへ統合・実行経路から退役 | ActiveInput | — |
| `autoplayer/adaptation.py` | 新コアへ統合・実行経路から退役 | DurationLearner | input_duration_attempts, input_durations |
| `autoplayer/anticipation.py` | 新コアへ統合・実行経路から退役 | validate_forecast, validate_expected, snapshot, Anticipation | forecast_stats |
| `autoplayer/appearance.py` | 内部改修して再利用 | descriptor, AppearanceBank | appearance_samples |
| `autoplayer/background.py` | 残す | BackgroundUnavailable, BackgroundEvidence | background_evidence |
| `autoplayer/battle_appearance.py` | 新コアへ統合・実行経路から退役 | BattleAppearanceMemory | battle_appearance_links |
| `autoplayer/battle_messages.py` | 内部改修して再利用 | read_messages | — |
| `autoplayer/cancellation.py` | 残す | OperationCancelled, call_interruptible, request_bytes, run_process | — |
| `autoplayer/capture_service.py` | 残す | CaptureTimeout, capture_worker, CaptureService | — |
| `autoplayer/clipboard.py` | 残す | copy_file | — |
| `autoplayer/combat.py` | 新コアへ統合・実行経路から退役 | relative_contacts, requires_message_ocr, CombatExperiments | combat_actions, combat_message_reads, combat_observations |
| `autoplayer/components.py` | 残す | install_capture, pull_model | — |
| `autoplayer/console_play.py` | 残す | ConsoleCommands, ConsolePlayer | — |
| `autoplayer/controls.py` | 残す | is_torneko, valid_key_count, dungeon_movement_keys | — |
| `autoplayer/core.py` | 新コアへ統合・実行経路から退役 | Settings, Decision, parse_decision, select_knowledge, response_placeholder, response_quality_issue, expected_schema, _decision_object_schema, decision_schema, ocr_heading_scene, LocalLLM, Controller | — |
| `autoplayer/current_evidence.py` | 内部改修して再利用 | historical, current | — |
| `autoplayer/dashboard.py` | 内部改修して再利用 | LearningTraces, replace_text, append_text, translucent_background, flow_positions, flow_frame_class, split_fraction, dashboard_sashes | — |
| `autoplayer/decision_protocol.py` | 新コアへ統合・実行経路から退役 | 定数・入口 | — |
| `autoplayer/demonstrations.py` | 新コアへ統合・実行経路から退役 | screen_kind, similarity, observed_effect, features, event_steps, HumanSegments, DemonstrationStore, DemonstrationRecorder | human_example_scores, human_examples, human_reuse |
| `autoplayer/diagnostics.py` | 残す | export_human_examples, DiagnosticStore, InputDiagnostics, export_diagnostics | cycles, runs |
| `autoplayer/equipment_preparation.py` | 新コアへ統合・実行経路から退役 | EquipmentPreparation | — |
| `autoplayer/experience_cycle.py` | 新コアへ統合・実行経路から退役 | same_floor_values, digest, maturity, ExperienceCycle | experience_checks_v1, experience_rules_v1 |
| `autoplayer/experience_review.py` | 新コアへ統合・実行経路から退役 | outcome_learning_allowed, ExperienceReview | experience_review_v1 |
| `autoplayer/exploration.py` | 新コアへ統合・実行経路から退役 | ExplorationRoutes | — |
| `autoplayer/exterior_actions.py` | 新コアへ統合・実行経路から退役 | ExteriorActions | — |
| `autoplayer/exterior_atlas.py` | 新コアへ統合・実行経路から退役 | signature, dark_signature, similarity, continuity, graph, ExteriorAtlas | exterior_entries_v1, exterior_maps_v1, exterior_portals_v1 |
| `autoplayer/exterior_screen.py` | 新コアへ統合・実行経路から退役 | flat_pixels, white_frames, observe_change, assumed_change | — |
| `autoplayer/fast_learning.py` | 新コアへ統合・実行経路から退役 | FastLearningGate | — |
| `autoplayer/floor_navigation.py` | 新コアへ統合・実行経路から退役 | room_diagonal_allowed, FloorNavigation | — |
| `autoplayer/game_guide.py` | 内部改修して再利用 | GameGuide | — |
| `autoplayer/grounding.py` | 内部改修して再利用 | reconcile | — |
| `autoplayer/guidance.py` | 新コアへ統合・実行経路から退役 | OperatorGuidance | — |
| `autoplayer/hud.py` | 内部改修して再利用 | boxed_fullness, boxed_statuses | — |
| `autoplayer/identification.py` | 新コアへ統合・実行経路から退役 | Identification | — |
| `autoplayer/important_events.py` | 内部改修して再利用 | brief, ImportantEvents | — |
| `autoplayer/improvement.py` | 新コアへ統合・実行経路から退役 | ImprovementSupervisor | improvement_events, improvement_state |
| `autoplayer/information_exploration.py` | 新コアへ統合・実行経路から退役 | InformationExploration | input_coverage_v1 |
| `autoplayer/interface_responses.py` | 新コアへ統合・実行経路から退役 | layout_group, InterfaceResponses | interface_responses_v1 |
| `autoplayer/interfaces.py` | 内部改修して再利用 | compact_text, has_text_content, ordinary_choice, normalize_item_menu, item_popup_layout, main_menu_context, torneko_menu_kind, result_screen, choice_action | — |
| `autoplayer/inventory_audit.py` | 新コアへ統合・実行経路から退役 | InventoryAudit | — |
| `autoplayer/inventory_description.py` | 内部改修して再利用 | line_box, locate_description | — |
| `autoplayer/inventory_identity.py` | 内部改修して再利用 | review_key, same_label, orange_text, equipped_indicator, row_image | — |
| `autoplayer/item_tasks.py` | 新コアへ統合・実行経路から退役 | ItemTasks | — |
| `autoplayer/knowledge.py` | 内部改修して再利用 | normalize, KnowledgeEngine | — |
| `autoplayer/learned_predictions.py` | 新コアへ統合・実行経路から退役 | LearnedPredictions | — |
| `autoplayer/learning_connection.py` | 新コアへ統合・実行経路から退役 | candidate, report, summary | — |
| `autoplayer/live_preview.py` | 残す | live_status, PreviewBudget, PreviewFrameCache | — |
| `autoplayer/loop_guard.py` | 新コアへ統合・実行経路から退役 | LoopGuard | — |
| `autoplayer/map_sampling.py` | 新コアへ統合・実行経路から退役 | MapSamplingPlan | — |
| `autoplayer/map_sensor.py` | 内部改修して再利用 | FloorConfirmation, MapSensor, read_grid, _read_grid | — |
| `autoplayer/mapless_navigation.py` | 新コアへ統合・実行経路から退役 | motion, MaplessNavigation | — |
| `autoplayer/memory.py` | 新コアへ統合・実行経路から退役 | LearningConnection, MemoryStore | claims, experiences, plans, transitions |
| `autoplayer/minimap.py` | 内部改修して再利用 | components, quantized_shape, read_minimap | — |
| `autoplayer/monitor_checkpoint.py` | 新コアへ統合・実行経路から退役 | MonitorCheckpoint | monitor_checkpoint |
| `autoplayer/novelty.py` | 新コアへ統合・実行経路から退役 | NoveltyExplorer | novelty_actions, novelty_outcomes, novelty_states |
| `autoplayer/observation.py` | 残す | TurnObservation, TurnObserver, image_color_facts | — |
| `autoplayer/observation_store.py` | 新コアへ統合・実行経路から退役 | ObservationStore, message_candidates | — |
| `autoplayer/observer_view.py` | 内部改修して再利用 | ObserverPanels, ObserverDigest | — |
| `autoplayer/ocr.py` | 残す | WindowsOCR, ScreenOCR | — |
| `autoplayer/ocr_components.py` | 残す | data_root, directory, valid_file, installed, HTTPSRedirects, download_file, install | — |
| `autoplayer/ocr_ui.py` | 残す | OCRTestWindow | — |
| `autoplayer/operational_control.py` | 新コアへ統合・実行経路から退役 | OperationalControl | — |
| `autoplayer/overlay.py` | 内部改修して再利用 | OverlayAPI, GameOverlay | — |
| `autoplayer/pacing.py` | 内部改修して再利用 | AdaptivePacing | — |
| `autoplayer/paddle_ocr.py` | 残す | PaddleOCR | — |
| `autoplayer/performance.py` | 残す | vision_image, ocr_context, knowledge_context, fast_policy_ready, compact_profile_prompt, context_retry_prompt, clean_text, model_data, fallback_schema, bounded_request_prompt | — |
| `autoplayer/placement.py` | 残す | fit_bounds, WindowPlacement | — |
| `autoplayer/policy.py` | 新コアへ統合・実行経路から退役 | profile_for, ExperiencePolicy | policy_actions, policy_events, policy_text_visits, policy_visits |
| `autoplayer/presentation.py` | 残す | display_json | — |
| `autoplayer/principles.py` | 新コアへ統合・実行経路から退役 | action_principles | — |
| `autoplayer/profiles.py` | 残す | ProfileStore, parse_bindings | — |
| `autoplayer/quick_steps.py` | 新コアへ統合・実行経路から退役 | QuickSteps | — |
| `autoplayer/recognition_cache.py` | 残す | CroppedRecognitionCache | — |
| `autoplayer/recording.py` | 残す | FrameRecorder, RecordedBackend | — |
| `autoplayer/recovery.py` | 残す | TargetUnavailable, ObservationRetry, TargetRecovery | — |
| `autoplayer/recovery_policy.py` | 新コアへ統合・実行経路から退役 | fresh, RecoveryPolicy | — |
| `autoplayer/research.py` | 内部改修して再利用 | public_references, reference_context | — |
| `autoplayer/runtime_check.py` | 残す | main | — |
| `autoplayer/session.py` | 残す | SessionControl | — |
| `autoplayer/state_snapshot.py` | 内部改修して再利用 | StateSnapshot | — |
| `autoplayer/strategies.py` | 新コアへ統合・実行経路から退役 | normalized, visible_text, progress_signal, MethodLearner | game_hypotheses, method_events, method_stats |
| `autoplayer/strength_growth.py` | 新コアへ統合・実行経路から退役 | StrengthGrowth | — |
| `autoplayer/supervision.py` | 新コアへ統合・実行経路から退役 | validate_plan, StateLedger, Cancel, Supervisor | — |
| `autoplayer/supervision_context.py` | 新コアへ統合・実行経路から退役 | pick, metrics, compact_supervision_prompt | — |
| `autoplayer/telemetry.py` | 内部改修して再利用 | RecognitionState, valid, grid_layout, grid_cell, paint_grid, RecognitionPanel | — |
| `autoplayer/ui.py` | 内部改修して再利用 | Application, main | — |
| `autoplayer/ui_budget.py` | 残す | UIEventQueue | — |
| `autoplayer/visual_responses.py` | 新コアへ統合・実行経路から退役 | VisualResponses | — |
| `autoplayer/window_capture.py` | 残す | BITMAPINFOHEADER, BITMAPINFO, WindowCapture | — |
| `autoplayer/windows.py` | 残す | GUITHREADINFO, WindowsGame, visible_windows, process_alive, window_targets, emergency_stop_requested | — |
| `autoplayer/world_model.py` | 新コアへ統合・実行経路から退役 | labels, metrics, changes, VisualSense, Inventory, WorldModel | item_use_stats, world_events |

新規：nextgenの状態観測、経験ストア、結果予測モデル、価値/情報利得選択、非同期監督、実行統合、検証・設計書。旧個別学習器への二重書込と新旧の多段キー上書きは廃止する。

## 実装済みの境界とデータ

- `nextgen/state.py`：ハッシュ別の有限解析キャッシュ、現在値/保持値、階変更確認、画素地図、匿名の連続追跡候補。全文への精査では画像形状・追跡を再実行しない。メッセージ専用の過去画像では現状を更新しない。
- `nextgen/store.py`：別SQLite、有限256件キュー、単一DBワーカー、最大64件まとめコミット、終了時フラッシュ。保存失敗を黙って捨てない。入力意図とOS受付は別途同期JSONL。
- `nextgen/model.py`：最大32例の新しい結果を重視する条件付き分布、一つの候補選択器、入力時間別の経験、4辺までの観測グラフ検索、2/4/8手後の持続/消失の照合、反復脱出、比較窓付き調整試行。
- `nextgen/supervisor.py`：目的、可視目標、候補価値、数値効果の仮説、外見の意味解釈を非同期に提案。画面内の文字や引用は命令にしない。画像内の対象ID・引用・観測可能feature・候補IDを検査。作用の仮説は支持/反例を保存し、外見の意味解釈は未検証のまま分離する。
- `nextgen/controller.py`：この一つのモデルへ入出力を接続。UIの自動攻略は旧Controllerを起動しない。停止・割込時のキー解放、古い判断の破棄、実画像記録を維持。

| 新表 | 内容 |
|---|---|
| `states` | 観測由来の状態、最後の画像特徴・地図、観測回数 |
| `transitions` | 前状態、実入力と時間、OS受付、後状態、入力前予測、実測差、自然変化、文章証拠 |
| `rules` | 条件×実入力時間別の結果分布、事前照合、反例、条件細分化 |
| `graph` | 実際に観測した状態間の遷移。地図なしでは実座標・移動成功と断定しない |
| `hypotheses` | LLMの入力効果仮説、支持・反例・信頼度。観測事実とは別 |
| `interpretations` | 対象ID・元画像・引用を付けた未確認の意味解釈 |
| `appearances` | 連続追跡候補からの外見変種。見た目の類似で同一種へ統合しない |
| `evaluations` | 予測照合、遅延関連、パラメータ試行と撤回 |

各表はゲーム・キー割当のprofileで分離。目標文だけを編集しても物理的な入力効果の経験を捨てない。旧DBを新規則の教師にしない。既存のお手本/単独コンソール記録は従来形式を維持し、新コアへの自動変換はしない。手動介入が入った前後の因果系列は切る。

## 選択と反証

候補は許可キーの単発、利用者が方向・補助と明示した組合せ、最大3種類の押下時間。意味ラベルの学習値でキーを上書きする後段はない。

候補評価は、可視目標の期待変化、結果分布の不確実性、到達先の新奇性、観測済み損失、一時的なLLM価値仮説を比較する。情報利得は不確実性/観測数に基づく近似であり、厳密なBayes最適探索ではない。LLMは入力時間の候補全てを説明せず、各キー組の代表候補を意味的に評価する。

結果予測は実入力前の分布で採点する。効果ベクトルだけでなく到達先の条件も一致判定に含める。同じ結果が続けば不確実性が下がり、反例は最近の分布へ反映される。反例が蓄積した広い条件は観測状態で分割する。誤った過去例を削除して正解扱いにしない。

遅延照合は直後の変化が2/4/8手後も残ったか、元に戻ったかを記録する。途中の別入力数に応じて因果確信度を下げ、自然変化も別欄で残す。これは交絡を除いた因果証明ではない。

反復は状態・実入力・結果の周期から検出し、反復に使った入力候補を一時的に強く下げて別の候補を実行する。床や経験は消去しない。8試行で区間を抜けなければ成功とはせず再監督する。正当な復路の再訪そのものを失敗にしない。

調整試行は探索重みを12例比較し、観測した損失や悪化で戻す。無対照の短期比較であり、因果的な優越性の証明ではない。待機は要求待機を含む総時間ではなく最後の活動時刻から推定し、待機が自己増大しないようにした。

## 表示・診断

新コアは予測一致率、新奇性、不確実性、情報獲得量、反復率、LLM要求率、選択時間、観測時間、局所目標進展、追跡候補継続率、位置信頼度をグラフへ渡す。欠測は0にしない。旧履歴表示の系列も残すが新コアからQ値やLLM自己評価を捏造しない。複数系列は2列の凡例で表示。半透明背景・LIVE独立・スクロール位置維持・折返しツールバーを維持する。

実行段階の計測に加え、UIは`ui-performance.json`へ通知処理・描画・待ち行列を別計測する。LLMのバックグラウンド推論時間は同期段階の時間へ足さない。新DBが大きくZIPから省略されても、`nextgen/summary.json`に最近の遷移、照合、仮説を残す。

## 実機で未実証の範囲

これは小さな経験的世界モデルへの中心構造の置換であり、万能なゲーム攻略能力の獲得を宣言するものではない。視覚の状態同定は粗い特徴と文字の仮グループで、似た場所の混同が残る。対象追跡は変化領域と連続性に基づき、静止した全アイテムの網羅検出、遮蔽後の再同定、全8方向の正確な種類識別は保証しない。高信頼な同種認識には実画像の追加評価が必要。

ミニマップの再接続は観測地図との高い一致がある場合だけ旧領域候補を再使用し、不一致は新領域へ分離する。地図なしのグラフは空間そのものの保証ではない。目標の数値や文章の可視条件を満たしたことは、全体目標・ゲームクリアを証明しない。新コアはLLMの完了宣言で自動終了しない。

長期目標の到達、反復の実機での減少、視覚同定、LLM無しの攻略割合、1手0.5秒以下、長時間性能は実ゲームの記録で別々に評価する。契約検査や実画像の再生だけから改善を断定しない。ゲームのメモリ/API/セーブには接続せず、背景操作不可の対象は既存Backendの対応外判定を維持する。


## 0.24.1 実機診断1793147045094047100

約22分44秒、317入力結果。LLM要求27・採用0。事前予測の採点は11件（一致4、不一致7）、残り306件は未予測。302例で実地図未取得、262例で文字枠の仮説が立っていた。末尾53結果の計測は平均OCR約2161ms、入力後観測約627ms。UI待ち通知0・高水位5で、描画待ちの詰まりは観測されなかった。

メニュー101例だけでも旧text_keyが26種類へ分断。同一レイアウトを文字集合の近似で仮グループにし、数値・微小OCR差・アニメーションと、選択行（同名なら位置も）を切り分ける。main-menuをハードコードして行動を選ぶ変更ではない。現在OCR原文と数値は変更せず保存する。

監督はカーソル/微小文字差で中止せず、操作・作戦改訂・階のドメイン変更で中止する。別画面で完了した有効応答は16件/64手まで保存し、同一scopeに戻った場合だけ採用。候補価値は元の観測文脈にも一致する場合のみ適用する。取り消しは失敗件数に混ぜず、失敗理由・破棄理由・採用をJSONLへ記録する。以前のZIPには監督の失敗理由が収録されておらず、27件全ての詳細原因を断定できない。

最短押下時間の候補で少なくとも2観測を得るまで、長い時間の候補を通常探索へ追加しない。最短で反応がある場合は8観測まで長さ比較を抑え、初回の未試行候補が3倍に増えて予測照合へ進めない問題を緩和する。ループ脱出時はこの抑制を外す。新旧の状態条件キーは区別し、旧遷移は削除しない。

同じメニューの実OCR101例を再生したレイアウト一致、別メニューとの分離、同名別行、非同期回答の再利用、キャンセルの分離、押下時間の段階探索を自動検査。実機での監督採用・予測改善・速度改善は未検証。


## 0.24.2 診断1793149872528392300

v0.24.1で約9分28秒、138手。最終監督統計は要求12・採用0・エラー12・取り消し0。末尾に残った拒否理由は「文字目標が不正」「未許可候補または採点」。前回の中止問題と、今回の応答形式拒否は分けて扱う。末尾47結果の観測文脈は6種類で、以前の全面的な状態分断とは違い、同じメニュー/選択行の文脈が繰り返し使われていた。候補が多く初回試行が続いていた。

従来のSchemaはtext/nodeでもvalueに数値を許し、operatorにもge/leを許す一方、アプリ検査はそれを拒否していた。新しい要求では文字/数値をanyOfで分け、featureも現在観測できるものだけ列挙する。内部の入力ID（キー配列と時間をJSON化した文字列）は保持し、LLMとの通信だけA00等の短いIDにする。IDの対応は要求時に固定し、候補IDの推測・曖昧な補正・未許可キー生成は行わない。

ヘッダーは従来通り厳格に検査し、提案配列は項目ごとに同じ検査を適用する。不正な一項目で独立した有効提案まで棄却せず、棄却理由と応答原文の最大8000文字をJSONLへ残す。採用した提案も未確認仮説であることは維持する。Schemaが不正を生成しにくくすることと、アプリ側で不正を実行しないことを両立させる。

幾何学的なメニューでは、初期の斜め/補助同時押しを単独キー各2観測まで抑える。監督の明示的な比較提案とループ脱出時には許可する。ゲーム名や特定メニューの正解キーを追加した変更ではない。

自動検査には短いIDの往復、型別Schema、文字目標への不正数値だけの棄却、未知候補の棄却、ヘッダー不正の全面棄却、単独キーからの探索、非同期ワーカー経由の有効な非空計画の採用を含める。過去のLLM応答原文はZIPにないため、受け取った応答そのものの再生とは区別する。実機での採用率・予測精度・攻略能力の改善は未実証。

# Semantic Routing チューニング用デモ環境 設計書

作成日: 2026-08-07
対象: `compose.yaml` / `kong.yaml`(Kong Gateway 3.15 + `ai-proxy-advanced`)

## 1. 目的

Kong AI Gateway の semantic routing について、`threshold` と target の `description` を
データに基づいて調整するワークフローを、そのまま人に見せられる形で構築する。

実運用では Databricks のような実験管理基盤の上でこの営みを行う想定だが、
本デモでは Langfuse を代替として使う。将来的には Langfuse に蓄積された実トラフィックの
トレースから Golden Dataset を育てる流れまで見せられる状態を目指す。

## 2. 現状と課題

### 2.1 semantic routing が設定として成立していない

`kong.yaml` の `ai-proxy-advanced` は `balancer.algorithm: semantic` と各 target の
`description` を持つが、**`embeddings` ブロックと `vectordb` ブロックが存在しない**。
この 2 つがなければ semantic balancer は動作しない。要件4で調整する `threshold` は
`config.vectordb.threshold` に置かれるため、まずこれらの追加が前提となる。

### 2.2 チューニングの反復コストが高い

`description` を 1 案変えるたびに `deck sync` と Data Plane 再起動が必要で、
デモ中に何十通りも試すことはできない(`deck sync` 後は OTel メトリクス export が
停止し DP 再起動が要る、という既知の挙動もある)。
Kong の外側でベクトル検索を再現し、そこで反復できる仕組みが必要。

### 2.3 題材が汎用サンプルのまま

現在の description は `"Specialist in pasta"` / `"CATCHALL"` で、実務の題材としての
説得力がない。また 2 つしかなく意味的に遠いため、
「調整が必要になる」状況が再現できない。

## 3. スコープ

### やること

1. 疑似本番 Redis から sandbox Redis へのベクトルデータ移送(要件1)
2. Semantic Routing と同一の埋め込みモデル・同一次元数による Golden Dataset の埋め込み生成(要件2)
3. sandbox Redis に対する KNN 近傍探索(要件3)
4. 探索結果に基づく threshold / description の調整ループ(要件4)
5. `kong.yaml` への `embeddings` / `vectordb` 追加と、実務を模した target への差し替え
6. Golden Dataset の Langfuse Dataset 登録と、評価結果の dataset run 記録

### やらないこと

- 実本番 Redis への接続(疑似本番を自前で用意する)
- Langfuse のトレースからの Golden Dataset 自動抽出(将来構想として §10 に記載)
- **AI Gateway v2(`kongctl` 経由の宣言的設定)。本リポジトリは Kong Gateway +
  `ai-proxy-advanced` のみを対象とする**

## 4. 前提として確認済みの事実

| 項目 | 確認結果 |
|---|---|
| Azure リソース | `shukawam-ai-foundry-resource` |
| 利用可能デプロイ | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, `text-embedding-3-large` |
| 埋め込み | `text-embedding-3-large` / 3072 次元 / api-version `2024-02-01` で疎通確認済み |
| Redis | `compose.yaml` の `kong-redis`(`redis:8.0.2`、Query Engine 同梱で `FT.*` 利用可)と、Langfuse 用の `redis`(redis-stack)の 2 つ |
| トレース | otel-collector が traces を Langfuse に export 済み(`config/observability/otel-collector-config.yaml:23`) |

## 5. アーキテクチャ

```
[疑似本番]                                  [sandbox]
 kong (3.15, ai-proxy-advanced)              sandbox-redis   ← compose.yaml に追加
 kong-redis                                     ↑
   │  Kong が起動時に target description        │ FT.CREATE + RESTORE
   │  を埋め込んでベクトルを書き込む              │
   └──── semtune.vectors.migrate() ─────────────┘
              SCAN + DUMP / RESTORE
              FT.INFO → FT.CREATE

 notebooks/semantic_routing_tuning.ipynb
   ①移送 → ②Golden埋め込み → ③KNN検索 → ④sweep/可視化 → ⑤description改訂→即再評価 → ⑥kong.yaml反映
```

### 設計の要点

**sandbox Redis 上でベクトルを差し替えて評価する。** これにより Kong を再デプロイせずに
description 案を高速に反復できる。§2.2 の課題への対処であり、この設計の中心。

**疑似本番と sandbox を物理的に別コンテナに分ける。** 「本番からデータを抜いて安全な場所で
実験する」という実際に踏むことになる手順を、そのままデモの構造にする。

## 6. ベクトル移送方式(要件1)

### 採用: キー単位 SCAN + DUMP/RESTORE + index 再作成

```
1. 疑似本番 kong-redis に FT._LIST → index 名を特定
2. FT.INFO <index> → スキーマ(キー prefix、ベクトルフィールド名、次元、距離メトリック、
   HASH/JSON の別、payload フィールド)を取得
3. sandbox-redis に同一スキーマで FT.CREATE
4. SCAN MATCH <prefix>* → 各キーを DUMP(バイナリ)
5. sandbox-redis に RESTORE。index は prefix 一致で自動的に取り込む
6. 検証: キー数の一致と、抜き取り 1 件のバイト完全一致
```

**選択理由**: 本番 Redis が他アプリと相乗りしている現実的なケースでも、対象 prefix だけを
選択的に抜ける。また `FT.INFO` の出力をデモで見せることで「本番のどこに何が入っているか」が
可視化される。実装は 60 行程度。

### 代替案と却下理由

- **RDB 丸ごとコピー**(`BGSAVE` → `docker cp`): 最も忠実で実装がほぼ不要だが、
  オール・オア・ナッシングで相乗り本番に使えない。**手順のみ spec に併記し、フォールバックとする。**
- **論理エクスポート**(値を読んで JSON 化して再投入): 可読性は高いが float32 の
  バイナリベクトルを経由するため往復での劣化リスクがあり、コード量も増える。却下。

## 7. コンポーネント

```
semantic-tuning/
  pyproject.toml                  # uv 管理。deps: redis, httpx, numpy, pandas,
                                  #   matplotlib, pyyaml, jupyterlab, langfuse
  semtune/
    vectors.py
    embed.py
    search.py
    evaluate.py
    langfuse_sync.py
  data/
    routing_targets.yaml
    golden_dataset.yaml
  notebooks/
    semantic_routing_tuning.ipynb
  tests/
    test_search.py
    test_evaluate.py
```

### `semtune/vectors.py`

Redis のベクトルストアを扱う。

- `inspect_index(client) -> IndexSpec` — `FT._LIST` / `FT.INFO` から index 名・prefix・
  ベクトルフィールド名・次元・距離メトリック・ストレージ種別を読み取る
- `create_index(client, spec)` — `IndexSpec` から `FT.CREATE` を発行
- `migrate(src, dst) -> MigrationReport` — 上記 §6 の 1〜6 を実行し、移送件数と検証結果を返す
- `upsert_target_vector(client, spec, target_name, vector, payload)` — sandbox 上で
  target のベクトルを差し替える(§9 の反復ループで使う)

依存: `redis` のみ。Azure にも Kong にも依存しない。

### `semtune/embed.py`

Azure OpenAI の埋め込み API を直接呼ぶ。

- `embed(texts: list[str]) -> np.ndarray` — shape `(n, 3072)` を返す
- モデル名・次元・api-version は `routing_targets.yaml` 由来の設定を単一の出所とし、
  Kong 側設定と食い違わないようにする
- SHA256(model + dimensions + text)をキーにしたファイルキャッシュ(`.cache/embeddings/`)。
  デモ中の再実行で API を叩き直さない
- 429 / 5xx は指数バックオフで 3 回リトライ

依存: `httpx`, `numpy`。

### `semtune/search.py`

sandbox Redis に対する KNN 検索と、Kong の選択規則の再現。

- `knn(client, spec, query_vector, k) -> list[Hit]` — `FT.SEARCH` の KNN クエリを発行し、
  target 名と距離のリストを返す
- `route(hits, threshold, catchall) -> RoutingDecision` — Kong の semantic balancer と
  同じ規則で 1 つの target を選ぶ。**この規則は実測(§14)で確定させてから実装する**

依存: `redis`, `numpy`。

### `semtune/evaluate.py`

Golden Dataset に対する評価。

- `evaluate(cases, decisions) -> EvalResult` — 正解率、target ごとの precision/recall、
  混同行列、誤ルーティング一覧(query / expected / actual / 1位と2位の距離差)
- `sweep(cases, hits, thresholds) -> pd.DataFrame` — threshold ごとの正解率と CATCHALL 落ち率
- `distance_distribution(cases, hits) -> pd.DataFrame` — 正解ペアと不正解ペアの距離分布

依存: `numpy`, `pandas`。Redis にも Azure にも依存しないので単体テストしやすい。

### `semtune/langfuse_sync.py`

- `push_dataset(cases)` — Golden Dataset を Langfuse の Dataset として登録(冪等)
- `push_run(run_name, decisions, eval_result, metadata)` — 評価を dataset run として
  記録。`metadata` に threshold と description のリビジョンを入れ、後から条件を追える形にする

依存: `langfuse` SDK。接続先は `compose.yaml` の `langfuse-web`(`pk-123456789` / `sk-123456789`)。

## 8. データ定義

### `data/routing_targets.yaml`

チューニングで**書き換わる側**。埋め込みモデル設定もここに置き、Kong 側と一致させる。

```yaml
embeddings:
  provider: azure
  instance: shukawam-ai-foundry-resource
  deployment: text-embedding-3-large
  api_version: "2024-02-01"
  dimensions: 3072
  distance_metric: cosine

threshold: 0.5   # 初期値。チューニングで確定させる

targets:
  - name: market-research
    deployment: gpt-5.6-sol
    description: 国内外の市場動向、金利・為替・株式の相場見通し、経済指標やアナリストレポートの要約、解説を行う
  - name: dev-support
    deployment: gpt-5.6-terra
    description: コード生成、レビュー、SQL、バッチ障害の調査などの開発支援を行う
  - name: general
    deployment: gpt-5.6-luna
    description: CATCHALL
```

`market-research` と `dev-support` は意味的には遠いが、**「市場データを扱う開発作業」**
のように題材と成果物が食い違うクエリでは容易に取り違えが起きる。この重なりを
boundary 事例として明示的にデータへ入れ、要件4の調整対象にする。

### `data/golden_dataset.yaml`

正解ラベルであり、チューニング中も**固定する側**。全 40 件、日本語。

```yaml
cases:
  - id: mr-001
    query: "今週の日銀金融政策決定会合の結果が円相場に与える影響を教えて"
    expected: market-research
    difficulty: easy
  - id: bd-001
    query: "株価の時系列データから移動平均とボラティリティを計算するPythonコードを書いて"
    expected: dev-support
    difficulty: boundary
    note: "題材は相場だが、求められている成果物はコードそのもの"
```

内訳:

- **easy 30 件**(各 target 10 件) — description から素直に導けるクエリ
- **boundary 10 件** — target 間の境界に位置し、初期設定では誤りやすいクエリ。
  - `market-research` × `dev-support`: 3 件
    (例: 「株価の時系列データから移動平均とボラティリティを計算するPythonコードを書いて」)
  - `market-research` × `general`: 3 件
    (例: 「今日の相場、どうだった」)
  - `dev-support` × `general`: 4 件
    (例: 「開発チーム向けのリリース手順書のドラフトを書いて」)

`expected` は「その業務を担当すべき target」として人が判断した値であり、
boundary 件については `note` に判断理由を必ず書く。デモ中に見ている側から
「これはどちらでもよいのでは」と問われる前提のデータであるため。

## 9. ノートブックの構成

`notebooks/semantic_routing_tuning.ipynb` を 6 セクションに分ける。

1. **疎通と移送** — 疑似本番 `kong-redis` の `FT._LIST` / `FT.INFO` を表示し、
   `migrate()` で `sandbox-redis` へ移送、検証結果を出す
2. **Golden Dataset の埋め込み** — `golden_dataset.yaml` を読み、`embed()` で 40 件を
   埋め込む。sandbox index の次元と突き合わせて一致を確認する(要件2の機械的な保証)
3. **KNN 近傍探索** — 各クエリで `FT.SEARCH` の KNN を実行し、top-k の target と
   距離を DataFrame で表示
4. **評価と可視化** — 混同行列、誤ルーティング一覧、threshold sweep 曲線、距離分布ヒストグラム
5. **description 改訂ループ** — `routing_targets.yaml` の description を書き換え →
   再埋め込み → `upsert_target_vector()` で sandbox の index を上書き → 3〜4 を再実行。
   before/after を並べて比較する
6. **Kong への反映** — 確定した threshold と description で `kong.yaml` に適用する差分を出力し、
   `deck gateway sync` を案内する

## 10. 評価指標

4 つを出す。

1. **混同行列と誤ルーティング一覧** — どのクエリがどこへ流れたか。1 位と 2 位の距離差を
   併記し、「僅差で外した」のか「まったく別物と判定された」のかを区別する
2. **threshold sweep 曲線** — 0.0〜1.0 を 0.01 刻みで走査し、正解率と CATCHALL 落ち率をプロット
3. **距離分布ヒストグラム** — 正解ペアの距離と不正解ペアの距離を重ねる。
   **この 2 つの分布が重なっている場合、threshold をどこに置いても救えない。
   すなわち description を書き直すべき、という切り分けができる。** 「しきい値の問題」と
   「説明文の問題」を分離して示せる点が、この指標を入れる理由
4. **description 改訂の before/after 比較** — 1〜3 を改訂前後で並べる

## 11. Langfuse 連携

### 今回のスコープ

- Golden Dataset を Langfuse の Dataset として登録する
- threshold / description の案ごとに評価を dataset run として記録し、
  スコア(正解率、boundary 件の正解率)を残す

これにより「Databricks のような実験管理基盤で行う評価管理の代替」として画面で示せる。

### 将来構想(今回は実装しない)

Kong の OTel トレースは既に Langfuse に流れている(`ai-proxy-advanced` の
ペイロードロギングを有効にすればプロンプト本文も乗る。正確なフィールド名は実装時に確認する)。実トラフィックのクエリを Langfuse から引き、
人手でラベル付けして Golden Dataset に追加する運用に発展させられる。
その状態では、本設計の評価ループがそのまま継続的チューニングの基盤になる。

## 12. エラー処理

- **次元不一致**: 埋め込みの次元と sandbox index の次元が異なる場合は即エラーで停止する。
  要件2の「同じモデル・同じ次元数」を人の注意力ではなく機械で保証する
- **移送の検証失敗**: キー数不一致、または抜き取り 1 件のバイト不一致で `MigrationReport` を
  失敗として返す
- **埋め込み API 障害**: 429 / 5xx は指数バックオフで 3 回リトライ。それでも失敗したら例外
- **index 未検出**: 疑似本番に index がない場合、「Kong に 1 度リクエストを流して
  ベクトルを生成させる」旨のメッセージを出す(Kong は初回に index を作るため)

## 13. テスト

- `tests/test_search.py` — `route()` の選択規則。threshold 境界、同着、CATCHALL フォールバック
- `tests/test_evaluate.py` — 混同行列、sweep、距離分布の計算。既知の入力に対する既知の出力
- 埋め込み API と Redis はモック。`search.py` と `evaluate.py` が Redis / Azure に
  直接依存しない設計にしているのはこのため
- ノートブックはテスト対象外。手動実行で確認する

## 14. 実装順序と未確定事項

### 最初に実測で確定させること

**Kong 3.15 の `ai-proxy-advanced` が Redis に作る index 名、キー prefix、スキーマ
(HASH か JSON か、ベクトルフィールド名、payload フィールド名)、および semantic balancer の
選択規則(threshold が距離の上限なのか類似度の下限なのか、CATCHALL の扱い)は
推測せず、実際に Kong を起動して `FT._LIST` / `FT.INFO` と実リクエストで確認する。**
ここを憶測で実装すると後続がすべてずれるため、実装の第 1 ステップに置く。

### 順序

1. `kong.yaml` に `embeddings` / `vectordb` を追加、実務を模した 3 target に差し替え、
   `deck gateway sync` + DP 再起動
2. Kong に 1 リクエスト流してベクトルを生成させ、Redis の実際の構造を調査(上記)
3. `compose.yaml` に `sandbox-redis` を追加
4. `semtune/vectors.py` と移送の動作確認
5. `data/*.yaml` の作成(Golden Dataset 40 件)
6. `semtune/embed.py` / `search.py` / `evaluate.py` と単体テスト
7. ノートブックの組み立て
8. `semtune/langfuse_sync.py`
9. `mise.toml` に `semtune-lab` タスク追加

## 15. デモの筋書き

> **未計測**: 以下の accuracy / fallback / 最良 threshold は、現在の 3 target 構成では
> まだ計測していない。ノートブックを通しで実行し、出た値でここを埋め直すこと。

1. 本番相当の Kong と Redis を見せ、`FT.INFO` で「semantic routing のベクトルが
   ここに入っている」ことを示す
2. sandbox へ移送する。本番には一切書き込まない
3. Golden Dataset 40 件を同じモデル・同じ次元で埋め込み、近傍探索を回す
4. 初期状態を見せる。threshold が狭すぎる場合は大半が CATCHALL に落ち、
   混同行列に target 間の混同は現れない。まず「しきい値が狭すぎる」ことが分かる
5. threshold sweep を見せる。**descriptions を一切変えずにしきい値を動かすだけで
   どこまで改善するか**を先に測る。ここで初めて target 間の混同が表面化する
6. `description` を書き直して再評価する。**各設定をそれぞれの最良 threshold で比較すること。**
   固定した threshold で比べると改訂が良く見えても、最良点どうしでは逆転しうる。
   距離分布とあわせて「しきい値の問題か説明文の問題か」を切り分けて示す
7. 勝った設定を `kong.yaml` に反映し、Kong 経由の実リクエストで `X-Kong-LLM-Model`
   ヘッダを見て狙い通りにルーティングされることを確認する
8. Langfuse を開き、評価が run として蓄積されていることを見せる。
   将来はここのトレースから Dataset を育てられる、と締める

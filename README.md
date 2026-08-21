# Semantic Routing Tuning

Kong AI Gateway の semantic routing の `threshold` と target `description` を、
Golden Dataset に対するベクトル近傍探索の結果をもとに調整するためのツール群。

設計: `docs/design.md`
Kong の Redis 構造の実測記録: `docs/kong-vectordb-structure.md`

> **本リポジトリのスコープ**: Kong Gateway (`ai-proxy-advanced` プラグイン) のみを対象とする。
> AI Gateway v2 (`kongctl` 経由の宣言的設定) は対象外。

## 構成

| 役割 | Redis | ポート |
|---|---|---|
| 疑似本番(Kong が書き込む) | `kong-redis` | 6381 |
| sandbox(ここで実験する) | `sandbox-redis` | 6380 |
| Langfuse 用 | `redis` | 6379 |

### ルーティング先 target

| target | デプロイメント | 役割 |
|---|---|---|
| `market-research` | `gpt-5.6-sol` | 市場動向・相場見通し・経済指標の解説 |
| `dev-support` | `gpt-5.6-terra` | コード生成、レビュー、SQL、バッチ障害の調査 |
| `general` | `gpt-5.6-luna` | CATCHALL(どの target にも当てはまらないとき) |

## 前提条件

フレッシュな clone から始める場合、以下がすべて揃っているか確認すること。
揃っていなければ、以下の手順は動かない。

- **リポジトリルートの `.env`**(gitignore 済み、clone 直後には存在しない)。
  以下の変数を定義すること(値はこのファイルには書かない):
  `PREFIX`(Konnect の region prefix)、`DECK_KONNECT_CONTROL_PLANE`,
  `AZURE_OPENAI_API_KEY`、`DECK_AZURE_OPENAI_API_KEY`(deck が `kong.yaml` の
  `header_value` を解決するのに使う。値は `AZURE_OPENAI_API_KEY` と同じにする)。
- **リポジトリルートの `.certs/`**(gitignore 済み)。Konnect の Data Plane が
  クラスタ証明書として使う `cluster.crt` / `cluster.key`。`compose.yaml` が
  これを `kong` コンテナの `/etc/kong/cluster-certs` にマウントする。
- **`~/.config/deck/.deck.yaml`**(リポジトリの外、マシンにグローバルな設定)。
  deck の Konnect 認証情報。`mise run deck:sync` が実行する
  `deck gateway sync kong.yaml` は `--config` 未指定時のデフォルト参照先として
  このパスを見る。
- **`semtune-gateway` という名前の Konnect control plane** が事前に存在すること
  (`kong.yaml` の `_konnect.control_plane_name` および `.env` の
  `DECK_KONNECT_CONTROL_PLANE` が指す名前)。
- **このデモが使うデプロイメントを持つ Azure OpenAI リソース**。リソース名
  `shukawam-ai-foundry-resource` とデプロイメント名(`gpt-5.6-sol` /
  `gpt-5.6-terra` / `gpt-5.6-luna` / `text-embedding-3-large`)は
  `config/kong/kong.yaml` と `data/routing_targets.yaml` の両方にハードコードされている。
  自分の環境で動かす場合は **この 2 ファイルを両方とも** 手持ちのリソース名・
  デプロイメント名に書き換えること。その際、3 つの target の `description`
  文字列は 2 ファイル間で 1 文字も違わないように保つこと — Kong は Redis の
  キーを `sha256(description)` から導くため、差異があると移送したベクトルと
  対応が取れなくなる(下記「注意」も参照)。
- **`uv`, `mise`, `docker`** がインストール済みであること。

## セットアップ

```bash
set -a; . ./.env; set +a  # DECK_AZURE_OPENAI_API_KEY などを deck / kong に見せる
mise run docker:restart
mise run deck:sync
docker compose -f compose.yaml restart kong

# Kong にリクエストを 1 本流してベクトルを生成させる
curl -s -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"今週の相場を教えて"}]}' > /dev/null

mise run semtune:lab
```

## テスト

```bash
mise run semtune:test
```

## デモの流れ

> **未計測**: 以下の数値(accuracy / boundary_accuracy / fallback_rate / 最良 threshold)は
> **現在の 3 target 構成では未計測**である。過去の計測値は別の target 構成
> (`market-research` / `regulatory-compliance` / `dev-support` / `general` の 4 つ)に
> 対するものであり、そのままでは当てはまらない。ノートブックを一度通しで実行し、
> 実際に出た値でこの表と本文を埋め直すこと。

| 設定 | threshold | accuracy | boundary_accuracy | fallback_rate |
|---|---|---|---|---|
| baseline description | 0.50(現行の設定値) | TBD | TBD | TBD |
| baseline description | TBD(baseline 自身の最良値) | TBD | TBD | TBD |
| revised description | 0.50 | TBD | TBD | TBD |
| revised description | TBD(revised 自身の最良値) | TBD | TBD | TBD |

> **注記(最良 threshold の扱いについて)**: sweep で得られる最良 threshold は、この
> Golden Dataset 上で accuracy を最大化する値をグリッドサーチした結果であり、
> held-out(未使用)データでの検証は行っていない。target の description と
> golden dataset の正解ラベルは同じ担当者が作成しており、データセット自体が
> 小規模かつ自己作成である。したがって得られた値は「確定した最適値」ではなく、
> **実トラフィックで確認すべき出発点**として扱うこと — これはまさに Langfuse
> のトレースパイプラインが実クエリから Dataset を育てて担う役割である。

1. 疑似本番の `FT.INFO` を見せ、semantic routing のベクトルがどこに入っているかを示す
2. sandbox へ移送する(本番には書き込まない)
3. Kong のベクトルと Python 側の埋め込みが一致することを確認する。
   本ノートブックの Section 2 では、Kong が内部で作ったベクトルと本ツールキットが
   同一モデル・同一次元数で作ったベクトルのコサイン距離を突き合わせ、
   0 に極めて近い値(過去の計測では最大 8.99e-07)であることを確認する。
   これが「Kong の外側でベクトル検索を再現し、そこで反復してよい」ことの根拠になる
4. Golden Dataset 40 件で近傍探索し、初期状態(現行の `threshold=0.5`)の混同行列を見せる。
   ここで見るべきは target 間の混同なのか、それとも catchall への集中なのか、という切り分けである
5. threshold sweep を見せる。**description を一切変えずに `threshold` を動かすだけで
   どこまで改善するか**を先に測る。threshold が狭すぎるうちは全件が catchall に落ちるため、
   target 同士が本当に紛らわしいかどうかは混同行列に現れない
6. description を書き直して再評価する。**固定した threshold での比較は誤った結論を導く** —
   各設定はそれぞれの最良 threshold で評価しなければならない、というのが本デモが伝える
   方法論上の要点。全体の accuracy と境界事例の boundary_accuracy がトレードオフになる
   ことがあり、その場合は一方的な勝ちではない
7. 確定値を `kong.yaml` に反映し、`X-Kong-LLM-Model` ヘッダで狙い通りのルーティングを確認する
8. Langfuse に評価が run として蓄積されていることを見せ、将来はトレースから Dataset を育てられると締める

## 注意

- `data/routing_targets.yaml` の `description` は `config/kong/kong.yaml` の対応する target と
  1 文字も違わないこと。Kong はキーを description の sha256 から導くため、
  差異があると移送したベクトルと対応が取れなくなる
- `vectordb.threshold` は**コサイン距離の上限**であり、類似度の下限ではない。
  大きくするほど採用されやすくなる
- `deck sync` の後は OTel メトリクスの export が止まるので Data Plane を再起動する
- Redis クライアントは `protocol=2` を明示して作ること。redis-py 8.1.0 は既定で
  RESP3 を話し、RESP3 だと `FT.INFO` / `FT.SEARCH` の応答が辞書型になり、
  このツールキットのパース処理(フラットなリスト前提)が `KeyError` を送出する
- Kong が Redis に書き込むベクトルは 3 個ではなく **2 個**。CATCHALL target
  (`description: CATCHALL`)に対応するベクトルは書き込まれない
- `push_run` は同名の dataset run が既に存在すると書き込みを拒否する。ノートブックの
  Langfuse セルは実行のたびに同名の run を先に削除してから記録しているため、
  何度再実行しても安全

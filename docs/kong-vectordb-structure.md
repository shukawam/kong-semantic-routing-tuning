# Kong 3.15 ai-proxy-advanced の Redis ベクトル構造(実測)

測定日: 2026-08-08
対象: kong/kong-gateway:3.15 / redis:8.0.2 / kong-redis コンテナ

> **この記録は当時の 4 target 構成に対するもの**(`market-research` /
> `regulatory-compliance` / `dev-support` / CATCHALL)。現在の `kong.yaml` は
> 3 target(`market-research` / `dev-support` / CATCHALL)なので、
> **キー数・`num_docs`・以下に載っている description とその sha256 は現行構成と一致しない**
> (現行では非 CATCHALL が 2 つなのでキーは 2 個になる)。
>
> 一方、**構造に関する記述はそのまま有効**である: `key_type JSON`、
> prefix が `semantic_routing:<plugin-id>:route:` という形式であること、
> ドキュメントが `vector` / `payload` の 2 キーだけを持つこと、
> `payload` が `{"hash": ...}` しか含まないこと、
> **キーの suffix が `sha256(description)` と一致すること**、
> **CATCHALL target のベクトルは書き込まれないこと**。
> これらが本ツールキットの実装根拠であり、target 数には依存しない。

## index

- 名前: `idx:vss_semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route`
- namespace: `semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route`
  (`idx:vss_` の後ろ全部。plugin instance の UUID と `:route` サフィックスを含む —
  単純な `semantic_routing` ではない)
- キー prefix: `semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route:`

## FT.INFO の出力

```
index_name
idx:vss_semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route
index_options

index_definition
key_type
JSON
prefixes
semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route:
default_score
1
indexes_all
false
attributes
identifier
$.vector
attribute
vector
type
VECTOR
algorithm
FLAT
data_type
FLOAT32
dim
3072
distance_metric
COSINE
num_docs
3
max_doc_id
48
num_terms
0
num_records
48
inverted_sz_mb
0
vector_index_sz_mb
12.016357421875
total_inverted_index_blocks
0
offset_vectors_sz_mb
0
doc_table_size_mb
0.01587677001953125
sortable_values_size_mb
0
key_table_size_mb
1.10626220703125e-4
tag_overhead_sz_mb
0
text_overhead_sz_mb
0
total_index_memory_sz_mb
0.015987396240234375
geoshapes_sz_mb
0
records_per_doc_avg
16
bytes_per_record_avg
0
offsets_per_term_avg
0
offset_bits_per_record_avg
nan
hash_indexing_failures
0
total_indexing_time
4.201000213623047
indexing
0
percent_indexed
1
number_of_uses
18
cleaning
0
gc_stats
bytes_collected
0
total_ms_run
0
total_cycles
0
average_cycle_time_ms
nan
last_run_time_ms
0
gc_numeric_trees_missed
0
gc_blocks_denied
0
cursor_stats
global_idle
0
global_total
0
index_capacity
128
index_total
0
dialect_stats
dialect_1
0
dialect_2
0
dialect_3
1
dialect_4
0
Index Errors
indexing failures
0
last indexing error
N/A
last indexing error key
N/A
background indexing status
OK
field statistics
identifier
$.vector
attribute
vector
Index Errors
indexing failures
0
last indexing error
N/A
last indexing error key
N/A
memory
12600064
marked_deleted
0
```

Key facts confirmed: `key_type JSON`, `prefixes` = `semantic_routing:<plugin-id>:route:`,
vector attribute `type VECTOR` / `algorithm FLAT` / `data_type FLOAT32` / `dim 3072` /
`distance_metric COSINE`. `num_docs: 3`.

## キー一覧

```
$ docker exec kong-redis redis-cli --scan --pattern "semantic_routing:*"
semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route:f4c7648a467323f7660ad93c3bad3340d53f95bb4c4be774d0dbdaa00c960fb5
semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route:8cb9842e8aa2c1632cbbc8a4dc9a11aceaf74384213a93e66d08448609008522
semantic_routing:7a05bb81-7b2d-4d95-a236-922bcd935481:route:3d3d9ff050ec9ba9684f5d0912ebeb9d964074ea1fbb4899d08cc503643b2158
```

`redis-cli DBSIZE` = `3` (confirms no other stray keys in this DB).

## ドキュメント構造

- キー数: **3**(4 ではない)。**CATCHALL target のベクトルは保存されない** — Kong は
  semantic 候補となる非 CATCHALL の 3 target(`gpt-5.6-sol` / `gpt-5.6-luna` / `gpt-5.4`)
  のみを埋め込んで Redis に書き込んだ。`gpt-5.4-mini`(`description: CATCHALL`)に対応する
  キーは存在しない。
- Redis の実データ型: `TYPE <key>` → `ReJSON-RL`(RedisJSON)。TTL は `-1`(無期限)。
- ドキュメントのトップレベルキー(`JSON.OBJKEYS $`): `vector`, `payload` の 2 つのみ。
- `$.vector`: `array` / 長さ `3072`(`JSON.TYPE` / `JSON.ARRLEN` で確認)。
- `$.payload`: **`{"hash": "<sha256 hex>"}` のみ**。target の description 本文や
  target 名などは payload に一切含まれない。実際の出力(3 件とも):

  ```
  $ docker exec kong-redis redis-cli JSON.GET "semantic_routing:...:f4c7648a...fb5" '$.payload'
  [{"hash":"f4c7648a467323f7660ad93c3bad3340d53f95bb4c4be774d0dbdaa00c960fb5"}]

  $ docker exec kong-redis redis-cli JSON.GET "semantic_routing:...:8cb9842e...522" '$.payload'
  [{"hash":"8cb9842e8aa2c1632cbbc8a4dc9a11aceaf74384213a93e66d08448609008522"}]

  $ docker exec kong-redis redis-cli JSON.GET "semantic_routing:...:3d3d9ff0...158" '$.payload'
  [{"hash":"3d3d9ff050ec9ba9684f5d0912ebeb9d964074ea1fbb4899d08cc503643b2158"}]
  ```

  **重要な実測事実(後続タスクへの申し送り)**: `payload` には description の平文もmodel名も
  入っていない。target を特定するために必要な情報は `hash` フィールド一つだけであり、それは
  キーの suffix と同一の値(sha256 hex)である。つまり `payload` は実質的にキーの suffix を
  再掲しているだけで、それ以上の情報は持っていない。target 本体(description 全文やどの
  target/model に対応するか)を知りたい場合は、`kong.yaml` 側(deck の設定)からの逆引きが
  必要で、Redis 側のドキュメントだけでは復元できない。

## 備考

- **キーの suffix が target description の sha256 16 進文字列と一致するか: 一致する(確認済み)。**

  測定当時(4 target 構成)、非 CATCHALL の 3 つの description の sha256 は、
  `--scan` で得られた 3 つのキーの suffix と **完全一致**した(1 対 1 で対応。
  順不同だがどのハッシュもどれかのキーに一致)。一方 `CATCHALL` の sha256
  (`5368ca54...`)に対応するキーは Redis 上に存在しなかった
  (= CATCHALL のベクトルは保存されないことの独立した裏付け)。

  結論: **`upsert_target_vector` 等でキーを sha256(description) から直接導出する方式は
  安全に使える**。ただし CATCHALL target だけは同じ方式で鍵を作っても Redis 上に一致する
  ドキュメントが存在しない点に注意。

  **現行(3 target)構成での対応表。** 検証は次のコマンドで再現できる:

  ```
  $ printf '%s' '国内外の市場動向、金利・為替・株式の相場見通し、経済指標やアナリストレポートの要約、解説を行う' | shasum -a 256
  7d9f9804601ab53de61f9404f83a6af781c3bd8ca4c5cd4b3a949b32fb0d37e7  -

  $ printf '%s' 'コード生成、レビュー、SQL、バッチ障害の調査などの開発支援を行う' | shasum -a 256
  49796037f96ead24e8986c4dcd7c61e453d6c1d117c793db11e5f01b08108f92  -

  $ printf '%s' 'CATCHALL' | shasum -a 256
  5368ca54b50a1aadb8887fb55623c3a2d6dc69429db8018be79b89cef3f50e2e  -
  ```

  したがって現行構成で Kong が書き込むキーは **2 個**で、suffix はそれぞれ
  `7d9f9804...`(market-research)と `49796037...`(dev-support)になるはずである。
  `5368ca54...`(CATCHALL)のキーは作られない。**上記のハッシュ値は description から
  計算したものであり、実機での突き合わせはまだ行っていない** — Kong を起動して
  `--scan` した結果と照合すること。

- namespace には plugin instance の UUID(`7a05bb81-7b2d-4d95-a236-922bcd935481`)と
  `:route` という route スコープの識別子が入っている。ブリーフのテンプレートが想定していた
  単純な `idx:vss_semantic_routing` ではなく、`idx:vss_semantic_routing:<plugin-id>:route`
  という形式だった。plugin/route を跨いで一意にするための実装だと考えられる。今後 Task 4 等で
  index 名やキー prefix をハードコードする場合は、この UUID 部分を実行時に
  `FT._LIST` などで取得する必要がある(固定文字列にしてはいけない)。

- Step 5 のリクエストは HTTP 200 で返ったが、レスポンスヘッダは
  `X-Kong-LLM-Model: azure/gpt-5.4-mini`(CATCHALL)だった。期待されていた
  `gpt-5.6-sol` ではなかったが、ブリーフの許容条件どおり「CATCHALL にフォールバックしても
  ベクトルさえ書ければ次に進んでよい」に該当し、実際に 3 件のベクトルが書き込まれたことを
  確認できたため続行した。Kong の notice ログには明示的な `no target can be found under
  threshold` という文字列は出力されなかった(ログレベルの都合と思われる)。Kong 再起動直後の
  数秒間(04:13:41 時点)には `./ai-proxy-advanced/balancer/semantic.lua:27: attempt to index
  field 'vectordb_conf' (a nil value)` という timer エラーが worker 起動直後に出ていたが、
  これは deck sync 直後の設定リロード中の一過性のものであり、04:13:47 の
  `plugin instance is recreated` 以降は解消し、04:13:59 の本番リクエストはエラーなく
  200 で応答した。

"""Golden Dataset の Langfuse 登録と、評価結果の run 記録。

顧客は本来 Databricks でこの評価管理を行っているが、本デモでは Langfuse を代替に使う。
将来的には Kong の OTel トレースから実クエリを拾って Dataset を育てる想定。
"""

from __future__ import annotations

from langfuse.api.core.api_error import ApiError

from semtune.config import Case
from semtune.evaluate import EvalResult
from semtune.search import RoutingDecision

DATASET_NAME = "semantic-routing-golden"


def build_dataset_items(cases: list[Case]) -> list[dict]:
    return [
        {
            "id": case.id,
            "input": {"query": case.query},
            "expected_output": {"target": case.expected},
            "metadata": {"difficulty": case.difficulty, "note": case.note},
        }
        for case in cases
    ]


def build_run_records(
    cases: list[Case],
    decisions: list[RoutingDecision],
    eval_result: EvalResult,
) -> list[dict]:
    records = []
    for case, decision in zip(cases, decisions, strict=True):
        records.append(
            {
                "item_id": case.id,
                "output": {
                    "selected": decision.selected,
                    "fell_back": decision.fell_back,
                    "top1_distance": decision.hits[0].distance if decision.hits else None,
                },
                "scores": {"correct": int(decision.selected == case.expected)},
            }
        )
    return records


def push_dataset(client, cases: list[Case]) -> None:
    """Dataset を作り(既にあればそのまま)、item を登録する。id が同じなら上書きされる。

    ``client.create_dataset`` / ``client.create_dataset_item`` は langfuse 4.14.3 でも
    健在(brief が書かれた 2.x 世代からシグネチャも変わっていない)ため、ここは brief と
    ほぼ同じ形のまま使える。``create_dataset_item`` は docstring 上 "Upserts if an item
    with id already exists" なので、同じ id を渡せば上書きになる。
    """
    client.create_dataset(name=DATASET_NAME)

    for item in build_dataset_items(cases):
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=item["id"],
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )


def push_run(
    client,
    run_name: str,
    cases: list[Case],
    decisions: list[RoutingDecision],
    eval_result: EvalResult,
    metadata: dict,
) -> None:
    """評価を dataset run として記録する。

    metadata には threshold と description のリビジョンを入れ、
    後からどの条件の結果か追えるようにする。

    langfuse 4.x では ``DatasetItem`` (``dataset.items`` の要素)から ``.run(...)``
    コンテキストマネージャが消えている(2.x の ``DatasetItemClient`` は 4.x では
    ``DatasetItem`` という pydantic モデルに置き換わり、実行系のメソッドを持たない)。
    ``root_span.update_trace(...)`` も無く、代わりに ``set_trace_io`` がある。

    4.x で「dataset run に item を結びつける」ための一次プリミティブは
    ``client.api.dataset_run_items.create(run_name=..., dataset_item_id=..., trace_id=...)``
    (低レベル OpenAPI クライアント)である。そこで:

    1. ``client.create_trace_id(seed=...)`` で決定的な trace_id を作る
       (item ごとに再実行しても同じ trace に集約されるように case id をシードにする)。
    2. ``client.start_as_current_observation(trace_context={"trace_id": trace_id}, ...)``
       で trace/span を作り、``span.score_trace(...)`` でスコアを記録する
       (score_trace はそのまま存置されている)。
    3. ``client.api.dataset_run_items.create(...)`` でその trace を
       ``run_name`` の dataset run item として dataset item に紐付ける。

    ``dataset_run_items.create`` に冪等キーは無く、同じ ``run_name`` で 2 回呼ぶと
    DatasetRunItem とスコアが二重に記録される(ノートブックのセル再実行で起こり得る)。
    そのため呼び出し前に ``run_name`` が既存かどうかを確認し、既存なら黙って重複させず
    例外を上げる。4.14.3 には run 全体を安全に upsert できる専用 API は無い
    (``delete_dataset_run`` + 再作成は非アトミックな合成になるため採用しなかった)。

    同様に、``cases`` に含まれる id が dataset にまだ無い場合、その case は
    黙って読み飛ばされず(=run item が作られないまま件数が減る)、例外を上げる。
    """
    dataset = client.get_dataset(DATASET_NAME)

    missing = sorted({case.id for case in cases} - {item.id for item in dataset.items})
    if missing:
        raise ValueError(
            f"{len(missing)} case id(s) not found in dataset {DATASET_NAME!r}: {missing}. "
            "Call push_dataset(client, cases) first so every case exists in Langfuse "
            "before recording a run against it."
        )

    if _dataset_run_exists(client, run_name):
        raise ValueError(
            f"dataset run {run_name!r} already exists for dataset {DATASET_NAME!r}. "
            "Re-running push_run with this run_name would duplicate its dataset run "
            "items and scores. Use a different run_name, or delete the existing run "
            f"first with client.delete_dataset_run(dataset_name={DATASET_NAME!r}, "
            "run_name=<run_name>)."
        )

    records = {r["item_id"]: r for r in build_run_records(cases, decisions, eval_result)}

    run_metadata = {
        **metadata,
        "accuracy": eval_result.accuracy,
        "boundary_accuracy": eval_result.boundary_accuracy,
        "fallback_rate": eval_result.fallback_rate,
    }

    for item in dataset.items:
        record = records.get(item.id)
        if record is None:
            continue

        trace_id = client.create_trace_id(seed=f"{run_name}:{item.id}")

        with client.start_as_current_observation(
            name=run_name,
            trace_context={"trace_id": trace_id},
            input=item.input,
            output=record["output"],
        ) as span:
            span.score_trace(name="correct", value=record["scores"]["correct"])

        client.api.dataset_run_items.create(
            run_name=run_name,
            dataset_item_id=item.id,
            trace_id=trace_id,
            metadata=run_metadata,
        )

    client.flush()


def _dataset_run_exists(client, run_name: str) -> bool:
    """``run_name`` の dataset run が既に存在するかどうかを確認する。

    ``client.get_dataset_run`` は見つからない場合、langfuse 4.14.3 の実サーバに
    対しては ``status_code == 404`` の ``ApiError`` を投げる(ライブ環境で確認済み)。
    それ以外のステータスコードや例外型は隠さずそのまま送出する。
    """
    try:
        client.get_dataset_run(dataset_name=DATASET_NAME, run_name=run_name)
        return True
    except ApiError as e:
        if e.status_code == 404:
            return False
        raise

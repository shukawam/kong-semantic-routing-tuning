from types import SimpleNamespace

import pytest
from langfuse.api.core.api_error import ApiError

from semtune.config import Case
from semtune.evaluate import evaluate
from semtune.langfuse_sync import DATASET_NAME, build_dataset_items, build_run_records, push_run
from semtune.search import Hit, decide

CASES = [
    Case(id="a", query="q1", expected="market-research", difficulty="easy"),
    Case(id="b", query="q2", expected="dev-support", difficulty="boundary", note="why"),
]

HITS = {
    "a": [Hit("market-research", 0.20)],
    "b": [Hit("market-research", 0.30)],
}


def test_dataset_items_carry_query_and_expected():
    items = build_dataset_items(CASES)

    assert items[0]["id"] == "a"
    assert items[0]["input"] == {"query": "q1"}
    assert items[0]["expected_output"] == {"target": "market-research"}


def test_dataset_items_carry_difficulty_and_note_as_metadata():
    items = build_dataset_items(CASES)

    assert items[1]["metadata"]["difficulty"] == "boundary"
    assert items[1]["metadata"]["note"] == "why"


def test_run_records_include_selected_target_and_correctness():
    decisions = [decide(HITS[c.id], 0.5, "general") for c in CASES]
    result = evaluate(CASES, decisions, ["market-research", "dev-support", "general"])

    records = build_run_records(CASES, decisions, result)

    assert records[0]["item_id"] == "a"
    assert records[0]["output"]["selected"] == "market-research"
    assert records[0]["scores"]["correct"] == 1
    assert records[1]["scores"]["correct"] == 0


def test_run_records_include_top1_distance():
    decisions = [decide(HITS[c.id], 0.5, "general") for c in CASES]
    result = evaluate(CASES, decisions, ["market-research", "dev-support", "general"])

    records = build_run_records(CASES, decisions, result)

    assert records[0]["output"]["top1_distance"] == 0.20


class _FakeDatasetRunItems:
    """`client.api.dataset_run_items` の最小スタブ。呼ばれた create() を記録するだけ。"""

    def __init__(self):
        self.created: list[dict] = []

    def create(self, **kwargs):
        self.created.append(kwargs)


class _FakeApi:
    def __init__(self):
        self.dataset_run_items = _FakeDatasetRunItems()


class _FakeSpan:
    def score_trace(self, *, name, value):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeLangfuseClient:
    """push_run が触れるメソッドだけを持つ Langfuse クライアントのフェイク。

    実クライアントに触れずに push_run のガード条件(重複 run / 欠落 case)を
    検証するためのもの。
    """

    def __init__(self, dataset_items, existing_run_names=()):
        self._dataset = SimpleNamespace(items=dataset_items)
        self._existing_run_names = set(existing_run_names)
        self.api = _FakeApi()
        self.flushed = False

    def get_dataset(self, name):
        assert name == DATASET_NAME
        return self._dataset

    def get_dataset_run(self, *, dataset_name, run_name):
        if run_name in self._existing_run_names:
            return SimpleNamespace(name=run_name)
        raise ApiError(status_code=404, body={"error": "LangfuseNotFoundError"})

    def create_trace_id(self, *, seed=None):
        return seed or "trace-id"

    def start_as_current_observation(self, *, name, trace_context, input, output):
        return _FakeSpan()

    def flush(self):
        self.flushed = True


def _decisions_and_result():
    decisions = [decide(HITS[c.id], 0.5, "general") for c in CASES]
    result = evaluate(CASES, decisions, ["market-research", "dev-support", "general"])
    return decisions, result


def test_push_run_raises_when_run_name_already_exists():
    decisions, result = _decisions_and_result()
    dataset_items = [SimpleNamespace(id=case.id, input={"query": case.query}) for case in CASES]
    client = FakeLangfuseClient(dataset_items, existing_run_names={"baseline"})

    with pytest.raises(ValueError) as exc_info:
        push_run(client, "baseline", CASES, decisions, result, metadata={})

    assert "baseline" in str(exc_info.value)
    assert client.api.dataset_run_items.created == []


def test_push_run_raises_when_case_id_missing_from_dataset():
    decisions, result = _decisions_and_result()
    # "b" is a golden case but was never pushed to the dataset.
    dataset_items = [SimpleNamespace(id="a", input={"query": "q1"})]
    client = FakeLangfuseClient(dataset_items)

    with pytest.raises(ValueError) as exc_info:
        push_run(client, "baseline", CASES, decisions, result, metadata={})

    assert "b" in str(exc_info.value)
    assert client.api.dataset_run_items.created == []

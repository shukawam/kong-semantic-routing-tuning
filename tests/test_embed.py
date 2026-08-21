import httpx
import numpy as np
import pytest

from semtune import embed as embed_module
from semtune.config import EmbeddingConfig
from semtune.embed import Embedder, cache_key, cosine_distance

CONFIG = EmbeddingConfig(
    provider="azure",
    instance="test-instance",
    deployment="text-embedding-3-large",
    api_version="2024-02-01",
    dimensions=4,
    distance_metric="cosine",
)


def test_cache_key_is_stable_for_same_input():
    assert cache_key(CONFIG, "hello") == cache_key(CONFIG, "hello")


def test_cache_key_differs_when_text_differs():
    assert cache_key(CONFIG, "hello") != cache_key(CONFIG, "world")


def test_cache_key_differs_when_dimensions_differ():
    other = EmbeddingConfig(**{**CONFIG.__dict__, "dimensions": 8})
    assert cache_key(CONFIG, "hello") != cache_key(other, "hello")


def test_cosine_distance_of_identical_vectors_is_zero():
    v = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
    assert cosine_distance(v, v) == pytest.approx(0.0, abs=1e-6)


def test_cosine_distance_of_opposite_vectors_is_two():
    v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_distance(v, -v) == pytest.approx(2.0, abs=1e-6)


def test_cosine_distance_of_orthogonal_vectors_is_one():
    a = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    assert cosine_distance(a, b) == pytest.approx(1.0, abs=1e-6)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FakeClient:
    """httpx.Client の最小の代役。呼ばれた回数を数える。"""

    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = 0

    def post(self, url, json, headers):
        self.calls += 1
        data = [{"embedding": v} for v in self.vectors[: len(json["input"])]]
        return FakeResponse({"data": data})


def test_embed_returns_matrix_with_configured_dimensions(tmp_path):
    client = FakeClient([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
    embedder = Embedder(CONFIG, api_key="k", cache_dir=tmp_path, client=client)

    result = embedder.embed(["a", "b"])

    assert result.shape == (2, 4)
    assert result.dtype == np.float32


def test_embed_rejects_dimension_mismatch(tmp_path):
    client = FakeClient([[1.0, 0.0, 0.0]])  # 3 dims but config says 4
    embedder = Embedder(CONFIG, api_key="k", cache_dir=tmp_path, client=client)

    with pytest.raises(ValueError, match="3072|dimension"):
        embedder.embed(["a"])


def test_embed_uses_cache_on_second_call(tmp_path):
    client = FakeClient([[1.0, 0.0, 0.0, 0.0]])
    embedder = Embedder(CONFIG, api_key="k", cache_dir=tmp_path, client=client)

    embedder.embed(["a"])
    embedder.embed(["a"])

    assert client.calls == 1


class FakeStatusResponse:
    """A response that always fails, either as a retryable status or via
    raise_for_status() raising httpx.HTTPStatusError (mirrors real httpx behavior
    for 4xx/5xx responses)."""

    def __init__(self, status_code):
        self.status_code = status_code

    def json(self):
        return {"data": []}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=request, response=response
            )


class ConstantStatusClient:
    """httpx.Client の代役。常に同じステータスコードを返し、呼び出し回数を数える。"""

    def __init__(self, status_code):
        self.status_code = status_code
        self.calls = 0

    def post(self, url, json, headers):
        self.calls += 1
        return FakeStatusResponse(self.status_code)


def test_embed_retries_up_to_max_retries_on_429(tmp_path, monkeypatch):
    monkeypatch.setattr(embed_module.time, "sleep", lambda _: None)
    client = ConstantStatusClient(429)
    embedder = Embedder(CONFIG, api_key="k", cache_dir=tmp_path, client=client)

    with pytest.raises(RuntimeError):
        embedder.embed(["a"])

    assert client.calls == embed_module.MAX_RETRIES


def test_embed_does_not_retry_on_401(tmp_path, monkeypatch):
    monkeypatch.setattr(embed_module.time, "sleep", lambda _: None)
    client = ConstantStatusClient(401)
    embedder = Embedder(CONFIG, api_key="k", cache_dir=tmp_path, client=client)

    with pytest.raises(Exception):
        embedder.embed(["a"])

    assert client.calls == 1

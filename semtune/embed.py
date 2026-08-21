"""Azure OpenAI の埋め込み API クライアント。

Kong の semantic routing が使うのと同一のモデル・次元数で埋め込みを作る。
次元が設定と一致しない場合は即座に失敗する(Global Constraints 参照)。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
import numpy as np

from semtune.config import EmbeddingConfig

MAX_RETRIES = 3
RETRY_STATUS = {429, 500, 502, 503, 504}


def cache_key(config: EmbeddingConfig, text: str) -> str:
    material = f"{config.deployment}|{config.dimensions}|{text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """RediSearch の COSINE と同じ定義(1 - cosine_similarity、範囲 [0, 2])。"""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / denom)


class Embedder:
    def __init__(
        self,
        config: EmbeddingConfig,
        api_key: str,
        cache_dir: Path | None = None,
        client: object | None = None,
    ) -> None:
        self.config = config
        self.api_key = api_key
        self.cache_dir = cache_dir
        self.client = client or httpx.Client(timeout=60.0)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def url(self) -> str:
        return (
            f"https://{self.config.instance}.openai.azure.com"
            f"/openai/deployments/{self.config.deployment}/embeddings"
            f"?api-version={self.config.api_version}"
        )

    def _cache_path(self, text: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{cache_key(self.config, text)}.npy"

    def _load_cached(self, text: str) -> np.ndarray | None:
        path = self._cache_path(text)
        if path is not None and path.exists():
            return np.load(path)
        return None

    def _store_cached(self, text: str, vector: np.ndarray) -> None:
        path = self._cache_path(text)
        if path is not None:
            np.save(path, vector)

    def _fetch(self, texts: list[str]) -> list[list[float]]:
        payload = {"input": texts, "dimensions": self.config.dimensions}
        headers = {"Content-Type": "application/json", "api-key": self.api_key}

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self.client.post(self.url, json=payload, headers=headers)
                if getattr(response, "status_code", 200) in RETRY_STATUS:
                    raise RuntimeError(f"retryable status {response.status_code}")
                response.raise_for_status()
                return [item["embedding"] for item in response.json()["data"]]
            except (RuntimeError, httpx.TransportError) as exc:
                # Only retry statuses in RETRY_STATUS (raised above as RuntimeError)
                # and transport-level failures (connection/timeout errors). Anything
                # else - e.g. a 401 surfaced via httpx.HTTPStatusError below - is not
                # transient and must not be retried.
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2**attempt)
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(f"embedding request failed with non-retryable status: {exc}") from exc

        raise RuntimeError(f"embedding request failed after {MAX_RETRIES} attempts") from last_error

    def embed(self, texts: list[str]) -> np.ndarray:
        result: list[np.ndarray | None] = [self._load_cached(t) for t in texts]
        missing = [i for i, v in enumerate(result) if v is None]

        if missing:
            fetched = self._fetch([texts[i] for i in missing])
            for i, raw in zip(missing, fetched, strict=True):
                vector = np.asarray(raw, dtype=np.float32)
                if vector.shape != (self.config.dimensions,):
                    raise ValueError(
                        f"dimension mismatch: expected {self.config.dimensions}, "
                        f"got {vector.shape[0]} for input {texts[i]!r}"
                    )
                self._store_cached(texts[i], vector)
                result[i] = vector

        return np.vstack([v for v in result if v is not None]).astype(np.float32)

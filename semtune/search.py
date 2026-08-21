"""Kong の semantic balancer の選択規則の再現と、Redis に対する KNN 検索。

decide() は純粋関数で、Redis にも Azure にも依存しない。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Hit:
    target: str
    distance: float


@dataclass(frozen=True)
class RoutingDecision:
    selected: str
    hits: list[Hit] = field(default_factory=list)
    fell_back: bool = False


def decide(hits: list[Hit], threshold: float, catchall: str) -> RoutingDecision:
    """Kong の semantic balancer と同じ規則で target を 1 つ選ぶ。

    threshold はコサイン距離の上限(VECTOR_RANGE の半径)。距離がこれ以下の
    最近傍を採用し、該当がなければ catchall へフォールバックする。
    CATCHALL target 自身は距離による選択の候補にしない。
    """
    candidates = sorted(
        (h for h in hits if h.target != catchall),
        key=lambda h: (h.distance, h.target),
    )

    for hit in candidates:
        if hit.distance <= threshold:
            return RoutingDecision(selected=hit.target, hits=candidates, fell_back=False)

    return RoutingDecision(selected=catchall, hits=candidates, fell_back=True)


def to_float32_bytes(vector: np.ndarray) -> bytes:
    """RediSearch の FLOAT32 ベクトルパラメータ形式に変換する。"""
    return np.asarray(vector, dtype=np.float32).tobytes()


def knn(client, index: str, query_vector: np.ndarray, k: int) -> list[Hit]:
    """sandbox Redis の index に KNN 検索を投げ、target 名と距離を返す。

    index は semtune が作った評価用 index を想定しており、payload に
    target 名を格納している。
    """
    raw = client.execute_command(
        "FT.SEARCH",
        index,
        f"(*)=>[KNN {k} @vector $query_vector AS score]",
        "SORTBY",
        "score",
        "LIMIT",
        "0",
        str(k),
        "RETURN",
        "4",
        "score",
        "$.payload",
        "AS",
        "payload",
        "DIALECT",
        "3",
        "PARAMS",
        "2",
        "query_vector",
        to_float32_bytes(query_vector),
    )

    hits: list[Hit] = []
    # raw = [total, key1, [field, value, ...], key2, [...], ...]
    for i in range(2, len(raw), 2):
        fields = raw[i]
        values = {
            _as_str(fields[j]): _as_str(fields[j + 1]) for j in range(0, len(fields), 2)
        }
        payload = json.loads(values["payload"])
        target = payload[0] if isinstance(payload, list) else payload
        hits.append(Hit(target=str(target), distance=float(values["score"])))

    return hits


def _as_str(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)

"""Redis のベクトルストアの内省・移送・書き込み。

semtune のなかで Redis を知るのはこのモジュールだけ。
Kong が作る index の構造は docs/notes/kong-vectordb-structure.md を参照。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np

from semtune.config import RoutingConfig

METRIC_MAP = {"cosine": "COSINE", "euclidean": "L2"}


@dataclass(frozen=True)
class IndexSpec:
    index: str
    prefix: str
    dimensions: int
    distance_metric: str
    storage: str


@dataclass(frozen=True)
class MigrationReport:
    index: str
    keys_copied: int
    verified: bool
    message: str


def _as_str(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _pairs_to_dict(flat: list) -> dict:
    return {_as_str(flat[i]): flat[i + 1] for i in range(0, len(flat) - 1, 2)}


def parse_ft_info(raw: list) -> IndexSpec:
    """FT.INFO の生レスポンスから IndexSpec を組み立てる。"""
    info = _pairs_to_dict(raw)

    definition = _pairs_to_dict(info["index_definition"])
    storage = _as_str(definition["key_type"])
    if storage != "JSON":
        raise ValueError(
            f"unsupported storage type {storage!r}; "
            "Kong の semantic routing は JSON ドキュメントを使う"
        )

    prefixes = definition["prefixes"]
    prefix = _as_str(prefixes[0])

    attribute = _pairs_to_dict(info["attributes"][0])

    return IndexSpec(
        index=_as_str(info["index_name"]),
        prefix=prefix,
        dimensions=int(attribute["dim"]),
        distance_metric=_as_str(attribute["distance_metric"]),
        storage=storage,
    )


def build_ft_create_args(spec: IndexSpec) -> list[str]:
    """Kong が発行するのと同じ FT.CREATE の引数列を組み立てる。"""
    return [
        "FT.CREATE",
        spec.index,
        "ON",
        "JSON",
        "PREFIX",
        "1",
        spec.prefix,
        "SCORE",
        "1.0",
        "SCHEMA",
        "$.vector",
        "AS",
        "vector",
        "VECTOR",
        "FLAT",
        "6",
        "TYPE",
        "FLOAT32",
        "DIM",
        str(spec.dimensions),
        "DISTANCE_METRIC",
        spec.distance_metric,
    ]


def discover_index(client, namespace_prefix: str = "semantic_routing:") -> IndexSpec:
    """FT._LIST から Kong の semantic routing index を発見する。"""
    names = [_as_str(n) for n in client.execute_command("FT._LIST")]
    matches = [n for n in names if n.startswith(f"idx:vss_{namespace_prefix}")]

    if not matches:
        raise LookupError(
            f"no index starting with 'idx:vss_{namespace_prefix}' found. "
            "Kong に 1 度リクエストを流してベクトルを生成させてください "
            "(ベクトルは起動時ではなく初回リクエスト時に書き込まれます)"
        )
    if len(matches) > 1:
        raise LookupError(f"multiple candidate indexes found: {matches}")

    return parse_ft_info(client.execute_command("FT.INFO", matches[0]))


def migrate(src, dst, spec: IndexSpec) -> MigrationReport:
    """src の index 配下のキーを dst へ複製する。

    index を同一スキーマで再作成してから、prefix 一致のキーを DUMP/RESTORE で移す。
    RediSearch は prefix にマッチするキーを自動で取り込むため、明示的な再索引は不要。
    """
    try:
        dst.execute_command("FT.DROPINDEX", spec.index)
    except Exception:  # noqa: BLE001 - index が無いのは正常
        pass

    dst.execute_command(*build_ft_create_args(spec))

    keys = [_as_str(k) for k in src.scan_iter(match=f"{spec.prefix}*", count=100)]
    for key in keys:
        payload = src.dump(key)
        dst.delete(key)
        dst.restore(key, 0, payload)

    dst_keys = [_as_str(k) for k in dst.scan_iter(match=f"{spec.prefix}*", count=100)]
    if len(dst_keys) != len(keys):
        return MigrationReport(
            index=spec.index,
            keys_copied=len(dst_keys),
            verified=False,
            message=f"key count mismatch: src={len(keys)} dst={len(dst_keys)}",
        )

    if keys:
        sample = keys[0]
        if src.dump(sample) != dst.dump(sample):
            return MigrationReport(
                index=spec.index,
                keys_copied=len(dst_keys),
                verified=False,
                message=f"byte mismatch on sampled key {sample}",
            )

    return MigrationReport(
        index=spec.index,
        keys_copied=len(dst_keys),
        verified=True,
        message=f"copied {len(dst_keys)} keys and verified",
    )


def create_eval_index(
    client,
    config: RoutingConfig,
    index: str = "idx:vss_semtune",
    prefix: str = "semtune:",
) -> IndexSpec:
    """評価用の index を作り直す。既存があれば中身ごと落とす。"""
    spec = IndexSpec(
        index=index,
        prefix=prefix,
        dimensions=config.embeddings.dimensions,
        distance_metric=METRIC_MAP[config.embeddings.distance_metric],
        storage="JSON",
    )

    try:
        client.execute_command("FT.DROPINDEX", index, "DD")
    except Exception:  # noqa: BLE001 - index が無いのは正常
        pass

    client.execute_command(*build_ft_create_args(spec))
    return spec


def upsert_target_vector(client, spec: IndexSpec, target: str, vector: np.ndarray) -> str:
    """評価用 index に target 1 件分のドキュメントを書く。

    Kong と同じ形 {"payload": ..., "vector": [...]} で保存する。
    payload には target 名だけを入れる(評価では target の同定しか要らない)。
    """
    key = f"{spec.prefix}{target}"
    document = {"payload": target, "vector": np.asarray(vector, dtype=np.float32).tolist()}
    client.execute_command("JSON.SET", key, "$", json.dumps(document))
    return key


def read_vector(client, key: str) -> np.ndarray:
    """ドキュメントの $.vector を numpy 配列で読む。"""
    raw = client.execute_command("JSON.GET", key, "$.vector")
    if raw is None:
        raise KeyError(f"no such key: {key}")
    values = json.loads(_as_str(raw))
    return np.asarray(values[0], dtype=np.float32)

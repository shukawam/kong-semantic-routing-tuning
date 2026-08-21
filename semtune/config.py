"""routing_targets.yaml と golden_dataset.yaml の読み込みとバリデーション。

このモジュールは他の semtune モジュールに依存しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CATCHALL_DESCRIPTION = "CATCHALL"


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    instance: str
    deployment: str
    api_version: str
    dimensions: int
    distance_metric: str


@dataclass(frozen=True)
class Target:
    name: str
    deployment: str
    description: str


@dataclass(frozen=True)
class RoutingConfig:
    embeddings: EmbeddingConfig
    threshold: float
    targets: list[Target]

    def catchall_name(self) -> str | None:
        for target in self.targets:
            if target.description == CATCHALL_DESCRIPTION:
                return target.name
        return None

    def target_names(self) -> list[str]:
        return [t.name for t in self.targets]


@dataclass(frozen=True)
class Case:
    id: str
    query: str
    expected: str
    difficulty: str
    note: str | None = None


def load_routing_config(path: Path) -> RoutingConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    embeddings = EmbeddingConfig(**raw["embeddings"])
    targets = [Target(**t) for t in raw["targets"]]

    names = [t.name for t in targets]
    if len(names) != len(set(names)):
        raise ValueError(f"target names must be unique: {names}")

    catchalls = [t for t in targets if t.description == CATCHALL_DESCRIPTION]
    if len(catchalls) != 1:
        raise ValueError(
            f"exactly one target must have description {CATCHALL_DESCRIPTION!r}, "
            f"found {len(catchalls)}"
        )

    return RoutingConfig(
        embeddings=embeddings,
        threshold=float(raw["threshold"]),
        targets=targets,
    )


def load_golden_dataset(path: Path, config: RoutingConfig) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    known = set(config.target_names())

    cases: list[Case] = []
    seen_ids: set[str] = set()
    for entry in raw["cases"]:
        case = Case(
            id=entry["id"],
            query=entry["query"],
            expected=entry["expected"],
            difficulty=entry["difficulty"],
            note=entry.get("note"),
        )
        if case.expected not in known:
            raise ValueError(
                f"case {case.id}: unknown expected target {case.expected!r}, "
                f"known targets are {sorted(known)}"
            )
        if case.id in seen_ids:
            raise ValueError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)
        cases.append(case)

    return cases

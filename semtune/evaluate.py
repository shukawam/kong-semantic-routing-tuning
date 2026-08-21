"""Golden Dataset に対する評価。

Redis にも Azure にも依存しない。検索済みの Hit を受け取って計算するだけ。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from semtune.config import Case
from semtune.search import Hit, RoutingDecision, decide


@dataclass
class EvalResult:
    accuracy: float
    boundary_accuracy: float
    fallback_rate: float
    confusion: pd.DataFrame
    mistakes: pd.DataFrame


def _margin(hits: list[Hit]) -> float | None:
    if len(hits) < 2:
        return None
    return round(hits[1].distance - hits[0].distance, 6)


def evaluate(
    cases: list[Case],
    decisions: list[RoutingDecision],
    target_names: list[str],
) -> EvalResult:
    if len(cases) != len(decisions):
        raise ValueError(
            f"cases and decisions must align: {len(cases)} vs {len(decisions)}"
        )

    known = set(target_names)
    for case, decision in zip(cases, decisions, strict=True):
        if case.expected not in known:
            raise ValueError(
                f"unknown expected target {case.expected!r} on case {case.id!r} "
                f"(expected side); known target_names are {target_names}"
            )
        if decision.selected not in known:
            raise ValueError(
                f"unknown selected target {decision.selected!r} on case {case.id!r} "
                f"(selected side); known target_names are {target_names}"
            )

    confusion = pd.DataFrame(0, index=target_names, columns=target_names, dtype=int)
    mistakes: list[dict] = []
    correct = 0
    boundary_total = 0
    boundary_correct = 0
    fallbacks = 0

    for case, decision in zip(cases, decisions, strict=True):
        confusion.loc[case.expected, decision.selected] += 1

        if decision.fell_back:
            fallbacks += 1

        is_correct = decision.selected == case.expected
        if is_correct:
            correct += 1

        if case.difficulty == "boundary":
            boundary_total += 1
            if is_correct:
                boundary_correct += 1

        if not is_correct:
            mistakes.append(
                {
                    "case_id": case.id,
                    "query": case.query,
                    "expected": case.expected,
                    "actual": decision.selected,
                    "difficulty": case.difficulty,
                    "top1_distance": decision.hits[0].distance if decision.hits else None,
                    "margin": _margin(decision.hits),
                }
            )

    total = len(cases)
    return EvalResult(
        accuracy=correct / total if total else 0.0,
        boundary_accuracy=boundary_correct / boundary_total if boundary_total else 0.0,
        fallback_rate=fallbacks / total if total else 0.0,
        confusion=confusion,
        mistakes=pd.DataFrame(
            mistakes,
            columns=[
                "case_id",
                "query",
                "expected",
                "actual",
                "difficulty",
                "top1_distance",
                "margin",
            ],
        ),
    )


def sweep(
    cases: list[Case],
    hits_by_case: dict[str, list[Hit]],
    thresholds: list[float],
    catchall: str,
    target_names: list[str],
) -> pd.DataFrame:
    """threshold ごとに正解率・boundary 正解率・フォールバック率を計算する。

    target_names は呼び出し側から渡す。Golden Dataset に出現する target だけ
    から導くと、golden case が 1 件も無い target を decide() が選んだ際に
    confusion 行列の KeyError で落ちるため。
    """
    rows = []
    for threshold in thresholds:
        decisions = [decide(hits_by_case[c.id], threshold, catchall) for c in cases]
        result = evaluate(cases, decisions, target_names)
        rows.append(
            {
                "threshold": threshold,
                "accuracy": result.accuracy,
                "boundary_accuracy": result.boundary_accuracy,
                "fallback_rate": result.fallback_rate,
            }
        )

    return pd.DataFrame(rows)


def distance_distribution(
    cases: list[Case],
    hits_by_case: dict[str, list[Hit]],
) -> pd.DataFrame:
    """全 (case, target) ペアの距離を、正解 target かどうかのフラグ付きで返す。

    正解ペアと不正解ペアの分布が重なっているなら、threshold をどこに置いても
    分離できない。すなわち description を書き直すべきという判断ができる。
    """
    rows = []
    for case in cases:
        for hit in hits_by_case[case.id]:
            rows.append(
                {
                    "case_id": case.id,
                    "target": hit.target,
                    "distance": hit.distance,
                    "is_correct_target": hit.target == case.expected,
                }
            )

    return pd.DataFrame(
        rows, columns=["case_id", "target", "distance", "is_correct_target"]
    )

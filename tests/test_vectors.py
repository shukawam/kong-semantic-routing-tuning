import pytest

from semtune.vectors import IndexSpec, build_ft_create_args, parse_ft_info

# Task 1 で実測した FT.INFO の形。キーと値が交互に並ぶフラットな配列。
FT_INFO_SAMPLE = [
    "index_name",
    "idx:vss_semantic_routing:abc123",
    "index_options",
    [],
    "index_definition",
    ["key_type", "JSON", "prefixes", ["semantic_routing:abc123:"], "default_score", "1"],
    "attributes",
    [
        [
            "identifier",
            "$.vector",
            "attribute",
            "vector",
            "type",
            "VECTOR",
            "algorithm",
            "FLAT",
            "data_type",
            "FLOAT32",
            "dim",
            3072,
            "distance_metric",
            "COSINE",
        ]
    ],
    "num_docs",
    4,
]


def test_parse_ft_info_extracts_index_name_and_prefix():
    spec = parse_ft_info(FT_INFO_SAMPLE)

    assert spec.index == "idx:vss_semantic_routing:abc123"
    assert spec.prefix == "semantic_routing:abc123:"


def test_parse_ft_info_extracts_vector_attributes():
    spec = parse_ft_info(FT_INFO_SAMPLE)

    assert spec.dimensions == 3072
    assert spec.distance_metric == "COSINE"
    assert spec.storage == "JSON"


def test_parse_ft_info_rejects_non_json_storage():
    broken = list(FT_INFO_SAMPLE)
    broken[5] = ["key_type", "HASH", "prefixes", ["x:"], "default_score", "1"]

    with pytest.raises(ValueError, match="HASH"):
        parse_ft_info(broken)


def test_build_ft_create_args_matches_kong_schema():
    spec = IndexSpec(
        index="idx:vss_semtune",
        prefix="semtune:",
        dimensions=3072,
        distance_metric="COSINE",
        storage="JSON",
    )

    args = build_ft_create_args(spec)

    assert args == [
        "FT.CREATE",
        "idx:vss_semtune",
        "ON",
        "JSON",
        "PREFIX",
        "1",
        "semtune:",
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
        "3072",
        "DISTANCE_METRIC",
        "COSINE",
    ]

import pytest

from semtune.config import (
    DATA_DIR,
    load_golden_dataset,
    load_routing_config,
)


@pytest.fixture
def routing_config():
    return load_routing_config(DATA_DIR / "routing_targets.yaml")


def test_routing_config_matches_kong_settings(routing_config):
    emb = routing_config.embeddings
    assert emb.provider == "azure"
    assert emb.instance == "shukawam-ai-foundry-resource"
    assert emb.deployment == "text-embedding-3-large"
    assert emb.api_version == "2024-02-01"
    assert emb.dimensions == 3072
    assert emb.distance_metric == "cosine"


def test_routing_config_has_exactly_one_catchall(routing_config):
    catchalls = [t for t in routing_config.targets if t.description == "CATCHALL"]
    assert len(catchalls) == 1
    assert routing_config.catchall_name() == catchalls[0].name


def test_target_names_are_unique(routing_config):
    names = [t.name for t in routing_config.targets]
    assert len(names) == len(set(names))


def test_golden_dataset_size_and_ids(routing_config):
    cases = load_golden_dataset(DATA_DIR / "golden_dataset.yaml", routing_config)
    assert len(cases) == 40
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_golden_dataset_difficulty_breakdown(routing_config):
    cases = load_golden_dataset(DATA_DIR / "golden_dataset.yaml", routing_config)
    easy = [c for c in cases if c.difficulty == "easy"]
    boundary = [c for c in cases if c.difficulty == "boundary"]
    assert len(easy) == 30
    assert len(boundary) == 10


def test_every_boundary_case_has_a_note(routing_config):
    cases = load_golden_dataset(DATA_DIR / "golden_dataset.yaml", routing_config)
    for case in cases:
        if case.difficulty == "boundary":
            assert case.note, f"boundary case {case.id} must explain its expected label"


def test_unknown_expected_target_is_rejected(tmp_path, routing_config):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "cases:\n"
        "  - id: x-001\n"
        "    query: test\n"
        "    expected: no-such-target\n"
        "    difficulty: easy\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no-such-target"):
        load_golden_dataset(bad, routing_config)

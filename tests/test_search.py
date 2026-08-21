from semtune.search import Hit, decide


def test_picks_nearest_hit_within_threshold():
    hits = [Hit("dev-support", 0.42), Hit("market-research", 0.31)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert decision.selected == "market-research"
    assert decision.fell_back is False


def test_falls_back_to_catchall_when_all_hits_exceed_threshold():
    hits = [Hit("market-research", 0.61), Hit("dev-support", 0.72)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert decision.selected == "general"
    assert decision.fell_back is True


def test_distance_exactly_at_threshold_is_accepted():
    hits = [Hit("market-research", 0.5)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert decision.selected == "market-research"
    assert decision.fell_back is False


def test_catchall_target_is_never_selected_by_distance():
    hits = [Hit("general", 0.10), Hit("market-research", 0.20)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert decision.selected == "market-research"
    assert decision.fell_back is False


def test_empty_hits_fall_back():
    decision = decide([], threshold=0.5, catchall="general")

    assert decision.selected == "general"
    assert decision.fell_back is True


def test_tie_is_broken_deterministically_by_target_name():
    hits = [Hit("dev-support", 0.30), Hit("market-research", 0.30)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert decision.selected == "dev-support"


def test_hits_are_returned_sorted_by_distance():
    hits = [Hit("dev-support", 0.42), Hit("market-research", 0.31)]

    decision = decide(hits, threshold=0.5, catchall="general")

    assert [h.target for h in decision.hits] == ["market-research", "dev-support"]

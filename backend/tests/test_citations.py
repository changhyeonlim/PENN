from penn.agent.citations import check, collect_ids


def test_collect_ids_walks_nested_results():
    res = {"events": [{"event_id": "lane_closures:2025-07887"}],
           "by_occupancy": {"Footway": ["lane_closures:2025-11904"]},
           "scheduled_now": ["recurring_events:broad-street-run-2026"]}
    assert collect_ids(res) == {"lane_closures:2025-07887", "lane_closures:2025-11904",
                                "recurring_events:broad-street-run-2026"}


def test_unknown_id_rejected():
    r = check("Closed for work [id:lane_closures:9999]", {"lane_closures:2025-07887"})
    assert not r.ok and "lane_closures:9999" in r.unknown


def test_uncited_answer_rejected_when_records_exist():
    assert not check("There is a permit.", {"lane_closures:2025-07887"}).ok


def test_good_answer():
    assert check("Permit in effect [id:lane_closures:2025-07887].", {"lane_closures:2025-07887"}).ok


def test_no_records_no_citation_ok():
    assert check("PENN has no record of activity here.", set()).ok

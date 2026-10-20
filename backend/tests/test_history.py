from penn.tools import history


def closure(series, year, date, street="Broad Street", start=None):
    return {"event_id": f"event_closures:{series}-{date}-x{street[:3]}", "series": series, "year": year,
            "date": date, "street": street, "from_street": "", "to_street": "", "start": start, "end": None,
            "conditional": False}


CAT = {"thanksgiving-day-parade": {"name": "Thanksgiving Day Parade", "category": "parade", "recurrence": "annual"},
       "no-kings-3": {"name": "No Kings 3 rally", "category": "rally_or_march", "recurrence": "one_off"}}


def test_groups_by_event_then_edition_with_years():
    rows = [closure("thanksgiving-day-parade", 2018, "2018-11-21"), closure("thanksgiving-day-parade", 2018, "2018-11-22"),
            closure("thanksgiving-day-parade", 2025, "2025-11-27"), closure("no-kings-3", 2026, "2026-03-28")]
    h = history.summarize(rows, CAT, {"events_in_penn": 24})
    first = h["events"][0]
    assert first["series"] == "thanksgiving-day-parade"          # most editions first
    assert first["years_seen_here"] == [2018, 2025] and len(first["editions"]) == 3
    assert first["recurrence"] == "annual" and first["name"] == "Thanksgiving Day Parade"
    assert h["summary"] == {"events_here": 2, "editions_here": 4, "years_here": [2018, 2025, 2026],
                            "by_category": {"parade": 1, "rally_or_march": 1},
                            "by_recurrence": {"annual": 1, "one_off": 1}}
    assert "gap in PENN's records" in h["coverage"]["note"] and h["coverage"]["events_in_penn"] == 24


def test_unknown_series_is_not_guessed():
    h = history.summarize([closure("mystery-event", 2024, "2024-06-01")], CAT, {})
    e = h["events"][0]
    assert e["recurrence"] is None and e["category"] is None and e["name"] == "Mystery Event"
    assert h["summary"]["by_recurrence"] == {"unknown": 1}


def test_long_editions_are_capped_and_counted():
    rows = [closure("thanksgiving-day-parade", 2022, "2022-11-24", street=f"Street {i}") for i in range(11)]
    ed = history.summarize(rows, CAT, {})["events"][0]["editions"][0]
    assert len(ed["closures"]) == history.MAX_CLOSURES_PER_EDITION and ed["more_closures"] == 3


def test_empty_place():
    h = history.summarize([], CAT, {})
    assert h["events"] == [] and h["summary"]["events_here"] == 0


def test_catalog_file_is_well_formed():
    cat = history.catalog()
    assert cat, "data/event_series.csv missing"
    for s, row in cat.items():
        assert row["recurrence"] in {"annual", "irregular", "one_off"}, s
        assert row["category"], s


def test_only_two_tools_are_offered_to_the_model():
    from penn import tools
    assert set(tools.TOOLS) == {"search_events", "get_location_history"}
    assert {s["function"]["name"] for s in tools.TOOL_SCHEMAS} == set(tools.TOOLS)


def test_each_edition_has_a_ready_citation():
    h = history.summarize([closure("no-kings-3", 2026, "2026-03-28")], CAT, {})
    ed = h["events"][0]["editions"][0]
    assert ed["cite"] == f"[id:{ed['closures'][0]['event_id']}]"


def test_digest_keeps_one_citable_record_per_year():
    rows = [closure("thanksgiving-day-parade", 2018, "2018-11-21"), closure("thanksgiving-day-parade", 2018, "2018-11-22"),
            closure("thanksgiving-day-parade", 2025, "2025-11-27"), closure("no-kings-3", 2026, "2026-03-28")]
    d = history.digest(rows, CAT)
    first = d["events"][0]
    assert first["name"] == "Thanksgiving Day Parade" and first["recurrence"] == "annual"
    assert [y["year"] for y in first["years"]] == [2018, 2025]
    assert first["years"][0]["event_id"].startswith("event_closures:thanksgiving-day-parade-2018-11-2")
    from penn.agent.citations import collect_ids
    assert len(collect_ids({"past_events_here": d})) == 3       # the ids are citable


def test_same_dates_finds_the_week_in_past_years():
    from datetime import date
    from penn.tools.history import same_dates, same_dates_text
    ed = lambda y, d, i: {"year": y, "date": d, "cite": f"[id:event_closures:{i}]"}
    summary = {"events": [
        {"name": "Rocky Run", "recurrence": "annual",
         "editions": [ed(2024, "2024-11-09", "rr24"), ed(2025, "2025-11-08", "rr25")]},
        {"name": "Philadelphia Marathon", "recurrence": "annual",
         "editions": [ed(2025, "2025-11-23", "pm25")]},                    # later in November: not the same week
        {"name": "Already announced", "recurrence": "annual",
         "editions": [ed(2025, "2025-11-10", "aa25"), ed(2026, "2026-11-10", "aa26")]},
    ]}
    got = same_dates(summary, date(2026, 11, 8), date(2026, 11, 14))
    assert [m["name"] for m in got] == ["Rocky Run"]
    assert [y["year"] for y in got[0]["years"]] == [2024, 2025]
    text = same_dates_text(got, 2026)
    assert "[id:event_closures:rr24]" in text and "not a schedule" in text and "2026" in text
    assert same_dates(summary, date(2026, 1, 1), date(2026, 12, 31)) == []    # a whole year is not "the same dates"


def test_same_dates_leaves_out_this_years_edition_just_before_the_window():
    """Philly Bike Ride ran Oct 17, 2026; asking about Oct 19-25 must not list 2026 as a "past year"
    or say PENN has no notice for it this year."""
    from datetime import date
    from penn.tools.history import same_dates
    ed = lambda y, d: {"year": y, "date": d, "cite": f"[id:event_closures:bike{y}]"}
    summary = {"events": [{"name": "Philly Bike Ride", "recurrence": "annual",
                           "editions": [ed(2024, "2024-10-19"), ed(2025, "2025-10-18"), ed(2026, "2026-10-17")]}]}
    assert same_dates(summary, date(2026, 10, 19), date(2026, 10, 25)) == []


def test_an_edition_lists_all_its_closures_here_so_one_cite_lights_them_all():
    rows = [closure("thanksgiving-day-parade", 2025, "2025-11-27", street=s) for s in ("Broad Street", "Market Street", "Parkway")]
    ed = history.summarize(rows, CAT, {})["events"][0]["editions"][0]
    assert ed["cite"] == f"[id:{rows[0]['event_id']}]" and ed["event_ids"] == [r["event_id"] for r in rows]

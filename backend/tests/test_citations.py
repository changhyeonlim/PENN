from penn.agent.citations import check, collect_ids, web_facts


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


def test_nemotron_corner_bracket_citations_accepted():
    ids = {"lane_closures:2026-19551", "event_closures:philly-10k-2026-08-23-a319c93e"}
    for text in ["Manholes 【lane_closures:2026-19551】", "Manholes 【id:lane_closures:2026-19551】",
                 "10K 【event_closures:philly-10k-2026-08-23-a319c93e】", "Manholes [id: lane_closures:2026-19551]"]:
        assert check(text, ids).ok, text


def test_made_up_citation_forms_still_rejected():
    ids = {"lane_closures:2026-19551"}
    for text in ["Manholes [citation: lane_closures:2026-19551]", "Manholes 【search_events】",
                 "Manholes [1]", 'Manholes 【{"cursor": 0, "loc": 0}】']:
        assert not check(text, ids).ok, text


def test_json_in_corner_brackets_accepted():
    ids = {"event_closures:philadelphia-marathon-2016-11-19-0cbd74a9"}
    assert check('Marathon 2016 【{"id":"event_closures:philadelphia-marathon-2016-11-19-0cbd74a9"}】', ids).ok
    assert not check('Marathon 【{"id":"event_closures:made-up-2016"}】', ids).ok


def test_event_id_json_and_bare_permit_numbers():
    ids = {"lane_closures:EUN2446475", "lane_closures:2026-19551"}
    assert check('Manholes 【{"event_id":"lane_closures:2026-19551"}】', ids).ok
    c = check("Gas leak [id:EUN2446475]", ids)
    assert c.ok and c.cited == {"lane_closures:EUN2446475"}       # reported back as the full ID
    assert not check("Gas leak [id:EUN9999999]", ids).ok             # bare numbers must match a returned ID


def test_plain_no_record_answer_needs_no_citation():
    ids = {"lane_closures:2026-19551"}
    assert not check("PENN has no record of a block party near 13th and Locust that day.", ids).ok   # first draft must cite
    assert check("PENN has no record of a block party near 13th and Locust that day.", ids, allow_no_record=True).ok
    # a "no record" sentence that also states specifics must still cite them
    assert not check("PENN has no record of a block party, but a permit runs on the 1300 block of Spruce until Nov 30.", ids, allow_no_record=True).ok


def test_group_members_are_citable_and_expand():
    from penn.agent.citations import collect_groups, collect_ids, expand_groups
    result = {"getting_around": {"new": [{"group": "8 block party permits", "event_ids": ["a:1", "a:2", "a:3"],
                                          "cite": "[id:a:1]"}]}}
    assert collect_ids(result) == {"a:1", "a:2", "a:3"}
    groups = collect_groups(result)
    assert expand_groups(["a:1", "b:9"], groups) == ["a:1", "b:9", "a:2", "a:3"]
    assert expand_groups(["b:9"], groups) == ["b:9"]
    # groups expand in the tools' order (an event's latest edition first), not the order the answer cites them
    editions = [["2025:n", "2025:a", "2025:b"], ["2021:n", "2021:a"]]
    assert expand_groups(["2021:n", "2025:n"], editions) == ["2021:n", "2025:n", "2025:a", "2025:b", "2021:a"]


def test_web_cited_lines_state_only_what_the_page_says():
    pages = {"web:0a1b2c3d": {"text": "Philly Street Food Festival, October 24 and 25 at Stateside Live."}}
    assert web_facts("The festival is Oct 24 [id:web:0a1b2c3d].", pages).ok
    assert not web_facts("The festival is Oct 31 [id:web:0a1b2c3d].", pages).ok
    assert not web_facts("The festival is Oct 31 【id:web:0a1b2c3d】.", pages).ok     # either bracket style is checked


def test_several_ids_in_one_bracket_are_each_cited():
    from penn.agent.citations import split_multi
    ids = {"ticketmaster:Z7r9jZ1AAvbp4", "ticketmaster:Z7r9jZ1AAvb4J"}
    # Super's line on the Old City trip question, which showed as raw text instead of two chips
    line = ("Girl from the North Country (Sun Oct 18 14:00 & 19:00) at Arden Theatre Co."
            "【ids:ticketmaster:Z7r9jZ1AAvbp4, ticketmaster:Z7r9jZ1AAvb4J】")
    c = check(line, ids)
    assert c.ok and c.cited == ids
    assert split_multi(line).endswith("[id:ticketmaster:Z7r9jZ1AAvbp4] [id:ticketmaster:Z7r9jZ1AAvb4J]")
    for form in ("[id: ticketmaster:Z7r9jZ1AAvbp4; ticketmaster:Z7r9jZ1AAvb4J]",
                 "【ticketmaster:Z7r9jZ1AAvbp4, id:ticketmaster:Z7r9jZ1AAvb4J】",
                 "【ids:ticketmaster:Z7r9jZ1AAvbp4】"):
        assert check(f"Shows {form}.", ids).ok, form
    assert split_multi("Permit [id:lane_closures:1].") == "Permit [id:lane_closures:1]."     # nothing to split
    bad = check("Shows 【ids:ticketmaster:Z7r9jZ1AAvbp4, ticketmaster:MADEUP】", ids)
    assert not bad.ok and bad.unknown == {"ticketmaster:MADEUP"}                              # each ID is still checked


def test_bare_ids_after_the_first_take_its_source():
    from penn.agent.citations import split_multi
    ids = {"lane_closures:2026-23904", "lane_closures:2026-21956", "lane_closures:2026-15496"}
    # Super's br06 answer, with its narrow no-break spaces
    line = "Three permits [ids:lane_closures:2026-23904, 2026-21956, 2026-15496]."
    c = check(line, ids)
    assert c.ok and c.cited == ids
    assert split_multi("【ids:a_b:1, 2; c_d:3, 4】") == "[id:a_b:1] [id:a_b:2] [id:c_d:3] [id:c_d:4]"
    assert not check("Permits [ids: 2026-23904, 2026-21956].", ids).ok    # the first ID still needs its source


def test_drop_unread_keeps_readable_cites():
    from penn.agent.citations import drop_unread
    assert drop_unread("Permit [id:a_b:1], see 【past_events_here】.") == "Permit [id:a_b:1], see."
    assert drop_unread("Shows 【[id:a_b:1] [id:a_b:2]】.") == "Shows 【[id:a_b:1] [id:a_b:2]】."
    assert drop_unread("Nothing odd [id:a_b:1].") == "Nothing odd [id:a_b:1]."


def test_unreadable_citation_brackets_fail_even_beside_good_ones():
    ids = {"lane_closures:2026-19551", "ticketmaster:Z7r9jZ1AAvbp4"}
    good = "Manholes [id:lane_closures:2026-19551]."
    for junk in (" Show 【ticketmaster】.", " Show [ids: Z7r9jZ1AAvbp4].", " Show 【1】."):
        c = check(good + junk, ids)
        assert not c.ok and "cannot read" in c.reason, junk
    assert check(good + " Show 【[id:ticketmaster:Z7r9jZ1AAvbp4]】.", ids).ok    # cites wrapped in 【】 still read
    assert check(good + " See [the City page](https://www.phila.gov/).", ids).ok  # a markdown link is not a citation


def test_id_prefix_in_any_case_is_a_citation():
    from penn.agent import citations
    ids = {"lane_closures:2026-15496"}
    for form in ("[ID: lane_closures:2026-15496]", "[Id:lane_closures:2026-15496]", "【ID: lane_closures:2026-15496】"):
        assert citations.check(f"A permit is in effect {form}.", ids).ok, form
    assert not citations.check("A permit is in effect [1].", ids).ok        # a source prefix is still required
    assert citations.WEB_CITE.findall("Per the site [ID: web:0123abcd].") == ["web:0123abcd"]

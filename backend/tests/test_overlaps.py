from penn.overlaps import same_as, why_same

WALNUT = (39.9489, -75.1555)


def row(source, rid, title, start, end=None, at=WALNUT, desc=""):
    return {"source": source, "event_id": f"{source}:{rid}", "title": title, "start_date": start,
            "end_date": end or start, "centroid_lat": at[0], "centroid_lon": at[1], "description": desc}


ADDAMS_PICK = row("visitphilly", "addams", "The Addams Family: The Musical at Walnut Street Theatre",
                  "2026-10-13", "2026-10-25", desc="Walnut Street Theatre, 825 Walnut Street, Philadelphia, PA")


def addams_show(day, at=(39.94893, -75.15558)):
    return row("ticketmaster", f"addams-{day}", "Walnut Street Theatre's THE ADDAMS FAMILY: The Musical", day, at=at,
               desc="Walnut Street Theatre, 825 Walnut St, Philadelphia, PA; Arts & Theatre")


def test_a_run_matches_each_performance_in_its_dates():
    assert why_same(ADDAMS_PICK, addams_show("2026-10-17")) == "same title"
    assert why_same(ADDAMS_PICK, addams_show("2026-10-25")) == "same title"
    assert why_same(ADDAMS_PICK, addams_show("2026-10-26")) is None       # after the run


def test_a_different_venue_is_a_different_event():
    assert why_same(ADDAMS_PICK, addams_show("2026-10-17", at=(39.9512, -75.1640))) is None   # ~800 m away


def test_games_match_on_the_home_team():
    linc = (39.9008, -75.1675)
    pick = row("visitphilly", "eagles", "Philadelphia Eagles at Lincoln Financial Field", "2026-10-18", at=linc,
               desc="Lincoln Financial Field, 1 Lincoln Financial Field Way, Philadelphia, PA")
    game = row("ticketmaster", "eagles", "Philadelphia Eagles vs. Carolina Panthers", "2026-10-18",
               at=(39.9012, -75.1674), desc="Lincoln Financial Field, One Lincoln Financial Way, Philadelphia, PA; Sports")
    # Without the venue's words the pick's title is just "Eagles", which the title test already catches.
    assert why_same(pick, game) in ("same title", "same team (eagles)")
    # Titles that share only the team still match a game, through the team.
    tailgate = {**pick, "title": "Philadelphia Eagles Home Opener Tailgate Celebration Weekend"}
    assert why_same(tailgate, game) == "same team (eagles)"


def test_the_same_named_arena_matches_though_the_pins_are_400_m_apart():
    pick = row("visitphilly", "flyers-2", "Philadelphia Flyers at Xfinity Mobile Arena", "2026-10-17", at=(39.9012, -75.17198),
               desc="Xfinity Mobile Arena, 3601 S. Broad Street, Philadelphia, PA")
    game = row("ticketmaster", "fly", "Philadelphia Flyers vs. Ottawa Senators", "2026-10-17", at=(39.90455, -75.17363),
               desc="Xfinity Mobile Arena, 3601 South Broad St, Philadelphia, PA; Sports")
    assert why_same(pick, game) is not None
    # Unnamed or differently named places still need to be within 200 m.
    assert why_same({**pick, "description": "Somewhere Else, 3601 S. Broad Street, Philadelphia, PA"}, game) is None


def test_a_concert_at_union_transfer_is_not_a_union_game():
    ut = (39.9612, -75.1550)
    pick = row("visitphilly", "u", "Philadelphia Union Watch Party at Union Transfer", "2026-10-18", at=ut,
               desc="Union Transfer, 1026 Spring Garden Street, Philadelphia, PA")
    gig = row("ticketmaster", "g", "Some Band", "2026-10-18", at=ut, desc="Union Transfer, 1026 Spring Garden St, Philadelphia, PA; Music")
    assert why_same(pick, gig) is None


def test_nearby_but_unrelated_events_stay_apart():
    fdr = (39.9036, -75.1702)
    market = row("visitphilly", "m", "Southeast Asian Market in FDR Park", "2026-10-01", "2026-10-31", at=fdr,
                 desc="FDR Park, South Broad Street & Pattison Avenue, Philadelphia, PA")
    game = row("ticketmaster", "f", "Philadelphia Flyers vs. Ottawa Senators", "2026-10-17", at=(39.9012, -75.1720),
               desc="Xfinity Mobile Arena, 3601 South Broad St, Philadelphia, PA; Sports")
    assert why_same(market, game) is None


def test_same_as_lists_pairs_by_event_id():
    rows = [ADDAMS_PICK, addams_show("2026-10-17"), addams_show("2026-10-18"),
            row("ticketmaster", "x", "Unrelated Comedy Night", "2026-10-17")]
    assert same_as(rows) == [["visitphilly:addams", "ticketmaster:addams-2026-10-17", "same title"],
                             ["visitphilly:addams", "ticketmaster:addams-2026-10-18", "same title"]]


def test_a_block_party_under_an_announced_closure_is_drawn_once():
    """OurFest, Oct 11 2026: block party permits on blocks the City's OurFest closure already closes that day."""
    from penn.overlaps import covered
    closure = {"event_id": "event_closures:ourfest-camac", "source": "event_closures", "event_type": "event",
               "start_date": "2026-10-11", "seg_ids": [1, 2, 3]}
    inside = {"event_id": "lane_closures:BP1", "source": "lane_closures", "event_type": "block_party",
              "start_date": "2026-10-11", "end_date": "2026-10-11", "seg_ids": [1, 2]}
    sticks_out = inside | {"event_id": "lane_closures:BP2", "seg_ids": [3, 4]}       # block 4 is not announced
    other_day = inside | {"event_id": "lane_closures:BP3", "start_date": "2026-10-12", "end_date": "2026-10-12"}
    assert covered([closure, inside, sticks_out, other_day]) == [["lane_closures:BP1", "event_closures:ourfest-camac"]]

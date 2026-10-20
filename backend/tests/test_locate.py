from penn.geo import locate


def test_real_neighborhoods_and_center_city_alias():
    a = locate.areas()
    assert "rittenhouse" in a and "logan square" in a and "logan" in a
    assert len(a["center city"]) > len(a["rittenhouse"])
    # Longest name first: "logan square" must be tried before "logan".
    names = list(a)
    assert names.index("logan square") < names.index("logan")


def test_from_text_matches_area_without_network():
    r = locate.from_text("What's happening in Center City this weekend?")
    assert r.method == "area" and r.label == "Center City" and r.cells
    r = locate.from_text("anything near Logan Square tomorrow")
    assert r.label == "Logan Square"


def test_places_outside_the_city_are_refused():
    for q in ["What road closures are there in Camden, New Jersey on Saturday?", "anything in Upper Darby tomorrow?"]:
        assert locate.from_text(q).method == "outside", q
    assert locate.from_click(-75.1196, 39.9259).method == "outside"      # Camden, NJ, across the river
    assert locate.from_click(-75.1636, 39.9512).method == "click"        # City Hall


def test_a_street_named_like_an_area_is_not_the_area(monkeypatch):
    # No geocoder in tests: what matters is that the neighborhood is not picked.
    monkeypatch.setattr(locate.httpx, "Client", lambda **kw: (_ for _ in ()).throw(locate.httpx.HTTPError("offline")))
    for q, area in [("Is Ludlow Street closed between 18th and 19th?", "Ludlow"),
                    ("anything on Rittenhouse St in Germantown?", "Rittenhouse"),
                    ("Fairmount Ave work this week", "Fairmount")]:
        assert locate.from_text(q).label != area, q
    assert locate.from_text("Is Ludlow Street closed?").method == "none"
    assert locate.from_text("anything in Ludlow this weekend?").label == "Ludlow"          # the neighborhood still works
    assert locate.from_text("Rittenhouse Square on Sunday").label == "Rittenhouse Square"   # the park (p3-10 landmark)
    assert locate.from_text("anything in Rittenhouse on Sunday?").label == "Rittenhouse"   # the neighborhood
    assert locate.from_text("Ludlow Street in Center City").label == "Center City"         # a later area still matches


def test_a_landmark_in_a_question_is_searched_there():
    r = locate.from_text("What's closed near the Art Museum this weekend?")
    assert r.method == "landmark" and r.label == "Philadelphia Museum of Art" and r.cells and r.point
    assert locate.from_text("Construction on Kelly Drive?").label == "Kelly Drive"
    assert locate.from_text("Is 13th and Locust busy?").method == "intersection"      # a corner still wins

from penn.geo.landmarks import Landmark, best, find, load


def lm(name, *aliases, kind="place"):
    from penn.geo.landmarks import _norm
    return Landmark(name, kind, (-75.17, 39.95), ((-75.18, 39.94), (-75.16, 39.96)), (1,),
                    tuple(dict.fromkeys(_norm(p) for p in (name, *aliases))))


ITEMS = (lm("Rittenhouse Square"), lm("LOVE Park", "jfk plaza"), lm("Philadelphia Museum of Art", "art museum", "rocky steps"),
         lm("Kimmel Center", "marian anderson hall"), lm("Marian Anderson Hall - Kimmel Center", kind="venue"),
         lm("Eakins Oval"))


def test_names_and_aliases_in_order_of_mention():
    got = find("Start at the Art Museum steps, around Eakins Oval, then to LOVE Park.", ITEMS)
    assert [l.name for l in got] == ["Philadelphia Museum of Art", "Eakins Oval", "LOVE Park"]


def test_whole_phrases_only():
    assert find("a lovely park nearby", ITEMS) == []
    assert [l.name for l in find("Rittenhouse Square's farmers market", ITEMS)] == ["Rittenhouse Square"]


def test_the_longest_phrase_wins_and_is_one_place():
    got = find("A concert at Marian Anderson Hall - Kimmel Center tonight", ITEMS)
    assert [l.name for l in got] == ["Marian Anderson Hall - Kimmel Center"]
    (b, n) = best("closures near the Art Museum and LOVE Park", ITEMS)
    assert b.name == "Philadelphia Museum of Art" and n == len("art museum")


def test_the_built_list_has_the_places_we_reviewed():
    names = {l.name for l in load()}
    assert {"LOVE Park", "Philadelphia Museum of Art", "Cherry Street Pier", "Singing Fountain", "Penn Treaty Park",
            "Lincoln Financial Field", "Kelly Drive"} <= names

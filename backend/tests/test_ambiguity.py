from penn.agent import ambiguity


def test_construction_and_closures():
    ns = ambiguity.notes("Is there construction near 20th and Arch?", "N 20th St & Arch St")
    assert len(ns) == 1 and "road work" in ns[0]
    ns = ambiguity.notes("How many closures are on the 1800 block of Ludlow Street?", "1800 block of Ludlow St")
    assert any("cannot confirm" in n for n in ns)


def test_whole_street_vs_searched_corner():
    ns = ambiguity.notes("Is anything happening on Spruce Street on Saturday?", "13th & Locust St (Washington Square West)")
    assert len(ns) == 1 and ns[0].startswith("Spruce Street is longer") and "13th & Locust" in ns[0]
    # the named street is part of the searched place: no note
    assert ambiguity.notes("What is on Walnut Street near 12th?", "S 12th St & Walnut St") == []


def test_plain_question_has_no_notes():
    assert ambiguity.notes("What permits are in effect near 13th and Locust on Saturday?", "S 13th St & Locust St") == []


def test_with_notes_puts_notes_first():
    out = ambiguity.with_notes("Two permits list a Full Closure.", "How many closures on Ludlow?")
    assert out.startswith("Note: ") and out.endswith("Two permits list a Full Closure.")

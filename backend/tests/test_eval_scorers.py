import re

from penn.eval import scorers
from penn.eval.schema import Question, Result, load_questions


def q(**kw) -> Question:
    base = dict(id="t1", question="?", type="trap", cells=[1], start="", end="", place="", window="",
                expect_ids=["lane_closures:A", "lane_closures:B"], required_facts=[], forbidden=[],
                expected_tools=["search_events"])
    return Question(**(base | kw))


def test_normalize_unifies_abbreviations_dates_and_dashes():
    t = scorers.normalize("Permit runs **October 13th**, 2026 on South 13th Street ‑ can’t confirm, 7:00 A.M.")
    assert "oct 13, 2026" in t and "s 13 st - cant confirm" in t and "7:00 am" in t


def test_fact_groups_match_any_alternative_on_whole_words():
    question = q(required_facts=[["oct 13", "2026-10-13"], ["footway", "sidewalk"], ["oct 1"]])
    r = Result(id="t1", answer="A Footway permit, in effect until Oct. 13th.")
    cov, missing = scorers.fact_coverage(question, r)
    assert missing == [2]            # "oct 1" must not match inside "oct 13"
    assert round(cov, 2) == 0.67


def test_regex_alternative():
    question = q(required_facts=[["re:\\b(cannot|cant)\\b[^.]{0,25}\\bconfirm"]])
    assert scorers.fact_coverage(question, Result(id="t1", answer="PENN can't confirm work is happening."))[0] == 1.0


def test_forbidden_respects_negation_lookbehind():
    pat = r"(?<!not a )(?<!not )(?<!no )\broad closure\b"
    question = q(forbidden=[pat])
    assert scorers.forbidden_hits(question, Result(id="t1", answer="This is not a road closure.")) == []
    assert scorers.forbidden_hits(question, Result(id="t1", answer="It is a road closure."))


def test_citation_and_id_recall():
    question = q()
    r = Result(id="t1", cited=["lane_closures:A", "lane_closures:X"], returned_ids=["lane_closures:A", "lane_closures:B"])
    assert scorers.citation_valid(r) == 0.5
    assert scorers.id_recall(question, r) == 0.5
    assert scorers.retrieval_recall(question, r) == 1.0
    assert scorers.id_recall(q(expect_ids=[]), r) is None


def test_pass_needs_every_check():
    question = q(required_facts=[["footway"]], forbidden=[r"is closed"])
    ok = Result(id="t1", answer="Footway permit [id:lane_closures:A]", tools_called=["search_events"],
                cited=["lane_closures:A"], returned_ids=["lane_closures:A"])
    assert scorers.score(question, ok).passed
    bad = Result(id="t1", answer="Footway permit; the street is closed", tools_called=["search_events"])
    assert not scorers.score(question, bad).passed


def test_question_file_loads_and_every_pattern_compiles():
    qs = load_questions()
    assert len(qs) == len({x.id for x in qs}) >= 20
    for x in qs:
        assert x.required_facts, x.id
        for group in x.required_facts:
            for alt in group:
                scorers._compile(alt)
        for pat in x.forbidden:
            re.compile(pat)


def test_hyphenated_phrase_and_plural_match():
    question = q(required_facts=[["in effect"], ["manhole"]])
    r = Result(id="t1", answer="A permit-in-effect to dig and set new PECO Manholes.")
    assert scorers.fact_coverage(question, r)[0] == 1.0


def test_tool_alternatives():
    question = q(expected_tools=["search_events|get_location_timeline"])
    assert scorers.tool_match(question, Result(id="t1", tools_called=["get_location_timeline"]))
    assert not scorers.tool_match(question, Result(id="t1", tools_called=[]))


def test_shape_match_counts_only_when_the_question_sets_it():
    passing = dict(answer="ok", tools_called=["search_events"])
    assert scorers.shape_match(q(), Result(id="t1")) is None
    brief = q(answer_shape="brief")
    assert scorers.shape_match(brief, Result(id="t1", answer_shapes=["brief"])) is True
    assert scorers.shape_match(brief, Result(id="t1", answer_shapes=["brief", "full"])) is False
    assert scorers.shape_match(brief, Result(id="t1")) is False                    # never searched
    r = scorers.score(q(expect_ids=[], answer_shape="brief"), Result(id="t1", answer_shapes=["full"], **passing))
    assert r.scores["shape_match"] is False and not r.passed
    r = scorers.score(q(expect_ids=[], answer_shape="brief"), Result(id="t1", answer_shapes=["brief"], **passing))
    assert r.passed


def test_runner_reads_the_shape_code_chose():
    import json
    from types import SimpleNamespace as NS
    from penn.eval.runner import RecordingClient
    msg = NS(tool_calls=None, content="done")
    real = NS(chat=NS(completions=NS(create=lambda **kw: NS(choices=[NS(message=msg)]))))
    rec = RecordingClient(real)
    tool = {"role": "tool", "content": json.dumps({"events": [{"event_id": "lane_closures:A"}],
                                                   "searched": {"answer_shape": "brief"}})}
    rec.chat.completions.create(messages=[{"role": "user", "content": "?"}, tool])
    rec.chat.completions.create(messages=[{"role": "user", "content": "?"}, tool])   # same history again
    assert rec.answer_shapes == ["brief"] and rec.returned_ids == {"lane_closures:A"}


def test_eval_answers_from_the_frozen_copy_by_default():
    from penn.config import settings
    from penn.eval import __main__ as cli
    before = settings.clickhouse_db
    try:
        assert cli.DEFAULT_DB == "penn_demo"
        cli.use_db("penn_demo")
        assert settings.clickhouse_db == "penn_demo"
    finally:
        cli.use_db(before)


def test_question_file_shapes_are_valid():
    for question in load_questions():
        assert question.answer_shape in (None, "brief", "full"), question.id
        assert question.place_method in ("eval", "area", "click", "intersection"), question.id


def test_summary_counts_pass_rate_by_difficulty():
    from penn.eval import report
    qs = [q(id="a", difficulty="easy"), q(id="b", difficulty="easy"), q(id="c", difficulty="hard"), q(id="d")]
    rs = [Result(id=x, passed=p, scores={"fact_coverage": 1.0}) for x, p in zip("abcd", [True, False, True, False])]
    assert report.summarize(qs, rs)["by_difficulty"] == {"easy": "1/2", "hard": "1/1", "unrated": "0/1"}


def test_question_difficulties_are_valid():
    for question in load_questions():
        assert question.difficulty in (None, "easy", "medium", "hard"), question.id

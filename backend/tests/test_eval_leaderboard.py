import json

from penn.eval import leaderboard
from penn.eval.schema import Question, Result


def q(id_, **kw) -> Question:
    base = dict(id=id_, question="?", type="lookup", cells=[1], start="", end="", place="", window="",
                expect_ids=[], required_facts=[["permit"]], forbidden=[], expected_tools=["search_events"])
    return Question(**(base | kw))


def test_percentile_interpolates_like_numpy():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert leaderboard.percentile(xs, 0.5) == 5.5
    assert round(leaderboard.percentile(xs, 0.9), 2) == 9.1
    assert leaderboard.percentile([], 0.9) is None


def test_format_counts_strict_lenient_and_missed_cites():
    r = Result(id="a", draft_texts=[
        "One [id:lane_closures:A], two 【lane_closures:B】, three [ID: lane_closures:C].",   # first draft: not strict
        "<think>[id:lane_closures:X]</think>Fixed [id:lane_closures:A] and [id:lane_closures:B].",
    ])
    f = leaderboard.format_stats([r])
    assert f["cite_attempts"] == 5            # the cite inside <think> does not count
    assert f["strict_share"] == 0.6 and f["accepted_share"] == 0.8 and f["missed"] == 1
    assert f["strict_first_drafts"] == 0.0 and f["answers_with_think_tags"] == 1


def test_board_compares_models_on_the_questions_every_run_answered(tmp_path):
    qs = [q("a"), q("b"), q("c", hold="being rewritten")]

    def run(name, model, answers, latency):
        rows = [{"id": i, "answer": t, "model": model, "tools_called": ["search_events"], "latency_ms": latency,
                 "calls": [{"latency_ms": latency, "output_tokens": 100}]} for i, t in answers.items()]
        path = tmp_path / name
        path.write_text(json.dumps({"summary": {"db": "penn_demo"}, "results": rows}), encoding="utf-8")
        return path

    paths = [
        run("s1.json", "nv/super", {"a": "a permit", "b": "nothing", "c": "a permit", "z": "x"}, 4000),
        run("s2.json", "nv/super", {"a": "a permit", "b": "a permit", "c": "a permit"}, 6000),
        run("u1.json", "nv/ultra", {"a": "a permit", "b": "a permit", "c": "nothing"}, 20000),
    ]
    registry = {"brackets": ["super", "ultra"], "models": {"nv/super": {"bracket": "super", "active_b": 12},
                                                           "nv/ultra": {"bracket": "ultra", "active_b": 55}}}
    board = leaderboard.build(paths, qs, registry)
    assert board["questions"] == 2 and board["held_not_counted"] == ["c"] and board["dropped"] == ["z"]
    sup, ultra = board["models"]                  # by bracket first: ultra's 2/2 does not rank above super
    assert ultra["model"] == "nv/ultra" and ultra["passed"]["per_run"] == [2] and ultra["bracket"] == "ultra"
    assert sup["passed"] == {"per_run": [1, 2], "min": 1, "max": 2, "mean": 1.5} and sup["runs"] == 2
    assert sup["latency_s"]["p50"] == 5.0 and sup["output_tokens_per_s"] == 20.83   # median of 25 and 16.67 tok/s


def test_baseline_subset_names_real_questions():
    from penn.eval.__main__ import subset_ids
    from penn.eval.schema import load_questions
    ids = subset_ids("baseline12")
    assert len(ids) == 12 and ids <= {q.id for q in load_questions()}


def test_every_registered_model_names_a_known_bracket():
    reg = leaderboard.load_registry()
    assert all(m["bracket"] in reg["brackets"] for m in reg["models"].values())

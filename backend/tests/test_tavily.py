from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from penn.web import tavily

NOW = datetime(2026, 10, 18, 14, tzinfo=timezone.utc)
PAGE = {"title": "Rocky Run 2026", "url": "https://www.rockyrun.example/race-day",
        "content": "Race day is Saturday, November 14, 2026.", "raw_content": "Full page. Saturday, November 14, 2026. Kelly Drive."}


class FakeTavily:
    def __init__(self, *replies):
        self.replies, self.sent = list(replies), []

    def post(self, url, json=None, headers=None):
        self.sent.append(json)
        status, body = self.replies.pop(0)
        return SimpleNamespace(status_code=status, headers={}, json=lambda: body)


@pytest.fixture(autouse=True)
def live(monkeypatch):
    monkeypatch.setattr(tavily, "settings", SimpleNamespace(tavily_api_key="k", pinned_now=""))


def test_only_the_event_name_and_year_leave_penn(tmp_path):
    t = FakeTavily((200, {"results": [PAGE]}))
    out = tavily.search("Rocky Run", 2026, ["rockyrun.example"], client=t, cache_dir=tmp_path, now=NOW)
    sent = t.sent[0]
    assert sent["query"] == "Rocky Run Philadelphia 2026"
    assert sent["include_domains"] == ["phila.gov", "rockyrun.example"] and sent["include_domains_mode"] == "prefer"
    (r,) = out["results"]
    assert r["event_id"] == tavily.web_id(PAGE["url"]) and r["site"] == "rockyrun.example"
    assert "Kelly Drive" in r["text"] and r["excerpt"].startswith("Race day")


def test_a_second_search_within_a_day_reads_the_cache(tmp_path):
    t = FakeTavily((200, {"results": [PAGE]}))
    tavily.search("Rocky Run", 2026, client=t, cache_dir=tmp_path, now=NOW)
    again = tavily.search("Rocky Run", 2026, client=t, cache_dir=tmp_path, now=NOW + timedelta(hours=3))
    assert again["cached"] and len(t.sent) == 1


def test_demo_mode_reads_only_the_cache(tmp_path, monkeypatch):
    tavily.search("Rocky Run", 2026, client=FakeTavily((200, {"results": [PAGE]})), cache_dir=tmp_path, now=NOW)
    monkeypatch.setattr(tavily, "settings", SimpleNamespace(tavily_api_key="k", pinned_now="2026-10-17T10:00:00-04:00"))
    t = FakeTavily()
    old = tavily.search("Rocky Run", 2026, client=t, cache_dir=tmp_path, now=NOW + timedelta(days=30))
    assert old["cached"] and t.sent == []                      # stale, but demo mode replays it
    miss = tavily.search("Some Other Event", 2026, client=t, cache_dir=tmp_path, now=NOW)
    assert miss["results"] == [] and "demo mode" in miss["note"] and t.sent == []


def test_a_rate_limit_or_plan_limit_gives_no_results_and_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(tavily.clock, "sleep", lambda s: None)
    out = tavily.search("Rocky Run", 2026, client=FakeTavily((429, {}), (429, {})), cache_dir=tmp_path, now=NOW)
    assert out["results"] == [] and "rate-limiting" in out["note"]
    out = tavily.search("Rocky Run", 2026, client=FakeTavily((432, {})), cache_dir=tmp_path, now=NOW)
    assert "plan limit" in out["note"]


def test_no_key_means_no_call(tmp_path, monkeypatch):
    monkeypatch.setattr(tavily, "settings", SimpleNamespace(tavily_api_key="", pinned_now=""))
    t = FakeTavily()
    assert "no Tavily key" in tavily.search("Rocky Run", 2026, client=t, cache_dir=tmp_path, now=NOW)["note"] and t.sent == []


def test_an_open_query_is_short_in_philadelphia_and_undated(tmp_path):
    t = FakeTavily((200, {"results": [PAGE]}))
    out = tavily.search_open("East Passyunk  food festival", client=t, cache_dir=tmp_path, now=NOW)
    sent = t.sent[0]
    assert sent["query"] == "East Passyunk food festival Philadelphia" and "start_date" not in sent
    assert "visitphilly.com" in sent["include_domains"] and out["results"][0]["event_id"] == tavily.web_id(PAGE["url"])
    assert tavily.open_body("food in Philly")["query"] == "food in Philly"
    assert len(tavily.open_body("x " * 500)["query"]) <= tavily.QUERY_CHARS + len(" Philadelphia")
    again = tavily.search_open("East Passyunk food festival", client=t, cache_dir=tmp_path, now=NOW)
    assert again["cached"] and len(t.sent) == 1

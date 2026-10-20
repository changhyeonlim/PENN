"""p1-11: the City's Special Events press releases, as event closure rows.

Big events (races, parades, the Bike Ride) are not in the Streets Department's Lane Closures layer:
the Office of Special Events announces their closures in a press release a few days ahead, and the
"upcoming road closures" page lists only small neighborhood events. This module reads those releases.

Stage 1, fetch. phila.gov's archive page (the-latest/archives) is filled from one JSON feed with the
5,000 newest City posts. Notices are the Special Events press releases titled "road closures",
"traffic advisory" or "travel advisory". Each notice's HTML is saved once, and again only if the City
edits it (updated_at changes):

    data/snapshots/phila_press/<published YYYY-MM-DD>-<slug>.html
    data/snapshots/phila_press/index.json      what was saved, keyed by post id

Stage 2, parse (parse_notice). Closure lists follow one template: a lead-in such as "The following
streets will be closed to vehicular traffic beginning at 3:30 a.m. on Saturday, October 17, and will
reopen no later than 2 p.m.:" and then one list item per street, "Kelly Drive between Spring Garden
Street and 25th Street". Lead-ins and items that don't fit are reported, never guessed. Parking
restrictions are skipped: they are not closures.

Stage 3, write. Rows go to data/event_closures/press_releases.csv in the event_closures format, so the
usual loader and centerline matcher take it from there. An event-year a curated file already has
(the checked research backfill) is left out, so the same closure is never drawn twice.
"""
from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

ARCHIVE_URL = "https://muuljp5pva.execute-api.us-east-1.amazonaws.com/"
SITE = "https://www.phila.gov"
USER_AGENT = "PENN-hackathon/0.1 (Philadelphia street-closure map; one fetch per notice)"
DEPARTMENT = "Special Events"
NOTICE_TITLE = re.compile(r"road closure|traffic advisory|travel advisory", re.I)


@dataclass
class Notice:
    id: int
    title: str
    published: str        # "YYYY-MM-DD HH:MM:SS", local
    updated_at: str
    url: str
    file: str = ""        # saved HTML, relative to the snapshot folder


def _departments(post: dict) -> list[str]:
    return [c.get("slang_name") or c.get("name") or "" for c in post.get("categories") or [] if isinstance(c, dict)]


def closure_notices(posts: list[dict]) -> list[Notice]:
    """Special Events press releases about road closures, newest first."""
    out = []
    for p in posts:
        if p.get("template") != "press_release" or DEPARTMENT not in _departments(p):
            continue
        if not NOTICE_TITLE.search(p.get("title", "")):
            continue
        link = p["link"] if p["link"].startswith("http") else SITE + p["link"]
        out.append(Notice(id=p["id"], title=p["title"].strip(), published=p["date"],
                          updated_at=p.get("updated_at") or p["date"], url=link))
    return sorted(out, key=lambda n: n.published, reverse=True)


def _client() -> httpx.Client:
    return httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def fetch_index(client: httpx.Client | None = None) -> list[Notice]:
    client = client or _client()
    r = client.get(ARCHIVE_URL)
    r.raise_for_status()
    posts = r.json()
    if not isinstance(posts, list) or len(posts) < 100:
        raise RuntimeError(f"archive feed returned {type(posts).__name__} of {len(posts) if isinstance(posts, list) else '?'} posts")
    return closure_notices(posts)


def _file_name(n: Notice) -> str:
    slug = n.url.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", slug)[:90]
    return f"{n.published[:10]}-{slug}.html"


@dataclass
class FetchReport:
    notices: int = 0
    saved: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def fetch_new(folder: Path, client: httpx.Client | None = None) -> FetchReport:
    """Save every notice not saved yet, or edited since it was saved. One request per new notice."""
    client = client or _client()
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / "index.json"
    index: dict[str, dict] = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    notices = fetch_index(client)
    rep = FetchReport(notices=len(notices))
    for n in notices:
        known = index.get(str(n.id))
        if known and known["updated_at"] == n.updated_at and (folder / known["file"]).exists():
            continue
        try:
            r = client.get(n.url)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            rep.failed.append(f"{n.url}: {exc}")
            continue
        n.file = _file_name(n)
        (folder / n.file).write_text(r.text, encoding="utf-8")
        index[str(n.id)] = {**n.__dict__, "fetched_at": datetime.now().isoformat(timespec="seconds")}
        rep.saved.append(n.file)
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return rep


# ---- stage 2: parse ----

class _Blocks(HTMLParser):
    """The release body as (kind, text) blocks: "h" headings, "p" paragraphs, "li" list items."""
    KINDS = {"h1": "h", "h2": "h", "h3": "h", "h4": "h", "h5": "h", "h6": "h", "p": "p", "li": "li"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._kind: str | None = None
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        elif tag in self.KINDS:
            self._flush()
            self._kind = self.KINDS[tag]
        elif tag == "br" and self._kind:
            self._buf.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.KINDS:
            self._flush()

    def handle_data(self, data):
        if self._kind and not self._skip:
            self._buf.append(data)

    def _flush(self):
        if self._kind:
            # A paragraph broken with <br> holds several lines; each is its own block.
            for line in "".join(self._buf).split("\n"):
                text = re.sub(r"\s+", " ", html.unescape(line)).strip(" \u00a0\u200b")
                if text:
                    self.blocks.append((self._kind, text))
        self._kind, self._buf = None, []


def blocks(page: str) -> list[tuple[str, str]]:
    """Blocks of the release body only: from the "For immediate release" line to the "###" end mark."""
    m = re.search(r"<main.*?</main>", page, re.S | re.I)
    p = _Blocks()
    p.feed(m.group(0) if m else page)
    p._flush()
    out = p.blocks
    start = next((i for i, (_, t) in enumerate(out) if t.startswith("For immediate release")), 0)
    end = next((i for i, (_, t) in enumerate(out) if t.strip() == "###"), len(out))
    return out[start:end]


MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
               "October", "November", "December"]
_MONTH = "(" + "|".join(MONTH_NAMES) + r"|Jan\.?|Feb\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"
_DAY = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
DATE = re.compile(_DAY + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", re.I)
_CLOCK_RE = r"(?:noon|midnight|\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?))"
TIME = re.compile(r"\b(" + _CLOCK_RE + ")", re.I)
END_ONLY = re.compile(r"\b(?:until|no later than)\s+(?:approximately\s+)?" + _CLOCK_RE, re.I)
# "The following streets will be closed ...:", "all roads listed below will be closed ...:"
LEAD_IN = re.compile(r"\b(following|listed below|below)\b.{0,60}\b(streets?|roads?|blocks?|ramps?)\b.{0,80}\bclosed\b", re.I)
OPEN_END = re.compile(r"conclu|as the (race|parade|event|march)|steps off|rolling basis|until further notice", re.I)
BETWEEN = re.compile(r"^(?P<street>.+?)\s+(?:between|from)\s+(?P<a>.+?)\s+(?:and|to)\s+(?P<b>.+)$", re.I)
PLACE_NOTE = re.compile(r"^(?P<street>.+?)\s+(?P<note>(?:in front of|around|near|including)\s.+)$", re.I)
CONDITIONAL = re.compile(r"\((?:if|as) needed\)|\bif (?:needed|necessary)\b|\bas needed\b", re.I)
STREETLIKE = re.compile(r"\b(between|street|st\.|avenue|ave\.|drive|dr\.|boulevard|blvd|parkway|pkwy|road|square|"
                        r"bridge|oval|lane|place|way)\b", re.I)


def _month(s: str) -> int:
    key = s.rstrip(".").lower()[:3]
    return [m.lower()[:3] for m in MONTH_NAMES].index(key) + 1


def _clock(s: str) -> str:
    s = s.lower().replace(" ", "")
    if s == "noon":
        return "12:00"
    if s == "midnight":
        return "00:00"
    m = re.match(r"(\d{1,2})(?::(\d{2}))?([ap])", s)
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def _dates(text: str, published: datetime) -> list[datetime]:
    """Every date in the text. A date with no year takes the notice's year, or the next year when it
    would otherwise fall well before the notice (a December notice for New Year's Day)."""
    out = []
    for m in DATE.finditer(text):
        try:
            y = int(m.group(3)) if m.group(3) else published.year
            d = datetime(y, _month(m.group(1)), int(m.group(2)))
        except ValueError:
            continue
        if not m.group(3) and (published - d).days > 60:
            d = d.replace(year=y + 1)
        out.append(d)
    return out


@dataclass
class LeadIn:
    date: str            # YYYY-MM-DD
    end_date: str        # "" when the closure ends the same day (or just past midnight)
    start: str           # HH:MM, or "" when not given
    end: str             # HH:MM, or "" for "until the event ends"


def parse_lead_in(text: str, published: datetime, fallback_date: datetime | None) -> LeadIn | None:
    """Times and dates from a closure lead-in. None when no date can be placed."""
    dates = _dates(text, published) or ([fallback_date] if fallback_date else [])
    if not dates:
        return None
    times = [_clock(t) for t in TIME.findall(text)]
    start = times[0] if times else ""
    end = times[1] if len(times) > 1 else ""
    if len(times) == 1 and END_ONLY.search(text) and not re.search(r"\b(beginning|starting|from)\b", text, re.I):
        start, end = "", times[0]          # "closed until 2 p.m." gives only an end
    if OPEN_END.search(text) and len(times) < 2:
        end = ""
    first, last = dates[0], dates[-1]
    return LeadIn(date=first.strftime("%Y-%m-%d"),
                  end_date=last.strftime("%Y-%m-%d") if last > first else "", start=start, end=end)


def parse_item(text: str) -> dict:
    """'Kelly Drive between Spring Garden Street and 25th Street' -> street, from_street, to_street, notes."""
    t = re.sub(r"[;,.]+$", "", text.strip()).strip()
    t = re.sub(r"\s+and$", "", t)
    conditional = bool(CONDITIONAL.search(t))
    t = CONDITIONAL.sub("", t).strip()
    notes = re.findall(r"\(([^)]*)\)", t)
    t = re.sub(r"\s*\([^)]*\)", "", t).strip()
    row = {"street": t, "from_street": "", "to_street": ""}
    if m := BETWEEN.match(t):
        row = {"street": m["street"], "from_street": m["a"], "to_street": m["b"]}
    elif m := PLACE_NOTE.match(t):
        row["street"] = m["street"]
        notes.append(m["note"])
    # "S. 22nd Street / N. 22nd Street" is one street whose prefix changes at Market.
    parts = [re.sub(r"^[NSEW]\.?\s+", "", p.strip(), flags=re.I) for p in row["street"].split("/")]
    if len(parts) > 1 and len({p.lower() for p in parts}) == 1:
        row["street"] = parts[0]
    return {**row, "conditional": "yes" if conditional else "", "notes": "; ".join(notes)}


GENERIC_HEADING = re.compile(r"road closures?|parking|transportation|septa|safety|information|details|"
                             r"alerts?|detours?|accessib|weather|tips|contact|media|schedule|lost and found", re.I)


def event_name(title: str) -> str:
    """'City Announces Road Closures and Other Details for the 2026 Philly Bike Ride' -> 'Philly Bike Ride'.
    '' when the title names only dates ("... Scheduled for November 1 - November 5")."""
    # The first "for" after the boilerplate, so "SEIU Unions for All March" stays whole.
    m = (re.search(r"\b(?:details|closures?|information|restrictions|scheduled)\s+(?:for|ahead of)\s+(.+)$", title, re.I)
         or re.search(r".*\b(?:for|ahead of)\b\s+(.+)$", title, re.I))
    name = m.group(1) if m else title
    name = re.sub(r"[\"“”]", "", name)
    name = re.sub(r"^(the|sunday\W?s|saturday\W?s)\s+", "", name.strip(), flags=re.I)
    name = re.sub(r"^\d{4}\s+", "", name)
    name = re.sub(r"\s+on\s+" + DATE.pattern + r".*$", "", name, flags=re.I)   # "... on Saturday, June 14, 2025"
    if not re.sub(DATE.pattern + r"|\bweekend\b|[-–—,\s]", "", name, flags=re.I).strip():
        return ""                                   # only dates left
    return name.strip(" .")


# A line that starts with the event's hours ("11 a.m. – 4 p.m."): the line above it names the event.
HOURS_LINE = re.compile(r"^\s*" + _CLOCK_RE, re.I)


def _event_line(bs: list[tuple[str, str]], i: int) -> bool:
    """Whether paragraph i names an event in a multi-event advisory, which names each event in a short
    line right above its hours ("Manayunk Fall Festival", then "11 a.m. – 4 p.m.") rather than in a heading."""
    kind, text = bs[i]
    return (kind == "p" and i + 1 < len(bs) and bs[i + 1][0] == "p" and bool(HOURS_LINE.match(bs[i + 1][1]))
            and len(text) < 90 and not LEAD_IN.search(text) and not text.rstrip().endswith((":", "."))
            and not _dated(text))


def _dated(text: str) -> bool:
    """A heading that is a date ("Saturday, April 5"), not an event name."""
    return bool(DATE.search(text)) and len(DATE.sub("", text).strip(" ,.-–:")) < 4


_STOP = {"the", "and", "of", "for", "a", "annual", "philadelphia", "philly", "city", "festival", "parade",
         "march", "rally", "day", "s", "on", "in"}


def _tokens(s: str) -> set[str]:
    s = re.sub(r"\(.*?\)", "", s.lower()).replace("’", "'").replace("'s", "")
    return {w for w in re.findall(r"[a-z0-9]+", s) if w not in _STOP and not re.fullmatch(r"\d{4}", w)}


def match_series(name: str, catalog: dict[str, str]) -> str | None:
    """The catalog series that best fits the notice's event name: most words shared, among series that
    share at least half their words with it or whose whole name appears in it ("Philly Bike Ride")."""
    want = _tokens(name)
    best, score = None, 0.0
    for slug, label in catalog.items():
        have = _tokens(label) | _tokens(slug.replace("-", " "))
        if not want or not have:
            continue
        if {w for w in have if w.isdigit()} - want:
            continue                     # "No Kings 3" is not the June 2025 "No Kings" protest
        shared = len(want & have)
        jaccard = shared / len(want | have)
        if (jaccard >= 0.5 or shared == len(_tokens(label) or have)) and jaccard > score:
            best, score = slug, jaccard
    return best


def slugify(s: str) -> str:
    s = s.lower().replace("’", "").replace("'", "")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")[:60]


@dataclass
class Parsed:
    rows: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # lead-ins or items that could not be read


def parse_notice(page: str, notice: Notice, catalog: dict[str, str]) -> Parsed:
    published = datetime.strptime(notice.published[:10], "%Y-%m-%d")
    bs = blocks(page)
    out = Parsed()
    name = event_name(notice.title)
    title_series = (match_series(name, catalog) or slugify(name)) if name else f"city-advisory-{notice.published[:10]}"
    # The event's own date, from the title or the opening lines, for lead-ins that give only times.
    intro_dates = _dates(notice.title, published) or _dates(" ".join(t for _, t in bs[:4]), published)
    fallback = intro_dates[0] if intro_dates else None
    heading = ""
    i = 0
    while i < len(bs):
        kind, text = bs[i]
        if kind == "h" or _event_line(bs, i):
            if not GENERIC_HEADING.search(text) and not _dated(text):
                heading = text
            i += 1
            continue
        if kind == "li" or not LEAD_IN.search(text) or not text.rstrip().endswith(":"):
            i += 1
            continue
        lead = parse_lead_in(text, published, fallback)
        # The street list: list items right after the lead-in, or short street-like lines when the
        # notice uses paragraphs instead of a list.
        j = i + 1
        items = []
        while j < len(bs) and bs[j][0] == "li":
            items.append(bs[j][1])
            j += 1
        if not items:
            while (j < len(bs) and bs[j][0] == "p" and len(bs[j][1]) < 140 and STREETLIKE.search(bs[j][1])
                   and not LEAD_IN.search(bs[j][1]) and not bs[j][1].endswith((":", "."))):
                items.append(bs[j][1])
                j += 1
        if lead is None:
            out.problems.append(f"no date in lead-in: {text[:120]}")
        elif not items:
            out.problems.append(f"no street list after: {text[:120]}")
        else:
            series = title_series
            # Multi-event advisories ("Center City Road Closures Scheduled for the April 4 Weekend")
            # name each event in a heading above its list.
            if re.search(r"advisory", notice.title, re.I) and heading:
                series = match_series(heading, catalog) or slugify(heading)
            for it in items:
                r = parse_item(it)
                if not r["street"]:
                    out.problems.append(f"unreadable item: {it[:100]}")
                    continue
                out.rows.append({"series": series, "year": lead.date[:4], "date": lead.date, **r,
                                 "closure_start": lead.start, "closure_end": lead.end,
                                 "end_date": lead.end_date, "source_url": notice.url})
        i = max(j, i + 1)
    return out


# ---- stage 3: write ----

CSV_COLUMNS = ["series", "year", "date", "street", "from_street", "to_street", "closure_start",
               "closure_end", "source_url", "end_date", "conditional", "notes"]
OUT_NAME = "press_releases.csv"


@dataclass
class BuildReport:
    notices: int = 0
    used: list[str] = field(default_factory=list)        # "date title: n rows"
    curated: list[str] = field(default_factory=list)     # skipped: a curated file has it
    empty: list[str] = field(default_factory=list)       # no closure list found
    problems: list[str] = field(default_factory=list)
    duplicates: int = 0
    rows: int = 0


def curated_keys(closures_dir: Path) -> tuple[set[tuple[str, str]], set[str]]:
    """(series, year) pairs and source URLs already in the curated closure files."""
    years, urls = set(), set()
    for path in closures_dir.glob("*.csv"):
        if path.name == OUT_NAME:
            continue
        with path.open(newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                years.add(((r.get("series") or "").strip(), (r.get("year") or "").strip()))
                urls.add((r.get("source_url") or "").strip().rstrip("/"))
    return years, urls


def build_csv(snapshots: Path, closures_dir: Path, catalog: dict[str, str]) -> BuildReport:
    """Parse every saved notice and rewrite closures_dir/press_releases.csv. Where two notices list the
    same closure, the newer one wins; event-years the curated files have are left to them."""
    from .event_closures import closure_id

    index_path = snapshots / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    notices = sorted((Notice(**{k: v[k] for k in ("id", "title", "published", "updated_at", "url", "file")})
                      for v in index.values()), key=lambda n: n.published, reverse=True)
    years, urls = curated_keys(closures_dir)
    rep = BuildReport(notices=len(notices))
    seen: set[str] = set()
    rows: list[dict] = []
    for n in notices:
        if n.url.rstrip("/") in urls:
            rep.curated.append(n.title)
            continue
        parsed = parse_notice((snapshots / n.file).read_text(encoding="utf-8"), n, catalog)
        rep.problems += [f"{n.title[:60]}: {p}" for p in parsed.problems]
        keep = [r for r in parsed.rows if (r["series"], r["year"]) not in years]
        if parsed.rows and not keep:
            rep.curated.append(n.title)
            continue
        if not keep:
            rep.empty.append(n.title)
            continue
        added = 0
        for r in keep:
            cid = closure_id(r["series"], r["date"], r["street"], r["from_street"], r["to_street"])
            if cid in seen:
                rep.duplicates += 1
                continue
            seen.add(cid)
            rows.append(r)
            added += 1
        rep.used.append(f"{n.published[:10]} {n.title[:80]}: {added} rows")
    rows.sort(key=lambda r: (r["date"], r["series"], r["closure_start"], r["street"], r["from_street"]))
    with (closures_dir / OUT_NAME).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in CSV_COLUMNS} for r in rows)
    rep.rows = len(rows)
    return rep

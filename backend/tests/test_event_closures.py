import csv
import logging
from datetime import date, datetime, timezone

from penn.ingestion.event_closures import closure_id, load_all, load_file

URL = "https://www.phila.gov/2026-07-24-travel-advisory/"
OLD_COLUMNS = ["series", "year", "date", "street", "from_street", "to_street",
               "closure_start", "closure_end", "source_url"]
COLUMNS = OLD_COLUMNS + ["end_date", "conditional", "notes"]


def row(**over):
    r = {"series": "test-run", "year": "2026", "date": "2026-07-25", "street": "Kelly Drive",
         "from_street": "Eakins Oval", "to_street": "Fairmount Avenue",
         "closure_start": "06:00", "closure_end": "12:00", "source_url": URL}
    r.update(over)
    return r


def write(tmp_path, rows, columns=COLUMNS, name="closures.csv"):
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return path


def load_one(tmp_path, **over):
    rows, errors = load_file(write(tmp_path, [row(**over)]))
    return (rows[0] if rows else None), errors


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# Required columns

def test_missing_required_value_is_an_error(tmp_path):
    for col in ("series", "year", "date", "street", "source_url"):
        r, errors = load_one(tmp_path, **{col: ""})
        assert r is None
        assert col in errors[0].reason


def test_missing_required_column_is_an_error(tmp_path):
    cols = [c for c in COLUMNS if c != "street"]
    rows, errors = load_file(write(tmp_path, [{k: v for k, v in row().items() if k != "street"}], cols))
    assert rows == []
    assert errors[0].line == 1 and "street" in errors[0].reason


def test_older_file_without_optional_columns_loads(tmp_path):
    rows, errors = load_file(write(tmp_path, [row()], OLD_COLUMNS))
    assert errors == []
    assert rows[0]["end_date"] == date(2026, 7, 25)
    assert rows[0]["conditional"] is False and rows[0]["notes"] == ""


# Formats

def test_bad_date_format_is_an_error(tmp_path):
    for bad in ("07/25/2026", "2026-7-25", "2026-02-30"):
        _, errors = load_one(tmp_path, date=bad)
        assert "date" in errors[0].reason
    _, errors = load_one(tmp_path, end_date="July 26")
    assert "end_date" in errors[0].reason


def test_bad_time_format_is_an_error(tmp_path):
    for bad in ("6:00", "6am", "24:00", "12:60", "06:00:00"):
        _, errors = load_one(tmp_path, closure_start=bad)
        assert "closure_start" in errors[0].reason
    _, errors = load_one(tmp_path, closure_end="9pm")
    assert "closure_end" in errors[0].reason


def test_conditional_must_be_yes_or_no(tmp_path):
    r, _ = load_one(tmp_path, conditional="yes")
    assert r["conditional"] is True
    _, errors = load_one(tmp_path, conditional="maybe")
    assert "conditional" in errors[0].reason


# Source

def test_source_must_be_phila_gov(tmp_path):
    for bad in ("http://www.phila.gov/x", "https://phila.gov/x", "https://example.org/x"):
        _, errors = load_one(tmp_path, source_url=bad)
        assert "source_url" in errors[0].reason


# Date order

def test_end_date_before_date_is_an_error(tmp_path):
    _, errors = load_one(tmp_path, end_date="2026-07-24")
    assert "before" in errors[0].reason


# Timestamps

def test_timestamps_are_philadelphia_time_stored_as_utc(tmp_path):
    r, _ = load_one(tmp_path)  # July: EDT, UTC-4
    assert r["start_ts"] == utc(2026, 7, 25, 10, 0)
    assert r["end_ts"] == utc(2026, 7, 25, 16, 0)
    r, _ = load_one(tmp_path, date="2026-01-01", closure_start="03:00", closure_end="")  # EST, UTC-5
    assert r["start_ts"] == utc(2026, 1, 1, 8, 0)


def test_end_ts_uses_end_date(tmp_path):
    r, _ = load_one(tmp_path, date="2026-05-29", end_date="2026-06-01",
                    closure_start="21:00", closure_end="05:00")
    assert r["start_ts"] == utc(2026, 5, 30, 1, 0)
    assert r["end_ts"] == utc(2026, 6, 1, 9, 0)
    assert r["end_date"] == date(2026, 6, 1)


def test_missing_end_time_leaves_end_ts_empty(tmp_path):
    r, errors = load_one(tmp_path, closure_end="")
    assert errors == []
    assert r["end_ts"] is None and r["start_ts"] is not None
    r, _ = load_one(tmp_path, closure_start="", closure_end="")
    assert r["start_ts"] is None and r["end_ts"] is None


def test_end_before_start_same_day_rolls_to_next_day_with_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        r, errors = load_one(tmp_path, closure_start="21:00", closure_end="02:00")
    assert errors == []
    assert r["end_ts"] == utc(2026, 7, 26, 6, 0)
    assert r["end_date"] == date(2026, 7, 26)
    assert "next day" in caplog.text and "closures.csv:2" in caplog.text


def test_end_equal_to_start_same_day_is_an_error(tmp_path):
    _, errors = load_one(tmp_path, closure_start="06:00", closure_end="06:00")
    assert "equals" in errors[0].reason


def test_end_equal_to_start_with_end_date_is_fine(tmp_path):
    r, errors = load_one(tmp_path, date="2026-07-03", end_date="2026-07-06",
                         closure_start="06:00", closure_end="06:00")
    assert errors == []
    assert r["end_ts"] == utc(2026, 7, 6, 10, 0)


def test_end_not_after_start_with_same_end_date_is_an_error(tmp_path):
    _, errors = load_one(tmp_path, end_date="2026-07-25", closure_start="21:00", closure_end="02:00")
    assert "not after" in errors[0].reason


# ID

def test_closure_id_is_stable_and_depends_on_segment(tmp_path):
    a, _ = load_one(tmp_path)
    b, _ = load_one(tmp_path, notes="changed", closure_end="13:00")
    c, _ = load_one(tmp_path, to_street="Girard Avenue")
    assert a["closure_id"] == b["closure_id"]
    assert a["closure_id"] != c["closure_id"]
    assert a["closure_id"].startswith("test-run-2026-07-25-")
    assert a["closure_id"] == closure_id("test-run", "2026-07-25", "Kelly Drive",
                                         "Eakins Oval", "Fairmount Avenue")


def test_duplicate_closure_is_an_error(tmp_path):
    rows, errors = load_file(write(tmp_path, [row(), row(notes="again")]))
    assert len(rows) == 1
    assert errors[0].line == 3 and "line 2" in errors[0].reason


# Files

def test_load_all_reads_every_csv_and_reports_file_and_line(tmp_path):
    write(tmp_path, [row()], name="a.csv")
    write(tmp_path, [row(street="Ok"), row(street="Bad", date="nope")], name="b.csv")
    (tmp_path / "ignored.txt").write_text("not a csv")
    rows, errors = load_all(tmp_path)
    assert len(rows) == 2
    assert [(e.file, e.line) for e in errors] == [("b.csv", 3)]

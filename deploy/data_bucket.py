"""The data saved by hand, carried to the deployed container through a private S3 bucket, so a new week of data
needs no image rebuild and no restart.

Two sources are saved by hand in a browser: visitphilly's weekly page (turned into a snapshot on your PC) and the
City's closure notices (`jobs/run_job.py press_releases --saved` files them and writes the closure rows). Only
those extracted files go to the bucket, never the sites' pages:

    data/snapshots/visitphilly/*.json        ->  visitphilly/<name>.json
    data/event_closures/press_releases.csv   ->  event_closures/press_releases.csv

    python deploy/data_bucket.py create      once: creates the bucket (private, the S3 default)
    python deploy/data_bucket.py upload      on your PC: sends files that are new or changed
    python deploy/data_bucket.py pull        in the container (deploy/endpoint/refresh.sh, daily and at start):
                                             fetches what's new or changed, then runs the loaders for it
    python deploy/data_bucket.py push-usage  in the container, same schedule: the demo's usage log (data/usage,
                                             written by backend/penn/guardrails.py) summarized per day, to usage/
    python deploy/data_bucket.py usage [N]   on your PC: the last N days (default 7): questions, cache hits,
                                             refusals by reason, visitors, tokens and estimated cost

Settings, from the environment (on your PC, .env): PENN_BUCKET, PENN_S3_ENDPOINT (e.g.
https://s3.us-east-1.amazonaws.com), PENN_S3_REGION, PENN_S3_KEY_ID, PENN_S3_SECRET. Without PENN_BUCKET both commands do nothing,
so local runs and judges' copies work without a bucket. Needs boto3 (`pip install -e "backend[bucket]"`).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# (bucket prefix, local folder, file pattern, the jobs that load it)
GROUPS = [
    ("visitphilly/", ROOT / "data" / "snapshots" / "visitphilly", "*.json", [["visitphilly"]]),
    ("event_closures/", ROOT / "data" / "event_closures", "press_releases.csv",
     [["event_closures"], ["geocode_closures"]]),
]


def _bucket():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    name = os.getenv("PENN_BUCKET", "").strip()
    if not name:
        return None, None
    import boto3
    s3 = boto3.client("s3", endpoint_url=os.environ["PENN_S3_ENDPOINT"], region_name=os.environ["PENN_S3_REGION"],
                      aws_access_key_id=os.environ["PENN_S3_KEY_ID"],
                      aws_secret_access_key=os.environ["PENN_S3_SECRET"])
    return s3, name


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _remote(s3, bucket: str, prefix: str) -> dict[str, str]:
    """key -> ETag (an MD5 of the content for files this small, uploaded in one piece)."""
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        out |= {o["Key"]: o["ETag"].strip('"') for o in page.get("Contents", [])}
    return out


def upload() -> int:
    s3, bucket = _bucket()
    if not s3:
        print("PENN_BUCKET is not set; nothing to upload")
        return 0
    sent = 0
    for prefix, folder, pattern, _ in GROUPS:
        remote = _remote(s3, bucket, prefix)
        for f in sorted(folder.glob(pattern)):
            key = prefix + f.name
            if remote.get(key) == _md5(f):
                continue
            s3.upload_file(str(f), bucket, key)
            print(f"uploaded {key}")
            sent += 1
    print(f"{sent} file(s) uploaded to {bucket}" if sent else f"{bucket} is already up to date")
    return 0


def pull() -> int:
    s3, bucket = _bucket()
    if not s3:
        print("data_bucket: PENN_BUCKET is not set; skipped")
        return 0
    failed = False
    for prefix, folder, _, jobs in GROUPS:
        changed = []
        for key, etag in sorted(_remote(s3, bucket, prefix).items()):
            dest = folder / key[len(prefix):]
            if dest.exists() and _md5(dest) == etag:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dest))
            changed.append(key)
        if not changed:
            continue
        print(f"data_bucket: new or changed: {', '.join(changed)}")
        for job in jobs:
            r = subprocess.run([sys.executable, str(ROOT / "jobs" / "run_job.py"), *job])
            failed |= r.returncode != 0
            if r.returncode:
                print(f"data_bucket: {' '.join(job)} failed ({r.returncode})", file=sys.stderr)
                break
    return 1 if failed else 0


USAGE = ROOT / "data" / "usage"
# Token Factory list prices for Nemotron 3 Super, $ per 1M tokens (Oct 2026). The estimate ignores the cheaper
# cached-input rate ($0.06), so it's an upper bound.
PRICE_IN = float(os.getenv("PENN_PRICE_IN", "0.30"))
PRICE_OUT = float(os.getenv("PENN_PRICE_OUT", "0.90"))


def _summary(day_file: Path) -> dict:
    import json
    from collections import Counter
    rows = [json.loads(line) for line in day_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    out = {"date": day_file.stem, "questions": len(rows), "visitors": len({(r["view"], r["visitor"]) for r in rows}),
           "outcomes": dict(Counter(r["outcome"] for r in rows)), "by_view": dict(Counter(r["view"] for r in rows)),
           "tokens_in": sum(r["tokens_in"] for r in rows), "tokens_out": sum(r["tokens_out"] for r in rows),
           "model_calls": sum(r["model_calls"] for r in rows)}
    fresh = sorted(r["ms"] for r in rows if r["outcome"] == "fresh")
    out["fresh_median_s"] = round(fresh[len(fresh) // 2] / 1000, 1) if fresh else None
    return out


def push_usage() -> int:
    """Each day's summary (today's so far, too) to usage/<date>.json; re-sent each run, so it stays current."""
    import json
    s3, bucket = _bucket()
    if not s3 or not USAGE.exists():
        return 0
    for f in sorted(USAGE.glob("*.jsonl")):
        s3.put_object(Bucket=bucket, Key=f"usage/{f.stem}.json", Body=json.dumps(_summary(f)).encode())
    return 0


def usage(days: int = 7) -> int:
    import json
    s3, bucket = _bucket()
    if not s3:
        raise SystemExit("set PENN_BUCKET (and the PENN_S3_* settings) in .env first")
    keys = sorted(_remote(s3, bucket, "usage/"))[-days:]
    if not keys:
        print("no usage summaries yet")
        return 0
    print(f"{'date':10} {'asked':>6} {'fresh':>6} {'cached':>6} {'no ans.':>7} {'refused':>7} {'visitors':>8} "
          f"{'tokens in':>10} {'out':>8} {'est. $':>7}  refusals")
    for k in keys:
        s = json.loads(s3.get_object(Bucket=bucket, Key=k)["Body"].read())
        o = s["outcomes"]
        refused = {r: n for r, n in o.items() if r not in ("fresh", "cached", "no_answer")}
        cost = s["tokens_in"] / 1e6 * PRICE_IN + s["tokens_out"] / 1e6 * PRICE_OUT
        print(f"{s['date']:10} {s['questions']:>6} {o.get('fresh', 0):>6} {o.get('cached', 0):>6} {o.get('no_answer', 0):>7} "
              f"{sum(refused.values()):>7} {s['visitors']:>8} {s['tokens_in']:>10,} {s['tokens_out']:>8,} "
              f"{cost:>7.2f}  {', '.join(f'{r} {n}' for r, n in sorted(refused.items())) or '-'}")
    return 0


def create() -> int:
    s3, bucket = _bucket()
    if not s3:
        raise SystemExit("set PENN_BUCKET (and the PENN_S3_* settings) in .env first")
    s3.create_bucket(Bucket=bucket)
    print(f"created bucket {bucket}")
    return 0


if __name__ == "__main__":
    commands = {"create": create, "upload": upload, "pull": pull, "push-usage": push_usage, "usage": usage}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in commands:
        raise SystemExit(__doc__)
    if cmd == "usage" and len(sys.argv) > 2:
        raise SystemExit(usage(int(sys.argv[2])))
    raise SystemExit(commands[cmd]())

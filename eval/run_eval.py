"""Send every eval question to the running API and save the raw results for grading."""
import json
import os
import sys
import time
from pathlib import Path

import httpx

API = os.getenv("NEXT_PUBLIC_PENN_API", "http://localhost:8000")
HERE = Path(__file__).parent


def main() -> None:
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"run-{int(time.time())}.jsonl"
    with httpx.Client(timeout=180) as c, out.open("w") as f:
        for line in (HERE / "questions.jsonl").read_text().splitlines():
            q = json.loads(line)
            body = {"question": q["question"]}
            if q.get("pin"):
                body["lat"], body["lon"] = q["pin"]
            r = c.post(f"{API}/agent", json=body)
            rec = {"id": q["id"], "status": r.status_code, "expect": q["expect"],
                   "trap": q.get("trap"), "response": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}
            f.write(json.dumps(rec) + "\n")
            print(q["id"], r.status_code)
    print("saved", out)


if __name__ == "__main__":
    sys.exit(main())

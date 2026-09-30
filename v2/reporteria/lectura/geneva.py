"""bond_schedule.jsonl de Geneva: un record por código con bond_specific y events (Interest / Sink / Mature)."""
from __future__ import annotations

import json
from pathlib import Path


def leer_jsonl(path: Path) -> dict[str, dict]:
    recs = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                r = json.loads(line)
                recs[str(r.get("code", "")).strip()] = r
    return recs

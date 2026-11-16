#!/usr/bin/env python3
"""
Deadlines (dato + klokkeslæt) for Klassiker Manager-runderne fra Holdet.

Holdet låser holdet når løbet starter. Starttiden står i spillets program
(schedules/<spil-id>) som "start" på hvert løb — det er deadline. (Rundernes
"end" i rounds-listen ligger efter løbet og er IKKE deadline.)

Løbene i programmet kobles på config'ens løb på navnet (samme regel som
startliste-importen); lykkes det ikke for alle, bruges rækkefølgen, hvis
antallet passer.

Output: data/klassiker/deadlines_<sæson>.json  {"deadlines": {slug: ISO-tid (UTC)}}
Filen kan også rettes i hånden, hvis Holdet flytter et tidspunkt.

Brug:
    python scripts/analysis/import_holdet_deadlines.py                 # config'ens holdet.game_id
    python scripts/analysis/import_holdet_deadlines.py --game-id 7xx
    python scripts/analysis/import_holdet_deadlines.py --file schedule.json   # en allerede hentet fil
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_klassiker_sheets import race_matcher  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/klassiker/config.json"
API = "https://www.holdet.dk/api/season"


def fetch(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    cfg = json.loads(CONFIG.read_text())
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-id", type=int, default=cfg["holdet"].get("game_id"))
    ap.add_argument("--file", help="en hentet schedules-fil i stedet for at spørge Holdet")
    args = ap.parse_args()
    if not args.file and not args.game_id:
        sys.exit("intet spil-id: sæt holdet.game_id i data/klassiker/config.json eller brug --game-id")

    sched = json.loads(Path(args.file).read_text()) if args.file else fetch(f"{API}/schedules/{args.game_id}")
    emb = sched["_embedded"]["events"]
    events = [emb[str(e)] for e in sched["events"] if str(e) in emb]
    events = [e for e in events if e.get("start")]
    races = cfg["races"]

    match = race_matcher(races)
    by_name = {}
    for e in events:
        slug = match(e.get("name", ""))
        if slug and slug not in by_name:
            by_name[slug] = e["start"]
    if len(by_name) == len(races):
        deadlines, how = by_name, "navn"
    elif len(events) == len(races):
        deadlines = {r["slug"]: e["start"] for r, e in zip(races, sorted(events, key=lambda e: e["start"]))}
        how = "rækkefølge"
    else:
        deadlines, how = by_name, "navn (ufuldstændig)"

    out = ROOT / f"data/klassiker/deadlines_{cfg['season']}.json"
    out.write_text(json.dumps({
        "season": cfg["season"], "game_id": args.game_id,
        "fetched": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "source": "Holdet.dk spilprogram: løbets start = deadline",
        "deadlines": {r["slug"]: deadlines[r["slug"]] for r in races if r["slug"] in deadlines},
    }, ensure_ascii=False, indent=1))
    print(f"Skrev {out.relative_to(ROOT)}: {len(deadlines)}/{len(races)} deadlines (koblet på {how})")
    for r in races:
        print(f"  {r['short']:9} {deadlines.get(r['slug'], '— mangler')}")


if __name__ == "__main__":
    main()

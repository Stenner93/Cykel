#!/usr/bin/env python3
"""
Hvor tit skiftede de faktisk ud — og hvor?

Spørgsmålet kom af ruteplanlæggeren: hvor lang må en "blok" være, før den
holder op med at ligne den måde man rent faktisk spiller på? I stedet for at
gætte et loft, kan vi se efter. Holdet-snapshottene har hvert holds lineup
runde for runde, så antallet af indkøb mellem to runder er direkte aflæseligt.

Det er ren adfærdsdata fra de 14 hold vi i forvejen følger (top-10, Kasper,
os og de to optaktshold), joinet på personId — ingen navnematchning.

Brug:
    python scripts/analysis/eval_transfer_rhythm.py
    python scripts/analysis/eval_transfer_rhythm.py --race vuelta2026 --out FIL
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RACES = {
    "vuelta2026": {
        "label": "Vuelta 2026",
        "holdet_dir": ROOT / "data/sources/vuelta2026/holdet",
        "route": ROOT / "data/routes/vuelta2026.json",
        "labels": {7271757: "os (Anders)", 7272262: "Kasper",
                   7285351: "optakt (TheFantasyTool)", 7280380: "optakt (Feltet.dk)"},
        "out": ROOT / "data/analysis/transfer_rhythm_vuelta.json",
        "web": ROOT / "web/data/vuelta2026_transfer_rhythm.json",
    },
}


def round_to_stage(H):
    """Holdets runde-nr -> løbets rigtige etapenummer (står i eventets navn)."""
    sched = json.loads((H / "reference/schedule.json").read_text())
    emb = sched["_embedded"]["events"]
    out = {}
    for idx, eid in enumerate(sched["events"], 1):
        m = re.match(r"\s*(\d+)\.", emb.get(str(eid), {}).get("name", ""))
        out[idx] = int(m.group(1)) if m else idx
    return out


def load(H):
    ref = json.loads((H / "reference/players.json").read_text())
    pl2pe = {it["id"]: it["personId"] for it in ref["items"]}
    name = {}
    for k, v in ref.get("_embedded", {}).get("persons", {}).items():
        name[int(k)] = f"{v.get('firstname','')} {v.get('lastname','')}".strip()
    return pl2pe, name


def rosters(H, tid, pl2pe):
    out = {}
    tdir = H / "teams" / str(tid)
    for rf in sorted(os.listdir(tdir)):
        rnd = int(rf.split("_")[1].split(".")[0])
        out[rnd] = {pl2pe[it["playerId"]] for it in json.loads((tdir / rf).read_text())["items"]}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default="vuelta2026", choices=sorted(RACES))
    ap.add_argument("--out")
    args = ap.parse_args()
    cfg = RACES[args.race]
    H = cfg["holdet_dir"]

    pl2pe, name = load(H)
    r2s = round_to_stage(H)
    tids = sorted(int(t) for t in os.listdir(H / "teams"))
    teams = {t: rosters(H, t, pl2pe) for t in tids}
    label = {t: cfg["labels"].get(t, f"top-10 ({t})") for t in tids}

    route = json.loads(Path(cfg["route"]).read_text())
    rest = set(route.get("rest_after", []))
    cat = {s["stage"]: s["category"] for s in route["stages"]}
    prof = {s["stage"]: s.get("profile_score") for s in route["stages"]}

    rounds = sorted(r2s)
    per_round, churn = [], collections.Counter()
    for rnd in rounds[1:]:
        ins = {}
        for t, r in teams.items():
            if rnd in r and rnd - 1 in r:
                ins[t] = len(r[rnd] - r[rnd - 1])
        if not ins:
            continue
        st = r2s[rnd]
        n = len(ins)
        tot = sum(ins.values())
        per_round.append({
            "round": rnd, "stage": st,
            "prev_stage": r2s[rnd - 1],
            "after_rest_day": r2s[rnd - 1] in rest,
            "category": cat.get(st), "profile_score": prof.get(st),
            "teams": n,
            "transfers": tot,
            "avg_per_team": round(tot / n, 2),
            "teams_that_moved": sum(1 for v in ins.values() if v),
            "by_team": {label[t]: v for t, v in sorted(ins.items())},
        })
        for t, v in ins.items():
            churn[label[t]] += v

    tot = sum(r["transfers"] for r in per_round)
    nrounds = len(per_round)
    out = {
        "note": "Faktisk skifteadfærd, aflæst af lineup-snapshots. 'transfers' er antal "
                "INDKØB mellem to runder, summeret over alle hold. Joinet på personId.",
        "teams": len(tids),
        "rounds_compared": nrounds,
        "avg_transfers_per_team_per_round": round(tot / (nrounds * len(tids)), 2),
        "total_transfers": tot,
        "by_team_total": dict(churn.most_common()),
        "rounds": per_round,
    }
    dest = Path(args.out) if args.out else cfg["out"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    dest.write_text(payload)
    # Ruteplanlæggeren viser tallene, så de også ligger under web/.
    if not args.out and cfg.get("web"):
        cfg["web"].write_text(payload)

    print(f"{cfg['label']} — {len(tids)} hold, {nrounds} runder sammenlignet")
    print(f"Gennemsnit: {out['avg_transfers_per_team_per_round']} indkøb pr. hold pr. runde\n")
    print(f"{'til etape':>10} {'kat':<7} {'profil':>6} {'hold der skiftede':>18} {'indkøb':>7} {'snit':>6}  hviledag før")
    for r in per_round:
        print(f"{'E'+str(r['stage']):>10} {str(r['category']):<7} {str(r['profile_score']):>6} "
              f"{str(r['teams_that_moved'])+'/'+str(r['teams']):>18} {r['transfers']:>7} "
              f"{r['avg_per_team']:>6} {'  ja' if r['after_rest_day'] else ''}")
    print("\nSamlet pr. hold:")
    for t, v in churn.most_common():
        print(f"  {v:>4} indkøb  {t}")
    print(f"\nSkrev {dest}")


if __name__ == "__main__":
    main()

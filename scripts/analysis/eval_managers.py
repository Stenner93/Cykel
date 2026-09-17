#!/usr/bin/env python3
"""
Task 2 — manager comparison, TdF 2026.

Compares our team (7145433) vs Kasper (7132927) vs the two optakt authors
(7157567, 7132842) vs the top-10 final managers, using the round-by-round
holdet snapshot joined to per-rider realised growth.

Metric: each round (= stage) a team scores the sum of its 8 riders' realised
`actual` growth, with the captain counted double (holdet's captain rule). This
is the same `actual` metric used in the indicator/optakter evaluations, so the
whole post-race analysis stays consistent.

NB: this is a points/growth-attribution model, not holdet's exact price/bank
value (bank isn't in the snapshot). It answers "which rider & captain CHOICES
created or cost value", which is the question we care about.

Outputs data/analysis/manager_eval[_<race>].json + a printed summary.
"""
import argparse
import json, os, collections, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RACES = {
    "tdf2026": {
        "label": "TdF 2026",
        "holdet_dir": ROOT / "data/sources/tdf2026/holdet",
        "pred": ROOT / "web/data/tdf2026_predictions.json",
        "our": 7145433, "kasper": 7132927, "optakt": {7157567, 7132842},
        "out": ROOT / "data/analysis/manager_eval.json",
    },
    "vuelta2026": {
        "label": "Vuelta 2026",
        "holdet_dir": ROOT / "data/sources/vuelta2026/holdet",
        "pred": ROOT / "web/data/vuelta2026_predictions.json",
        "our": 7271757, "kasper": 7272262, "optakt": {7285351},
        "out": ROOT / "data/analysis/manager_eval_vuelta.json",
    },
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("å", "aa")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def resolve(ref_name, pred_names_norm):
    """Map a holdet reference name onto the prediction file's canonical name.

    Holdet's reference dump spells names differently from the rider database
    ("Enric Mas Nicolau" vs "Enric Mas", "Gregor Muhlberger" vs "Gregor
    Mühlberger"), so an exact string join silently drops riders — and dropping
    them is invisible in the output, which is how a whole field's biggest
    grower once vanished from every single roster at once. Match on normalised
    text, then on one name being a token-prefix of the other, then on
    first+last token. Deliberately NO surname-only fallback: "Lucas Hamilton"
    must not collapse onto "Chris Hamilton".
    """
    n = norm(ref_name)
    if n in pred_names_norm:
        return pred_names_norm[n]
    toks = n.split()
    for cand_n, cand in pred_names_norm.items():
        ct = cand_n.split()
        if ct[: len(toks)] == toks or toks[: len(ct)] == ct:
            return cand
    if len(toks) >= 2:
        key = (toks[0], toks[-1])
        for cand_n, cand in pred_names_norm.items():
            ct = cand_n.split()
            if len(ct) >= 2 and (ct[0], ct[-1]) == key:
                return cand
    return None


def load(H, pred_path):
    # Build playerId -> canonical rider name. The names come from the race's
    # OWN saved reference dump (not the live data/cache/holdet_players.json,
    # which reflects whichever cartridge the daily scraper currently tracks),
    # then get resolved onto the prediction file's spelling — see resolve().
    ref = json.loads((H / "reference/players.json").read_text())
    persons = ref.get("_embedded", {}).get("persons", {})
    raw = {}
    for it in ref.get("items", []):
        person = persons.get(str(it.get("personId"))) or persons.get(it.get("personId"))
        if it.get("id") is None or not person:
            continue
        full = " ".join(x for x in (person.get("firstName"), person.get("lastName")) if x).strip()
        if full:
            raw[it["id"]] = full

    pred = json.loads(pred_path.read_text())
    actual = {}          # stage -> {name: growth}
    field_best = {}      # stage -> best growth in whole field
    for s in pred["stages"]:
        d = {r["name"]: (r.get("actual") or 0) for r in s["riders"]}
        actual[s["num"]] = d
        field_best[s["num"]] = max(d.values()) if d else 0

    pred_names_norm = {norm(r["name"]): r["name"]
                       for s in pred["stages"] for r in s["riders"]}
    pid2name, unresolved = {}, []
    for pid, full in raw.items():
        canon = resolve(full, pred_names_norm)
        if canon is None:
            unresolved.append(full)
        else:
            pid2name[pid] = canon
    if unresolved:
        print(f"[advarsel] {len(unresolved)} rytter(e) i Holdets referencedata kunne ikke "
              f"kobles til prediktionsfilen og tæller derfor 0: {', '.join(sorted(unresolved))}",
              file=sys.stderr)
    return pid2name, actual, field_best


def team_rounds(H, tid, pid2name):
    """Return {round: {'roster': [names], 'captain': name}}."""
    out = {}
    tdir = H / "teams" / str(tid)
    for rf in sorted(os.listdir(tdir)):
        r = int(rf.split("_")[1].split(".")[0])
        items = json.loads((tdir / rf).read_text())["items"]
        roster, captain, dropped = [], None, 0
        for it in items:
            nm = pid2name.get(it["playerId"])
            if nm is None:
                dropped += 1
                continue
            roster.append(nm)
            if it["role"] == "captain":
                captain = nm
        if dropped:
            print(f"[advarsel] hold {tid} runde {r}: {dropped} af {len(items)} ryttere "
                  f"kunne ikke kobles og tæller 0", file=sys.stderr)
        out[r] = {"roster": roster, "captain": captain}
    return out


def analyse_team(H, tid, pid2name, actual, field_best):
    rounds = team_rounds(H, tid, pid2name)
    cum, curve = 0, []
    contrib = collections.defaultdict(float)          # name -> total contribution
    cap_bonus_total = 0                                # extra from captain doubling
    cap_best_own = 0                                   # if captained roster's best each rd
    cap_best_field = 0                                 # theoretical field-best captain
    transfers = 0
    prev = None
    per_round = []
    for r in sorted(rounds):
        info = rounds[r]
        ros, cap = info["roster"], info["captain"]
        ag = actual.get(r, {})
        rscore = 0
        for nm in ros:
            g = ag.get(nm, 0)
            c = g * (2 if nm == cap else 1)
            rscore += c
            contrib[nm] += c
        cap_g = ag.get(cap, 0) if cap else 0
        cap_bonus_total += cap_g                        # the doubled portion == one extra cap_g
        best_own = max((ag.get(n, 0) for n in ros), default=0)
        cap_best_own += best_own
        cap_best_field += field_best.get(r, 0)
        cum += rscore
        curve.append(round(cum / 1e6, 2))
        if prev is not None:
            transfers += len(set(ros) - set(prev))
        prev = ros
        per_round.append({"round": r, "score_M": round(rscore / 1e6, 3),
                          "captain": cap, "cap_growth_M": round(cap_g / 1e6, 3),
                          "best_own_M": round(best_own / 1e6, 3)})
    return {
        "team_id": tid,
        "total_M": round(cum / 1e6, 2),
        "curve_M": curve,
        "transfers": transfers,
        "captain_captured_M": round(cap_bonus_total / 1e6, 2),
        "captain_best_own_M": round(cap_best_own / 1e6, 2),
        "captain_best_field_M": round(cap_best_field / 1e6, 2),
        "captain_efficiency_vs_own": round(cap_bonus_total / cap_best_own, 3) if cap_best_own else None,
        "contrib": {k: round(v / 1e6, 3) for k, v in contrib.items()},
        "per_round": per_round,
        "_rounds": rounds,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default="tdf2026", choices=sorted(RACES))
    args = ap.parse_args()
    cfg = RACES[args.race]
    H, OUR, KASPER, OPTAKT = cfg["holdet_dir"], cfg["our"], cfg["kasper"], cfg["optakt"]

    pid2name, actual, field_best = load(H, cfg["pred"])
    manifest = json.loads((H / "manifest.json").read_text())
    all_ids = [int(t) for t in os.listdir(H / "teams")]
    top10_all = [t for t in all_ids if t not in ({OUR, KASPER} | OPTAKT)]

    A = {t: analyse_team(H, t, pid2name, actual, field_best) for t in all_ids}

    n = len(manifest.get("rounds", [])) or max((len(A[t]["curve_M"]) for t in all_ids), default=0)

    # Only compare top-10 teams with a complete round-by-round curve — a team
    # whose lineup-fetch failed for some rounds (holdet flakiness / privacy)
    # would otherwise silently truncate the median/best comparison.
    top10 = [t for t in top10_all if len(A[t]["curve_M"]) == n]
    incomplete = [t for t in top10_all if t not in top10]
    if incomplete:
        print(f"[note] excluding {len(incomplete)} team(s) with incomplete round data: {incomplete}")

    # ---- cumulative comparison ----
    def curve(tid):
        return A[tid]["curve_M"]

    top10_median = [round(sorted(A[t]["curve_M"][i] for t in top10)[len(top10) // 2], 2)
                    for i in range(n)] if top10 else []
    top10_best_id = max(top10, key=lambda t: A[t]["total_M"]) if top10 else None

    # ---- our team attribution ----
    our = A[OUR]
    our_contrib = sorted(our["contrib"].items(), key=lambda kv: kv[1])
    worst = our_contrib[:6]
    best = list(reversed(our_contrib[-6:]))

    # ---- missed gains: riders top-10 owned (round-count) that we never owned ----
    our_ever = {nm for r in our["_rounds"].values() for nm in r["roster"]}
    # season growth per rider (sum of actual over all stages)
    season_growth = collections.defaultdict(float)
    for st, d in actual.items():
        for nm, g in d.items():
            season_growth[nm] += g
    top10_ownership = collections.defaultdict(int)   # rider -> #top10 rounds owned
    for t in top10:
        for r in A[t]["_rounds"].values():
            for nm in set(r["roster"]):
                top10_ownership[nm] += 1
    missed = []
    for nm, own in top10_ownership.items():
        if nm not in our_ever and season_growth.get(nm, 0) > 0:
            missed.append((nm, own, round(season_growth[nm] / 1e6, 2)))
    missed.sort(key=lambda x: (-x[2], -x[1]))

    # ---- captain post-mortem: our biggest captain wins/misses ----
    cap_rounds = []
    for pr in our["per_round"]:
        r = pr["round"]
        regret = round(pr["best_own_M"] - pr["cap_growth_M"], 3)  # left on table within roster
        cap_rounds.append({"round": r, "captain": pr["captain"],
                           "cap_growth_M": pr["cap_growth_M"],
                           "best_own_M": pr["best_own_M"], "regret_M": regret})
    cap_misses = sorted(cap_rounds, key=lambda x: -x["regret_M"])[:5]

    out = {
        "note": "Growth-attribution comparison. score = sum(rider actual growth), "
                "captain doubled. Not holdet's exact price/bank value.",
        "incomplete_teams_excluded": incomplete,
        "ranking_total_M": sorted(
            [{"team_id": t, "label": manifest["our_teams"].get(str(t), "top-10"),
              "total_M": A[t]["total_M"], "transfers": A[t]["transfers"],
              "captain_captured_M": A[t]["captain_captured_M"],
              "captain_eff_vs_own": A[t]["captain_efficiency_vs_own"]}
             for t in all_ids], key=lambda x: -x["total_M"]),
        "curves_M": {
            "os": curve(OUR), "kasper": curve(KASPER),
            "optakt": {str(t): curve(t) for t in OPTAKT},
            "top10_median": top10_median,
            "top10_best": curve(top10_best_id) if top10_best_id else None,
            "top10_best_id": top10_best_id,
        },
        "our_best_picks_M": [{"rider": k, "contribution_M": v} for k, v in best],
        "our_worst_picks_M": [{"rider": k, "contribution_M": v} for k, v in worst],
        "our_missed_gains": [{"rider": nm, "top10_rounds_owned": own, "season_growth_M": g}
                             for nm, own, g in missed[:12]],
        "our_captain_misses": cap_misses,
    }
    cfg["out"].write_text(json.dumps(out, indent=2, ensure_ascii=False))

    # ---- print ----
    print(f"MANAGER-SAMMENLIGNING — {cfg['label']} (vækst-attribution, kaptajn dobbelt)\n")
    print(f"{'#':>2} {'hold':>9} {'label':16} {'total(M)':>9} {'transfers':>9} {'kaptajn(M)':>10} {'kap-eff':>7}")
    for i, row in enumerate(out["ranking_total_M"], 1):
        print(f"{i:>2} {row['team_id']:>9} {row['label']:16} {row['total_M']:>9.2f} "
              f"{row['transfers']:>9} {row['captain_captured_M']:>10.2f} "
              f"{(row['captain_eff_vs_own'] or 0):>7.2f}")
    our_rank = next(i for i, r in enumerate(out["ranking_total_M"], 1) if r["team_id"] == OUR)
    top10_line = (f"top-10 median {top10_median[-1]}M   top-10 bedste {A[top10_best_id]['total_M']}M"
                  if top10_best_id else "(ingen komplette top-10 hold at sammenligne med)")
    print(f"\nVores hold: nr. {our_rank}/{len(all_ids)}   slut-total {A[OUR]['total_M']}M   {top10_line}")
    print("\nVores BEDSTE ryttervalg (bidrag, M):")
    for p in out["our_best_picks_M"]:
        print(f"  +{p['contribution_M']:>6.2f}  {p['rider']}")
    print("\nVores DÅRLIGSTE / mest spildte plads (M):")
    for p in out["our_worst_picks_M"]:
        print(f"  {p['contribution_M']:>7.2f}  {p['rider']}")
    print("\nStørste kaptajns-fejlvalg (regret = bedste egne rytter minus valgt kaptajn, M):")
    for c in out["our_captain_misses"]:
        print(f"  R{c['round']:>2}  valgte {c['captain']:<22} +{c['cap_growth_M']:.2f}  "
              f"vs bedste egne +{c['best_own_M']:.2f}  (tabt {c['regret_M']:.2f})")
    print("\nMissede gevinster (ryttere top-10 havde, vi aldrig ejede):")
    for m in out["our_missed_gains"][:8]:
        print(f"  {m['season_growth_M']:>5.2f}M  {m['rider']:<24} (ejet i {m['top10_rounds_owned']} top-10 runder)")
    print(f"\nWrote {cfg['out'].relative_to(ROOT)}")


if __name__ == "__main__":
    main()

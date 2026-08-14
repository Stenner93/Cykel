#!/usr/bin/env python3
"""Samlet, konsistent kalibrering af optakt-kaptajner vs. model — Tour + Giro.

Samme metrik for alle: for hver (kilde, etape) findes optaktens kaptajns
placering i etapens faktiske vækstfelt.
  - Kaptajn = #1  : havde kaptajnen størst faktisk vækst
  - Top-3         : kaptajnen blandt de 3 største
  - Andel fanget  : kaptajnens vækst / etapens bedst mulige kaptajn-vækst (snit)
Model-kaptajn = højeste exp pr. etape. Kilder: Feltet + Simon (Tour), Ingemann (Giro).

Kør: python3 scripts/analysis/score_optakt_combined.py
Skriver: web/data/optakt_calibration_combined.json
"""
import json, unicodedata, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def norm(s): return unicodedata.normalize("NFKD", s or "").encode("ascii","ignore").decode().lower()
def load(p): return json.load(open(os.path.join(ROOT, p), encoding="utf-8"))

def find(riders, key):
    k = norm(key); m = [r for r in riders if k in norm(r["name"])]
    return m[0] if m else None
def rank_of(riders, rider):
    order = sorted(riders, key=lambda x: (x.get("actual") or 0), reverse=True)
    return next((i for i,r in enumerate(order,1) if r["id"]==rider["id"]), None)
def best_actual(riders): return max((r.get("actual") or 0) for r in riders) or 1

def stage_map(pred): return {s["num"]: s for s in pred["stages"]}

# ── captains per source ────────────────────────────────────────────────────────
tdf = stage_map(load("web/data/tdf2026_predictions.json"))
giro = stage_map(load("web/data/giro2026_predictions.json"))
feltet = {s["stage"]: s.get("captain") for s in load("data/analysis/optakt_picks_feltet.json")}
simon  = {s["stage"]: s.get("captain") for s in load("data/analysis/optakt_picks_simon.json")}
INGE = {1:'Milan',4:'Lund',5:'Ciccone',6:'Magnier',7:'Vingegaard',9:'Vingegaard',10:'Ganna',
        11:'Eulálio',13:'Eulálio',14:'Vingegaard',15:'Milan',16:'Vingegaard',17:'Vingegaard',18:'Narváez',19:'Vingegaard'}

# observations: (race_stages, stage_num, captain_name)
SRC = {
  # Frederik Ingemann ER Feltets ekspert → Feltet(Tour) + Ingemann(Giro) er samme kilde.
  "Feltet (Ingemann)": ([(tdf,  n, c) for n,c in feltet.items() if c and n in tdf]
                        + [(giro, n, c) for n,c in INGE.items()  if n in giro]),
  "Simon K. Kjær":     [(tdf,  n, c) for n,c in simon.items()  if c and n in tdf],
}

def model_cap(stage): return max(stage["riders"], key=lambda x: x.get("exp") or 0)

def score(obs, capfn):
    n=h1=h3=0; cap=[]
    for stages, num, capname in obs:
        st = stages[num]; rs = st["riders"]
        c = capfn(st, capname)
        if not c: continue
        n += 1
        rk = rank_of(rs, c); h1 += rk==1; h3 += rk<=3
        cap.append((c.get("actual") or 0)/best_actual(rs))
    return {"n":n, "hit1":round(100*h1/n), "hit3":round(100*h3/n), "captured":round(100*sum(cap)/n)}

opt = lambda st, name: find(st["riders"], name)
mdl = lambda st, name: model_cap(st)

rows = {}
for src, obs in SRC.items():
    rows[src] = score(obs, opt)
allobs = [o for obs in SRC.values() for o in obs]
rows["Optakt samlet"] = score(allobs, opt)
rows["Model (samme etaper)"] = score(allobs, mdl)

out = {"note":"Kaptajn-kalibrering, konsistent metrik, Tour+Giro. captured = andel af bedst mulige kaptajn-vækst.",
       "rows": rows}
json.dump(out, open(os.path.join(ROOT,"web/data/optakt_calibration_combined.json"),"w"), ensure_ascii=False, indent=2)

print(f"{'Kilde':22}{'n':>4}{'Kaptajn=#1':>12}{'Top-3':>8}{'Andel fanget':>14}")
for k in ["Model (samme etaper)","Feltet (Ingemann)","Simon K. Kjær","Optakt samlet"]:
    r=rows[k]; print(f"{k:22}{r['n']:>4}{r['hit1']:>11}%{r['hit3']:>7}%{r['captured']:>13}%")

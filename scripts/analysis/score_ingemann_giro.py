#!/usr/bin/env python3
"""Score Frederik Ingemanns Giro-optakter (kaptajnvalg) mod den faktiske vækst.

Tredje optakt-kilde ved siden af Tour-optakterne (Feltet.dk + Simon K. Kjær).
Ingemanns kaptajn pr. etape er læst direkte af optakterne i
archive/data/giro2026/ingemann_optakter.txt og facit kommer fra
web/data/giro2026_predictions.json (felterne 'exp', 'actual', 'is_cap').

Kør:  python3 scripts/analysis/score_ingemann_giro.py
Skriver:  web/data/giro_optakt_calibration.json
"""
import json, unicodedata, os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED = os.path.join(ROOT, "web/data/giro2026_predictions.json")
OUT  = os.path.join(ROOT, "web/data/giro_optakt_calibration.json")

# Ingemanns kaptajn pr. etape (surname-nøgle), kun etaper med facit i predictions.
# E17 primær = Vingegaard (han nævner Narváez som gamble). E8/E12/E20 mangler optakt/facit.
ING_CAPTAIN = {1:'Milan',4:'Lund',5:'Ciccone',6:'Magnier',7:'Vingegaard',9:'Vingegaard',
               10:'Ganna',11:'Eulálio',13:'Eulálio',14:'Vingegaard',15:'Milan',16:'Vingegaard',
               17:'Vingegaard',18:'Narváez',19:'Vingegaard'}

def norm(s): return unicodedata.normalize('NFKD', s).encode('ascii','ignore').decode().lower()
def find(riders, key):
    k = norm(key); m = [r for r in riders if k in norm(r['name'])]
    return m[0] if m else None
def rank_of(riders, rider):
    order = sorted(riders, key=lambda x: (x.get('actual') or 0), reverse=True)
    return next((i for i,r in enumerate(order,1) if r['id']==rider['id']), None)

def score(stages, capfn):
    n=hits1=hits3=hits5=0; captured=[]; det=[]
    for num, ing in ING_CAPTAIN.items():
        s = stages.get(num)
        if not s: continue
        cap = capfn(s, ing)
        if not cap: continue
        n += 1
        best = max((r.get('actual') or 0) for r in s['riders']) or 1
        a = cap.get('actual') or 0
        rk = rank_of(s['riders'], cap)
        hits1 += rk==1; hits3 += rk<=3; hits5 += rk<=5
        captured.append(a/best)
        det.append({'stage':num,'type':s['type'],'captain':cap['name'],'actual':a,'rank':rk,'best':best})
    return {'n':n,'hit1':round(100*hits1/n),'hit3':round(100*hits3/n),'hit5':round(100*hits5/n),
            'captured':round(100*sum(captured)/n),'detail':det}

def main():
    d = json.load(open(PRED, encoding='utf-8'))
    stages = {s['num']: s for s in d['stages']}
    ing = score(stages, lambda s,k: find(s['riders'], k))
    mdl = score(stages, lambda s,k: max(s['riders'], key=lambda x: x.get('exp') or 0))
    # head-to-head på fanget kaptajn-vækst
    idet = {x['stage']:x for x in ing['detail']}; mdet = {x['stage']:x for x in mdl['detail']}
    iw=mw=tie=0
    for num in ING_CAPTAIN:
        if num in idet and num in mdet:
            a,b = idet[num]['actual'], mdet[num]['actual']
            iw += a>b; mw += b>a; tie += a==b
    out = {'source':'Frederik Ingemann (Feltet) — Giro 2026 optakter',
           'metric':'kaptajnvalg vs. faktisk vækst (giro2026_predictions.json)',
           'stages_scored': ing['n'], 'ingemann':ing, 'model':mdl,
           'head_to_head':{'ingemann':iw,'model':mw,'tie':tie}}
    json.dump(out, open(OUT,'w',encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f"Ingemann: #1={ing['hit1']}%  top3={ing['hit3']}%  top5={ing['hit5']}%  fanget={ing['captured']}%")
    print(f"Model:    #1={mdl['hit1']}%  top3={mdl['hit3']}%  top5={mdl['hit5']}%  fanget={mdl['captured']}%")
    print(f"Head-to-head (kaptajn-vækst): Ingemann {iw} · Model {mw} · lige {tie}")
    print("Skrev", OUT)

if __name__ == '__main__':
    main()

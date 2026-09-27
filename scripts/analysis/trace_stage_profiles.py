#!/usr/bin/env python3
"""
Aflæser etapeprofilernes form ud af PCS-billederne og gemmer dem som tal.

Formålet er det simple grafiske udtryk — en ren streg i sidens egne farver —
men tegnet efter den FAKTISKE rute i stedet for et skøn ud fra
stigningskategorier. PCS har ingen højdedata på siden, kun billedet, så
formen læses ud af billedet.

Sådan:
  1. Profilerne fra La Flamme Rouge er en massiv grøn flade på hvid bund.
     For hver billedkolonne findes den øverste grønne pixel — det er
     profilens overkant.
  2. Kurven udjævnes let, så JPEG-støj og tekstlinjer ikke tælles med.
  3. Formen gemmes normaliseret, 0 til 1 af etapens eget højdespænd.

Y-aksen er skaleret forskelligt fra etape til etape — LFR vælger selv
intervallet, og der står ingen tal i billedet vi kan læse uden OCR. Derfor
kan pixelhøjder IKKE sammenlignes på tværs, og en flad etape ville se ud som
en bjergetape hvis hver form blev tegnet i fuld højde.

Løsningen er ikke at opfinde en metermålestok, men at lade tegningens HØJDE
komme fra et tal vi faktisk kender: etapens højdemeter i forhold til løbets
hårdeste. E17 med 930 hm tegnes derfor i 19 % af kassens højde ved siden af
E9 med 4952. Formen er ægte, højden er en kendt størrelse, og der påstås
ikke noget om absolutte højdemeter over havet.

Et forsøg på at kalibrere med vmeters / samlet pixelstigning blev kasseret:
på flade etaper er pixelstigningen så lille at støj dominerer, og E16 endte
med et højdespænd på 1854 m ud af 1914 højdemeter i alt. Det kan ikke passe.

Brug:
    python scripts/analysis/trace_stage_profiles.py --race vuelta2026
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit("kræver Pillow: pip install Pillow")

ROOT = Path(__file__).resolve().parents[2]
N_POINTS = 140          # nok til at en stigning kan ses, lidt nok til at fylde intet
SMOOTH = 7              # kolonner i det glidende gennemsnit


def is_green(p):
    r, g, b = p[:3]
    return g > 80 and g > r + 20 and g > b + 25


def trace(path: Path):
    """-> liste af højder i pixel over bundlinjen, én pr. billedkolonne."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()

    cols, top = [], {}
    for x in range(w):
        first = None
        for y in range(h):
            if is_green(px[x, y]):
                first = y
                break
        if first is not None:
            top[x] = first
            cols.append(x)
    if len(cols) < 50:
        return None
    x0, x1 = min(cols), max(cols)

    # Bundlinjen er den laveste grønne pixel — den er fælles for hele fladen.
    base = 0
    for x in range(x0, x1 + 1, 3):
        for y in range(h - 1, -1, -1):
            if is_green(px[x, y]):
                base = max(base, y)
                break

    # Huller (lodrette hjælpelinjer tegnet hen over fladen) fyldes lineært.
    series = []
    last = None
    for x in range(x0, x1 + 1):
        if x in top:
            last = base - top[x]
        series.append(last if last is not None else 0)
    for i in range(len(series)):
        if series[i] is None:
            series[i] = 0

    # Let udjævning: fjerner JPEG-kant og tekst der stikker ned i fladen.
    k = SMOOTH
    sm = []
    for i in range(len(series)):
        a, b = max(0, i - k // 2), min(len(series), i + k // 2 + 1)
        sm.append(sum(series[a:b]) / (b - a))
    return sm


def resample(series, n):
    out = []
    for i in range(n):
        pos = i * (len(series) - 1) / (n - 1)
        lo = int(pos)
        hi = min(lo + 1, len(series) - 1)
        f = pos - lo
        out.append(series[lo] * (1 - f) + series[hi] * f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", required=True, help="fx vuelta2026")
    ap.add_argument("--route", help="rutefil (default: data/routes/<race>.json)")
    ap.add_argument("--out", help="default: web/data/<race>_stage_shapes.json")
    args = ap.parse_args()

    imgdir = ROOT / "data/profiles" / args.race
    manifest = imgdir / "manifest.json"
    if not manifest.exists():
        raise SystemExit(f"ingen profiler i {imgdir} — kør scripts/scrape/fetch_stage_profiles.py først")
    files = json.loads(manifest.read_text())["files"]

    route_path = Path(args.route) if args.route else ROOT / f"data/routes/{args.race}.json"
    route = json.loads(route_path.read_text())
    vm = {s["stage"]: s.get("vmeters") for s in route["stages"]}

    shapes, warn = {}, []
    for k, name in sorted(files.items(), key=lambda kv: int(kv[0])):
        stage = int(k)
        series = trace(imgdir / name)
        if series is None:
            warn.append(f"E{stage}: fandt ingen profilflade i {name}")
            continue
        pts = resample(series, N_POINTS)
        lo, hi = min(pts), max(pts)
        rng = (hi - lo) or 1.0
        shapes[str(stage)] = {
            # Formen, 0 = etapens laveste punkt, 1 = dens højeste.
            "shape": [round((p - lo) / rng, 3) for p in pts],
            "vmeters": vm.get(stage),
        }
        if vm.get(stage) is None:
            warn.append(f"E{stage}: ingen vmeters i rutefilen — tegnes i fuld højde")

    out = Path(args.out) if args.out else ROOT / f"web/data/{args.race}_stage_shapes.json"
    out.write_text(json.dumps({
        "race": args.race,
        "source": "Formen er aflæst af PCS-profilbillederne: øverste grønne pixel pr. billedkolonne, "
                  "let udjævnet og normaliseret til etapens eget højdespænd. Tegningens højde "
                  "skaleres efter etapens højdemeter i forhold til løbets hårdeste, så en flad "
                  "etape også ser flad ud. Ingen absolutte højdemeter påstås.",
        "n_points": N_POINTS,
        # Tegningens højde skaleres mod løbets hårdeste etape.
        "max_vmeters": max((v["vmeters"] or 0 for v in shapes.values()), default=0),
        "stages": shapes,
    }, ensure_ascii=False, separators=(",", ":")))

    print(f"Skrev {out.relative_to(ROOT)} — {len(shapes)} etaper, {out.stat().st_size//1024} kB")
    mx = max((v["vmeters"] or 0 for v in shapes.values()), default=1) or 1
    print(f"{'etape':>6} {'højdemeter':>11} {'højde i kassen':>15}  form")
    for k in sorted(shapes, key=int):
        v = shapes[k]
        frac = (v["vmeters"] or 0) / mx
        bar = "\u2586" * max(1, round(frac * 28))
        print(f"{'E'+k:>6} {v['vmeters'] or 0:>11} {frac*100:>14.0f}%  {bar}")
    for w in warn:
        print("  ! " + w)


if __name__ == "__main__":
    main()

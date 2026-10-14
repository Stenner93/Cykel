#!/usr/bin/env python3
"""
Klassikerprofilerne gjort klar til den mørke side.

For hvert PCS-profilbillede (La Flamme Rouge, grøn flade på hvid bund):

  1. Mørk udgave: den hvide bund fjernes, det grønne beholdes, og al tekst,
     ikoner og pavé-bjælker bliver lys tekstfarve med gennemsigtighed efter
     hvor mørke de var — så halvtoner (antialiasing, grå bjælker) bevares.
     -> web/data/klassiker/profiles/<år>/<slug>_dark.png

  2. Formen: øverste grønne pixel pr. kolonne, medianfiltreret (ikoner og
     JPEG-støj giver enkeltdyk), normaliseret 0-1. Som Vuelta-planlæggerens
     trace_stage_profiles.py.

  3. Markørerne: hver stigning, pavé og by har en tynd lodret streg ned
     gennem den grønne flade. Den er mørkere end naboerne og kan findes
     uden OCR. Stregens x-position giver km; skalaen er den sorte km-bjælke
     under profilen, som går præcis fra start til mål.
     Navnene står kun som tekst i billedet og aflæses derfor i hånden —
     se data/klassiker/route_marks_<år>.json.

Output: data/klassiker/profile_trace_<år>.json

Brug:
    python scripts/analysis/trace_classics_profiles.py            # config'ens data_year
    python scripts/analysis/trace_classics_profiles.py --year 2027
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/klassiker/config.json"
TEXT = (0xF2, 0xEE, 0xDF)
N_POINTS = 180


def is_green(p):
    r, g, b = p[:3]
    return g > 80 and g > r + 20 and g > b + 25


def lum(p):
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def dark_version(im):
    w, h = im.size
    out = Image.new("RGBA", (w, h))
    pi, po = im.load(), out.load()
    for y in range(h):
        for x in range(w):
            p = pi[x, y]
            if is_green(p):
                po[x, y] = (*p, 255)
            else:
                a = max(0.0, min(1.0, (250 - lum(p)) / 200))     # hvid -> 0, sort -> 1
                po[x, y] = (*TEXT, int(a * 255))
    return out


def baseline(im):
    """Øverste række i den sorte km-bjælke under profilen."""
    w, h = im.size
    px = im.load()
    dark = [sum(1 for x in range(0, w, 2) if lum(px[x, y]) < 60) / (w / 2) for y in range(h)]
    for y in range(h - 1, h // 2, -1):
        if dark[y] > 0.6 and dark[y - 1] <= 0.6:
            return y
    raise ValueError("fandt ingen km-bjælke")


def trace(im):
    w, h = im.size
    px = im.load()
    base = baseline(im)
    # Grus- og brostensstrækninger er tegnet med gråt i selve fladen, så
    # både grønt og fladens grå tæller som profil — men kun hvor der er fyld
    # hele vejen ned (ellers ville tekst og streger over profilen tælle).
    def fill(p):
        L = lum(p)
        # grøn, grå, eller en blanding af de to i kanten af en grå bjælke
        return is_green(p) or (max(p) - min(p) < 70 and 95 < L < 205)
    # En label-streg krydser fladen som en mørk lodret linje; den tæller som
    # fyld, når der er fyld på begge sider af den.
    def fill_at(x, y):
        p = px[x, y]
        if fill(p):
            return True
        return lum(p) < 125 and 2 <= x < w - 2 and fill(px[x - 2, y]) and fill(px[x + 2, y])
    top = {}
    for x in range(w):
        t = None
        for y in range(base - 1, 0, -1):
            if fill_at(x, y) or (y > 2 and fill_at(x, y - 1) and fill_at(x, y - 2)):
                t = y
            else:
                break
        top[x] = t
    xs = [x for x in range(w) if top[x] is not None and base - top[x] > 2 and any(is_green(px[x, y]) for y in range(top[x], base))]
    # Km-skalaen: den sorte bjælke under profilen går præcis fra start til mål.
    bar = [x for x in range(w) if lum(px[x, base + 3]) < 70]
    x0, x1 = (bar[0], bar[-1]) if len(bar) > w / 2 else (xs[0], xs[-1])
    heights = [base - (top[x] if top[x] is not None else base) for x in range(x0, x1 + 1)]
    n = len(heights)
    E = 6                                                                    # kanterne er støj (start-/målflag)
    heights = [heights[min(max(i, E), n - 1 - E)] for i in range(n)]
    med = [sorted(heights[max(0, i - 3):i + 4])[len(heights[max(0, i - 3):i + 4]) // 2] for i in range(n)]
    mx = max(med) or 1
    shape = []
    for i in range(N_POINTS):
        a = int(i * n / N_POINTS)
        b = max(a + 1, int((i + 1) * n / N_POINTS))
        shape.append(round(sum(med[a:b]) / (b - a) / mx, 3))

    # Lodrette label-streger gennem fladen: tydeligt mørkere end ±2 px.
    cand = []
    for x in range(x0 + 2, x1 - 1):
        hits = sum(1 for y in range(base - 16, base - 3)
                   if (lum(px[x - 2, y]) + lum(px[x + 2, y])) / 2 - lum(px[x, y]) > 25)
        if hits >= 9:
            cand.append(x)
    groups = []
    for x in cand:
        if groups and x - groups[-1][-1] <= 1:
            groups[-1].append(x)
        else:
            groups.append([x])
    lines = [((g[0] + g[-1]) / 2 - x0) / (x1 - x0) for g in groups]           # brøkdel af distancen
    return {"shape": shape, "lines_frac": [round(v, 4) for v in lines], "px_width": x1 - x0}


def main():
    cfg = json.loads(CONFIG.read_text())
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=cfg["data_year"])
    args = ap.parse_args()
    pdir = ROOT / "web/data/klassiker/profiles" / str(args.year)
    files = json.loads((pdir / "manifest.json").read_text())["files"]
    res = json.loads((ROOT / "data/sources/classics/results.json").read_text())
    dist = {r["race_slug"]: r.get("distance_km") for r in res["races"] if r["year"] == args.year}

    out = {}
    for r in cfg["races"]:
        f = files.get(r["slug"])
        if not f:
            continue
        im = Image.open(pdir / f).convert("RGB")
        dark_version(im).save(pdir / f"{r['slug']}_dark.png", optimize=True)
        t = trace(im)
        d = dist.get(r["slug"])
        t["distance_km"] = d
        t["lines_km"] = [round(v * d, 1) for v in t["lines_frac"]] if d else None
        out[r["slug"]] = t
        print(f"  {r['short']:9} {len(t['lines_frac']):>2} markører  {d} km")
    dst = ROOT / f"data/klassiker/profile_trace_{args.year}.json"
    dst.write_text(json.dumps({"year": args.year, "races": out}, ensure_ascii=False, indent=1))
    print(f"Skrev {dst.relative_to(ROOT)} og *_dark.png")


if __name__ == "__main__":
    main()

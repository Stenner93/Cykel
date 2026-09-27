#!/usr/bin/env python3
"""
Henter procyclingstats' etapeprofilbilleder ned til siden.

Ruteplanlæggeren tegner selv en skematisk profil af de kategoriserede
stigninger, men den rigtige højdeprofil siger mere: hvor stejlt, hvor langt
fra mål, hvor mange små knæk der ikke er kategoriserede. Ligger billederne i
web/img/profiles/<race>/, bruger planlæggeren dem i stedet for skitsen.

NB: PCS kan ikke nås hverken fra Claude-sandkassen eller fra GitHub Actions
(begge afvises), så dette script skal køres fra din egen maskine:

    python scripts/scrape/fetch_stage_profiles.py \
        --slug vuelta-a-espana/2026 --race vuelta2026 --stages 21

Derefter commit web/img/profiles/<race>/ — det er ~20 små billeder.
Kan et billede ikke hentes, springes etapen bare over og planlæggeren falder
tilbage på skitsen for netop den.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("kræver requests: pip install requests")

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://www.procyclingstats.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; cykel-manager/1.0)"}

# PCS lægger profilbilledet i en <img> under /race/.../stage-N; filnavnet
# varierer, så vi tager det første billede der ligger i profiles-mappen.
IMG_RE = re.compile(r'<img[^>]+src="([^"]*(?:profiles|stageprofile)[^"]*\.(?:jpg|jpeg|png))"', re.I)


def fetch_stage(sess, slug, stage, outdir):
    url = f"{BASE}/race/{slug}/stage-{stage}"
    r = sess.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    m = IMG_RE.search(r.text)
    if not m:
        return None, "intet profilbillede på siden"
    src = m.group(1)
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = BASE + src
    elif not src.startswith("http"):
        src = f"{BASE}/{src.lstrip('/')}"
    img = sess.get(src, headers=HEADERS, timeout=20)
    if img.status_code != 200:
        return None, f"billede HTTP {img.status_code}"
    ext = ".png" if src.lower().endswith(".png") else ".jpg"
    dest = outdir / f"stage-{stage:02d}{ext}"
    dest.write_bytes(img.content)
    return dest.name, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="fx vuelta-a-espana/2026")
    ap.add_argument("--race", required=True, help="fx vuelta2026 — mappenavnet under web/img/profiles")
    ap.add_argument("--stages", type=int, required=True)
    ap.add_argument("--delay", type=float, default=1.5, help="sekunder mellem kald (vær pæn)")
    args = ap.parse_args()

    outdir = ROOT / "web/img/profiles" / args.race
    outdir.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()

    got, failed = {}, []
    for st in range(1, args.stages + 1):
        name, err = fetch_stage(sess, args.slug, st, outdir)
        if name:
            got[st] = name
            print(f"  E{st:>2}  {name}")
        else:
            failed.append(st)
            print(f"  E{st:>2}  — {err}")
        time.sleep(args.delay)

    (outdir / "manifest.json").write_text(json.dumps(
        {"race": args.race, "slug": args.slug, "files": got}, ensure_ascii=False, indent=2))
    print(f"\n{len(got)}/{args.stages} profiler hentet til web/img/profiles/{args.race}/")
    if failed:
        print(f"Manglede: {', '.join('E'+str(s) for s in failed)} — planlæggeren tegner skitsen for dem.")


if __name__ == "__main__":
    main()

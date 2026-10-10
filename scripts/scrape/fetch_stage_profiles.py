#!/usr/bin/env python3
"""
Henter procyclingstats' etapeprofilbilleder ned til siden.

Ruteplanlæggeren kan selv tegne en skematisk profil af de kategoriserede
stigninger, men den rigtige højdeprofil siger mere: hvor stejlt, hvor langt
fra mål, og alle de småknæk der aldrig bliver kategoriseret. Ligger
billederne i data/profiles/<race>/, bruger planlæggeren dem automatisk.

Først prøves oversigtssiden /race/<slug>/route/stage-profiles, som ofte har
alle profiler på ét opslag. For et afsluttet løb viser PCS dog resultater
dér i stedet, og så hentes de manglende profiler fra de enkelte etapesider.
Allerede hentede profiler bevares altid.

Browseropsætningen (Cloudflare, proxy-CA) bor i pcs_browser.py. Kør pænt:
det er ét sideopslag plus ~21 billeder, én gang pr. løb.

Brug:
    python scripts/scrape/fetch_stage_profiles.py \
        --slug vuelta-a-espana/2026 --race vuelta2026 --stages 21

Derefter committes data/profiles/<race>/.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcs_browser import BASE, pcs_page  # noqa: E402  — fælles PCS-browser

ROOT = Path(__file__).resolve().parents[2]
STAGE_IN_NAME = re.compile(r"-stage-(\d+)-")


def capture(page, url, timeout, wanted=None):
    """Indlæs en side og grib de profilbilleder SIDEN selv henter.

    Et separat kald bagefter bliver afvist af Cloudflare, fordi det mangler
    browserens egne headere — men netop derfor har vi originalbytes her.
    -> {etape: (url, bytes)}"""
    grabbed = {}

    def on_response(resp):
        u = resp.url
        if "/images/profiles/" not in u or not resp.ok:
            return
        m = STAGE_IN_NAME.search(u)
        if not m:
            return
        st = int(m.group(1))
        if wanted is not None and st != wanted:
            return
        try:
            grabbed.setdefault(st, (u, resp.body()))
        except Exception:
            pass

    page.on("response", on_response)
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if not resp or resp.status != 200:
            return {}
        page.wait_for_timeout(3500)
        # Billederne indlæses dovent, så siden rulles i trin til bunden.
        for _ in range(8):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(500)
        page.wait_for_timeout(1500)
    finally:
        page.remove_listener("response", on_response)
    return grabbed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="fx vuelta-a-espana/2026")
    ap.add_argument("--race", required=True, help="fx vuelta2026 — mappen under data/profiles")
    ap.add_argument("--stages", type=int, help="antal etaper (til opsamling på etapesiderne)")
    ap.add_argument("--timeout", type=int, default=60000)
    # Nogle miljøer har Chromium liggende uden for Playwrights egen mappe.
    ap.add_argument("--chromium", default=None,
                    help="sti til Chromium (default: PLAYWRIGHT_CHROMIUM eller /opt/pw-browsers/chromium)")
    args = ap.parse_args()

    outdir = ROOT / "data/profiles" / args.race
    outdir.mkdir(parents=True, exist_ok=True)

    # Det der allerede er hentet, bevares. En kørsel der finder færre
    # billeder end sidst, må aldrig skrumpe manifestet — det skete én gang,
    # da PCS ændrede oversigtssiden, og 20 af 21 profiler forsvandt.
    mf = outdir / "manifest.json"
    have = {}
    if mf.exists():
        have = {int(k): v for k, v in json.loads(mf.read_text()).get("files", {}).items()
                if (outdir / v).exists()}

    with pcs_page(args.chromium) as (page, ctx):
        # 1) Oversigtssiden: alle profiler på ét opslag, når PCS viser dem dér.
        grabbed = capture(page, f"{BASE}/race/{args.slug}/route/stage-profiles", args.timeout)
        print(f"Oversigtssiden gav {len(grabbed)} profiler")

        # 2) Resten hentes på de enkelte etapesider. PCS har ladet oversigten
        #    vise resultater i stedet for profiler for et afsluttet løb.
        n = args.stages or (max(grabbed) if grabbed else 0)
        for st in range(1, n + 1):
            if st in grabbed:
                continue
            got1 = capture(page, f"{BASE}/race/{args.slug}/stage-{st}", args.timeout, wanted=st)
            grabbed.update(got1)
            print(f"  E{st:>2} via etapesiden: {'ok' if st in got1 else '—'}")

    for st in sorted(grabbed):
        src, data = grabbed[st]
        ext = ".png" if src.lower().split("?")[0].endswith(".png") else ".jpg"
        name = f"stage-{st:02d}{ext}"
        (outdir / name).write_bytes(data)
        have[st] = name
        print(f"  E{st:>2}  {name}  {len(data)//1024} kB")

    if not have:
        sys.exit("ingen profilbilleder — er løbets slug rigtig?")
    mf.write_text(json.dumps(
        {"race": args.race, "slug": args.slug,
         "source": f"{BASE}/race/{args.slug}",
         "files": {str(k): v for k, v in sorted(have.items())}},
        ensure_ascii=False, indent=2))
    print(f"\n{len(have)} profiler i data/profiles/{args.race}/ ({len(grabbed)} hentet i denne kørsel)")
    if args.stages:
        missing = sorted(set(range(1, args.stages + 1)) - set(have))
        if missing:
            print("Mangler stadig: " + ", ".join(f"E{s}" for s in missing)
                  + " — planlæggeren tegner skitsen for dem.")


if __name__ == "__main__":
    main()

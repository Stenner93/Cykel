#!/usr/bin/env python3
"""
Henter procyclingstats' profilbilleder for Klassiker Manager-løbene.

Et endagsløb har ét profilbillede, og det ligger på løbets oversigtsside
(/race/<slug>/<år>). Billedet gribes fra sidens egne svar, fordi Cloudflare
afviser et separat kald (samme greb som fetch_stage_profiles.py).

Løbene læses fra data/klassiker/config.json. Billederne lægges under web/,
så siden kan vise dem:
    web/data/klassiker/profiles/<år>/<slug>.jpg  +  manifest.json

Allerede hentede profiler bevares; en kørsel kan kun tilføje.

Brug:
    python scripts/scrape/fetch_classics_profiles.py              # config'ens data_year
    python scripts/scrape/fetch_classics_profiles.py --year 2027  # når ruterne er ude
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcs_browser import BASE, pcs_page  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/klassiker/config.json"


def grab(page, url, timeout=60000):
    got = {}

    def on_response(resp):
        if "/images/profiles/" in resp.url and resp.ok and not got:
            try:
                got["url"], got["body"] = resp.url, resp.body()
            except Exception:
                pass

    page.on("response", on_response)
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        if not resp or resp.status != 200:
            return None
        page.wait_for_timeout(3000)
        for _ in range(4):
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(400)
    finally:
        page.remove_listener("response", on_response)
    return got or None


def main():
    cfg = json.loads(CONFIG.read_text())
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=cfg["data_year"])
    args = ap.parse_args()

    outdir = ROOT / "web/data/klassiker/profiles" / str(args.year)
    outdir.mkdir(parents=True, exist_ok=True)
    mf = outdir / "manifest.json"
    have = json.loads(mf.read_text())["files"] if mf.exists() else {}
    have = {k: v for k, v in have.items() if (outdir / v).exists()}

    with pcs_page() as (page, ctx):
        for r in cfg["races"]:
            slug = r["slug"]
            if slug in have:
                continue
            g = grab(page, f"{BASE}/race/{slug}/{args.year}")
            if not g:
                g = grab(page, f"{BASE}/race/{slug}/{args.year}/route")
            if not g:
                print(f"  — {r['short']}: ingen profil endnu")
                continue
            ext = ".png" if g["url"].lower().split("?")[0].endswith(".png") else ".jpg"
            (outdir / f"{slug}{ext}").write_bytes(g["body"])
            have[slug] = f"{slug}{ext}"
            print(f"  ✓ {r['short']:9} {len(g['body']) // 1024} kB")

    mf.write_text(json.dumps({"year": args.year, "source": BASE,
                              "files": dict(sorted(have.items()))}, ensure_ascii=False, indent=1))
    print(f"\n{len(have)}/{len(cfg['races'])} profiler i {outdir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

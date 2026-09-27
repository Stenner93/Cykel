#!/usr/bin/env python3
"""
Henter procyclingstats' etapeprofilbilleder ned til siden.

Ruteplanlæggeren kan selv tegne en skematisk profil af de kategoriserede
stigninger, men den rigtige højdeprofil siger mere: hvor stejlt, hvor langt
fra mål, og alle de småknæk der aldrig bliver kategoriseret. Ligger
billederne i data/profiles/<race>/, bruger planlæggeren dem automatisk.

Alle profilerne til et løb ligger på ÉN side — /race/<slug>/route/stage-profiles
— så der hentes én side og derefter billederne. Ingen grund til at kalde 21
etapesider.

PCS ligger bag Cloudflare, som afviser almindelige HTTP-klienter med en
udfordring ("Just a moment..."). Derfor køres det gennem Playwrights
Chromium, som er en rigtig browser og kommer igennem som enhver anden
besøgende. Kør pænt: det er ét sideopslag plus ~21 billeder, én gang pr. løb.

Brug:
    python scripts/scrape/fetch_stage_profiles.py \
        --slug vuelta-a-espana/2026 --race vuelta2026

Derefter committes data/profiles/<race>/.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("kræver playwright: pip install playwright && playwright install chromium")

ROOT = Path(__file__).resolve().parents[2]
BASE = "https://www.procyclingstats.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
STAGE_IN_NAME = re.compile(r"-stage-(\d+)-")

# Nogle kørselsmiljøer (bl.a. Claudes sandkasse) sender HTTPS gennem en proxy
# der terminerer TLS med sit eget CA. curl og python læser den fra CA-bundlet,
# men Chromiums egen certifikatverifikation gør ikke. Derfor udpeges netop de
# CA'er ved deres offentlige nøgle, så browseren stoler på dem og kun dem.
# Det er ikke det samme som at slå certifikatkontrol fra.
CA_BUNDLES = ["/root/.ccr/ca-bundle.crt"]
INTERCEPTION_CA = re.compile(r"proxy|egress|inspection|intercept", re.I)


def proxy_ca_pins() -> list[str]:
    pins = []
    for path in CA_BUNDLES:
        p = Path(path)
        if not p.exists():
            continue
        for pem in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                              p.read_text(), re.S):
            subj = subprocess.run(["openssl", "x509", "-noout", "-subject"],
                                  input=pem, capture_output=True, text=True).stdout
            if not INTERCEPTION_CA.search(subj):
                continue
            spki = subprocess.run(
                "openssl x509 -pubkey -noout | openssl pkey -pubin -outform der "
                "| openssl dgst -sha256 -binary | openssl enc -base64",
                input=pem, shell=True, capture_output=True, text=True).stdout.strip()
            if spki and spki not in pins:
                pins.append(spki)
    return pins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="fx vuelta-a-espana/2026")
    ap.add_argument("--race", required=True, help="fx vuelta2026 — mappen under web/img/profiles")
    ap.add_argument("--timeout", type=int, default=60000)
    # Nogle miljøer har Chromium liggende uden for Playwrights egen mappe.
    ap.add_argument("--chromium", default=None,
                    help="sti til Chromium (default: PLAYWRIGHT_CHROMIUM eller /opt/pw-browsers/chromium)")
    args = ap.parse_args()

    outdir = ROOT / "data/profiles" / args.race
    outdir.mkdir(parents=True, exist_ok=True)

    launch_args = ["--disable-blink-features=AutomationControlled"]
    pins = proxy_ca_pins()
    if pins:
        launch_args.append("--ignore-certificate-errors-spki-list=" + ",".join(pins))
        print(f"Stoler på {len(pins)} proxy-CA'er ved deres offentlige nøgle")

    exe = args.chromium or os.environ.get("PLAYWRIGHT_CHROMIUM") or "/opt/pw-browsers/chromium"
    launch = {"args": launch_args}
    if Path(exe).exists():
        launch["executable_path"] = exe

    url = f"{BASE}/race/{args.slug}/route/stage-profiles"
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        ctx = browser.new_context(locale="en-US", user_agent=UA,
                                  viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()

        # Billederne gribes mens SIDEN selv henter dem. Et separat kald bagefter
        # bliver afvist af Cloudflare, fordi det mangler browserens egne headere
        # — men netop derfor har vi allerede originalbytes her.
        grabbed = {}

        def on_response(resp):
            u = resp.url
            if "/images/profiles/" not in u:
                return
            m = STAGE_IN_NAME.search(u)
            if not m or not resp.ok:
                return
            try:
                grabbed[int(m.group(1))] = (u, resp.body())
            except Exception:
                pass

        page.on("response", on_response)

        resp = page.goto(url, wait_until="domcontentloaded", timeout=args.timeout)
        if not resp or resp.status != 200:
            browser.close()
            sys.exit(f"{url} svarede {resp.status if resp else 'intet'}")
        page.wait_for_timeout(5000)
        # Billederne indlæses dovent, så siden rulles i trin til bunden.
        for _ in range(12):
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(700)
        page.wait_for_timeout(3000)

        got = {}
        for stage in sorted(grabbed):
            src, data = grabbed[stage]
            ext = ".png" if src.lower().split("?")[0].endswith(".png") else ".jpg"
            name = f"stage-{stage:02d}{ext}"
            (outdir / name).write_bytes(data)
            got[stage] = name
            print(f"  E{stage:>2}  {name}  {len(data)//1024} kB")
        if not got:
            browser.close()
            sys.exit("ingen profilbilleder blev hentet — er løbets slug rigtig?")
        browser.close()

    (outdir / "manifest.json").write_text(json.dumps(
        {"race": args.race, "slug": args.slug, "source": url,
         "files": {str(k): v for k, v in sorted(got.items())}},
        ensure_ascii=False, indent=2))
    print(f"\n{len(got)} profiler i data/profiles/{args.race}/")
    missing = sorted(set(range(1, max(got) + 1)) - set(got)) if got else []
    if missing:
        print("Manglede: " + ", ".join(f"E{s}" for s in missing)
              + " — planlæggeren tegner skitsen for dem.")


if __name__ == "__main__":
    main()

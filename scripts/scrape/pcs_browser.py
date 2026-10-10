#!/usr/bin/env python3
"""
Fælles browseropsætning til procyclingstats.

PCS ligger bag Cloudflare, som afviser almindelige HTTP-klienter med en
udfordring ("Just a moment..."). En rigtig browser kommer igennem som enhver
anden besøgende, så alle PCS-scrapere går gennem Playwrights Chromium.

Nogle kørselsmiljøer (bl.a. Claudes sandkasse) sender HTTPS gennem en proxy
der terminerer TLS med sit eget CA. curl og python læser den fra CA-bundlet,
men Chromiums egen certifikatverifikation gør ikke. Derfor udpeges netop de
CA'er ved deres offentlige nøgle, så browseren stoler på dem og kun dem.
Det er ikke det samme som at slå certifikatkontrol fra.

Brug:
    from pcs_browser import pcs_page
    with pcs_page() as (page, ctx):
        page.goto("https://www.procyclingstats.com/race/...")
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("kræver playwright: pip install playwright && playwright install chromium")

BASE = "https://www.procyclingstats.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

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


def launch_kwargs(chromium: str | None = None) -> dict:
    args = ["--disable-blink-features=AutomationControlled"]
    pins = proxy_ca_pins()
    if pins:
        args.append("--ignore-certificate-errors-spki-list=" + ",".join(pins))
    kw = {"args": args}
    # Nogle miljøer har Chromium liggende uden for Playwrights egen mappe.
    exe = chromium or os.environ.get("PLAYWRIGHT_CHROMIUM") or "/opt/pw-browsers/chromium"
    if Path(exe).exists():
        kw["executable_path"] = exe
    return kw


@contextmanager
def pcs_page(chromium: str | None = None, viewport=(1400, 1000)):
    """-> (page, context). Lukker browseren når blokken forlades."""
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs(chromium))
        ctx = browser.new_context(locale="en-US", user_agent=UA,
                                  viewport={"width": viewport[0], "height": viewport[1]})
        try:
            yield ctx.new_page(), ctx
        finally:
            browser.close()

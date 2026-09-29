#!/usr/bin/env python3
"""
Bygger en selvstændig HTML-fil med udkast til den udvidede etapeboks.

Boksen er den der folder sig ud under oversigten når man klikker en etape.
Den nuværende har alt på én flex-række — profil, tal, dropdown og knap i
vidt forskellige højder på samme midterlinje — og det er dét der får
elementerne til at virke forskudt.

Brug:
    python scripts/analysis/build_stagebox_drafts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_payload import render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TMPL = Path(__file__).resolve().parent / "stagebox_drafts.tmpl.html"
OUT = ROOT / "web/etapeboks-udkast.html"


def main():
    data = render(TMPL, OUT)
    print(f"Skrev {OUT.relative_to(ROOT)} — {len(data['stages'])} etaper, "
          f"{OUT.stat().st_size // 1024} kB")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Bygger en selvstændig HTML-fil med layout-udkast til ruteplanlæggeren.

Filen skal kunne åbnes hvor som helst — også sendt direkte i en samtale —
så etapedataene lægges ind i selve filen i stedet for at blive hentet.
Skabelonen ligger i scripts/analysis/layout_drafts.tmpl.html og har ét
pladsholder-mærke, __STAGES__, hvor JSON'en skydes ind.

Brug:
    python scripts/analysis/build_layout_drafts.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage_payload import render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TMPL = Path(__file__).resolve().parent / "layout_drafts.tmpl.html"
OUT = ROOT / "web/planlaegger-layouts.html"


def main():
    data = render(TMPL, OUT)
    print(f"Skrev {OUT.relative_to(ROOT)} — {len(data['stages'])} etaper, "
          f"{OUT.stat().st_size // 1024} kB")


if __name__ == "__main__":
    main()

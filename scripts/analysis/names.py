#!/usr/bin/env python3
"""
Navnematchning på tværs af datakilder.

Holdet, rytterdatabasen og procyclingstats staver navne forskelligt ("Enric
Mas Nicolau" vs. "Enric Mas", "Gregor Muhlberger" vs. "Gregor Mühlberger").
Et exact string-join taber derfor ryttere — og det gør det lydløst, hvilket
er præcis sådan feltets største vækster engang forsvandt fra samtlige 13 hold
på én gang uden at nogen opdagede det.

Derfor bor matchningen ét sted, og et opslag der ikke lykkes skal støje.
"""
from __future__ import annotations

import unicodedata


def norm(s: str) -> str:
    """Navnet skrællet for accenter, tegnsætning og versaler."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("å", "aa")
    return " ".join("".join(c for c in s.lower() if c.isalnum() or c == " ").split())


def resolve(ref_name: str, candidates_norm: dict[str, str]) -> str | None:
    """Slå et navn op i {normaliseret navn: kanonisk navn}.

    Først exact på normaliseret tekst, så hvor det ene navn er et
    token-præfiks af det andet ("Enric Mas" ⊂ "Enric Mas Nicolau"), til sidst
    på fornavn+efternavn. Bevidst INGEN efternavn-alene-udvej: "Lucas
    Hamilton" må ikke kollapse på "Chris Hamilton".
    """
    n = norm(ref_name)
    if n in candidates_norm:
        return candidates_norm[n]
    toks = n.split()
    for cand_n, cand in candidates_norm.items():
        ct = cand_n.split()
        if ct[: len(toks)] == toks or toks[: len(ct)] == ct:
            return cand
    if len(toks) >= 2:
        key = (toks[0], toks[-1])
        for cand_n, cand in candidates_norm.items():
            ct = cand_n.split()
            if len(ct) >= 2 and (ct[0], ct[-1]) == key:
                return cand
    return None


def index(names) -> dict[str, str]:
    """{normaliseret navn: kanonisk navn} til brug i resolve()."""
    return {norm(n): n for n in names}

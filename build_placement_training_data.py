"""
build_placement_training_data.py

Bygger ML-træningsdata med NORMALISERET ETAPEPLACING som target.

  norm_pos = 1.0 - (position - 1) / (field_size - 1)
    1.0  = vinderen
    0.0  = sidst placerede (ikke DNF)
    DNF-ryttere udelades fra normalisering (de har ikke en gyldig placering)

Fordele over holdet_pts_norm:
  - 10× mere træningsdata (GT + 7 et-ugersløb × 5 år ≈ 90.000 rækker)
  - Target er fysisk (reel placering), ikke støjfyldt holdet-point-mix
  - xrace_form_10 virker korrekt: lav gennemsnitlig plac. → høj norm_pos

Kilder:
  data/ml/historical_results.json    — PCS stage-resultater (alle løb)
  data/cache/cyclingoracle.json      — CO disciplin-ratings
  data/cache/pcs_form.json           — PCS specialties + form

Output:
  data/ml/placement_training_data.csv
  data/ml/placement_training_data_meta.json

Kørsel:
    python build_placement_training_data.py
"""
from __future__ import annotations
import csv
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT    = Path(__file__).parent
ML_DIR  = ROOT / "data" / "ml"
CACHE   = ROOT / "data" / "cache"

OUT_CSV  = ML_DIR / "placement_training_data.csv"
OUT_META = ML_DIR / "placement_training_data_meta.json"

CO_KEYS   = ["mtn", "spr", "hll", "itt", "cob", "gc"]
SPEC_KEYS = ["climber", "sprint", "tt", "hills"]

# Kronologisk rækkefølge inden for et år — bruges til korrekt xrace_form-beregning
RACE_ORDER: dict[str, int] = {
    "pn":        1,    # Paris-Nice (marts)
    "tirreno":   2,    # Tirreno-Adriatico (marts)
    "catalunya": 3,    # Volta a Catalunya (sen marts)
    "basque":    4,    # Itzulia Basque Country (april)
    "romandie":  5,    # Tour de Romandie (april/maj)
    "giro":      6,    # Giro d'Italia (maj-juni)
    "dauphine":  7,    # Critérium du Dauphiné (juni)
    "suisse":    8,    # Tour de Suisse (juni)
    "tdf":       9,    # Tour de France (juli)
    "vuelta":   10,    # Vuelta a España (august)
}

# TTT-etaper udelades — holdet-scoring er team-baseret for TTT, ikke individuel
SKIP_TYPES = {"ttt"}


def _slug_to_id(slug: str) -> str:
    return slug.replace("-", "_")


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _lookup(rid: str, data: dict) -> dict:
    """Opslag med prefix-fallback og accent-normalisering.

    Håndterer fx 'carlos_rodriguez_cano' → 'carlos_rodriguez' og
    'juan_pedro_lopez_perez' → 'juan_pedro_lopez' når CO/PCS bruger
    kortere holdet-ID end det fulde PCS-slugnavn.
    """
    if rid in data:
        return data[rid]
    # Accent-normalisering (juan_pedro_lópez → juan_pedro_lopez)
    norm = _strip_accents(rid)
    if norm != rid and norm in data:
        return data[norm]
    # Prefix-truncation: fjern efternavn(e) bagfra
    parts = rid.split("_")
    for n in range(len(parts) - 1, 1, -1):
        prefix = "_".join(parts[:n])
        if prefix in data:
            return data[prefix]
        norm_prefix = _strip_accents(prefix)
        if norm_prefix != prefix and norm_prefix in data:
            return data[norm_prefix]
    return {}


def load_co() -> dict[str, dict]:
    path = CACHE / "cyclingoracle.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {rid: {k.lower(): v for k, v in d.get("ratings", {}).items()} for rid, d in raw.items()}


def load_specs() -> dict[str, dict]:
    """
    Load PCS specialty scores keyed by both holdet rider_id AND rider slug.

    Sources (merged, current roster takes precedence):
      1. pcs_form.json        — current race roster (keyed by holdet rider_id)
      2. pcs_historical_specialties.json — historical riders (keyed by PCS slug)
    """
    out: dict[str, dict] = {}

    # Historical specialties by slug (lower priority)
    hist_path = CACHE / "pcs_historical_specialties.json"
    if hist_path.exists():
        hist_raw = json.loads(hist_path.read_text(encoding="utf-8"))
        for slug, specs in hist_raw.items():
            if specs:
                out[slug] = {k.lower(): v for k, v in specs.items()}

    # Current roster (higher priority — overwrites historical where overlap)
    form_path = CACHE / "pcs_form.json"
    if form_path.exists():
        raw = json.loads(form_path.read_text(encoding="utf-8"))
        for rid, d in raw.items():
            specs = d.get("pcs_specialties") or {}
            if specs:
                out[rid] = {k.lower(): v for k, v in specs.items()}
            # Also index by PCS slug so slug-based lookups work
            url = d.get("pcs_url", "")
            if specs and "/rider/" in url:
                slug = url.split("/rider/")[-1].strip("/")
                out[slug] = {k.lower(): v for k, v in specs.items()}

    return out


def load_pcs_form() -> dict[str, dict]:
    path = CACHE / "pcs_form.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for rid, d in raw.items():
        fbt = d.get("form_by_type") or {}
        if fbt:
            out[rid] = fbt
    return out


def load_slug_to_rid(pcs_form_path: Path) -> dict[str, str]:
    if not pcs_form_path.exists():
        return {}
    raw = json.loads(pcs_form_path.read_text(encoding="utf-8"))
    mapping = {}
    for rid, d in raw.items():
        url = d.get("pcs_url", "")
        if "/rider/" in url:
            slug = url.split("/rider/")[-1].strip("/")
            mapping[slug] = rid
    return mapping


def rolling_avg(positions: list[int], n: int) -> float | None:
    last = positions[-n:]
    return round(sum(last) / len(last), 1) if last else None  # None → NaN i CSV → LightGBM missing


def rolling_top_n_rate(positions: list[int], n: int, top_n: int) -> float | None:
    last = positions[-n:]
    return round(sum(1 for p in last if p <= top_n) / len(last), 4) if last else None


def main() -> None:
    print("Indlæser data…")
    pcs_path    = ML_DIR / "historical_results.json"
    if not pcs_path.exists():
        print(f"Fejl: {pcs_path} ikke fundet.")
        print("Kør først: python scrape_pcs_history.py")
        return

    pcs_records = json.loads(pcs_path.read_text(encoding="utf-8"))
    co_data     = load_co()
    spec_data   = load_specs()
    pcs_form    = load_pcs_form()
    slug_to_rid = load_slug_to_rid(CACHE / "pcs_form.json")

    print(f"  PCS resultater:   {len(pcs_records):,}")
    print(f"  CO ratings:       {len(co_data)} ryttere")
    print(f"  PCS specialties:  {len(spec_data)} ryttere")
    print(f"  Slug→ID mapping:  {len(slug_to_rid)} ryttere")

    # Sorter kronologisk: (år, løb-rækkefølge, etape)
    pcs_records_sorted = sorted(
        pcs_records,
        key=lambda r: (r["year"], RACE_ORDER.get(r["race"], 99), r["stage"])
    )

    # Grupper PCS resultater pr. (race, year, stage)
    stage_index: dict[tuple, list[dict]] = defaultdict(list)
    for r in pcs_records_sorted:
        stage_index[(r["race"], r["year"], r["stage"])].append(r)

    # Byg per-rytter kronologisk historik til xrace_form — separat pr. etapetype
    # {slug: {stage_type: [positions]}} — forhindrer at bjergestage-placeringer
    # forurener sprintform og omvendt (Magnus Cort nr. 153 på en bjergetape)
    rider_xrace_history: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    rows: list[dict] = []
    n_stages        = 0
    n_skipped_type  = 0
    n_co_hits       = 0
    n_spec_hits     = 0
    n_no_rid        = 0

    # Gruppér etaper og kør i kronologisk rækkefølge
    # For hvert løb-år-etape: beregn norm_pos for alle ikke-DNF ryttere
    seen_stage_keys: set[tuple] = set()
    for r in pcs_records_sorted:
        key = (r["race"], r["year"], r["stage"])
        if key in seen_stage_keys:
            continue
        seen_stage_keys.add(key)

        race, year, snum = r["race"], r["year"], r["stage"]
        stype = r.get("stage_type", "hilly")

        if stype in SKIP_TYPES:
            n_skipped_type += 1
            continue

        stage_results = stage_index[key]

        # Klassifikation: kun ryttere med gyldig numerisk position (ikke DNF)
        finishers = [
            res for res in stage_results
            if not res.get("dnf") and isinstance(res.get("position"), int) and res["position"] < 900
        ]
        field_size = len(finishers)
        if field_size < 5:
            continue  # for få ryttere til meningsfuld normalisering

        n_stages += 1

        # Beregn in-race historik for alle ryttere (etaper 1..snum-1 i DETTE løb)
        in_race_hist: dict[str, list[int]] = defaultdict(list)
        for prev_s in range(1, snum):
            prev_key = (race, year, prev_s)
            for prev_r in stage_index.get(prev_key, []):
                if not prev_r.get("dnf") and isinstance(prev_r.get("position"), int):
                    in_race_hist[prev_r["rider_slug"]].append(prev_r["position"])

        # Opbyg rækker for denne etape
        for res in finishers:
            slug    = res["rider_slug"]
            pos     = res["position"]
            norm_pos = round(1.0 - (pos - 1) / (field_size - 1), 6) if field_size > 1 else 1.0

            # Opslag i CO/PCS via holdet rider_id (if known) eller slug-fallback.
            # _lookup() håndterer prefix-fallback (carlos_rodriguez_cano →
            # carlos_rodriguez) og accent-normalisering (lópez → lopez).
            rid = slug_to_rid.get(slug) or _slug_to_id(slug)

            co   = _lookup(rid, co_data)
            spec = _lookup(rid, spec_data)
            form = _lookup(rid, pcs_form)

            if co:
                n_co_hits += 1
            if spec:
                n_spec_hits += 1

            # Cross-race form: KUN samme etapetype — sprint-form til spurtetaper,
            # bjerg-form til bjergetaper osv. Undgår at nr. 153 på en bjergetape
            # ødelægger Merliers sprintform-signal.
            xrace_hist = rider_xrace_history.get(slug, {}).get(stype, [])
            xrace_form_10       = rolling_avg(xrace_hist, 10)
            xrace_top3_rate_10  = rolling_top_n_rate(xrace_hist, 10, 3)
            xrace_top10_rate_10 = rolling_top_n_rate(xrace_hist, 10, 10)

            # In-race rolling form
            in_race = in_race_hist.get(slug, [])
            gt_form_5  = rolling_avg(in_race, 5)
            gt_form_10 = rolling_avg(in_race, 10)
            gt_wins    = sum(1 for p in in_race if p == 1)

            # Startlist quality (normaliseret 0-1 baseret på PCS 0-1000 skala)
            quality_raw = res.get("startlist_quality", 1000.0)
            quality_norm = round(quality_raw / 1000.0, 4)

            rows.append({
                # Kontekst (ikke features)
                "race":       race,
                "year":       year,
                "stage":      snum,
                "stage_type": stype,
                "rider_slug": slug,
                "position":   pos,
                "field_size": field_size,
                # Stage-type flags
                "is_sprint":   int(stype == "sprint"),
                "is_mountain": int(stype == "mountain"),
                "is_hilly":    int(stype == "hilly"),
                "is_tt":       int(stype in ("tt",)),
                # CO ratings (nuværende snapshot — specialiteter er stabile over 5 år)
                "co_mtn": co.get("mtn", -1),
                "co_spr": co.get("spr", -1),
                "co_hll": co.get("hll", -1),
                "co_itt": co.get("itt", -1),
                "co_cob": co.get("cob", -1),
                "co_gc":  co.get("gc",  -1),
                # PCS specialties
                "spec_climber": spec.get("climber", -1),
                "spec_sprint":  spec.get("sprint",  -1),
                "spec_tt":      spec.get("tt",      -1),
                "spec_hills":   spec.get("hills",   -1),
                # PCS form by type (nuværende snapshot)
                "form_overall":  form.get("overall",  -1),
                "form_sprint":   form.get("sprint",   -1),
                "form_mountain": form.get("mountain", -1),
                "form_hilly":    form.get("hilly",    -1),
                "form_tt":       form.get("tt",       -1),
                # In-race rolling form
                "gt_form_5":        gt_form_5,
                "gt_form_10":       gt_form_10,
                "gt_wins_so_far":   gt_wins,
                # Cross-race form (seneste 10 etaper på tværs af løb, samme etapetype)
                "xrace_form_10":       xrace_form_10,
                "xrace_top3_rate_10":  xrace_top3_rate_10,
                "xrace_top10_rate_10": xrace_top10_rate_10,
                # Feltets styrke (PCS startlist quality / 1000)
                "startlist_quality": quality_norm,
                # Interaktions-features: etapetype × signal (hjælper modellen skelne
                # sprintspecialister fra GC-ryttere på sprintetaper, og omvendt)
                "sprint_co_spr":    co.get("spr", -1) * int(stype == "sprint"),
                "sprint_spec":      spec.get("sprint", -1) * int(stype == "sprint"),
                "sprint_xrace_top3": xrace_top3_rate_10 if stype == "sprint" else 0.0,
                "mtn_co_mtn":       co.get("mtn", -1) * int(stype == "mountain"),
                # Etapeprofil: lav score = flad, høj score = bakket/bjerg
                "profile_score":        res.get("profile_score") or 100,
                "sprint_profile_score": (res.get("profile_score") or 100) * int(stype == "sprint"),
                # Target
                "norm_pos": norm_pos,
            })

        # Opdater xrace_form EFTER etapen er behandlet (kronologisk korrekt)
        for res in finishers:
            if isinstance(res.get("position"), int) and res["position"] < 900:
                rider_xrace_history[res["rider_slug"]][stype].append(res["position"])

    print(f"\nBygget {len(rows):,} trænings-rækker")
    print(f"  Etaper:          {n_stages}")
    print(f"  Springet over:   {n_skipped_type} TTT-etaper")
    print(f"  CO hits:         {n_co_hits}/{len(rows)} ({100*n_co_hits//max(1,len(rows))}%)")
    print(f"  Spec hits:       {n_spec_hits}/{len(rows)} ({100*n_spec_hits//max(1,len(rows))}%)")

    if not rows:
        print("Ingen rækker — afbryder")
        return

    ML_DIR.mkdir(parents=True, exist_ok=True)

    # Skriv CSV
    fieldnames = list(rows[0].keys())
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV gemt: {OUT_CSV}  ({OUT_CSV.stat().st_size // 1024} KB)")

    feature_cols = [c for c in fieldnames if c not in (
        "race", "year", "stage", "stage_type", "rider_slug",
        "position", "field_size", "norm_pos",
    )]

    meta = {
        "n_rows":       len(rows),
        "n_stages":     n_stages,
        "feature_cols": feature_cols,
        "target_col":   "norm_pos",
        "races":        sorted({r["race"] for r in rows}),
        "years":        sorted({r["year"] for r in rows}),
        "notes": (
            "norm_pos: 1.0=vinder, 0.0=sidst (DNF udelades). "
            "CO/PCS snapshot er nuværende værdier — specialiteter antages stabile 2021-2025. "
            "xrace_form_10: gennemsnitsplacering i seneste 10 etaper på tværs af løb. "
            "startlist_quality: PCS feltsstyrke / 1000 (0-1 skala). "
            "TTT-etaper er udeladt (team-scoring kræver særlig behandling)."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Meta gemt: {OUT_META}")


if __name__ == "__main__":
    main()

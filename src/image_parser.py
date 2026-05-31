"""
VeloScore image → structured JSON.

When Claude Code is running (i.e. this project's primary usage),
the user uploads the VeloScore screenshot directly in the chat and
Claude extracts the table. This module provides:

  1. parse_veloscore_text(text) — for when the table is pasted as text
  2. A template/schema for the resulting JSON so every data source
     produces the same format.

Claude reads the image and returns structured JSON matching VELOSCORE_SCHEMA.
Paste Claude's output into data/stage_N_veloscore.json.
"""
from __future__ import annotations
import json
import re
from pathlib import Path


VELOSCORE_SCHEMA = {
    "stage": 0,
    "stage_type": "sprint",   # sprint | mountain | tt | hilly | cobbled
    "predictions": [
        # {
        #   "rank": 1,
        #   "rider": "Full Name",
        #   "total": 1077,
        #   "veloscore": 9.8,
        #   "stars": 4,
        #   "predictors": {"Axelgaard": 4, "Morrizz": 3, ...}
        # }
    ],
}


CLAUDE_EXTRACTION_PROMPT = """
Du ser et VeloScore-billede med en prediktorstabel. Udtræk ALL data og returner præcis dette JSON-format:

{
  "stage": <etapenummer>,
  "stage_type": "<sprint|mountain|tt|hilly|cobbled>",
  "predictions": [
    {
      "rank": 1,
      "rider": "Fuldt navn",
      "total": <total score>,
      "veloscore": <0.0-10.0>,
      "stars": <1-5>,
      "predictors": {
        "<predictor_name>": <star_count_1_to_4>
      }
    }
  ]
}

Medtag ALLE ryttere i tabellen, ikke kun toppen.
Brug altid fulde navne.
For stjerner: * = 1, ** = 2, *** = 3, **** = 4, — = 0.
"""


def parse_veloscore_text(text: str, stage: int | None = None) -> dict:
    """
    Parse a VeloScore table from plain text (e.g. copy-pasted).
    Returns dict matching VELOSCORE_SCHEMA.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    predictions = []

    for line in lines:
        # Try to find rider name + score pattern
        m = re.match(r"(\d+)\s+(.+?)\s+(\d{2,4})\s+([\d.]+)?", line)
        if m:
            predictions.append({
                "rank": int(m.group(1)),
                "rider": m.group(2).strip(),
                "total": int(m.group(3)),
                "veloscore": float(m.group(4)) if m.group(4) else None,
                "stars": None,
                "predictors": {},
            })

    return {
        "stage": stage or 0,
        "stage_type": "unknown",
        "predictions": predictions,
    }


def load_stage_veloscore(stage: int, data_dir: Path | None = None) -> dict | None:
    """Load pre-extracted VeloScore JSON for a given stage."""
    d = data_dir or Path(__file__).parent.parent / "data"
    path = d / f"stage_{stage:02d}_veloscore.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def save_stage_veloscore(data: dict, stage: int, data_dir: Path | None = None):
    """Save extracted VeloScore data to file."""
    d = data_dir or Path(__file__).parent.parent / "data"
    path = d / f"stage_{stage:02d}_veloscore.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved VeloScore for stage {stage} → {path}")

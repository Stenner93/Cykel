#!/usr/bin/env python3
"""One-off diagnostic: how does Holdet actually represent Tadej Pogačar's
stage-8 abandonment in its raw data? Needed to fix the DNF-exclusion logic
in build_vuelta_web_data.py, which scanned fetch_stage_actions() for a
ruleId==1080 action and found nothing (dnf_stage_by_id stayed empty on the
first attempt). Dumps the raw stage-8 actions for his personId, the
scoring-summary entries for stages 7-10, and whether he's still present in
those summaries — so the exclusion can be re-based on whatever signal
Holdet actually provides. Kører KUN i GitHub Actions. Skriver INTET.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "build"))
import build_vuelta_web_data as b  # noqa: E402

POGACAR_PID = 4206
GAME_ID = b.VUELTA_GAME_ID


def main():
    print(f"=== Fetching schedule for game {GAME_ID} ===")
    events, event_info = b._h.fetch_schedule(GAME_ID)
    finished_eids = [e for e in events if event_info.get(e, {}).get("status") == "finished"]
    print(f"finished events: {len(finished_eids)}")

    for _pos, eid in enumerate(events, start=1):
        stage_num = b._true_stage_num(eid, event_info, _pos)
        if stage_num not in (6, 7, 8, 9, 10):
            continue
        status = event_info.get(eid, {}).get("status")
        print(f"\n=== Stage {stage_num} (eid={eid}, status={status}) ===")
        if status != "finished":
            print("  not finished — skipping actions/summary fetch")
            continue

        acts = b.fetch_stage_actions(GAME_ID, eid)
        print(f"  total actions this stage: {len(acts)}")
        pog_acts = [a for a in acts if a.get("personId") == POGACAR_PID]
        print(f"  Pogacar (pid={POGACAR_PID}) actions: {len(pog_acts)}")
        for a in pog_acts:
            print(f"    {json.dumps(a, ensure_ascii=False)}")
        # also show rule ids present at all, for context
        rule_ids = sorted(set(a.get("ruleId") for a in acts))
        print(f"  distinct ruleIds this stage: {rule_ids}")

        summary = b.fetch_scoring_summary(GAME_ID, eid)
        print(f"  scoring-summary entries: {len(summary)}")
        print(f"  Pogacar in summary: {POGACAR_PID in summary}"
              + (f"  total={summary.get(POGACAR_PID)}" if POGACAR_PID in summary else ""))

    print("\n[FÆRDIG]")


if __name__ == "__main__":
    main()

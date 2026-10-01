#!/usr/bin/env python3
"""Attach and reconcile September placement coverage to the reach report."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--audits", required=True)
    args = parser.parse_args()
    report_path = Path(args.report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("period") != {"from": "2026-09-01", "to": "2026-09-30"}:
        raise RuntimeError("Report is not the full September 2026 period")
    audit_files = sorted(Path(args.audits).glob("september-*.audit.json"))
    if len(audit_files) != 8:
        raise RuntimeError(f"Expected eight placement audits, found {len(audit_files)}")
    placements = {}
    cursor = dt.date(2026, 9, 1)
    audited_total = 0
    for path in audit_files:
        audit = json.loads(path.read_text(encoding="utf-8"))
        start = dt.date.fromisoformat(audit["from"])
        end = dt.date.fromisoformat(audit["to"])
        if start != cursor or end < start:
            raise RuntimeError(f"Gap or overlap in audit at {path.name}")
        cursor = end + dt.timedelta(days=1)
        chunk_total = sum(item["impressions"] for item in audit["placements"])
        if chunk_total != audit["totalImpressions"]:
            raise RuntimeError(f"Placement counts do not reconcile in {path.name}")
        audited_total += chunk_total
        for item in audit["placements"]:
            placement_id = str(item["placementId"])
            existing = placements.get(placement_id)
            if existing is None:
                placements[placement_id] = dict(item)
            else:
                if any(existing[key] != item[key] for key in ("placementName", "source", "project", "scope")):
                    raise RuntimeError(f"Placement classification changed for {placement_id}")
                existing["impressions"] += item["impressions"]
    if cursor != dt.date(2026, 10, 1):
        raise RuntimeError("Placement audits do not cover September 30")
    if audited_total != report["total"]["impressions"]:
        raise RuntimeError(f"Audited impressions {audited_total} != report total {report['total']['impressions']}")
    project_impressions = Counter()
    for item in placements.values():
        project_impressions[item["project"]] += item["impressions"]
    report_projects = {row["project"]: row["impressions"] for row in report["byProject"]}
    if dict(project_impressions) != report_projects:
        raise RuntimeError("Placement totals do not reconcile to project totals")
    report["byPlacement"] = sorted(
        placements.values(),
        key=lambda item: (-item["impressions"], item["project"].casefold(), item["placementId"]),
    )
    report["placementAudit"] = {
        "activePlacementCount": len(placements),
        "auditedImpressions": audited_total,
        "unmatchedPlacementImpressions": report["unknownPlacementImpressions"],
        "allImpressionsAssignedToAGroup": True,
    }
    report["important"] += " Every active placement is listed in byPlacement and reconciles to a project group and the Total."
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "period": report["period"],
        "total": report["total"],
        "placementAudit": report["placementAudit"],
        "volga": [row for row in report["byProject"] if "Волга" in row["project"]],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

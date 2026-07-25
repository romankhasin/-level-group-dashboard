#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "update_fraud_data.py"
VALIDATE = ROOT / ".github" / "workflows" / "validate-fraud-data.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Missing expected block: {label}")
    return text.replace(old, new, 1)


text = TARGET.read_text(encoding="utf-8")
text = replace_once(text, "DATA_VERSION = 3", "DATA_VERSION = 4", "data version")
text = replace_once(
    text,
    '        "automation": False,\n',
    '        "automation_visits": 0,\n',
    "automation bucket",
)
text = replace_once(
    text,
    '    automation = bool(AUTOMATION_RE.search(browser_version))\n    if automation:\n        bucket["automation"] = True\n',
    '    automation = bool(AUTOMATION_RE.search(browser_version))\n    if automation:\n        bucket["automation_visits"] += 1\n',
    "automation counter",
)
text = replace_once(
    text,
    '    visit_risk = summarize_visit_risk(bucket, visits)\n    metrics = {\n',
    '    visit_risk = summarize_visit_risk(bucket, visits)\n    automation_visits = int(bucket.get("automation_visits") or 0)\n    metrics = {\n',
    "automation finalize variable",
)
text = replace_once(
    text,
    '        "visitRisk": visit_risk,\n        "automation": bool(bucket["automation"]),\n        "concentrationScope": "daily",\n',
    '        "visitRisk": visit_risk,\n        "automationVisits": automation_visits,\n        "automationShare": automation_visits / visits if visits else 0.0,\n        "automation": automation_visits > 0,\n        "concentrationScope": "daily",\n',
    "automation output",
)
text = replace_once(
    text,
    '        "campaignFilter": list(CAMPAIGN_TOKENS),\n        "privacy": "Public files contain daily aggregates only; raw IP, ClientID and VisitID are never committed.",\n',
    '        "campaignFilter": list(CAMPAIGN_TOKENS),\n        "scoringModel": "directional-volume-multisignal-stage1",\n        "privacy": "Public files contain daily aggregates only; raw IP, ClientID and VisitID are never committed.",\n',
    "catalog scoring model",
)
text = replace_once(
    text,
    '    assert result["visitRisk"]["reviewVisits"] == 0\n    assert "1234567890123456789" not in json.dumps(result)\n',
    '    assert result["visitRisk"]["reviewVisits"] == 0\n    assert result["automationVisits"] == 0\n    assert result["automationShare"] == 0\n\n    automation_row = dict(rows[0])\n    automation_row["ym:s:browser"] = "HeadlessChrome"\n    automation_store: dict[tuple[str, str], dict] = {}\n    process_visit(automation_store, automation_row, quality_goal_id=411053186)\n    automation_result = finalize_bucket(\n        "mts", "2026-07-01", automation_store[("mts", "2026-07-01")]\n    )\n    assert automation_result["automationVisits"] == 1\n    assert automation_result["automationShare"] == 1\n    assert "1234567890123456789" not in json.dumps(result)\n',
    "automation self test",
)
TARGET.write_text(text, encoding="utf-8")

validate = VALIDATE.read_text(encoding="utf-8")
validate = replace_once(validate, 'grep -q "DATA_VERSION = 3"', 'grep -q "DATA_VERSION = 4"', "validate version")
validate = replace_once(
    validate,
    '          grep -q \'"campaignFilter": list(CAMPAIGN_TOKENS)\' scripts/update_fraud_data.py\n',
    '          grep -q \'"campaignFilter": list(CAMPAIGN_TOKENS)\' scripts/update_fraud_data.py\n'
    '          grep -q \'"automationVisits": automation_visits\' scripts/update_fraud_data.py\n'
    '          grep -q \'"automationShare": automation_visits / visits\' scripts/update_fraud_data.py\n'
    '          grep -q \'directional-volume-multisignal-stage1\' scripts/update_fraud_data.py\n'
    '          grep -q \'assert automation_result["automationVisits"] == 1\' scripts/update_fraud_data.py\n',
    "validate automation fields",
)
VALIDATE.write_text(validate, encoding="utf-8")
print("Applied scoring stage 1 data changes")

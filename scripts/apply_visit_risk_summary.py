#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "update_fraud_data.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Missing expected block: {label}")
    return text.replace(old, new, 1)


text = TARGET.read_text(encoding="utf-8")

text = replace_once(
    text,
    """import urllib.request

ROOT = Path(__file__).resolve().parents[1]
""",
    """import urllib.request

from fraud_visit_classifier import append_visit_feature, summarize_visit_risk

ROOT = Path(__file__).resolve().parents[1]
""",
    "classifier import",
)

text = replace_once(
    text,
    """STATUS_PATH = DATA_DIR / "status.json"

START_DATE = dt.date(2026, 5, 1)
""",
    """STATUS_PATH = DATA_DIR / "status.json"
DATA_VERSION = 2

START_DATE = dt.date(2026, 5, 1)
""",
    "data version",
)

text = replace_once(
    text,
    """        "unknown_browser_visits": 0,
        "automation": False,
    }
""",
    """        "unknown_browser_visits": 0,
        "automation": False,
        "visit_features": [],
    }
""",
    "visit feature storage",
)

text = replace_once(
    text,
    """    if AUTOMATION_RE.search(browser_version):
        bucket["automation"] = True
""",
    """    automation = bool(AUTOMATION_RE.search(browser_version))
    if automation:
        bucket["automation"] = True

    append_visit_feature(
        bucket,
        client_id=client_id,
        ip=ip,
        subnet=subnet_of(ip) if ip else "",
        profile=profile,
        bounce=bool(safe_int(field_value(row, "ym:s:bounce"))),
        duration=max(0, safe_int(field_value(row, "ym:s:visitDuration"))),
        is_new=bool(safe_int(field_value(row, "ym:s:isNewUser"))),
        cookie_enabled=bool(safe_int(field_value(row, "ym:s:cookieEnabled"))),
        quality_goal=quality_goal_id in parse_goals(field_value(row, "ym:s:goalsID")),
        automation=automation,
    )
""",
    "append visit features",
)

text = replace_once(
    text,
    """    top_client = hidden_top_entry(bucket["client_counts"], client_id_visits)
    metrics = {
""",
    """    top_client = hidden_top_entry(bucket["client_counts"], client_id_visits)
    visit_risk = summarize_visit_risk(bucket, visits)
    metrics = {
""",
    "visit risk summary",
)

text = replace_once(
    text,
    """        "cookieEnabledShare": bucket["cookie_sum"] / visits if visits else 0.0,
        "automation": bool(bucket["automation"]),
""",
    """        "cookieEnabledShare": bucket["cookie_sum"] / visits if visits else 0.0,
        "visitRisk": visit_risk,
        "automation": bool(bucket["automation"]),
""",
    "visit risk output",
)

text = text.replace('"version": 1,', '"version": DATA_VERSION,', 2)

text = replace_once(
    text,
    """        path = month_path(counter_id, month)
        if force or not path.exists():
            result.append((month, available_start, available_end))
            continue
""",
    """        path = month_path(counter_id, month)
        existing_payload = read_json(path, {}) if path.exists() else {}
        existing_version = int(existing_payload.get("version") or 0) if isinstance(existing_payload, dict) else 0
        if force or not path.exists() or existing_version < DATA_VERSION:
            result.append((month, available_start, available_end))
            continue
""",
    "versioned backfill",
)

text = replace_once(
    text,
    """    assert result["topIp"]["key"] == "скрыто"
    assert "1234567890123456789" not in json.dumps(result)
""",
    """    assert result["topIp"]["key"] == "скрыто"
    assert result["visitRisk"]["classifiedVisits"] == 2
    assert result["visitRisk"]["highRiskVisits"] == 0
    assert result["visitRisk"]["reviewVisits"] == 0
    assert "1234567890123456789" not in json.dumps(result)
""",
    "self-test visit risk",
)

TARGET.write_text(text, encoding="utf-8")
print("Visit-risk summary applied")

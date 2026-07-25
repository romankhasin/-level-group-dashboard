#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "update_fraud_data.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"Missing expected block: {label}")
    return text.replace(old, new, 1)


text = TARGET.read_text(encoding="utf-8")

text = replace_once(text, "DATA_VERSION = 2", "DATA_VERSION = 3", "data version")

text = replace_once(
    text,
    """QUALITY_CALL_GOAL_IDS = {
    53197618: 411053186,
    100470605: 411053614,
}
REFRESH_DAYS = 3
""",
    """QUALITY_CALL_GOAL_IDS = {
    53197618: 411053186,
    100470605: 411053614,
}
CAMPAIGN_TOKENS = ("prg", "med", "mrk")
REFRESH_DAYS = 3
""",
    "campaign tokens",
)

text = replace_once(
    text,
    '    "ym:s:<attribution>UTMSource",\n',
    '    "ym:s:<attribution>UTMSource",\n    "ym:s:<attribution>UTMCampaign",\n',
    "campaign field",
)

text = replace_once(
    text,
    """def source_name(value: object) -> str:
    text = str(value or "").strip()
    return "Не определено" if text.lower() in INVALID_IDS else text.lower()


""",
    """def source_name(value: object) -> str:
    text = str(value or "").strip()
    return "Не определено" if text.lower() in INVALID_IDS else text.lower()


def campaign_in_scope(value: object) -> bool:
    campaign = str(value or "").strip().lower()
    return bool(campaign) and any(token in campaign for token in CAMPAIGN_TOKENS)


""",
    "campaign helper",
)

text = replace_once(
    text,
    """    report_date = field_value(row, "ym:s:date").strip()
    if not report_date:
        return
    source = source_name(field_value(row, "ym:s:<attribution>UTMSource", "UTMSource"))
""",
    """    report_date = field_value(row, "ym:s:date").strip()
    if not report_date:
        return
    campaign = field_value(row, "ym:s:<attribution>UTMCampaign", "UTMCampaign")
    if not campaign_in_scope(campaign):
        return
    source = source_name(field_value(row, "ym:s:<attribution>UTMSource", "UTMSource"))
""",
    "visit campaign filter",
)

text = replace_once(
    text,
    """    rows.sort(key=lambda item: (item["date"], item["source"]))
    return rows, {
        "requestId": request_id,
        "rawVisits": raw_visits,
        "dailySourceRows": len(rows),
""",
    """    rows.sort(key=lambda item: (item["date"], item["source"]))
    included_visits = sum(int(row.get("visits") or 0) for row in rows)
    return rows, {
        "requestId": request_id,
        "rawVisits": raw_visits,
        "includedVisits": included_visits,
        "excludedVisits": max(0, raw_visits - included_visits),
        "campaignFilter": list(CAMPAIGN_TOKENS),
        "dailySourceRows": len(rows),
""",
    "operation campaign counts",
)

text = replace_once(
    text,
    """            "source": "Yandex Metrica Logs API",
            "privacy": "Only daily aggregates are stored; raw IP, ClientID and VisitID are discarded.",
""",
    """            "source": "Yandex Metrica Logs API",
            "campaignFilter": list(CAMPAIGN_TOKENS),
            "privacy": "Only daily aggregates are stored; raw IP, ClientID and VisitID are discarded.",
""",
    "month campaign metadata",
)

text = replace_once(
    text,
    """        "refresh": "daily, previous 3 complete days",
        "privacy": "Public files contain daily aggregates only; raw IP, ClientID and VisitID are never committed.",
""",
    """        "refresh": "daily, previous 3 complete days",
        "campaignFilter": list(CAMPAIGN_TOKENS),
        "privacy": "Public files contain daily aggregates only; raw IP, ClientID and VisitID are never committed.",
""",
    "catalog campaign metadata",
)

text = text.replace(
    '            "ym:s:lastUTMSource": "MTS",\n',
    '            "ym:s:lastUTMSource": "MTS",\n            "ym:s:lastUTMCampaign": "level_prg_test",\n',
)

text = replace_once(
    text,
    """            "ym:s:cookieEnabled": "1",
        },
    ]
""",
    """            "ym:s:cookieEnabled": "1",
        },
        {
            "ym:s:date": "2026-07-01",
            "ym:s:counterUserIDHash": "999",
            "ym:s:clientID": "9999999999999999999",
            "ym:s:ipAddress": "10.20.30.99",
            "ym:s:lastUTMSource": "MTS",
            "ym:s:lastUTMCampaign": "brand_search_only",
            "ym:s:bounce": "1",
            "ym:s:visitDuration": "0",
            "ym:s:isNewUser": "1",
            "ym:s:goalsID": "[]",
            "ym:s:browser": "Chrome",
            "ym:s:browserMajorVersion": "138",
            "ym:s:browserMinorVersion": "0",
            "ym:s:operatingSystem": "Android",
            "ym:s:deviceCategory": "2",
            "ym:s:screenWidth": "360",
            "ym:s:screenHeight": "800",
            "ym:s:cookieEnabled": "1",
        },
    ]
""",
    "excluded campaign self-test row",
)

text = replace_once(
    text,
    """    result = finalize_bucket("mts", "2026-07-01", store[("mts", "2026-07-01")])
    assert result["visits"] == 2
""",
    """    result = finalize_bucket("mts", "2026-07-01", store[("mts", "2026-07-01")])
    assert campaign_in_scope("LEVEL_PRG_VIDEO")
    assert campaign_in_scope("level-med-july")
    assert campaign_in_scope("brand_mrk_test")
    assert not campaign_in_scope("brand_search_only")
    assert result["visits"] == 2
""",
    "campaign self-test assertions",
)

TARGET.write_text(text, encoding="utf-8")
print("Campaign filter applied")

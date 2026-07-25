#!/usr/bin/env python3
"""Privacy-safe visit-level risk classification for Traffic Fraud Lab.

The classifier receives raw visit attributes only inside the GitHub Action. It returns
counts and aggregated reason statistics; identifiers are never included in its output.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable


REASON_LABELS = {
    "automation_browser": "automation/headless-браузер",
    "repeated_clientid": "повторные визиты одного ClientID",
    "concentrated_ip": "концентрация одного IP",
    "concentrated_subnet": "концентрация одной подсети",
    "concentrated_profile": "одинаковый технический профиль",
    "short_bounce": "очень короткие визиты с отказом",
    "no_cookie_new_short": "новые короткие визиты без cookies",
}


@dataclass(frozen=True, slots=True)
class VisitFeature:
    client_id: str
    ip: str
    subnet: str
    profile: str
    bounce: bool
    duration: int
    is_new: bool
    cookie_enabled: bool
    quality_goal: bool
    automation: bool


def append_visit_feature(
    bucket: dict,
    *,
    client_id: str,
    ip: str,
    subnet: str,
    profile: str,
    bounce: bool,
    duration: int,
    is_new: bool,
    cookie_enabled: bool,
    quality_goal: bool,
    automation: bool,
) -> None:
    bucket.setdefault("visit_features", []).append(
        VisitFeature(
            client_id=client_id,
            ip=ip,
            subnet=subnet,
            profile=profile,
            bounce=bool(bounce),
            duration=max(0, int(duration or 0)),
            is_new=bool(is_new),
            cookie_enabled=bool(cookie_enabled),
            quality_goal=bool(quality_goal),
            automation=bool(automation),
        )
    )


def _cluster_signal(
    count: int,
    total: int,
    *,
    medium_count: int,
    medium_share: float,
    strong_count: int,
    strong_share: float,
) -> str:
    if count <= 0 or total <= 0:
        return ""
    share = count / total
    if count >= strong_count and share >= strong_share:
        return "strong"
    if count >= medium_count and share >= medium_share:
        return "medium"
    return ""


def _score_visit(feature: VisitFeature, bucket: dict, visits: int) -> tuple[str, set[str]]:
    score = 0
    families: set[str] = set()
    reasons: set[str] = set()

    client_total = max(1, sum(bucket.get("client_counts", {}).values()))
    if feature.client_id:
        count = int(bucket["client_counts"].get(feature.client_id, 0))
        signal = _cluster_signal(
            count,
            client_total,
            medium_count=6,
            medium_share=0.01,
            strong_count=15,
            strong_share=0.02,
        )
        if signal:
            score += 18 if signal == "strong" else 10
            families.add("client")
            reasons.add("repeated_clientid")

    if feature.ip:
        count = int(bucket["ip_counts"].get(feature.ip, 0))
        signal = _cluster_signal(
            count,
            visits,
            medium_count=10,
            medium_share=0.012,
            strong_count=25,
            strong_share=0.025,
        )
        if signal:
            score += 18 if signal == "strong" else 10
            families.add("network")
            reasons.add("concentrated_ip")

    if feature.subnet:
        count = int(bucket["subnet_counts"].get(feature.subnet, 0))
        signal = _cluster_signal(
            count,
            visits,
            medium_count=35,
            medium_share=0.08,
            strong_count=90,
            strong_share=0.15,
        )
        if signal:
            score += 14 if signal == "strong" else 8
            families.add("network")
            reasons.add("concentrated_subnet")

    if feature.profile:
        count = int(bucket["profile_counts"].get(feature.profile, 0))
        signal = _cluster_signal(
            count,
            visits,
            medium_count=50,
            medium_share=0.25,
            strong_count=120,
            strong_share=0.45,
        )
        if signal:
            score += 14 if signal == "strong" else 7
            families.add("technical")
            reasons.add("concentrated_profile")

    if feature.automation:
        score += 40
        families.add("technical")
        reasons.add("automation_browser")

    if feature.bounce and feature.duration <= 3:
        score += 12
        families.add("behavior")
        reasons.add("short_bounce")
    elif feature.bounce and feature.duration <= 8:
        score += 7
        families.add("behavior")
        reasons.add("short_bounce")

    if (
        not feature.cookie_enabled
        and feature.is_new
        and feature.duration <= 10
        and not feature.quality_goal
    ):
        score += 7
        families.add("environment")
        reasons.add("no_cookie_new_short")

    if feature.automation:
        risk = "high"
    elif score >= 42 and len(families) >= 2:
        risk = "high"
    elif score >= 34 and len(families) >= 3:
        risk = "high"
    elif score >= 24 and len(families) >= 2:
        risk = "review"
    else:
        risk = "low"
    return risk, reasons


def summarize_visit_risk(bucket: dict, visits: int) -> dict:
    features: Iterable[VisitFeature] = bucket.get("visit_features") or ()
    levels = Counter()
    reason_counts = Counter()

    for feature in features:
        risk, reasons = _score_visit(feature, bucket, visits)
        levels[risk] += 1
        if risk != "low":
            for reason in reasons:
                reason_counts[reason] += 1

    classified = sum(levels.values())
    if classified < visits:
        levels["low"] += visits - classified
    suspicious = levels["high"] + levels["review"]

    reasons = [
        {
            "code": code,
            "label": REASON_LABELS[code],
            "visits": count,
            "shareOfSuspicious": count / suspicious if suspicious else 0.0,
        }
        for code, count in reason_counts.most_common()
        if code in REASON_LABELS
    ]

    client_coverage = (
        sum(bucket.get("client_counts", {}).values()) / visits if visits else 0.0
    )
    ip_coverage = sum(bucket.get("ip_counts", {}).values()) / visits if visits else 0.0
    if visits < 200 or min(client_coverage, ip_coverage) < 0.35:
        confidence = "Низкая"
    elif visits < 500 or min(client_coverage, ip_coverage) < 0.65:
        confidence = "Средняя"
    else:
        confidence = "Высокая"

    if not suspicious:
        comment = "Выраженных сочетаний признаков на уровне отдельных визитов не найдено."
    else:
        top_labels = [item["label"] for item in reasons[:2]]
        reason_text = " и ".join(top_labels) if top_labels else "совпадение нескольких независимых признаков"
        comment = (
            f"{suspicious} визитов требуют внимания: {levels['high']} высокого риска и "
            f"{levels['review']} требуют проверки. Основные причины — {reason_text}."
        )

    return {
        "classifiedVisits": visits,
        "highRiskVisits": levels["high"],
        "reviewVisits": levels["review"],
        "lowRiskVisits": levels["low"],
        "suspiciousVisits": suspicious,
        "suspiciousShare": suspicious / visits if visits else 0.0,
        "confidence": confidence,
        "comment": comment,
        "reasons": reasons,
        "method": "rule-based visit clusters v1",
    }


def self_test() -> None:
    bucket = {
        "client_counts": Counter(),
        "ip_counts": Counter(),
        "subnet_counts": Counter(),
        "profile_counts": Counter(),
        "visit_features": [],
    }
    for index in range(60):
        suspicious = index < 30
        client_id = "client-cluster" if suspicious else f"client-{index}"
        ip = "10.20.30.40" if suspicious else f"10.20.31.{index}"
        subnet = "10.20.30.0/24" if suspicious else "10.20.31.0/24"
        profile = "HeadlessChrome · Linux · desktop · 1920x1080" if suspicious else f"Chrome · Android · mobile · {index}x800"
        bucket["client_counts"][client_id] += 1
        bucket["ip_counts"][ip] += 1
        bucket["subnet_counts"][subnet] += 1
        bucket["profile_counts"][profile] += 1
        append_visit_feature(
            bucket,
            client_id=client_id,
            ip=ip,
            subnet=subnet,
            profile=profile,
            bounce=suspicious,
            duration=1 if suspicious else 120,
            is_new=True,
            cookie_enabled=not suspicious,
            quality_goal=False,
            automation=suspicious,
        )

    result = summarize_visit_risk(bucket, 60)
    assert result["classifiedVisits"] == 60
    assert result["highRiskVisits"] == 30
    assert result["lowRiskVisits"] == 30
    assert result["suspiciousVisits"] == 30
    serialized = str(result)
    assert "client-cluster" not in serialized
    assert "10.20.30.40" not in serialized
    assert result["reasons"][0]["visits"] == 30
    print("Visit-level classifier self-test passed")


if __name__ == "__main__":
    self_test()

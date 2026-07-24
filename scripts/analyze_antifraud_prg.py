#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("antifraud_prg_53197618")
RAW = ROOT / "raw"
OUT = ROOT / "antifraud_report_prg_may_june_2026.html"
SOURCE_FILE = RAW / "source.csv"
TECH_FILE = RAW / "tech.csv"
IP_FILE = RAW / "ip.csv"
META_FILE = RAW / "meta.json"


@dataclass
class SourceProfile:
    source: str
    visits: int = 0
    users: int = 0
    bounce: float = 0.0
    duration: float = 0.0
    new_share: float = 0.0
    quality_conv: float = 0.0
    primary_conv: float = 0.0
    tech_visits: int = 0
    ip_visits: int = 0
    ipv6_visits: int = 0
    browsers: Counter = field(default_factory=Counter)
    oses: Counter = field(default_factory=Counter)
    models: Counter = field(default_factory=Counter)
    resolutions: Counter = field(default_factory=Counter)
    ips: Counter = field(default_factory=Counter)
    subnets: Counter = field(default_factory=Counter)
    tech_candidates: list[dict] = field(default_factory=list)
    score: int = 0
    risk: str = "low"
    flags: list[str] = field(default_factory=list)
    recommendation: str = ""
    confidence: str = ""


def text(value: object) -> str:
    return str(value or "").strip()


def number(value: object) -> float:
    raw = text(value).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def parse_duration(value: object) -> float:
    raw = text(value)
    if not raw:
        return 0.0
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(raw)
    except ValueError:
        return 0.0


def fmt_int(value: float | int) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def fmt_pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%".replace(".", ",")


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def is_total_row(row: dict) -> bool:
    first = text(next(iter(row.values()), "")).lower()
    return first.startswith("итого")


def subnet_key(ip_value: str) -> str:
    try:
        ip = ipaddress.ip_address(ip_value)
        network = ipaddress.ip_network(f"{ip}/{'64' if ip.version == 6 else '24'}", strict=False)
        return str(network)
    except ValueError:
        return "Не определено"


def top_item(counter: Counter, total: int) -> tuple[str, int, float]:
    if not counter or total <= 0:
        return "—", 0, 0.0
    name, visits = counter.most_common(1)[0]
    return name, visits, visits / total


def weighted_candidates(profiles: dict[str, SourceProfile], tech_rows: list[dict]) -> None:
    automation = ("headless", "selenium", "puppeteer", "phantom", "crawler", "spider", " bot", "bot ")
    unknown_tokens = ("не определено", "unknown", "undefined", "not set", "n/a")
    for row in tech_rows:
        if is_total_row(row):
            continue
        source = text(row.get("UTM Source")) or "Не определено"
        p = profiles.setdefault(source, SourceProfile(source=source))
        visits = int(round(number(row.get("Визиты"))))
        if visits <= 0:
            continue
        os_name = text(row.get("Операционная система (детально)")) or "Не определено"
        browser = text(row.get("Версия браузера")) or "Не определено"
        model = text(row.get("Модель устройства")) or "Не определено"
        resolution = text(row.get("Разрешение")) or "Не определено"
        bounce = number(row.get("Отказы"))
        duration = parse_duration(row.get("Время на сайте"))
        qconv = number(row.get("Конверсия (Первично-качественные звонки UIS)"))
        pconv = number(row.get("Конверсия (Первичные звонки UIS)"))
        p.tech_visits += visits
        p.oses[os_name] += visits
        p.browsers[browser] += visits
        p.models[model] += visits
        p.resolutions[resolution] += visits
        browser_l = browser.lower()
        all_l = " | ".join((os_name, browser, model, resolution)).lower()
        candidate_score = 0
        reasons: list[str] = []
        if any(token in browser_l for token in automation):
            candidate_score += 100
            reasons.append("автоматизированный / headless-браузер")
        if bounce >= 0.95 and duration <= 5:
            candidate_score += 60
            reasons.append("95%+ отказов и почти нулевое время")
        elif bounce >= 0.85 and duration <= 15:
            candidate_score += 40
            reasons.append("высокий отказ и короткое время")
        if any(token in all_l for token in unknown_tokens) and (bounce >= 0.75 or duration <= 10):
            candidate_score += 20
            reasons.append("неопределившиеся технические параметры")
        if bounce <= 0.01 and visits >= 300:
            candidate_score += 12
            reasons.append("аномально низкий отказ")
        if qconv == 0 and pconv == 0 and visits >= 1000 and bounce >= 0.65:
            candidate_score += 10
            reasons.append("нет звонковых конверсий")
        min_visits = max(100, int(p.visits * 0.001) if p.visits else 100)
        if candidate_score and visits >= min_visits:
            p.tech_candidates.append({
                "score": candidate_score, "visits": visits, "browser": browser,
                "os": os_name, "model": model, "resolution": resolution,
                "bounce": bounce, "duration": duration, "quality": qconv,
                "primary": pconv, "reasons": reasons,
            })


def score_profile(p: SourceProfile, total: SourceProfile) -> None:
    score = 0
    flags: list[str] = []
    if p.bounce >= 0.70:
        score += 25; flags.append(f"очень высокий отказ {fmt_pct(p.bounce)}")
    elif p.bounce >= 0.55:
        score += 17; flags.append(f"высокий отказ {fmt_pct(p.bounce)}")
    elif p.bounce >= 0.45:
        score += 8
    if p.bounce >= total.bounce + 0.15 and p.visits >= 500:
        score += 7; flags.append(f"отказ выше общего PRG на {fmt_pct(p.bounce-total.bounce)}")
    if p.duration < 30:
        score += 20; flags.append(f"время на сайте {fmt_duration(p.duration)}")
    elif p.duration < 60:
        score += 13; flags.append(f"короткое время {fmt_duration(p.duration)}")
    elif p.duration < 90:
        score += 5
    if p.visits >= 1000:
        if p.quality_conv == 0:
            score += 9; flags.append("нет первично-качественных звонков")
        elif total.quality_conv and p.quality_conv < total.quality_conv * 0.25:
            score += 6; flags.append("низкая конверсия в качественный звонок")
        if p.primary_conv == 0:
            score += 9; flags.append("нет первичных звонков")
        elif total.primary_conv and p.primary_conv < total.primary_conv * 0.25:
            score += 6; flags.append("низкая конверсия в первичный звонок")
    if p.new_share >= 0.995:
        score += 6; flags.append(f"почти весь трафик новый — {fmt_pct(p.new_share)}")
    elif p.new_share >= 0.99:
        score += 3
    top_browser, _, top_browser_share = top_item(p.browsers, p.tech_visits)
    _, _, top_ip_share = top_item(p.ips, p.ip_visits)
    _, _, top_subnet_share = top_item(p.subnets, p.ip_visits)
    candidates = sorted(p.tech_candidates, key=lambda x: (x["score"], x["visits"]), reverse=True)
    headless_visits = sum(c["visits"] for c in candidates if "headless" in c["browser"].lower() or "selenium" in c["browser"].lower())
    headless_share = headless_visits / p.tech_visits if p.tech_visits else 0.0
    if headless_share >= 0.05:
        score += 25; flags.append(f"headless/automation {fmt_pct(headless_share)} трафика")
    elif headless_share >= 0.01:
        score += 16; flags.append(f"заметная доля headless/automation — {fmt_pct(headless_share)}")
    elif headless_visits >= 100:
        score += 8; flags.append("обнаружен headless/automation-срез")
    if candidates:
        top_c = candidates[0]
        c_share = top_c["visits"] / p.tech_visits if p.tech_visits else 0.0
        if top_c["score"] >= 60 and c_share >= 0.10:
            score += 15; flags.append(f"критичный техсрез {fmt_pct(c_share)} источника")
        elif top_c["score"] >= 40 and c_share >= 0.03:
            score += 8; flags.append("аномальный браузерно-устройственный срез")
    if top_browser_share >= 0.75 and p.tech_visits >= 1000:
        score += 8; flags.append(f"браузерная концентрация: {top_browser} — {fmt_pct(top_browser_share)}")
    elif top_browser_share >= 0.55 and p.tech_visits >= 1000:
        score += 4
    if top_ip_share >= 0.10:
        score += 15; flags.append(f"один IP даёт {fmt_pct(top_ip_share)}")
    elif top_ip_share >= 0.05:
        score += 10; flags.append(f"концентрация на IP — {fmt_pct(top_ip_share)}")
    elif top_ip_share >= 0.02 and p.ip_visits >= 1000:
        score += 5
    if top_subnet_share >= 0.20:
        score += 12; flags.append(f"одна подсеть даёт {fmt_pct(top_subnet_share)}")
    elif top_subnet_share >= 0.10:
        score += 7; flags.append(f"концентрация подсети — {fmt_pct(top_subnet_share)}")
    elif top_subnet_share >= 0.05 and p.ip_visits >= 1000:
        score += 3
    ipv6_share = p.ipv6_visits / p.ip_visits if p.ip_visits else 0.0
    if ipv6_share >= 0.70 and p.bounce > total.bounce + 0.10:
        score += 4; flags.append(f"IPv6 {fmt_pct(ipv6_share)} при слабом поведении")
    tech_cov = min(1.0, p.tech_visits / p.visits) if p.visits else 0.0
    ip_cov = min(1.0, p.ip_visits / p.visits) if p.visits else 0.0
    if p.visits >= 1000 and tech_cov >= 0.80 and ip_cov >= 0.70:
        p.confidence = "высокая полнота срезов"
    elif tech_cov >= 0.50 and ip_cov >= 0.40:
        p.confidence = "средняя полнота срезов"
    else:
        p.confidence = "ограниченная полнота срезов"
        if score >= 60:
            score -= 5
    if p.visits < 300:
        score = min(score, 39); flags.append("малый объём — вывод предварительный")
    p.score = max(0, min(100, int(round(score))))
    p.flags = list(dict.fromkeys(flags))[:6]
    if p.score >= 60:
        p.risk = "high"
        p.recommendation = "Запросить у площадки расшифровку по плейсментам, SSP/apps/sites, user-agent и IP/subnet. До ответа точечно ограничить наиболее аномальные техсрезы; не отключать источник целиком без верификации."
    elif p.score >= 30:
        p.risk = "medium"
        p.recommendation = "Оставить источник в работе с усиленным мониторингом. Проверить отмеченные браузеры, подсети и плейсменты, сопоставить с Target Ads/Adriver и динамикой звонков."
    else:
        p.risk = "low"
        p.recommendation = "Критичной комбинации признаков не обнаружено. Сохранять регулярный контроль качества и отдельно отслеживать новые аномальные срезы."


def risk_label(risk: str) -> str:
    return {"high": "Высокий приоритет", "medium": "Средний приоритет", "low": "Низкий приоритет"}[risk]


def risk_sort(risk: str) -> int:
    return {"high": 0, "medium": 1, "low": 2}[risk]


def esc(value: object) -> str:
    return html.escape(text(value))


def candidate_html(p: SourceProfile) -> str:
    candidates = sorted(p.tech_candidates, key=lambda x: (x["score"], x["visits"]), reverse=True)[:3]
    if not candidates:
        return '<p class="muted">Критичных технических комбинаций по заданным правилам не найдено.</p>'
    blocks = []
    for c in candidates:
        share = c["visits"] / p.tech_visits if p.tech_visits else 0.0
        dims = " · ".join(v for v in (c["browser"], c["os"], c["model"], c["resolution"]) if v and v != "Не определено")
        blocks.append(f'<div class="anomaly"><b>{esc(dims or "Неопределившийся техсрез")}</b><span>{fmt_int(c["visits"])} визитов · {fmt_pct(share)} источника · отказ {fmt_pct(c["bounce"])} · время {fmt_duration(c["duration"])}</span><small>{esc("; ".join(c["reasons"]))}</small></div>')
    return "".join(blocks)


def build_html(profiles: list[SourceProfile], total: SourceProfile, meta: dict) -> str:
    profiles = sorted(profiles, key=lambda p: (risk_sort(p.risk), -p.score, -p.visits, p.source.lower()))
    total_visits = total.visits
    high = [p for p in profiles if p.risk == "high"]
    medium = [p for p in profiles if p.risk == "medium"]
    high_visits = sum(p.visits for p in high)
    tech_coverage = sum(p.tech_visits for p in profiles) / total_visits if total_visits else 0.0
    ip_coverage = sum(p.ip_visits for p in profiles) / total_visits if total_visits else 0.0
    if high:
        overall = f"Выделено {len(high)} источников с высокой приоритетностью проверки. Их совокупный объём — {fmt_int(high_visits)} визитов ({fmt_pct(high_visits/total_visits if total_visits else 0)} PRG-трафика). Это не автоматическое доказательство фрода: основание для претензии появляется только после подтверждения плейсментов, user-agent и IP/подсетей."
    elif medium:
        overall = "Источников с критичной комбинацией признаков не найдено, но есть зоны среднего риска. Основная задача — точечно проверить технические срезы и сопоставить их с рекламными логами и звонковыми целями."
    else:
        overall = "На уровне доступных выгрузок критичной комбинации антифрод-признаков не обнаружено. Это не исключает точечного фрода на отдельных плейсментах, поэтому регулярный мониторинг всё равно нужен."
    rows, cards = [], []
    for p in profiles:
        top_browser, _, top_browser_share = top_item(p.browsers, p.tech_visits)
        top_os, _, top_os_share = top_item(p.oses, p.tech_visits)
        top_model, _, top_model_share = top_item(p.models, p.tech_visits)
        top_res, _, top_res_share = top_item(p.resolutions, p.tech_visits)
        top_ip, _, top_ip_share = top_item(p.ips, p.ip_visits)
        top_subnet, _, top_subnet_share = top_item(p.subnets, p.ip_visits)
        ipv6_share = p.ipv6_visits / p.ip_visits if p.ip_visits else 0.0
        tech_cov = min(1.0, p.tech_visits / p.visits) if p.visits else 0.0
        ip_cov = min(1.0, p.ip_visits / p.visits) if p.visits else 0.0
        flags = " · ".join(p.flags[:3]) or "критичных флагов не найдено"
        source_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", p.source.lower())
        rows.append(f'<tr data-risk="{p.risk}" data-source="{esc(p.source.lower())}"><td><a href="#{esc(source_id)}">{esc(p.source)}</a></td><td>{fmt_int(p.visits)}</td><td>{fmt_pct(p.bounce)}</td><td>{fmt_duration(p.duration)}</td><td>{fmt_pct(p.quality_conv, 3)}</td><td>{fmt_pct(p.primary_conv, 3)}</td><td><span class="pill {p.risk}">{risk_label(p.risk)}</span></td><td><b>{p.score}</b></td><td class="flags-cell">{esc(flags)}</td></tr>')
        cards.append(f'''<section class="source-card {p.risk}" id="{esc(source_id)}" data-risk="{p.risk}" data-source="{esc(p.source.lower())}">
<div class="source-head"><div><div class="eyebrow">UTM Source</div><h2>{esc(p.source)}</h2><p>{esc(flags)}</p></div><div class="score-badge {p.risk}"><span>{risk_label(p.risk)}</span><b>{p.score}/100</b><small>{esc(p.confidence)}</small></div></div>
<div class="metrics"><div><b>{fmt_int(p.visits)}</b><span>визиты</span></div><div><b>{fmt_pct(p.bounce)}</b><span>отказы</span></div><div><b>{fmt_duration(p.duration)}</b><span>время</span></div><div><b>{fmt_pct(p.new_share)}</b><span>новые</span></div><div><b>{fmt_pct(p.quality_conv, 3)}</b><span>ПК-звонки</span></div><div><b>{fmt_pct(p.primary_conv, 3)}</b><span>первичные звонки</span></div></div>
<div class="detail-grid"><div class="detail"><h3>IP / подсети</h3><p><b>IPv6:</b> {fmt_pct(ipv6_share)}</p><p><b>Топ IP:</b> {esc(top_ip)} · {fmt_pct(top_ip_share)}</p><p><b>Топ подсеть:</b> {esc(top_subnet)} · {fmt_pct(top_subnet_share)}</p><p class="coverage">Покрытие IP-среза: {fmt_pct(ip_cov)}</p></div>
<div class="detail"><h3>Технический профиль</h3><p><b>Браузер:</b> {esc(top_browser)} · {fmt_pct(top_browser_share)}</p><p><b>ОС:</b> {esc(top_os)} · {fmt_pct(top_os_share)}</p><p><b>Модель:</b> {esc(top_model)} · {fmt_pct(top_model_share)}</p><p><b>Разрешение:</b> {esc(top_res)} · {fmt_pct(top_res_share)}</p><p class="coverage">Покрытие техсреза: {fmt_pct(tech_cov)}</p></div>
<div class="detail wide"><h3>Аномальные комбинации</h3>{candidate_html(p)}</div><div class="detail wide action"><h3>Рекомендация</h3><p>{esc(p.recommendation)}</p></div></div></section>''')
    generated = text(meta.get("generated_at"))[:19].replace("T", " ")
    goals = meta.get("goals") or {}
    quality_id = ((goals.get("quality") or {}).get("id") or "—")
    primary_id = ((goals.get("primary") or {}).get("id") or "—")
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Антифрод PRG · 53197618 · май–июнь 2026</title><style>
:root{{--ink:#20233a;--muted:#777985;--line:#e7e2d8;--paper:#f7f4ef;--gold:#b28a58;--red:#b84545;--redbg:#fff1ef;--amber:#9b6a19;--amberbg:#fff7e6;--green:#317058;--greenbg:#eef8f2}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;font-family:Inter,Arial,sans-serif;color:var(--ink);background:linear-gradient(145deg,#fff,var(--paper));line-height:1.5}}a{{color:inherit}}.wrap{{max-width:1320px;margin:auto;padding:0 22px}}header{{background:radial-gradient(circle at 85% 0,#d9edf9 0,transparent 35%),linear-gradient(135deg,#fff,#f5f0e8);border-bottom:1px solid var(--line)}}.hero{{padding:46px 0 38px;display:grid;grid-template-columns:1.5fr .8fr;gap:28px;align-items:end}}.brand{{font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase;color:var(--gold)}}h1{{font-size:clamp(34px,5vw,64px);line-height:1.02;letter-spacing:-.045em;margin:14px 0 18px;max-width:880px}}.subtitle{{font-size:17px;color:var(--muted);max-width:820px}}.hero-note{{background:rgba(255,255,255,.78);border:1px solid var(--line);border-radius:24px;padding:22px;box-shadow:0 16px 50px rgba(32,35,58,.08)}}.hero-note b{{display:block;font-size:26px;margin-top:6px}}main{{padding:30px 0 70px}}.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:26px}}.kpi{{background:#fff;border:1px solid var(--line);border-radius:18px;padding:18px;min-height:112px}}.kpi b{{font-size:25px;display:block;letter-spacing:-.03em}}.kpi span{{font-size:12px;color:var(--muted)}}.panel{{background:#fff;border:1px solid var(--line);border-radius:26px;padding:26px;margin:18px 0;box-shadow:0 14px 45px rgba(32,35,58,.055)}}.panel h2{{font-size:28px;margin:0 0 8px;letter-spacing:-.03em}}.lead{{color:var(--muted);font-size:16px;max-width:1100px}}.method-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-top:22px}}.method{{border:1px solid var(--line);border-radius:18px;padding:18px;background:#fbfaf8}}.method i{{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;background:var(--ink);color:#fff;font-style:normal;font-weight:800}}.method h3{{margin:14px 0 6px;font-size:16px}}.method p{{margin:0;color:var(--muted);font-size:13px}}.signals{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:16px}}.signal{{padding:14px;border-radius:15px;background:#f5f3ef;font-size:13px}}.signal b{{display:block;margin-bottom:4px}}.summary-box{{padding:20px;border-left:4px solid var(--gold);background:#faf7f1;border-radius:0 18px 18px 0;font-size:17px}}.quality-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:16px}}.quality{{padding:16px;border:1px solid var(--line);border-radius:16px}}.quality b{{font-size:22px;display:block}}.quality small{{color:var(--muted)}}.controls{{display:flex;flex-wrap:wrap;gap:9px;margin:18px 0}}button{{border:1px solid var(--line);background:#fff;padding:10px 14px;border-radius:999px;font-weight:700;color:var(--ink);cursor:pointer}}button.active{{background:var(--ink);color:#fff;border-color:var(--ink)}}input{{flex:1;min-width:240px;border:1px solid var(--line);padding:11px 15px;border-radius:999px;font:inherit}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:18px}}table{{border-collapse:collapse;width:100%;min-width:1050px;background:#fff}}th,td{{padding:13px 14px;border-bottom:1px solid var(--line);font-size:13px;text-align:right;vertical-align:top}}th{{position:sticky;top:0;background:#f4f1eb;z-index:1;color:#5d5f6a;font-size:11px;text-transform:uppercase;letter-spacing:.05em}}th:first-child,td:first-child,th:last-child,td:last-child{{text-align:left}}td:first-child{{font-weight:800}}.flags-cell{{max-width:320px;color:var(--muted)}}.pill{{display:inline-block;border-radius:999px;padding:5px 9px;font-weight:800;font-size:11px;white-space:nowrap}}.pill.high{{background:var(--redbg);color:var(--red)}}.pill.medium{{background:var(--amberbg);color:var(--amber)}}.pill.low{{background:var(--greenbg);color:var(--green)}}.section-title{{margin:34px 0 15px;font-size:29px;letter-spacing:-.03em}}.source-card{{background:#fff;border:1px solid var(--line);border-left:5px solid var(--green);border-radius:24px;padding:24px;margin:15px 0;box-shadow:0 12px 35px rgba(32,35,58,.05);scroll-margin-top:18px}}.source-card.high{{border-left-color:var(--red)}}.source-card.medium{{border-left-color:var(--amber)}}.source-head{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start}}.source-head h2{{margin:3px 0;font-size:29px;letter-spacing:-.03em}}.source-head p{{margin:0;color:var(--muted)}}.eyebrow{{font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:var(--gold);font-weight:800}}.score-badge{{min-width:180px;text-align:right;padding:13px 15px;border-radius:17px;background:var(--greenbg);color:var(--green)}}.score-badge.high{{background:var(--redbg);color:var(--red)}}.score-badge.medium{{background:var(--amberbg);color:var(--amber)}}.score-badge span,.score-badge small{{display:block;font-size:11px}}.score-badge b{{display:block;font-size:25px}}.metrics{{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:20px 0}}.metrics>div{{padding:14px;background:#f7f5f1;border-radius:15px}}.metrics b{{display:block;font-size:19px}}.metrics span{{font-size:11px;color:var(--muted)}}.detail-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.detail{{border:1px solid var(--line);border-radius:17px;padding:17px}}.detail h3{{margin:0 0 10px;font-size:15px}}.detail p{{margin:5px 0;font-size:13px}}.detail.wide{{grid-column:1/-1}}.detail.action{{background:#f1f7f3;border-color:#d6e9dc}}.coverage{{color:var(--muted);font-size:11px!important;margin-top:10px!important}}.anomaly{{display:grid;grid-template-columns:1fr auto;gap:4px 16px;padding:11px 0;border-bottom:1px solid var(--line)}}.anomaly:last-child{{border-bottom:0}}.anomaly span{{font-size:12px;color:var(--muted)}}.anomaly small{{grid-column:1/-1;color:var(--red)}}.muted{{color:var(--muted)}}.foot{{margin-top:28px;padding:18px;border-radius:17px;background:#efede8;color:var(--muted);font-size:12px}}[hidden]{{display:none!important}}@media(max-width:1050px){{.hero{{grid-template-columns:1fr}}.kpis{{grid-template-columns:repeat(3,1fr)}}.method-grid{{grid-template-columns:repeat(2,1fr)}}.signals{{grid-template-columns:repeat(2,1fr)}}.metrics{{grid-template-columns:repeat(3,1fr)}}}}@media(max-width:650px){{.wrap{{padding:0 14px}}.hero{{padding:32px 0}}.kpis,.quality-grid,.method-grid,.signals,.metrics,.detail-grid{{grid-template-columns:1fr 1fr}}.panel,.source-card{{padding:18px}}.source-head{{display:block}}.score-badge{{margin-top:13px;text-align:left}}.detail.wide{{grid-column:auto}}.anomaly{{grid-template-columns:1fr}}}}
</style></head><body><header><div class="wrap hero"><div><div class="brand">Level Group · Antifraud</div><h1>Комплексная проверка PRG-трафика</h1><p class="subtitle">Счётчик 53197618 · 1 мая — 30 июня 2026 · фильтр UTM Campaign содержит “prg”. Анализ объединяет поведение, звонковые цели, IP/подсети и технический профиль по каждой площадке.</p></div><div class="hero-note"><span>Главный принцип</span><b>Не один показатель, а совпадение независимых признаков</b><small>Сформировано: {esc(generated)} UTC</small></div></div></header><main class="wrap">
<section class="kpis"><div class="kpi"><b>{fmt_int(total.visits)}</b><span>PRG-визиты</span></div><div class="kpi"><b>{fmt_pct(total.bounce)}</b><span>общий отказ</span></div><div class="kpi"><b>{fmt_duration(total.duration)}</b><span>среднее время</span></div><div class="kpi"><b>{fmt_pct(total.quality_conv,3)}</b><span>ПК-звонки</span></div><div class="kpi"><b>{fmt_pct(total.primary_conv,3)}</b><span>первичные звонки</span></div><div class="kpi"><b>{len(high)} / {len(medium)}</b><span>высокий / средний приоритет</span></div></section>
<section class="panel" id="methodology"><h2>Методика: как работает проверка</h2><p class="lead">Отчёт не маркирует трафик как фрод по одному отклонению. Для каждого UTM Source строится единая карточка, затем поведение и конверсии сопоставляются с IP-концентрацией и техническими комбинациями. Скоринг нужен для приоритизации ручной проверки, а не для автоматического обвинения площадки.</p><div class="method-grid"><div class="method"><i>1</i><h3>Берём площадку целиком</h3><p>Визиты, отказы, время, новые посетители и две звонковые цели.</p></div><div class="method"><i>2</i><h3>Добавляем IP-профиль</h3><p>Доля IPv6, концентрация на одном IP и подсетях /24 или /64.</p></div><div class="method"><i>3</i><h3>Добавляем технику</h3><p>ОС, браузер, модель, разрешение и аномальные комбинации внутри площадки.</p></div><div class="method"><i>4</i><h3>Складываем признаки</h3><p>Высокий приоритет появляется, когда совпадают несколько независимых красных флагов.</p></div></div><div class="signals"><div class="signal"><b>Поведение</b>Отказы и время относительно общего PRG.</div><div class="signal"><b>Конверсии</b>Первичные и первично-качественные звонки UIS.</div><div class="signal"><b>IP</b>Один IP, подсеть, IPv4/IPv6.</div><div class="signal"><b>Техника</b>Headless, unknown, однотипные браузеры и устройства.</div><div class="signal"><b>Объём</b>На малых выборках вывод автоматически ограничивается.</div></div></section>
<section class="panel"><h2>Общий вывод</h2><div class="summary-box">{esc(overall)}</div><div class="quality-grid"><div class="quality"><b>{fmt_pct(high_visits/total_visits if total_visits else 0)}</b><small>доля high-priority трафика</small></div><div class="quality"><b>{fmt_pct(tech_coverage)}</b><small>покрытие техвыгрузки по визитам</small></div><div class="quality"><b>{fmt_pct(ip_coverage)}</b><small>покрытие IP-выгрузки по визитам</small></div></div><p class="muted">API-выгрузки сформированы без семплирования. Цели: первично-качественные звонки UIS — ID {quality_id}; первичные звонки UIS — ID {primary_id}. Если покрытие среза ниже 100%, это отдельно учтено в уверенности вывода.</p></section>
<section class="panel"><h2>Рейтинг площадок</h2><p class="lead">Фильтры меняют одновременно таблицу и карточки ниже.</p><div class="controls"><button class="active" data-filter="all">Все</button><button data-filter="high">Высокий</button><button data-filter="medium">Средний</button><button data-filter="low">Низкий</button><input id="search" placeholder="Поиск по UTM Source"></div><div class="table-wrap"><table><thead><tr><th>Источник</th><th>Визиты</th><th>Отказы</th><th>Время</th><th>ПК-звонки</th><th>Первичные</th><th>Приоритет</th><th>Скоринг</th><th>Основные флаги</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section><h2 class="section-title">Комплексный анализ по каждой площадке</h2>{''.join(cards)}<div class="foot">Период: 01.05.2026–30.06.2026. Счётчик: 53197618. Фильтр: UTM Campaign содержит “prg”. Источники данных: три API-выгрузки Яндекс Метрики — агрегат по UTM Source, UTM Source + IP и UTM Source + ОС/браузер/модель/разрешение. Скоринг является аналитической приоритизацией и не заменяет верификацию по плейсментам, рекламным логам и данным площадки.</div></main><script>const buttons=[...document.querySelectorAll('button[data-filter]')];const search=document.getElementById('search');let active='all';function apply(){{const q=search.value.trim().toLowerCase();document.querySelectorAll('[data-risk][data-source]').forEach(el=>{{const okRisk=active==='all'||el.dataset.risk===active;const okText=!q||el.dataset.source.includes(q);el.hidden=!(okRisk&&okText)}})}}buttons.forEach(btn=>btn.addEventListener('click',()=>{{buttons.forEach(b=>b.classList.remove('active'));btn.classList.add('active');active=btn.dataset.filter;apply()}}));search.addEventListener('input',apply);</script></body></html>'''


def main() -> None:
    for required in (SOURCE_FILE, TECH_FILE, IP_FILE, META_FILE):
        if not required.exists():
            raise FileNotFoundError(required)
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    source_rows = read_csv(SOURCE_FILE)
    tech_rows = read_csv(TECH_FILE)
    ip_rows = read_csv(IP_FILE)
    profiles: dict[str, SourceProfile] = {}
    total = SourceProfile(source="Итого")
    for row in source_rows:
        source = text(row.get("UTM Source")) or "Не определено"
        target = total if is_total_row(row) else profiles.setdefault(source, SourceProfile(source=source))
        target.visits = int(round(number(row.get("Визиты"))))
        target.users = int(round(number(row.get("Посетители"))))
        target.bounce = number(row.get("Отказы"))
        target.duration = parse_duration(row.get("Время на сайте"))
        target.new_share = number(row.get("Доля новых посетителей"))
        target.quality_conv = number(row.get("Конверсия (Первично-качественные звонки UIS)"))
        target.primary_conv = number(row.get("Конверсия (Первичные звонки UIS)"))
    if total.visits <= 0:
        totals = meta.get("totals") or []
        total.visits, total.users = int(totals[0]), int(totals[1])
        total.bounce, total.duration = float(totals[2]), parse_duration(totals[3])
        total.new_share, total.quality_conv, total.primary_conv = float(totals[4]), float(totals[5]), float(totals[6])
    weighted_candidates(profiles, tech_rows)
    for row in ip_rows:
        if is_total_row(row):
            continue
        source = text(row.get("UTM Source")) or "Не определено"
        p = profiles.setdefault(source, SourceProfile(source=source))
        ip_value = text(row.get("IP-адрес")) or "Не определено"
        visits = int(round(number(row.get("Визиты"))))
        if visits <= 0:
            continue
        p.ip_visits += visits; p.ips[ip_value] += visits; p.subnets[subnet_key(ip_value)] += visits
        try:
            if ipaddress.ip_address(ip_value).version == 6:
                p.ipv6_visits += visits
        except ValueError:
            pass
    for p in profiles.values():
        score_profile(p, total)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(list(profiles.values()), total, meta), encoding="utf-8")
    print(json.dumps({"output": str(OUT), "sources": len(profiles), "visits": total.visits}, ensure_ascii=False))


if __name__ == "__main__":
    main()

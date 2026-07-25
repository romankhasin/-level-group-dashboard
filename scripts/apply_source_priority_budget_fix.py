#!/usr/bin/env python3
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "index.html"
UPDATE = ROOT / "scripts" / "update_data.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_replace(text: str, pattern: str, replacement: str, label: str, expected: int | None = None) -> str:
    updated, count = re.subn(pattern, replacement, text, flags=re.S)
    if expected is not None and count != expected:
        raise RuntimeError(f"{label}: expected {expected} matches, found {count}")
    if expected is None and count < 1:
        raise RuntimeError(f"{label}: no matches")
    return updated


def patch_index() -> None:
    text = INDEX.read_text(encoding="utf-8")

    old_coverage = '''    function googleVerifierCoverageKeys() {
      return new Set(
        (AUTOMATIC_GOOGLE_VERIFIER_ROWS || [])
          .filter(isYandexMtsPrgVerifierRow)
          .map(verifierSourceRowKey)
          .filter(Boolean)
      );
    }
'''
    new_coverage = '''    function googleVerifierRowHasMediaFacts(row) {
      return ["impressions", "clicks", "cost"]
        .some(field => parseNumber((row || {})[field]) !== 0);
    }

    function googleVerifierCoverageKeys() {
      return new Set(
        (AUTOMATIC_GOOGLE_VERIFIER_ROWS || [])
          .filter(row => isYandexMtsPrgVerifierRow(row) && googleVerifierRowHasMediaFacts(row))
          .map(verifierSourceRowKey)
          .filter(Boolean)
      );
    }
'''
    if "function googleVerifierRowHasMediaFacts" not in text:
        text = replace_once(text, old_coverage, new_coverage, "Google coverage helper")

    old_merge = '''      // For Yandex/MTS PRG, the existence of a Google row always wins for
      // the exact date + technical_mark, including rows with zero values.
      // Target Ads is used only when that Google row is absent.
      (AUTOMATIC_GOOGLE_VERIFIER_ROWS || []).forEach((row, index) => {
        const key = verifierSourceRowKey(row) || `google||${index}`;
        if (isYandexMtsPrgVerifierRow(row) || !merged.has(key)) {
          merged.set(key, row);
        }
      });
'''
    new_merge = '''      // For Yandex/MTS PRG, a non-zero Google row wins for the exact
      // date + technical_mark. A zero Google row falls back to Target Ads.
      // The Map keeps only one row per date + campaign, so sources never sum.
      (AUTOMATIC_GOOGLE_VERIFIER_ROWS || []).forEach((row, index) => {
        const key = verifierSourceRowKey(row) || `google||${index}`;
        const googleHasMediaFacts = googleVerifierRowHasMediaFacts(row);
        if ((isYandexMtsPrgVerifierRow(row) && googleHasMediaFacts) || !merged.has(key)) {
          merged.set(key, row);
        }
      });
'''
    if old_merge in text:
        text = replace_once(text, old_merge, new_merge, "Browser merge priority")
    elif "const googleHasMediaFacts = googleVerifierRowHasMediaFacts(row);" not in text:
        raise RuntimeError("Browser merge priority: expected source block not found")

    helper_anchor = '''    function parseVerifierRows(rawRows) {
'''
    actual_cost_helper = '''    function verifierRowHasActualCost(row) {
      if (Object.prototype.hasOwnProperty.call(row || {}, "has_actual_cost")) {
        return Boolean(row.has_actual_cost);
      }
      return isGoogleVerifierSource(row);
    }

    function parseVerifierRows(rawRows) {
'''
    if "function verifierRowHasActualCost" not in text:
        text = replace_once(text, helper_anchor, actual_cost_helper, "Actual-cost helper")

    actual_cost_pattern = (
        r'Object\.prototype\.hasOwnProperty\.call\(row, "has_actual_cost"\)'
        r'\s*\?\s*Boolean\(row\.has_actual_cost\)'
        r'\s*:\s*Boolean\((?:costColumn|costCol)\)'
    )
    text, count = re.subn(actual_cost_pattern, "verifierRowHasActualCost(row)", text, flags=re.S)
    if count not in {0, 3}:
        raise RuntimeError(f"Actual-cost inference: expected 3 or 0 replacements, found {count}")
    if count == 0 and text.count("verifierRowHasActualCost(row)") < 3:
        raise RuntimeError("Actual-cost inference: patched calls are missing")

    INDEX.write_text(text, encoding="utf-8")


def patch_update_script() -> None:
    text = UPDATE.read_text(encoding="utf-8")

    helper_anchor = '''def merge_verifier_rows(targetads_rows: list[dict], google_rows: list[dict]) -> list[dict]:
'''
    helper = '''def google_row_has_media_facts(row: dict) -> bool:
    return any(number(row.get(metric)) != 0 for metric in ("impressions", "clicks", "cost"))


def merge_verifier_rows(targetads_rows: list[dict], google_rows: list[dict]) -> list[dict]:
'''
    if "def google_row_has_media_facts" not in text:
        text = replace_once(text, helper_anchor, helper, "Backend media-facts helper")

    text = text.replace(
        '    """Merge media facts with strict Google priority for Yandex/MTS PRG.\n\n'
        '    For an exact date + campaign, any matching Google row wins, including a\n'
        '    row containing zero values. Target Ads remains the fallback only when the\n'
        '    Google row is absent. Google rows outside Yandex/MTS PRG are used only when\n'
        '    Target Ads has no matching row.\n'
        '    """',
        '    """Merge media facts without duplicating Google and Target Ads.\n\n'
        '    For Yandex/MTS PRG, a matching Google row wins only when it contains\n'
        '    non-zero media facts. When Google is zero or absent, Target Ads remains\n'
        '    the fallback. Every date + campaign key produces exactly one row.\n'
        '    """',
    )

    old_condition = '        if is_yandex_mts_prg_campaign(campaign) or key not in merged:\n            merged[key] = row\n'
    new_condition = '        if (is_yandex_mts_prg_campaign(campaign) and google_row_has_media_facts(row)) or key not in merged:\n            merged[key] = row\n'
    if old_condition in text:
        text = replace_once(text, old_condition, new_condition, "Backend merge priority")
    elif new_condition not in text:
        raise RuntimeError("Backend merge priority: expected condition not found")

    UPDATE.write_text(text, encoding="utf-8")


def main() -> None:
    patch_index()
    patch_update_script()
    print("Source priority and budget logic patched")


if __name__ == "__main__":
    main()

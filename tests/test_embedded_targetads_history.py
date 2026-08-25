import calendar
import csv
import datetime as dt
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EmbeddedTargetAdsHistoryTests(unittest.TestCase):
    def test_embedded_history_matches_declared_period_and_schema(self):
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        match = re.search(
            r'const EMBEDDED_TARGETADS_HISTORY_URL = "([^"]+)";',
            html,
        )
        self.assertIsNotNone(match)

        relative_path = match.group(1)
        period = re.search(
            r"targetads_history_(\d{4})-(\d{2})_(\d{4})-(\d{2})\.csv$",
            relative_path,
        )
        self.assertIsNotNone(period)

        start_year, start_month, end_year, end_month = map(int, period.groups())
        expected_start = dt.date(start_year, start_month, 1)
        expected_end = dt.date(
            end_year,
            end_month,
            calendar.monthrange(end_year, end_month)[1],
        )

        path = ROOT / relative_path
        self.assertTrue(path.is_file())

        required = {
            "interaction_dt",
            "placement_nm",
            "impressions",
            "clicks",
            "givt",
            "fraud_impressions",
        }
        dates = []
        rows = 0
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertTrue(required.issubset(set(reader.fieldnames or [])))
            for row in reader:
                rows += 1
                dates.append(dt.date.fromisoformat(row["interaction_dt"]))
                for field in ("impressions", "clicks", "givt", "fraud_impressions"):
                    float(row[field] or 0)

        self.assertGreater(rows, 0)
        self.assertEqual(min(dates), expected_start)
        self.assertEqual(max(dates), expected_end)
        self.assertTrue(all(expected_start <= value <= expected_end for value in dates))


if __name__ == "__main__":
    unittest.main()

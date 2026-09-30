import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import update_data
import update_data_auto


class CompactDashboardDataTests(unittest.TestCase):
    def test_round_trip_preserves_values_and_missing_fields(self):
        rows = [
            {"interaction_dt": "2026-09-25", "source": "Target Ads", "impressions": 12, "note": None},
            {"interaction_dt": "2026-09-26", "source": "Target Ads", "impressions": 0},
        ]

        compact = update_data.compact_rows(rows)

        self.assertEqual(update_data.expand_compact_rows(compact), rows)
        self.assertEqual(update_data.expand_compact_rows(rows), rows)
        self.assertEqual(update_data.compact_row_count(compact), 2)

    def test_invalid_compact_data_cannot_erase_baseline(self):
        with self.assertRaises(ValueError):
            update_data.expand_compact_rows({"columns": ["source"], "data": [[2]], "dictionaries": {"source": ["Target Ads"]}})

    def test_incremental_targetads_keeps_compact_baseline(self):
        previous_rows = [
            {"interaction_dt": "2026-09-25", "source": "Target Ads", "impressions": 12},
            {"interaction_dt": "2026-09-26", "source": "Target Ads", "impressions": 13},
        ]
        fresh_rows = [{"interaction_dt": "2026-09-27", "source": "Target Ads", "impressions": 14}]
        previous_latest = {
            "period": {"to": "2026-09-26"},
            "verifierRows": update_data.compact_rows(previous_rows),
            "status": {"targetads": {"enabled": True, "mode": "automatic_raw_v2_incremental"}},
        }

        with (
            patch.object(update_data, "targetads_project_id", return_value=12787),
            patch.object(update_data, "validate_targetads_token"),
            patch.object(update_data, "fetch_targetads_metadata", return_value=({}, {})),
            patch.object(update_data, "fetch_targetads_period", return_value=(fresh_rows, {"jobs": []})),
        ):
            rows, status = update_data_auto.targetads_incremental_rows(
                "test-token", dt.date(2026, 9, 27), previous_latest
            )

        self.assertEqual(rows, [*previous_rows, *fresh_rows])
        self.assertEqual(status["from"], "2026-09-27")
        self.assertEqual(status["baseline_rows"], 2)


if __name__ == "__main__":
    unittest.main()

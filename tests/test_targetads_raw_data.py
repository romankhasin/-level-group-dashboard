import gzip
import io
import unittest
from unittest.mock import patch

from scripts import update_data


class TargetAdsRawDataTests(unittest.TestCase):
    def test_aggregates_gzip_csv_by_date_and_placement(self):
        source = (
            "InteractionDate,InteractionPlacementId\n"
            "2026-07-01,42\n"
            "2026-07-01,42\n"
            "2026-07-02,99\n"
        ).encode()
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode="wb") as archive:
            archive.write(source)

        with patch.object(update_data.urllib.request, "urlopen", return_value=io.BytesIO(compressed.getvalue())):
            result = update_data.aggregate_targetads_csv("https://example.test/report.csv.gz", "Impression")

        self.assertEqual(
            result,
            {
                ("2026-07-01", "42"): 2,
                ("2026-07-02", "99"): 1,
            },
        )

    def test_raw_data_windows_do_not_exceed_three_calendar_days(self):
        windows = list(
            update_data.date_chunks(
                update_data.dt.date(2026, 7, 1),
                update_data.dt.date(2026, 7, 8),
                days=3,
            )
        )

        self.assertEqual(
            windows,
            [
                (update_data.dt.date(2026, 7, 1), update_data.dt.date(2026, 7, 3)),
                (update_data.dt.date(2026, 7, 4), update_data.dt.date(2026, 7, 6)),
                (update_data.dt.date(2026, 7, 7), update_data.dt.date(2026, 7, 8)),
            ],
        )


if __name__ == "__main__":
    unittest.main()

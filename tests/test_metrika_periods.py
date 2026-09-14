import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

from scripts import update_data


class MetrikaPeriodTests(unittest.TestCase):
    def test_fetch_metrika_period_reads_every_api_page(self):
        pages = [
            {
                "total_rows": 3,
                "data": [
                    self.api_row("2026-09-01", "a_sep26_prg", 2),
                    self.api_row("2026-09-02", "b_sep26_prg", 3),
                ],
            },
            {
                "total_rows": 3,
                "data": [self.api_row("2026-09-03", "c_sep26_prg", 4)],
            },
        ]

        with patch.object(update_data, "request_json", side_effect=pages) as request:
            rows = update_data.fetch_metrika_period(
                "token",
                100470605,
                update_data.dt.date(2026, 9, 1),
                update_data.dt.date(2026, 9, 3),
            )

        self.assertEqual(sum(row["Визиты"] for row in rows), 9)
        offsets = [
            urllib.parse.parse_qs(urllib.parse.urlsplit(call.args[0]).query)["offset"][0]
            for call in request.call_args_list
        ]
        self.assertEqual(offsets, ["1", "3"])

    def test_volga_backfill_does_not_expand_unrelated_counter_ranges(self):
        yesterday = update_data.dt.date(2026, 9, 13)
        existing = []
        for counter_id in update_data.COUNTER_IDS:
            campaign = "lvol_yandex_msk_banner_sep26_sale_none_prg" if counter_id == 53197618 else "sp_yandex_msk_banner_sep26_sale_none_prg"
            existing.append(
                {
                    "counter_id": counter_id,
                    "Дата визита": "2026-09-10",
                    "UTM Campaign": campaign,
                    "Посадочная": update_data.COUNTER_CONFIGS[counter_id].get("landing_host", "level.ru"),
                    "Тип устройства": "desktop",
                    "Визиты": 1,
                    "Отказы": 0,
                    "Время на сайте": 10,
                    update_data.METRIKA_QUALITY_CALL_FIELD: 0,
                }
            )

        with tempfile.TemporaryDirectory() as directory:
            history_path = Path(directory) / "metrika.json"
            history_path.write_text(json.dumps({"rows": existing}), encoding="utf-8")
            with (
                patch.object(update_data, "METRIKA_HISTORY_PATH", history_path),
                patch.object(update_data, "fetch_metrika_period", return_value=[]),
            ):
                _, status = update_data.update_metrika("token", yesterday)

        for counter_id in update_data.COUNTER_IDS:
            self.assertEqual(status["ranges"][str(counter_id)]["from"], "2026-09-11")
            self.assertEqual(status["ranges"][str(counter_id)]["to"], "2026-09-13")

    @staticmethod
    def api_row(date, campaign, visits):
        return {
            "dimensions": [
                {"name": date},
                {"name": campaign},
                {"name": "https://level.ru/path"},
                {"name": "desktop"},
            ],
            "metrics": [visits, 20.0, 45.0, 0],
        }


if __name__ == "__main__":
    unittest.main()

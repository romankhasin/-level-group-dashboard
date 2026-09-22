import unittest

from scripts import update_data


class MetrikaCounterConfigurationTests(unittest.TestCase):
    def test_main_report_uses_all_five_counters(self):
        expected = (53197618, 100470605, 110064588, 110064048, 102348376)

        self.assertEqual(update_data.METRIKA_COUNTER_IDS, expected)
        self.assertEqual(update_data.COUNTER_IDS, expected)
        self.assertEqual(tuple(update_data.COUNTER_CONFIGS), expected)

    def test_startup_summary_exposes_counter_ids(self):
        summary = update_data.build_startup_summary(
            {
                "generatedAt": "2026-09-22T00:00:00Z",
                "rawRows": [],
                "verifierRows": [],
                "status": {
                    "metrika": {
                        "ranges": {
                            "53197618": {},
                            "100470605": {},
                            "110064588": {},
                            "110064048": {},
                            "102348376": {},
                        }
                    }
                },
            }
        )

        self.assertEqual(
            summary["status"]["metrika"]["counterIds"],
            [53197618, 100470605, 102348376, 110064048, 110064588],
        )


if __name__ == "__main__":
    unittest.main()

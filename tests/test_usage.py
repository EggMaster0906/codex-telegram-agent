from __future__ import annotations

import unittest

from app.usage import (
    ProviderUsage,
    build_usage_message,
    parse_antigravity_usage_output,
    parse_codex_usage_response,
)


class UsageTests(unittest.TestCase):
    def test_parses_codex_remaining_percentage(self) -> None:
        report = parse_codex_usage_response(
            {
                "id": 2,
                "result": {
                    "rateLimits": {},
                    "rateLimitsByLimitId": {
                        "codex": {
                            "primary": {
                                "usedPercent": 22,
                                "windowDurationMins": 10_080,
                                "resetsAt": 1_800_000_000,
                            },
                            "secondary": {
                                "usedPercent": 40,
                                "windowDurationMins": 300,
                                "resetsAt": 1_700_000_000,
                            },
                        }
                    },
                },
            }
        )

        self.assertEqual(report.provider, "Codex")
        self.assertEqual(report.groups[0].windows[0].label, "每週")
        self.assertEqual(report.groups[0].windows[0].remaining_percent, 78)
        self.assertEqual(report.groups[0].windows[1].label, "5 小時")
        self.assertEqual(report.groups[0].windows[1].remaining_percent, 60)

    def test_falls_back_to_legacy_codex_rate_limit_view(self) -> None:
        report = parse_codex_usage_response(
            {
                "result": {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 10,
                            "windowDurationMins": 60,
                        }
                    }
                }
            }
        )

        self.assertEqual(report.groups[0].windows[0].label, "1 小時")
        self.assertEqual(report.groups[0].windows[0].remaining_percent, 90)

    def test_parses_antigravity_tui_output(self) -> None:
        report = parse_antigravity_usage_output(
            "\x1b[2JGEMINI MODELS\r\n"
            "Weekly Limit\r\n[████] 82.50%\r\nQuota available\r\n"
            "Five Hour Limit\r\n[████] 70.00%\r\nQuota available\r\n"
            "CLAUDE AND GPT MODELS\r\n"
            "Weekly Limit\r\n[████] 64.00%\r\nQuota available\r\n"
            "Five Hour Limit\r\nDisabled\r\n"
        )

        self.assertEqual(report.provider, "Antigravity")
        self.assertEqual(report.groups[0].name, "Gemini 模型")
        self.assertEqual(report.groups[0].windows[0].remaining_percent, 82.5)
        self.assertEqual(report.groups[1].name, "Claude 與 GPT 模型")
        self.assertIsNone(report.groups[1].windows[1].remaining_percent)
        self.assertEqual(report.groups[1].windows[1].status, "Disabled")

    def test_formats_partial_failure_and_reset_time(self) -> None:
        codex = parse_codex_usage_response(
            {
                "result": {
                    "rateLimits": {
                        "primary": {
                            "usedPercent": 25,
                            "windowDurationMins": 10_080,
                            "resetsAt": 1_800_000_000,
                        }
                    }
                }
            }
        )
        antigravity = parse_antigravity_usage_output(
            "GEMINI MODELS\nWeekly Limit\n100.00%\nQuota available"
        )

        message = build_usage_message(codex, antigravity)

        self.assertIn("Codex", message)
        self.assertIn("每週：剩餘 75%", message)
        self.assertIn("重設", message)
        self.assertIn("Antigravity", message)
        self.assertIn("Gemini 模型", message)
        self.assertIn("每週：剩餘 100%", message)

    def test_formats_one_provider_failure_without_hiding_other_usage(self) -> None:
        codex = ProviderUsage("Codex", error="查詢逾時")
        antigravity = parse_antigravity_usage_output(
            "GEMINI MODELS\nWeekly Limit\n50.00%\nQuota available"
        )

        message = build_usage_message(codex, antigravity)

        self.assertIn("Codex\n  無法取得：查詢逾時", message)
        self.assertIn("Antigravity", message)
        self.assertIn("每週：剩餘 50%", message)


if __name__ == "__main__":
    unittest.main()

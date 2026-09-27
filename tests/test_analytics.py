"""Dashboard number tests on an in-memory database.

    python -m unittest discover -s tests -v
"""

import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analytics  # noqa: E402
import job_logger as jl  # noqa: E402

TODAY = date(2026, 9, 27)  # a Sunday


def ago(days):
    return TODAY - timedelta(days=days)


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.db = jl.Database(sqlite3.connect(":memory:"), "sqlite", "test")
        with mock.patch("builtins.print"):
            jl.ensure_schema(self.db, assume_yes=True)
        cache = {}
        rows = [  # company, days ago, channel, resume, [(status, days ago)]
            ("Offer Co", 30, "LinkedIn", "Data", [("Phone Screen", 25), ("Interview", 20), ("Offer", 10)]),
            ("Skipped Screen", 28, "Referral", "Data", [("Interview", 20)]),
            ("Rejected Co", 26, "LinkedIn", "General", [("Rejected", 20)]),
            ("Quiet Co", 25, "Indeed", "General", []),
            ("Ghosted Co", 24, "Indeed", "General", [("Ghosted", 2)]),
            ("Teacher Demo", 10, "Referral", "Teaching", [("Demo lesson", 5)]),
            ("Viewed Only", 3, "LinkedIn", "Data", [("Viewed", 1)]),
            ("New Co", 0, None, None, []),
        ]
        for company, days, channel, resume, events in rows:
            app_id = jl.insert_application(self.db, {
                "company_name": company, "role_title": "Analyst", "date_applied": ago(days), "channel": channel,
                "resume_version_id": jl.resolve_resume_version(self.db, resume, cache) if resume else None,
            })
            for status, when in events:
                jl.add_status(self.db, app_id, status, ago(when))
        apps, events = analytics.load(self.db)
        self.summary = analytics.summarize(apps, events, today=TODAY).set_index("company")

    def test_current_status_and_replies(self):
        s = self.summary
        self.assertEqual(s.loc["Offer Co", "status"], "Offer")
        self.assertEqual(s.loc["New Co", "status"], "Applied")
        # Rejections and your own reply stages count as responses; Viewed and Ghosted don't
        replied = set(s.index[s["replied"]])
        self.assertEqual(replied, {"Offer Co", "Skipped Screen", "Rejected Co", "Teacher Demo"})
        self.assertEqual(s.loc["Offer Co", "days_to_reply"], 5)

    def test_funnel_counts_later_stages_as_passing_earlier_ones(self):
        funnel = analytics.funnel(self.summary.reset_index()).set_index("stage")["applications"]
        # Skipped Screen went straight to Interview; Teacher Demo's 'Demo lesson' is a first-reply stage
        self.assertEqual(funnel.to_dict(), {"Applied": 8, "Phone Screen": 3, "Interview": 2, "Offer": 1})

    def test_no_word_list(self):
        quiet = analytics.waiting(self.summary.reset_index())
        self.assertEqual(list(quiet["company"]), ["Quiet Co"])  # Ghosted Co has an update, New Co is recent

    def test_kpis(self):
        numbers = analytics.kpis(self.summary.reset_index(), today=TODAY)
        self.assertEqual(numbers["applications"], 8)
        self.assertEqual(numbers["this_week"], 2)  # Viewed Only (Thursday) and New Co (Sunday)
        self.assertAlmostEqual(numbers["response_rate"], 4 / 8)
        self.assertAlmostEqual(numbers["interview_rate"], 2 / 8)
        self.assertEqual(numbers["no_word"], 1)

    def test_weekly_fills_empty_weeks(self):
        weeks = analytics.weekly(self.summary.reset_index())
        self.assertEqual(list(weeks["week"].dt.weekday.unique()), [0])  # weeks start Monday
        self.assertEqual(weeks["applications"].sum(), 8)
        self.assertEqual(weeks["running_total"].iloc[-1], 8)
        self.assertEqual(len(weeks), 5)  # Aug 24 week through Sep 21 week, including any empty weeks

    def test_rates_by_channel(self):
        rates = analytics.rates_by(self.summary.reset_index(), "channel").set_index("channel")
        self.assertEqual(rates.loc["Referral", "response_rate"], 1.0)
        self.assertEqual(rates.loc["(not set)", "applications"], 1)

    def test_empty_database(self):
        db = jl.Database(sqlite3.connect(":memory:"), "sqlite", "empty")
        jl.apply_schema_updates(db)
        apps, events = analytics.load(db)
        summary = analytics.summarize(apps, events, today=TODAY)
        self.assertEqual(analytics.kpis(summary, today=TODAY)["applications"], 0)
        self.assertTrue(analytics.weekly(summary).empty)


if __name__ == "__main__":
    unittest.main()

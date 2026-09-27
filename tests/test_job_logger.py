"""Tests run on an in-memory SQLite database, so they never touch your MySQL data.

    python -m unittest discover -s tests -v
"""

import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import job_logger as jl  # noqa: E402

TODAY = date(2026, 9, 27)


def memory_db():
    db = jl.Database(sqlite3.connect(":memory:"), "sqlite", "test database")
    db.conn.execute("PRAGMA foreign_keys = ON")
    with mock.patch("builtins.print"):
        jl.ensure_schema(db, assume_yes=True)
    return db


def application(**changes):
    app = {"company_name": "Acme Health", "role_title": "Staff Nurse", "location": "Baltimore, MD",
           "date_applied": date(2026, 9, 20), "channel": "Indeed", "resume_version_id": None,
           "job_posting_url": "https://acme.example/jobs/1", "industry": "Healthcare",
           "salary_listed": "$38-$45/hr", "notes": None}
    app.update(changes)
    return app


class ParsingTests(unittest.TestCase):
    def test_parse_date(self):
        cases = {
            "": TODAY, "today": TODAY, "yesterday": date(2026, 9, 26), "3 days ago": date(2026, 9, 24),
            "2026-09-20": date(2026, 9, 20), "9/20/2026": date(2026, 9, 20), "9/20/26": date(2026, 9, 20),
            "9/20": date(2026, 9, 20), "12/31": date(2025, 12, 31), "2026-09-20 00:00:00": date(2026, 9, 20),
            "Sep 20, 2026": date(2026, 9, 20),
        }
        for text, expected in cases.items():
            self.assertEqual(jl.parse_date(text, TODAY), expected, text)
        self.assertIsNone(jl.parse_date("someday", TODAY))
        self.assertIsNone(jl.parse_date("2/30", TODAY))

    def test_normalize_url(self):
        self.assertEqual(jl.normalize_url("https://www.linkedin.com/jobs/view/123/?trk=abc&refId=x#top"),
                         "https://linkedin.com/jobs/view/123")
        self.assertEqual(jl.normalize_url("linkedin.com/jobs/view/123"), "https://linkedin.com/jobs/view/123")
        # parameters that identify the job itself must survive
        self.assertIn("currentJobId=42",
                      jl.normalize_url("https://www.linkedin.com/jobs/search/?currentJobId=42&utm_source=x"))
        self.assertIn("opportunityId=abc",
                      jl.normalize_url("https://recruiting.ultipro.com/x/Detail?opportunityId=abc&utm_source=bandana"))

    def test_guess_channel(self):
        self.assertEqual(jl.guess_channel("https://www.linkedin.com/jobs/view/1"), "LinkedIn")
        self.assertEqual(jl.guess_channel("https://bbinsurance.wd1.myworkdayjobs.com/en-US/Careers/job/1"),
                         "Company site")
        self.assertEqual(jl.guess_channel("bandana.com/jobs/1"), "Bandana")
        self.assertIsNone(jl.guess_channel("https://example.org/careers"))
        self.assertIsNone(jl.guess_channel(""))

    def test_normalize_status(self):
        self.assertEqual(jl.normalize_status("Submitted"), "Applied")
        self.assertEqual(jl.normalize_status(""), "Applied")
        self.assertEqual(jl.normalize_status("phone screen"), "Phone Screen")
        self.assertEqual(jl.normalize_status("Demo lesson"), "Demo lesson")

    def test_load_env(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text('﻿# comment\nJL_TEST_A="quoted value"\nJL_TEST_B=has=equals\n\n'
                                'JL_TEST_C=from file\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"JL_TEST_C": "already set"}):
                jl.load_env(env_file)
                self.assertEqual(os.environ["JL_TEST_A"], "quoted value")
                self.assertEqual(os.environ["JL_TEST_B"], "has=equals")
                self.assertEqual(os.environ["JL_TEST_C"], "already set")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.db = memory_db()

    def test_insert_writes_applied_status_and_location(self):
        app_id = jl.insert_application(self.db, application())
        events = self.db.query("SELECT status, event_date FROM status_events WHERE application_id = %s", (app_id,))
        self.assertEqual(events, [("Applied", "2026-09-20")])
        self.assertEqual(self.db.query("SELECT location FROM applications")[0][0], "Baltimore, MD")

    def test_location_kept_in_notes_on_older_tables(self):
        self.db.has_location = False
        jl.insert_application(self.db, application(notes="referred by Sam"))
        self.assertEqual(self.db.query("SELECT notes FROM applications")[0][0],
                         "referred by Sam; Location: Baltimore, MD")

    def test_ensure_schema_adds_location_to_an_older_table(self):
        db = jl.Database(sqlite3.connect(":memory:"), "sqlite", "old database")
        for statement in jl.schema_statements("sqlite"):
            db.execute(statement.replace("location VARCHAR(255),", ""))
        self.assertFalse(db.has_column("applications", "location"))
        with mock.patch("builtins.print"):
            jl.ensure_schema(db, assume_yes=True)
        self.assertTrue(db.has_location)
        self.assertTrue(db.has_column("applications", "location"))

    def test_backfill_applied_events(self):
        self.db.execute("INSERT INTO applications (company_name, role_title, date_applied) VALUES (%s, %s, %s)",
                        ("Old Co", "Analyst", date(2026, 9, 1)))
        self.db.commit()
        self.assertEqual(jl.backfill_applied_events(self.db, assume_yes=True), 1)
        self.assertEqual(jl.backfill_applied_events(self.db, assume_yes=True), 0)
        self.assertEqual(self.db.query("SELECT event_date FROM status_events"), [("2026-09-01",)])

    def test_duplicates(self):
        jl.insert_application(self.db, application())
        existing = jl.load_existing(self.db)
        # same link, with tracking junk added
        self.assertIsNotNone(jl.match_existing(existing, "Other", "Other", "https://www.acme.example/jobs/1/?utm_source=x"))
        # same company and role, no link
        self.assertIsNotNone(jl.match_existing(existing, "ACME HEALTH", "staff nurse", ""))
        # same company and role but a different posting link: a different job
        self.assertIsNone(jl.match_existing(existing, "Acme Health", "Staff Nurse", "https://acme.example/jobs/2"))

    def test_current_status_is_the_latest_event(self):
        app_id = jl.insert_application(self.db, application())
        jl.add_status(self.db, app_id, "Interview", date(2026, 9, 25))
        self.assertEqual(self.db.query(jl.CURRENT_STATUS_SQL)[0][4], "Interview")


SEMICOLON_LOG = '''company_name;role_title;date_applied;channel;job_posting_url;industry;salary_listed;status;notes
"Northwind Analytics";"Business Intelligence Analyst";"2026-09-26";"Lever";"https://jobs.lever.co/northwind/abc123";"Ad Tech";"$93,500-$110,000";"Submitted";"cover letter; portfolio link"
"Contoso Insurance";"Claims Data Analyst";"2026-09-26";"Job board -> Ashby";"https://jobs.ashbyhq.com/contoso/def456";"Insurance";"$80,000-$100,000";"Submitted";"SQL questions; referral"
'''

SPREADSHEET = '''Company,Job Title,Date Applied,Status,Pay,Link
Mercy Hospital,Registered Nurse - ICU,9/15/2026,Rejected,"$41-$52/hr",https://mercy.example/jobs/77
Lincoln Elementary,4th Grade Teacher,09/18/2026,Interview,,
Café Luna,Shift Supervisor,9/20/2026,,,
Northside Electric,,9/19/2026,,,
Bright Retail,Store Manager,someday,,,
'''


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        self.db = memory_db()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        quiet = mock.patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

    def write(self, name, text, encoding="utf-8"):
        path = self.folder / name
        path.write_text(text, encoding=encoding)
        return path

    def test_import_semicolon_log_then_skip_duplicates(self):
        path = self.write("log.csv", SEMICOLON_LOG)
        self.assertEqual(jl.import_applications(self.db, path, resume_name="Data Analytics", assume_yes=True), 2)
        self.assertEqual(jl.import_applications(self.db, path, resume_name="Data Analytics", assume_yes=True), 0)
        rows = self.db.query("SELECT a.company_name, rv.version_name, a.salary_listed FROM applications a "
                             "JOIN resume_versions rv ON rv.resume_version_id = a.resume_version_id "
                             "ORDER BY a.application_id")
        self.assertEqual(rows[0], ("Northwind Analytics", "Data Analytics", "$93,500-$110,000"))
        self.assertEqual(self.db.query("SELECT status FROM status_events"), [("Applied",), ("Applied",)])

    def test_import_a_spreadsheet_from_any_field(self):
        path = self.write("jobs.csv", SPREADSHEET, encoding="cp1252")  # Excel's plain CSV on Windows
        self.assertEqual(jl.import_applications(self.db, path, assume_yes=True), 3)  # 2 rows have problems
        events = self.db.query("SELECT a.company_name, se.status FROM status_events se "
                               "JOIN applications a ON a.application_id = se.application_id "
                               "ORDER BY se.status_event_id")
        self.assertEqual(events, [("Mercy Hospital", "Applied"), ("Mercy Hospital", "Rejected"),
                                  ("Lincoln Elementary", "Applied"), ("Lincoln Elementary", "Interview"),
                                  ("Café Luna", "Applied")])

    def test_import_is_all_or_nothing(self):
        path = self.write("log.csv", SEMICOLON_LOG)
        with mock.patch.object(jl, "add_status", side_effect=[None, RuntimeError("disk full")]):
            self.assertEqual(jl.import_applications(self.db, path, assume_yes=True), 0)
        self.assertEqual(self.db.query("SELECT COUNT(*) FROM applications")[0][0], 0)

    def test_export_round_trip(self):
        app_id = jl.insert_application(self.db, application())
        jl.add_status(self.db, app_id, "Phone Screen", date(2026, 9, 24))
        out = self.folder / "export.csv"
        jl.export_applications(self.db, out)
        with open(out, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["current_status"], "Phone Screen")
        self.assertEqual(rows[0]["location"], "Baltimore, MD")
        # the export imports cleanly into a fresh database
        self.assertEqual(jl.import_applications(memory_db(), out, assume_yes=True), 1)


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.db = memory_db()

    def run_with_answers(self, command, answers):
        with mock.patch("builtins.input", side_effect=answers), mock.patch("builtins.print"):
            return command(self.db)

    def test_add_job_without_an_api_key(self):
        answers = ["Mercy Hospital", "Registered Nurse - ICU", "Baltimore, MD", "$41-$52/hr", "Healthcare",
                   "https://www.indeed.com/viewjob?jk=abc123",  # link
                   "yesterday",                                  # date applied
                   "",                                           # channel: accept the guess (Indeed)
                   "ICU RN", "y",                                # a new resume version
                   "night shift"]                                # notes
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            app_id = self.run_with_answers(jl.add_job, answers)
        row = self.db.query("SELECT company_name, channel, notes FROM applications WHERE application_id = %s",
                            (app_id,))[0]
        self.assertEqual(row, ("Mercy Hospital", "Indeed", "night shift"))
        self.assertEqual(self.db.query("SELECT version_name FROM resume_versions"), [("ICU RN",)])
        self.assertEqual(self.db.query("SELECT status FROM status_events"), [("Applied",)])

    def test_add_job_with_claude_catches_a_duplicate(self):
        jl.insert_application(self.db, application(company_name="Contoso Insurance", job_posting_url=None,
                                                   role_title="Claims Data Analyst"))
        extracted = {"company_name": "Contoso Insurance", "role_title": "Claims Data Analyst",
                     "location": "Remote", "salary_range": "$80,000-$100,000", "industry": "Insurance"}
        answers = ["the posting text", "END",  # paste
                   "", "", "", "", "",         # keep every extracted field
                   "",                         # no link
                   "n"]                        # don't log it twice
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), \
                mock.patch.object(jl, "extract_fields", return_value=extracted):
            self.assertIsNone(self.run_with_answers(jl.add_job, answers))
        self.assertEqual(self.db.query("SELECT COUNT(*) FROM applications")[0][0], 1)

    def test_update_status_by_searching(self):
        jl.insert_application(self.db, application())
        jl.insert_application(self.db, application(company_name="Lincoln Elementary", role_title="4th Grade Teacher",
                                                   job_posting_url=None))
        self.run_with_answers(jl.update_status, ["lincoln", "Interview", "2026-09-30", "demo lesson"])
        self.assertEqual(self.db.query("SELECT status, event_date, notes FROM status_events WHERE status = 'Interview'"),
                         [("Interview", "2026-09-30", "demo lesson")])


if __name__ == "__main__":
    unittest.main()

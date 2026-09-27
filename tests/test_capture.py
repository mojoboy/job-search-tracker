"""Link-reading tests with saved sample pages, so they run offline and never call the Claude API.

    python -m unittest discover -s tests -v
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture  # noqa: E402

JSON_LD_PAGE = """<html><head><title>Staff Nurse</title>
<script type="application/ld+json">{
  "@context": "https://schema.org", "@type": "JobPosting",
  "title": "Registered Nurse &amp; Charge Nurse",
  "hiringOrganization": {"@type": "Organization", "name": "001 Mercy Health &amp; Care"},
  "jobLocation": [{"@type": "Place", "address": {"addressLocality": "Baltimore", "addressRegion": "MD",
                   "addressCountry": {"name": "US"}}}],
  "baseSalary": {"@type": "MonetaryAmount", "currency": "USD",
                 "value": {"@type": "QuantitativeValue", "minValue": 38.5, "maxValue": 45, "unitText": "HOUR"}},
  "industry": "Healthcare",
  "description": "<p>Care for patients.</p><ul><li>BLS required</li><li>Nights</li></ul>"
}</script></head><body>...</body></html>"""

GRAPH_PAGE = """<script type="application/ld+json">{"@graph": [{"@type": "Organization", "name": "X"},
  {"@type": ["JobPosting"], "title": "Teacher", "hiringOrganization": "Lincoln Schools",
   "jobLocationType": "TELECOMMUTE",
   "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "minValue": 52000, "maxValue": 61000},
   "description": "Teach 4th grade."}]}</script>"""


class TextTests(unittest.TestCase):
    def test_html_to_text(self):
        text = capture.html_to_text("<p>Hello&nbsp;there</p><script>var x=1;</script><ul><li>One</li><li>Two</li></ul>")
        self.assertEqual(text, "Hello there\n\n- One\n\n- Two")

    def test_format_pay(self):
        self.assertEqual(capture.format_pay(80000, 100000, "USD", "YEAR"), "$80,000-$100,000/yr")
        self.assertEqual(capture.format_pay(38.5, 45, "USD", "HOUR"), "$38.50-$45/hr")
        self.assertEqual(capture.format_pay(52000, 52000), "$52,000")
        self.assertEqual(capture.format_pay(None, None), "")
        self.assertEqual(capture.format_pay(30000, 35000, "GBP", "YEAR"), "£30,000-£35,000/yr")

    def test_clean(self):
        self.assertEqual(capture.clean("001 Brown &amp; Brown,  Inc"), "Brown & Brown, Inc")


class JobPostingTests(unittest.TestCase):
    def test_reads_schema_org_job_posting(self):
        job = capture.from_job_posting(capture.find_job_posting(JSON_LD_PAGE))
        self.assertEqual(job["company_name"], "Mercy Health & Care")
        self.assertEqual(job["role_title"], "Registered Nurse & Charge Nurse")
        self.assertEqual(job["location"], "Baltimore, MD")
        self.assertEqual(job["salary_range"], "$38.50-$45/hr")
        self.assertEqual(job["industry"], "Healthcare")
        self.assertIn("- BLS required", job["description"])

    def test_graph_remote_and_flat_salary(self):
        job = capture.from_job_posting(capture.find_job_posting(GRAPH_PAGE))
        self.assertEqual(job["company_name"], "Lincoln Schools")
        self.assertEqual(job["location"], "Remote")
        self.assertEqual(job["salary_range"], "$52,000-$61,000")

    def test_no_job_posting(self):
        self.assertIsNone(capture.find_job_posting("<html><script type='application/ld+json'>{bad json</script></html>"))


class ReadLinkTests(unittest.TestCase):
    def test_generic_page_uses_json_ld(self):
        with mock.patch.object(capture, "fetch", return_value=JSON_LD_PAGE):
            result = capture.read_link("careers.mercy.example/jobs/1", use_claude=False)
        self.assertEqual(result["role_title"], "Registered Nurse & Charge Nurse")
        self.assertEqual(result["url"], "https://careers.mercy.example/jobs/1")
        self.assertEqual(result["source"], "the job data built into the page")

    def test_greenhouse_uses_its_api(self):
        api = {"title": "Data Analyst", "company_name": "Air", "location": {"name": "Arlington, VA"},
               "content": "&lt;p&gt;Analyze data.&lt;/p&gt;",
               "pay_input_ranges": [{"min_cents": 8000000, "max_cents": 9500000, "currency_type": "USD"}]}
        with mock.patch.object(capture, "fetch_json", return_value=api) as fetch_json:
            result = capture.read_link("https://job-boards.greenhouse.io/air/jobs/4077374009", use_claude=False)
        self.assertIn("boards-api.greenhouse.io/v1/boards/air/jobs/4077374009", fetch_json.call_args[0][0])
        self.assertEqual((result["company_name"], result["salary_range"]), ("Air", "$80,000-$95,000"))
        self.assertEqual(result["description"], "Analyze data.")

    def test_linkedin_uses_the_public_job_page(self):
        page = ('<h2 class="top-card-layout__title">Data Analyst</h2>'
                '<a class="topcard__org-name-link" href="#">Droisys</a>'
                '<span class="topcard__flavor topcard__flavor--bullet">United States</span>'
                '<div class="compensation__salary">Base pay range $70,000.00/yr - $128,000.00/yr</div>'
                '<div class="show-more-less-html__markup"><p>SQL and Python.</p></div>')
        with mock.patch.object(capture, "fetch", return_value=page) as fetch:
            result = capture.read_link("https://www.linkedin.com/jobs/view/data-analyst-at-droisys-4469020984/",
                                       use_claude=False)
        self.assertTrue(fetch.call_args[0][0].endswith("/jobPosting/4469020984"))
        self.assertEqual(result["salary_range"], "$70,000/yr - $128,000/yr")
        self.assertEqual(result["company_name"], "Droisys")

    def test_oracle_uses_its_api(self):
        api = {"items": [{"Title": "BI Analyst I", "PrimaryLocation": "United States", "WorkplaceType": "Remote",
                          "ExternalDescriptionStr": "<p>" + "Reports and dashboards. " * 20 + "</p>"}]}
        with mock.patch.object(capture, "fetch_json", return_value=api) as fetch_json:
            result = capture.read_link("https://emvo.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/"
                                       "sites/CX_3002/job/4485", use_claude=False)
        self.assertIn("Id=%224485%22,siteNumber=CX_3002", fetch_json.call_args[0][0])
        self.assertEqual(result["location"], "United States (Remote)")

    def test_page_that_needs_a_browser(self):
        with mock.patch.object(capture, "fetch", return_value="<html><body><div id='root'></div></body></html>"):
            with self.assertRaises(capture.CaptureError):
                capture.read_link("https://example.com/job/1", use_claude=False)

    def test_icims_asks_for_the_embeddable_page(self):
        with mock.patch.object(capture, "fetch", return_value=JSON_LD_PAGE) as fetch:
            capture.read_link("https://careers-acme.icims.com/jobs/8901/analyst/job", use_claude=False)
        self.assertTrue(fetch.call_args[0][0].endswith("/job?in_iframe=1"))


class ClaudeFillTests(unittest.TestCase):
    def test_fills_only_blank_fields(self):
        result = {"company_name": "", "role_title": "Analyst", "location": "Remote", "salary_range": "",
                  "industry": "", "description": "Acme Bank is hiring...", "source": "the page"}
        claude = {"company_name": "Acme Bank", "role_title": "WRONG", "location": "WRONG",
                  "salary_range": "", "industry": "Finance"}
        with mock.patch.object(capture.jl, "extract_fields", return_value=claude):
            filled = capture.complete(result, use_claude=True)
        self.assertEqual((filled["company_name"], filled["role_title"], filled["industry"]),
                         ("Acme Bank", "Analyst", "Finance"))
        self.assertTrue(filled["source"].endswith("+ Claude"))

    def test_claude_failure_becomes_a_warning(self):
        result = {"company_name": "", "role_title": "", "location": "", "salary_range": "", "industry": "",
                  "description": "text", "source": "the page"}
        with mock.patch.object(capture.jl, "extract_fields", side_effect=RuntimeError("bad key")):
            filled = capture.complete(result, use_claude=True)
        self.assertIn("bad key", filled["warning"])

    def test_pasted_text_without_a_key(self):
        result = capture.read_text("Some job", use_claude=False)
        self.assertEqual(result["description"], "Some job")
        self.assertIn("API key", result["warning"])


if __name__ == "__main__":
    unittest.main()

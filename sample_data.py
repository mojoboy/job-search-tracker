"""
sample_data.py
---------------
Made-up applications for the public demo and for screenshots. The companies are the fictional
names Microsoft uses in its samples; the links point to example.com. Dates are relative to today,
so the demo always looks current.
"""

from datetime import date, timedelta

import job_logger as jl

DESCRIPTION = ("Sample posting for the demo. The real app keeps each posting's full text here, "
               "so you still have it when the interview comes, even after the job is taken down.")

# company, role, days ago, channel, resume version, location, pay, industry, [(status, days ago)]
APPLICATIONS = [
    ("Northwind Traders", "Data Analyst", 55, "LinkedIn", "Data Analytics", "Remote", "$72,000-$85,000/yr", "Retail",
     [("Phone Screen", 48), ("Interview", 41), ("Offer", 30)]),
    ("Contoso Insurance", "Claims Data Analyst", 52, "Company site", "Data Analytics", "Arlington, VA",
     "$80,000-$95,000/yr", "Insurance", [("Rejected", 44)]),
    ("Fabrikam Health", "Reporting Analyst", 50, "Indeed", "Healthcare", "Baltimore, MD", "$34-$41/hr",
     "Healthcare", [("Phone Screen", 43), ("Rejected", 36)]),
    ("Tailspin Toys", "BI Analyst", 47, "LinkedIn", "Data Analytics", "Remote", "", "Retail", []),
    ("Adventure Works", "Junior Data Analyst", 45, "Referral", "Data Analytics", "Washington, DC",
     "$70,000-$78,000/yr", "Manufacturing", [("Phone Screen", 41), ("Interview", 35)]),
    ("Litware", "Operations Analyst", 42, "LinkedIn", "General", "Remote", "$65,000-$75,000/yr", "Software",
     [("Ghosted", 10)]),
    ("Proseware", "Data Quality Analyst", 40, "Job board", "Data Analytics", "Remote", "$78,000-$92,000/yr",
     "Software", [("Skills test", 35), ("Interview", 28)]),
    ("Wide World Importers", "Supply Chain Analyst", 38, "Indeed", "General", "Columbia, MD", "", "Logistics", []),
    ("Woodgrove Bank", "Risk Data Analyst", 35, "Company site", "Data Analytics", "McLean, VA",
     "$85,000-$100,000/yr", "Finance", [("Viewed", 33)]),
    ("Alpine Ski House", "Marketing Analyst", 33, "LinkedIn", "General", "Remote", "", "Hospitality",
     [("Rejected", 22)]),
    ("Coho Winery", "Sales Analyst", 30, "Job board", "General", "Remote", "$60,000-$70,000/yr", "Food & Beverage",
     []),
    ("Lucerne Publishing", "Research Analyst", 28, "Referral", "Data Analytics", "Remote", "", "Media",
     [("Phone Screen", 21)]),
    ("Margie's Travel", "Data Analyst", 24, "LinkedIn", "Data Analytics", "Remote", "$70,000-$80,000/yr", "Travel",
     []),
    ("City Power & Light", "Reporting Analyst", 21, "Company site", "Data Analytics", "Washington, DC",
     "$76,000-$90,000/yr", "Energy", [("Phone Screen", 15), ("Interview", 8)]),
    ("Fourth Coffee", "Business Analyst", 19, "Job board", "General", "Remote", "", "Food & Beverage", []),
    ("Graphic Design Institute", "Data Coordinator", 17, "Indeed", "Healthcare", "Silver Spring, MD",
     "$26-$31/hr", "Education", [("Rejected", 9)]),
    ("Lincoln Elementary School", "Data & Assessment Coordinator", 15, "Company site", "Teaching",
     "Alexandria, VA", "$58,000-$66,000/yr", "Education", [("Demo lesson", 7)]),
    ("Humongous Insurance", "Analytics Associate", 12, "LinkedIn", "Data Analytics", "Remote",
     "$75,000-$88,000/yr", "Insurance", []),
    ("Blue Yonder Airlines", "Operations Data Analyst", 10, "Company site", "Data Analytics", "Arlington, VA",
     "", "Aviation", [("Viewed", 6)]),
    ("Trey Research", "Junior Data Scientist", 8, "Referral", "Data Analytics", "Remote", "$82,000-$95,000/yr",
     "Research", [("Phone Screen", 3)]),
    ("VanArsdel", "Data Analyst", 6, "LinkedIn", "Data Analytics", "Remote", "$70,000-$82,000/yr", "Retail", []),
    ("Wingtip Toys", "Inventory Analyst", 4, "Indeed", "General", "Baltimore, MD", "$62,000-$70,000/yr", "Retail",
     []),
    ("Relecloud", "Cloud Cost Analyst", 2, "Job board", "Data Analytics", "Remote", "$85,000-$98,000/yr",
     "Software", []),
    ("Bellows College", "Institutional Research Analyst", 1, "Company site", "Data Analytics", "Remote", "",
     "Education", []),
]


def build(db, today=None):
    """Create the tables in an empty database and fill them with the sample applications."""
    today = today or date.today()
    jl.apply_schema_updates(db)
    cache = {}
    for company, role, days, channel, resume, location, pay, industry, events in APPLICATIONS:
        slug = company.lower().replace(" ", "-").replace("'", "").replace("&", "and")
        app_id = jl.insert_application(db, {
            "company_name": company,
            "role_title": role,
            "location": location,
            "date_applied": today - timedelta(days=days),
            "channel": channel,
            "resume_version_id": jl.resolve_resume_version(db, resume, cache),
            "job_posting_url": f"https://example.com/jobs/{slug}",
            "industry": industry,
            "salary_listed": pay or None,
            "notes": None,
            "job_description": DESCRIPTION,
        }, commit=False)
        for status, when in events:
            jl.add_status(db, app_id, status, today - timedelta(days=when), commit=False)
    db.commit()

"""
job_logger.py
--------------
Log job applications and their status changes without writing SQL.

    python job_logger.py                  log a job you applied to (paste the posting; Claude fills in the details)
    python job_logger.py status           record what happened: Phone Screen, Interview, Offer, Rejected...
    python job_logger.py import FILE.csv  add many applications from a spreadsheet saved as CSV
    python job_logger.py export [FILE]    save every application and its current status to a CSV file
    python job_logger.py setup            check the database and apply one-time updates

Setup (once):
    pip install anthropic mysql-connector-python
    Copy .env.example to .env in this folder and fill in ANTHROPIC_API_KEY and DB_PASSWORD.
    python job_logger.py setup

No API key? It still works: you type the job details yourself.
"""

import argparse
import csv
import io
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

HERE = Path(__file__).resolve().parent

EXTRACT_TOOL = {
    "name": "log_job_application",
    "description": "Extract structured job application fields from raw job posting text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "company_name": {"type": "string"},
            "role_title": {"type": "string"},
            "location": {
                "type": "string",
                "description": "city and state, or 'Remote'; add '(hybrid)' or '(on-site)' when the posting says so",
            },
            "salary_range": {
                "type": "string",
                "description": "pay as listed, with the period, e.g. '$88,000-$133,000/yr' or '$32-$41/hr'; empty string if not listed",
            },
            "industry": {
                "type": "string",
                "description": "best-guess industry, e.g. 'Healthcare', 'Education', 'Finance', 'Tech', 'Retail', 'Construction'",
            },
        },
        "required": ["company_name", "role_title", "location", "salary_range", "industry"],
    },
}
FIELD_LABELS = {
    "company_name": "Company",
    "role_title": "Role",
    "location": "Location",
    "salary_range": "Pay",
    "industry": "Industry",
}
REQUIRED_FIELDS = ("company_name", "role_title")

DEFAULT_CHANNELS = ["LinkedIn", "Indeed", "Company site", "Job board", "Referral", "Recruiter", "Career fair"]
DEFAULT_STATUSES = ["Applied", "Viewed", "Phone Screen", "Interview", "Offer", "Rejected", "Ghosted", "Withdrawn"]
STATUS_ALIASES = {"submitted": "Applied", "sent": "Applied", "screening": "Phone Screen", "no response": "Ghosted"}

# Website of the job link -> channel offered as the default
CHANNEL_BY_DOMAIN = {
    "linkedin.com": "LinkedIn",
    "indeed.com": "Indeed",
    "bandana.com": "Bandana",
    "ziprecruiter.com": "ZipRecruiter",
    "glassdoor.com": "Glassdoor",
    "joinhandshake.com": "Handshake",
    "usajobs.gov": "USAJOBS",
    "governmentjobs.com": "GovernmentJobs",
    "myworkdayjobs.com": "Company site",
    "greenhouse.io": "Company site",
    "lever.co": "Company site",
    "ashbyhq.com": "Company site",
    "icims.com": "Company site",
    "oraclecloud.com": "Company site",
    "smartrecruiters.com": "Company site",
    "ultipro.com": "Company site",
    "taleo.net": "Company site",
    "successfactors.com": "Company site",
}
# Query-string parameters that only record where a click came from
TRACKING_PARAM = re.compile(
    r"^(utm_\w+|trk\w*|trackingid|refid|ref|src|source|gh_src|lever-(source|origin)|in_iframe)$", re.I
)
# Longest value each column accepts
COLUMN_LIMITS = {"company_name": 255, "role_title": 255, "location": 255, "channel": 50,
                 "job_posting_url": 2048, "industry": 100, "salary_listed": 100}


# ---------------------------------------------------------------- settings

def load_env(path=HERE / ".env"):
    """Load KEY=VALUE lines from .env into the environment (real environment variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


# ---------------------------------------------------------------- database

class Database:
    """Runs SQL written with %s placeholders against MySQL (or an in-memory SQLite database in the tests)."""

    def __init__(self, conn, engine, label):
        self.conn = conn
        self.engine = engine
        self.label = label
        self.has_location = True

    def _run(self, sql, params):
        if self.engine == "sqlite":
            sql = sql.replace("%s", "?")
        cursor = self.conn.cursor()
        if params:
            cursor.execute(sql, tuple(params))
        else:
            cursor.execute(sql)
        return cursor

    def query(self, sql, params=()):
        cursor = self._run(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows

    def execute(self, sql, params=()):
        cursor = self._run(sql, params)
        row_id = cursor.lastrowid
        cursor.close()
        return row_id

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def has_table(self, table):
        if self.engine == "sqlite":
            sql = "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = %s"
        else:
            sql = "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
        return self.query(sql, (table,))[0][0] > 0

    def has_column(self, table, column):
        if self.engine == "sqlite":
            return any(row[1] == column for row in self.query(f"PRAGMA table_info({table})"))
        sql = ("SELECT COUNT(*) FROM information_schema.COLUMNS "
               "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s")
        return self.query(sql, (table, column))[0][0] > 0


def get_db_connection(create_database=False):
    import mysql.connector

    settings = {
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "3306")),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASSWORD", ""),
    }
    name = os.environ.get("DB_NAME", "job_search_tracker")
    if create_database:
        server = mysql.connector.connect(**settings)
        server.cursor().execute(f"CREATE DATABASE IF NOT EXISTS `{name}`")
        server.close()
    conn = mysql.connector.connect(database=name, **settings)
    return Database(conn, "mysql", f"MySQL database '{name}' on {settings['host']}")


def connect_or_exit(allow_create=False, assume_yes=False):
    """Connect, or explain in plain words what to fix."""
    try:
        return get_db_connection()
    except ImportError as e:
        sys.exit(f"A Python package is missing ({e.name}). Run: pip install anthropic mysql-connector-python")
    except Exception as e:
        if getattr(e, "errno", None) == 1049:  # MySQL: unknown database
            if allow_create and (assume_yes or yes_no("That database doesn't exist yet. Create it?", default=True)):
                return get_db_connection(create_database=True)
            sys.exit(f"{e}\nRun: python job_logger.py setup")
        sys.exit(f"Couldn't connect to the database: {e}\n"
                 "Check that MySQL is running (on Windows, the MySQL80 service) "
                 "and that DB_USER and DB_PASSWORD in .env are right.")


def schema_statements(engine):
    key = "INTEGER PRIMARY KEY AUTOINCREMENT" if engine == "sqlite" else "INT AUTO_INCREMENT PRIMARY KEY"
    return [
        f"""CREATE TABLE IF NOT EXISTS resume_versions (
            resume_version_id {key},
            version_name VARCHAR(50) NOT NULL,
            focus_area VARCHAR(100),
            notes VARCHAR(255)
        )""",
        f"""CREATE TABLE IF NOT EXISTS applications (
            application_id {key},
            company_name VARCHAR(255) NOT NULL,
            role_title VARCHAR(255) NOT NULL,
            location VARCHAR(255),
            date_applied DATE NOT NULL,
            channel VARCHAR(50),
            resume_version_id INT,
            job_posting_url VARCHAR(2048),
            industry VARCHAR(100),
            salary_listed VARCHAR(100),
            notes TEXT,
            FOREIGN KEY (resume_version_id) REFERENCES resume_versions(resume_version_id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS status_events (
            status_event_id {key},
            application_id INT NOT NULL,
            status VARCHAR(50) NOT NULL,
            event_date DATE NOT NULL,
            notes TEXT,
            FOREIGN KEY (application_id) REFERENCES applications(application_id)
        )""",
    ]


def ensure_schema(db, assume_yes=False):
    """Create missing tables and add the columns this version of the script uses."""
    missing = [table for table in ("resume_versions", "applications", "status_events") if not db.has_table(table)]
    if missing:
        if not (assume_yes or yes_no(f"These tables don't exist yet: {', '.join(missing)}. Create them?", default=True)):
            sys.exit("Can't continue without them.")
        for statement in schema_statements(db.engine):
            db.execute(statement)
        db.commit()
        print("Created the tables.")
    db.has_location = db.has_column("applications", "location")
    if not db.has_location and (assume_yes or yes_no(
            "Your applications table has no 'location' column, so locations get thrown away. Add it?", default=True)):
        db.execute("ALTER TABLE applications ADD COLUMN location VARCHAR(255)")
        db.commit()
        db.has_location = True
        print("Added the 'location' column.")


def backfill_applied_events(db, assume_yes=False):
    """Give older applications the 'Applied' status row the funnel queries count from."""
    rows = db.query(
        "SELECT a.application_id, a.date_applied FROM applications a WHERE NOT EXISTS "
        "(SELECT 1 FROM status_events se WHERE se.application_id = a.application_id AND se.status = 'Applied')"
    )
    if not rows or not (assume_yes or yes_no(
            f"{len(rows)} applications have no 'Applied' status yet, so the funnel can't count them. "
            "Add it, dated the day you applied?", default=True)):
        return 0
    for application_id, applied in rows:
        add_status(db, application_id, "Applied", applied, "added by setup", commit=False)
    db.commit()
    return len(rows)


# ---------------------------------------------------------------- prompts

def yes_no(question, default=False):
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return answer.startswith("y") if answer else default


def ask(label, current="", required=False):
    """Enter keeps the current value, '-' clears it, anything else replaces it."""
    while True:
        hint = f" [{current}]" if current else ""
        typed = input(f"  {label}{hint}: ").strip()
        value = "" if typed == "-" else (typed or current)
        if value or not required:
            return value
        print(f"  {label} is required.")


def choose(title, options, default=None):
    """Numbered menu. Enter picks the default, a number picks that option, other text is used as typed."""
    print(f"\n{title}")
    for number, option in enumerate(options, 1):
        print(f"  {number}) {option}")
    hint = f" (Enter = {default})" if default else ""
    while True:
        answer = input(f"Pick a number or type your own{hint}: ").strip()
        if not answer and default:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1]
        if answer and not answer.isdigit():
            return next((option for option in options if option.lower() == answer.lower()), answer)
        print("  Type a number from the list, or a name.")


def ask_date(label, allow_future=True):
    while True:
        when = parse_date(input(f"{label} (Enter = today; or yesterday, 9/24, 2026-09-24): "))
        if when and (allow_future or when <= date.today()):
            return when
        print("  That date is in the future." if when else "  I didn't recognize that date.")


def read_pasted_text():
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


def unique(items):
    """Drop repeats (ignoring case) and blanks, keeping the first spelling."""
    seen, kept = set(), []
    for item in items:
        if item and item.lower() not in seen:
            seen.add(item.lower())
            kept.append(item)
    return kept


# ---------------------------------------------------------------- parsing

def parse_date(text, today=None):
    """Read '', 'today', 'yesterday', '3 days ago', '9/24', '9/24/2026', '2026-09-24' or 'Sep 24, 2026'."""
    today = today or date.today()
    text = (text or "").strip().lower()
    if text in ("", "today"):
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    match = re.fullmatch(r"(\d+)\s*days?\s*ago", text)
    if match:
        return today - timedelta(days=int(match[1]))
    for candidate in (text, text.split(" ")[0]):  # the second try drops a time like '00:00:00'
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                pass
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", text)
    if match:
        try:
            when = date(today.year, int(match[1]), int(match[2]))
            return when if when <= today else when.replace(year=today.year - 1)
        except ValueError:
            return None
    return None


def normalize_url(url):
    """Drop tracking parameters, #fragments, 'www.' and trailing slashes so a posting always matches itself."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    query = "&".join(p for p in parts.query.split("&") if p and not TRACKING_PARAM.match(p.split("=", 1)[0]))
    return urlunsplit(("https", host, parts.path.rstrip("/"), query, ""))


def guess_channel(url):
    host = urlsplit(normalize_url(url)).hostname or ""
    for domain, channel in CHANNEL_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return channel
    return None


def normalize_status(text):
    """'submitted' -> 'Applied', 'phone screen' -> 'Phone Screen'; statuses it doesn't know are kept as typed."""
    text = (text or "").strip()
    if not text:
        return "Applied"
    lowered = text.lower()
    return STATUS_ALIASES.get(lowered) or next((s for s in DEFAULT_STATUSES if s.lower() == lowered), text)


# ---------------------------------------------------------------- Claude

def extract_fields(job_text):
    import anthropic

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-5"),
        max_tokens=500,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "log_job_application"},
        messages=[{"role": "user", "content": f"Extract the fields from this job posting:\n\n{job_text[:40000]}"}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Claude didn't return the fields.")


# ---------------------------------------------------------------- applications

def load_existing(db):
    """Index logged applications by cleaned-up link and by company + role, to catch duplicates."""
    existing = ({}, {})
    for app_id, company, role, applied, url in db.query(
            "SELECT application_id, company_name, role_title, date_applied, job_posting_url FROM applications"):
        remember(existing, f"#{app_id}", company, role, applied, url)
    return existing


def remember(existing, ref, company, role, applied, url):
    by_url, by_name = existing
    url = normalize_url(url)
    if url:
        by_url.setdefault(url, (ref, applied))
    by_name.setdefault((company.strip().lower(), role.strip().lower()), (ref, applied, url))


def match_existing(existing, company, role, url):
    """(ref, date applied) if this job is already logged: same link, or same company + role
    unless the two have different links (then they're different postings)."""
    by_url, by_name = existing
    url = normalize_url(url)
    if url in by_url:
        return by_url[url]
    same_name = by_name.get((company.strip().lower(), role.strip().lower()))
    if same_name and not (url and same_name[2] and url != same_name[2]):
        return same_name[:2]
    return None


def insert_application(db, app, commit=True):
    """Insert an application plus its 'Applied' status row, all-or-nothing."""
    app = dict(app)
    if not db.has_location and app.get("location"):  # older table: keep the location in the notes instead
        app["notes"] = "; ".join(filter(None, [app.get("notes"), f"Location: {app['location']}"]))
    for column, limit in COLUMN_LIMITS.items():
        if isinstance(app.get(column), str):
            app[column] = app[column][:limit]
    columns = ["company_name", "role_title", "date_applied", "channel", "resume_version_id",
               "job_posting_url", "industry", "salary_listed", "notes"]
    if db.has_location:
        columns.insert(2, "location")
    try:
        app_id = db.execute(
            f"INSERT INTO applications ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
            [app.get(column) for column in columns],
        )
        add_status(db, app_id, "Applied", app["date_applied"], commit=False)
        if commit:
            db.commit()
    except Exception:
        db.rollback()
        raise
    return app_id


def add_status(db, application_id, status, event_date, notes=None, commit=True):
    db.execute(
        "INSERT INTO status_events (application_id, status, event_date, notes) VALUES (%s, %s, %s, %s)",
        (application_id, status, event_date, notes),
    )
    if commit:
        db.commit()


def choose_channel(db, url):
    used = [row[0] for row in db.query(
        "SELECT channel FROM applications WHERE channel IS NOT NULL AND channel <> '' "
        "GROUP BY channel ORDER BY COUNT(*) DESC LIMIT 10")]
    guess = guess_channel(url)
    return choose("Where did you find the job?", unique(DEFAULT_CHANNELS + used + [guess]), default=guess)


def choose_resume_version(db):
    """Pick the resume you sent; a new name can be added on the spot. Returns its id, or None."""
    versions = db.query("SELECT resume_version_id, version_name FROM resume_versions ORDER BY resume_version_id")
    last = db.query("SELECT rv.version_name FROM applications a JOIN resume_versions rv "
                    "ON rv.resume_version_id = a.resume_version_id ORDER BY a.application_id DESC LIMIT 1")
    skip = "No resume version"
    while True:
        picked = choose("Which resume did you send?", [name for _, name in versions] + [skip],
                        default=last[0][0] if last else None)
        if picked == skip:
            return None
        for version_id, name in versions:
            if name.lower() == picked.lower():
                return version_id
        if len(picked) > 50:
            print("  Keep the name under 50 characters.")
        elif yes_no(f"Add '{picked}' as a new resume version?", default=True):
            version_id = db.execute("INSERT INTO resume_versions (version_name) VALUES (%s)", (picked,))
            db.commit()
            return version_id


def resolve_resume_version(db, name, cache):
    """Id of the resume version with this name (or id). New names are added; unknown ids give None."""
    key = name.strip().lower()
    if key not in cache:
        rows = db.query("SELECT resume_version_id FROM resume_versions WHERE LOWER(version_name) = %s", (key,))
        if not rows and key.isdigit():
            rows = db.query("SELECT resume_version_id FROM resume_versions WHERE resume_version_id = %s", (int(key),))
        if rows:
            cache[key] = rows[0][0]
        elif key.isdigit():
            cache[key] = None
        else:
            cache[key] = db.execute("INSERT INTO resume_versions (version_name) VALUES (%s)", (name.strip()[:50],))
    return cache[key]


# ---------------------------------------------------------------- commands

def add_job(db):
    """Paste a posting (or type the details), check them, and save with an 'Applied' status."""
    fields = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        print("Paste the job posting below, then type END on its own line.")
        print("(Type END right away to fill in the details yourself.)\n", flush=True)
        job_text = read_pasted_text()
        if job_text.strip():
            print("\nReading it with Claude...", flush=True)
            try:
                fields = extract_fields(job_text)
            except Exception as e:  # bad key, no internet, rate limit...
                print(f"Claude couldn't read it ({e}). Fill in the details yourself.")
    else:
        print("No ANTHROPIC_API_KEY in .env, so fill in the details yourself.")

    print("\nPress Enter to keep a value, type to change it, or '-' to clear it:")
    details = {key: ask(label, str(fields.get(key) or "").strip(), required=key in REQUIRED_FIELDS)
               for key, label in FIELD_LABELS.items()}
    url = input("\nJob posting link (Enter to skip): ").strip()

    existing = match_existing(load_existing(db), details["company_name"], details["role_title"], url)
    if existing and not yes_no(f"You already logged this as {existing[0]} on {existing[1]}. Log it again?"):
        print("Not logged.")
        return None

    applied = ask_date("\nDate applied", allow_future=False)
    channel = choose_channel(db, url)
    resume_version_id = choose_resume_version(db)
    notes = input("\nNotes (Enter to skip): ").strip()

    app_id = insert_application(db, {
        "company_name": details["company_name"],
        "role_title": details["role_title"],
        "location": details["location"] or None,
        "date_applied": applied,
        "channel": channel,
        "resume_version_id": resume_version_id,
        "job_posting_url": url or None,
        "industry": details["industry"] or None,
        "salary_listed": details["salary_range"] or None,
        "notes": notes or None,
    })
    print(f"\nLogged {details['company_name']} - {details['role_title']} as application #{app_id} (status: Applied).")
    return app_id


CURRENT_STATUS_SQL = """
    SELECT a.application_id, a.company_name, a.role_title, a.date_applied,
           (SELECT se.status FROM status_events se WHERE se.application_id = a.application_id
            ORDER BY se.event_date DESC, se.status_event_id DESC LIMIT 1) AS current_status
    FROM applications a
    ORDER BY a.date_applied DESC, a.application_id DESC
"""


def pick_application(apps):
    """List recent applications; pick one by number or search by company/role. Enter cancels."""
    shown = apps[:15]
    while True:
        print()
        for app_id, company, role, applied, status in shown:
            line = f"  #{app_id:<5} {applied}  {company} - {role}"
            print(f"{line[:90]:<90}  [{status or 'no status'}]")
        answer = input("\nApplication # (or part of a company or role name to search; Enter to cancel): ")
        answer = answer.strip().lstrip("#")
        if not answer:
            return None
        if answer.isdigit():
            matches = [app for app in apps if app[0] == int(answer)]
        else:
            matches = [app for app in apps if answer.lower() in f"{app[1]} {app[2]}".lower()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            shown = matches
        else:
            print("  No match.")


def update_status(db):
    """Pick an application and record what happened (phone screen, interview, rejection...)."""
    apps = db.query(CURRENT_STATUS_SQL)
    if not apps:
        print("No applications yet. Log one first with: python job_logger.py")
        return None
    app = pick_application(apps)
    if not app:
        return None
    app_id, company, role = app[0], app[1], app[2]
    used = [row[0] for row in db.query("SELECT DISTINCT status FROM status_events")]
    options = unique([status for status in DEFAULT_STATUSES + used if status != "Applied"])
    status = normalize_status(choose(f"What happened with {company} - {role}?", options))
    when = ask_date("When", allow_future=True)
    notes = input("Notes (Enter to skip): ").strip()
    add_status(db, app_id, status, when, notes or None)
    print(f"\nSaved: {company} - {role} -> {status} on {when}.")
    return app_id


COLUMN_ALIASES = {
    "company_name": ("company_name", "company", "employer", "organization"),
    "role_title": ("role_title", "role", "job_title", "title", "position"),
    "location": ("location", "city"),
    "date_applied": ("date_applied", "applied_on", "date", "applied"),
    "channel": ("channel", "source", "found_on", "where_found"),
    "job_posting_url": ("job_posting_url", "url", "link", "job_url", "job_link", "posting_url"),
    "industry": ("industry", "sector", "field"),
    "salary_listed": ("salary_listed", "salary", "pay", "salary_range", "compensation"),
    "notes": ("notes", "note", "comments"),
    "status": ("status", "current_status", "stage"),
    "resume_version": ("resume_version", "resume", "resume_version_name", "resume_version_id"),
}


def read_csv_rows(path):
    """Rows of a CSV from Excel, Google Sheets or this tool (comma, semicolon or tab separated)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")  # Excel's plain "CSV" on Windows
    header = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=header.count)
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))


def map_columns(header):
    """Match headers like 'Company' or 'Job Title' to the tracker's fields: {field: column index}."""
    names = [re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") for name in header]
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        index = next((names.index(alias) for alias in aliases if alias in names), None)
        if index is not None:
            mapping[field] = index
    return mapping


def parse_rows(rows, mapping, today=None):
    """Turn CSV rows into application dicts. Returns (apps, problems)."""
    today = today or date.today()
    apps, problems = [], []
    for line, row in enumerate(rows, start=2):  # line 1 is the header
        if not any(value.strip() for value in row):
            continue
        cell = {field: row[index].strip() if index < len(row) else "" for field, index in mapping.items()}
        company, role = cell.get("company_name", ""), cell.get("role_title", "")
        applied_text = cell.get("date_applied", "")
        applied = parse_date(applied_text, today) if applied_text else None
        if not company or not role:
            problems.append(f"line {line}: no company or role")
        elif not applied or applied > today:
            problems.append(f"line {line} ({company}): date applied '{applied_text}' is missing, unreadable or in the future")
        else:
            apps.append({
                "line": line,
                "company_name": company,
                "role_title": role,
                "location": cell.get("location") or None,
                "date_applied": applied,
                "channel": cell.get("channel") or None,
                "job_posting_url": cell.get("job_posting_url") or None,
                "industry": cell.get("industry") or None,
                "salary_listed": cell.get("salary_listed") or None,
                "notes": cell.get("notes") or None,
                "status": normalize_status(cell.get("status")),
                "resume_version": cell.get("resume_version") or None,
            })
    return apps, problems


def import_applications(db, path, resume_name=None, assume_yes=False):
    """Add applications from a CSV, skipping ones already logged. All-or-nothing."""
    if not Path(path).exists():
        print(f"Can't find {path}")
        return 0
    rows = read_csv_rows(path)
    if not rows:
        print("That file is empty.")
        return 0
    mapping = map_columns(rows[0])
    missing = [field for field in ("company_name", "role_title", "date_applied") if field not in mapping]
    if missing:
        print(f"Couldn't find a column for: {', '.join(missing)}.")
        print(f"The file's headers are: {', '.join(rows[0])}")
        return 0

    apps, problems = parse_rows(rows[1:], mapping)
    existing = load_existing(db)
    new, duplicates = [], []
    for app in apps:
        match = match_existing(existing, app["company_name"], app["role_title"], app["job_posting_url"])
        if match:
            duplicates.append(f"line {app['line']}: {app['company_name']} - {app['role_title']} "
                              f"is already logged ({match[0]})")
        else:
            new.append(app)
            remember(existing, f"line {app['line']} of this file", app["company_name"], app["role_title"],
                     app["date_applied"], app["job_posting_url"])

    print(f"\n{len(new)} new, {len(duplicates)} already logged, {len(problems)} with problems.")
    for message in (duplicates + problems)[:20]:
        print(f"  {message}")
    if not new:
        return 0
    if not (assume_yes or yes_no(f"Import {len(new)} applications?", default=True)):
        print("Nothing imported.")
        return 0

    cache, default_resume_id = {}, None
    if "resume_version" not in mapping:
        if resume_name:
            default_resume_id = resolve_resume_version(db, resume_name, cache)
        elif not assume_yes:
            print("\nThe file has no resume column.")
            default_resume_id = choose_resume_version(db)
    line = None
    try:
        for app in new:
            line, status, resume = app.pop("line"), app.pop("status"), app.pop("resume_version")
            app["resume_version_id"] = resolve_resume_version(db, resume, cache) if resume else default_resume_id
            app_id = insert_application(db, app, commit=False)
            if status != "Applied":
                add_status(db, app_id, status, date.today(), "imported; the real date is unknown", commit=False)
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Stopped at line {line}: {e}\nNothing was imported.")
        return 0
    print(f"Imported {len(new)} applications.")
    return len(new)


EXPORT_HEADER = ["application_id", "date_applied", "company_name", "role_title", "location", "channel",
                 "resume_version", "industry", "salary_listed", "current_status", "status_date",
                 "job_posting_url", "notes"]


def export_applications(db, path=None):
    """Write every application with its current status to a CSV that opens cleanly in Excel."""
    path = Path(path) if path else HERE / f"applications_export_{date.today()}.csv"
    latest = ("(SELECT se.{} FROM status_events se WHERE se.application_id = a.application_id "
              "ORDER BY se.event_date DESC, se.status_event_id DESC LIMIT 1)")
    rows = db.query(f"""
        SELECT a.application_id, a.date_applied, a.company_name, a.role_title,
               {'a.location' if db.has_location else 'NULL'}, a.channel, rv.version_name,
               a.industry, a.salary_listed, {latest.format('status')}, {latest.format('event_date')},
               a.job_posting_url, a.notes
        FROM applications a
        LEFT JOIN resume_versions rv ON rv.resume_version_id = a.resume_version_id
        ORDER BY a.date_applied, a.application_id""")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_HEADER)
        writer.writerows(rows)
    print(f"Saved {len(rows)} applications to {path}")
    return path


def setup(db, assume_yes=False):
    """Report what's in the database and apply one-time fixes."""
    added = backfill_applied_events(db, assume_yes)
    if added:
        print(f"Added the 'Applied' status to {added} applications.")
    counts = [db.query(f"SELECT COUNT(*) FROM {table}")[0][0]
              for table in ("applications", "status_events", "resume_versions")]
    print(f"{counts[0]} applications, {counts[1]} status updates, {counts[2]} resume versions.")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print("Claude API key: " + ("found." if has_key else "not set, so you'll type job details yourself."))
    print("\nYou're set. Log a job with: python job_logger.py")


def main(argv=None):
    load_env()
    parser = argparse.ArgumentParser(description="Log job applications and status changes without writing SQL.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("add", help="log a job you applied to (the default)")
    commands.add_parser("status", help="record a reply, interview, offer or rejection")
    import_cmd = commands.add_parser("import", help="add applications from a CSV file")
    import_cmd.add_argument("file")
    import_cmd.add_argument("--resume", help="resume version for every row, when the file has no resume column")
    import_cmd.add_argument("--yes", action="store_true", help="don't ask questions")
    export_cmd = commands.add_parser("export", help="save all applications to a CSV file")
    export_cmd.add_argument("file", nargs="?")
    setup_cmd = commands.add_parser("setup", help="check the database and apply one-time updates")
    setup_cmd.add_argument("--yes", action="store_true", help="don't ask questions")
    args = parser.parse_args(argv)
    command = args.command or "add"
    assume_yes = getattr(args, "yes", False)

    try:
        db = connect_or_exit(allow_create=command == "setup", assume_yes=assume_yes)
        try:
            if command == "setup":
                print(f"Connected to {db.label}.")
            ensure_schema(db, assume_yes)
            if command == "add":
                add_job(db)
            elif command == "status":
                update_status(db)
            elif command == "import":
                import_applications(db, args.file, args.resume, assume_yes)
            elif command == "export":
                export_applications(db, args.file)
            else:
                setup(db, assume_yes)
        finally:
            db.close()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Anything you hadn't finished wasn't saved.")


if __name__ == "__main__":
    main()

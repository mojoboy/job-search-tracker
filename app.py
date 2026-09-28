"""
app.py
-------
The Job Search Tracker web app: log a job from its link, record what happens, and see what's working.

    streamlit run app.py      (or double-click start.bat)

demo.py runs the same app on made-up sample data; that's the public demo.
"""

import html
import os
import sqlite3
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import analytics
import capture
import job_logger as jl
import sample_data

REPO_URL = "https://github.com/mojoboy/job-search-tracker"
AUTHOR_URL = "https://mojoboy.github.io"
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "logo.svg")
NO_RESUME = "No resume version"

# Chart colors, checked for contrast and color-blind safety on the app's #0B0B10 background
ACCENT = "#8B7CFF"                                              # the single series color (6:1 on the background)
FUNNEL_SHADES = ["#4A3DB0", "#6655E6", "#8B7CFF", "#B9AEFF"]    # one hue, dim (first stage) to bright (offer)
INK_2, MUTED, GRID, AXIS = "#A1A1AA", "#8B8B96", "#22222B", "#2E2E38"
# System fonts for charts: they measure label widths before web fonts load, so Inter would get clipped
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"

# Pipeline board columns: name, badge color, and the statuses that belong there.
# "In conversation" takes every reply stage not listed (Phone Screen, Skills test, Demo lesson...).
BOARD = [
    ("Applied", "gray", {"Applied", "Viewed"}),
    ("In conversation", "blue", None),
    ("Interviewing", "violet", {"Interview"}),
    ("Offer", "green", {"Offer"}),
    ("Closed", "red", {"Rejected", "Ghosted", "Withdrawn"}),
]
DOT_COLORS = {"gray": "#8B8B96", "blue": "#60A5FA", "violet": "#A99CFF", "green": "#4ADE80", "red": "#F87171"}
CARDS_PER_COLUMN = 25

NOISE = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E"
         "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='3' "
         "stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.07 0'/%3E"
         "%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")")

CSS = """
<style>
:root {
  --jt-bg: #0B0B10; --jt-surface: #111118; --jt-line: #24242E;
  --jt-ink: #F4F4F6; --jt-ink-2: #A1A1AA; --jt-violet: #8B7CFF; --jt-violet-hi: #B9AEFF;
  --jt-ease: cubic-bezier(0.2, 0.7, 0.2, 1);
}
::selection { background: rgba(139, 124, 255, 0.35); color: #FFFFFF; }
.stApp * { scrollbar-width: thin; scrollbar-color: #2E2E3A transparent; }

/* ---- backdrop: violet and cyan light, a fine grid behind the title, a little film grain ---- */
.stApp {
  background:
    """ + NOISE + """ repeat,
    radial-gradient(1100px 520px at 18% -12%, rgba(139, 124, 255, 0.17), transparent 62%),
    radial-gradient(900px 420px at 92% -8%, rgba(34, 211, 238, 0.08), transparent 60%),
    var(--jt-bg);
}
.stApp::before {
  content: ""; position: fixed; inset: 0; pointer-events: none;
  background-image: linear-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255, 255, 255, 0.045) 1px, transparent 1px);
  background-size: 56px 56px; background-position: center top;
  -webkit-mask-image: radial-gradient(ellipse 55% 40% at 50% 0%, #000 15%, transparent 70%);
          mask-image: radial-gradient(ellipse 55% 40% at 50% 0%, #000 15%, transparent 70%);
}

/* ---- header: frosted glass, the current page lit up ---- */
[data-testid="stHeader"] {
  background: rgba(11, 11, 16, 0.62);
  -webkit-backdrop-filter: saturate(160%) blur(16px); backdrop-filter: saturate(160%) blur(16px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}
/* the app's name beside the logo (the logo is a link on every page but the first) */
[data-testid="stHeader"] :has(> [data-testid="stHeaderLogo"]) { display: inline-flex; align-items: center; gap: 0.65rem; }
[data-testid="stHeader"] :has(> [data-testid="stHeaderLogo"])::after {
  content: "Job Search Tracker"; color: var(--jt-ink); font-weight: 600; font-size: 0.95rem; letter-spacing: -0.01em;
  white-space: nowrap; padding-right: 1.1rem; margin-right: 0.4rem; border-right: 1px solid rgba(255, 255, 255, 0.1);
}
@media (max-width: 640px) { [data-testid="stHeader"] :has(> [data-testid="stHeaderLogo"])::after { content: none; } }
[data-testid="stTopNavLink"] { border-radius: 999px; transition: background-color 0.2s ease, box-shadow 0.2s ease; }
[data-testid="stTopNavLink"]:hover { background: rgba(255, 255, 255, 0.05); }
[data-testid="stTopNavLink"][aria-current="page"] {
  background: rgba(139, 124, 255, 0.14); box-shadow: inset 0 0 0 1px rgba(139, 124, 255, 0.4);
}
[data-testid="stMainBlockContainer"], .block-container { padding-top: 5.5rem; padding-bottom: 3rem; max-width: 1240px; }

/* ---- type ---- */
h1 {
  letter-spacing: -0.04em; font-weight: 700;
  background: linear-gradient(100deg, #FFFFFF 0%, #FFFFFF 40%, #D6CFFF 50%, #FFFFFF 60%, #FFFFFF 100%);
  background-size: 260% 100%; background-position: 0 0;
  -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: jt-sheen 1.8s var(--jt-ease) 0.3s both;
}
.jt-eyebrow {
  display: flex; align-items: center; gap: 0.6rem; margin: 0 0 -0.7rem;
  font-family: 'Geist Mono', ui-monospace, monospace; font-size: 0.74rem; letter-spacing: 0.16em;
  text-transform: uppercase; color: var(--jt-violet-hi);
}
.jt-eyebrow::before { content: ""; width: 26px; height: 1px; background: linear-gradient(90deg, transparent, var(--jt-violet)); }
.jt-subtitle { color: var(--jt-ink-2); font-size: 1.05rem; margin: -0.5rem 0 1.5rem; }
.jt-col-head { display: flex; justify-content: space-between; align-items: center; font-weight: 600;
               font-size: 0.9rem; padding: 0 0.2rem 0.5rem; color: var(--jt-ink); }
.jt-col-head > span:first-child { display: inline-flex; align-items: center; gap: 0.55rem; }
.jt-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--c); box-shadow: 0 0 10px var(--c); }
.jt-count { background: #1C1C26; color: var(--jt-ink-2); border: 1px solid #2A2A35; border-radius: 999px;
            padding: 0 0.55rem; font-size: 0.76rem; font-family: 'Geist Mono', ui-monospace, monospace; }
.jt-card-title { font-weight: 600; line-height: 1.3; color: var(--jt-ink); }
.jt-card-role { color: var(--jt-ink-2); font-size: 0.88rem; line-height: 1.35; }
.jt-history { color: var(--jt-ink-2); font-size: 0.9rem; margin: 0.2rem 0 0.8rem; }

/* ---- the demo's "sample data" banner and footer ---- */
.jt-demo { display: flex; gap: 0.8rem; align-items: center; flex-wrap: wrap; padding: 0.7rem 1rem;
           border: 1px solid rgba(139, 124, 255, 0.35); border-radius: 0.9rem; margin-bottom: 1.4rem;
           background: linear-gradient(90deg, rgba(139, 124, 255, 0.13), rgba(139, 124, 255, 0.03));
           color: #D4D4D8; font-size: 0.92rem; }
.jt-demo b { color: var(--jt-ink); }
.jt-demo a, .jt-footer a { color: var(--jt-violet-hi); }
.jt-tag { font-family: 'Geist Mono', ui-monospace, monospace; font-size: 0.72rem; letter-spacing: 0.08em;
          text-transform: uppercase; color: var(--jt-bg); background: var(--jt-violet); border-radius: 999px;
          padding: 0.15rem 0.6rem; animation: jt-breathe 3.2s ease-in-out infinite; }
.jt-footer { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; margin-top: 3rem;
             padding-top: 1.1rem; border-top: 1px solid var(--jt-line); color: #8B8B96;
             font-family: 'Geist Mono', ui-monospace, monospace; font-size: 0.75rem; letter-spacing: 0.02em; }
.jt-footer a { text-decoration: none; }
.jt-footer a:hover { text-decoration: underline; }

/* ---- surfaces: quiet cards that light up violet when you point at them ---- */
[class*="st-key-jtcard"], [class*="st-key-jtrecent"], [data-testid="stMetric"] {
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.025), rgba(255, 255, 255, 0) 55%), var(--jt-surface);
}
[class*="st-key-jtcard"]:hover, [class*="st-key-jtrecent"]:hover, [data-testid="stMetric"]:hover {
  border-color: rgba(139, 124, 255, 0.55) !important;
}
[data-testid="stMetric"] { position: relative; overflow: hidden; }
[data-testid="stMetric"]::after {
  content: ""; position: absolute; left: 14%; right: 14%; top: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(185, 174, 255, 0.75), transparent);
}
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; letter-spacing: -0.035em; font-weight: 600; }
@media (max-width: 640px) {  /* two number tiles per row on phones instead of one */
  [data-testid="stColumn"]:has(> [data-testid="stVerticalBlock"] [data-testid="stMetric"]) {
    min-width: calc(50% - 0.5rem) !important; flex: 1 1 calc(50% - 0.5rem) !important;
  }
}
[data-testid="stDialog"] {
  background: rgba(5, 5, 8, 0.55) !important; -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
}
[data-testid="stDialog"] > div {
  background: var(--jt-surface) !important; border: 1px solid rgba(139, 124, 255, 0.28);
  box-shadow: 0 30px 90px rgba(0, 0, 0, 0.65), 0 0 70px rgba(139, 124, 255, 0.14) !important;
  animation: jt-pop 0.35s var(--jt-ease) both;
}
[data-testid="stToast"] { background: #15151D !important; border: 1px solid rgba(139, 124, 255, 0.35); }

/* ---- primary buttons: violet with dark text (6:1); the label rolls over when you point at it ---- */
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"],
.stDownloadButton button[kind="primary"] {
  color: var(--jt-bg) !important; font-weight: 600; border: 0 !important;
  background: linear-gradient(180deg, #A395FF, #8B7CFF) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 0 0 1px rgba(139, 124, 255, 0.5);
}
button[kind="primary"] [data-testid="stMarkdownContainer"],
button[kind="primaryFormSubmit"] [data-testid="stMarkdownContainer"] { overflow: hidden; }
button[kind="primary"] [data-testid="stMarkdownContainer"] p,
button[kind="primaryFormSubmit"] [data-testid="stMarkdownContainer"] p {
  margin: 0; line-height: 1.5; text-shadow: 0 1.5em 0 currentColor; transition: transform 0.45s var(--jt-ease);
}
button[kind="primary"]:hover [data-testid="stMarkdownContainer"] p,
button[kind="primaryFormSubmit"]:hover [data-testid="stMarkdownContainer"] p { transform: translateY(-1.5em); }

/* ---- motion: pages glide in, cards arrive one after another, things lift when you point at them ---- */
@keyframes jt-rise { from { opacity: 0; transform: translateY(14px); filter: blur(6px); }
                     to { opacity: 1; transform: none; filter: none; } }
@keyframes jt-pop { from { opacity: 0; transform: translateY(10px) scale(0.98); } to { opacity: 1; transform: none; } }
@keyframes jt-sheen { from { background-position: 100% 0; } to { background-position: 0 0; } }
@keyframes jt-breathe { 0%, 100% { box-shadow: 0 0 12px rgba(139, 124, 255, 0.4); }
                        50% { box-shadow: 0 0 24px rgba(139, 124, 255, 0.85); } }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > * {
  animation: jt-rise 0.6s var(--jt-ease) both;
}
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > :nth-child(2) { animation-delay: 60ms; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > :nth-child(3) { animation-delay: 120ms; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > :nth-child(4) { animation-delay: 180ms; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] > :nth-child(n+5) { animation-delay: 240ms; }
[class*="st-key-jtcard"], [class*="st-key-jtrecent"] {
  animation: jt-pop 0.45s var(--jt-ease) both;
  animation-delay: calc(var(--jt-i, 12) * 45ms + 150ms);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
[class*="st-key-jtcard"]:hover, [class*="st-key-jtrecent"]:hover, [data-testid="stMetric"]:hover {
  transform: translateY(-2px);
  box-shadow: 0 0 0 1px rgba(139, 124, 255, 0.25), 0 14px 36px rgba(139, 124, 255, 0.16);
}
[data-testid="stMetric"] { transition: transform 0.2s ease, box-shadow 0.2s ease; }
[data-testid="stColumn"] [data-testid="stMetric"] { animation: jt-pop 0.5s var(--jt-ease) both; }
[data-testid="stColumn"]:nth-child(2) [data-testid="stMetric"] { animation-delay: 70ms; }
[data-testid="stColumn"]:nth-child(3) [data-testid="stMetric"] { animation-delay: 140ms; }
[data-testid="stColumn"]:nth-child(4) [data-testid="stMetric"] { animation-delay: 210ms; }
[data-testid="stColumn"]:nth-child(5) [data-testid="stMetric"] { animation-delay: 280ms; }
.stButton button, .stFormSubmitButton button, .stDownloadButton button {
  transition: transform 0.15s ease, box-shadow 0.2s ease, background-color 0.15s ease;
}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button[kind="primaryFormSubmit"]:hover,
.stDownloadButton button[kind="primary"]:hover {
  transform: translateY(-1px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.35), 0 0 0 1px rgba(185, 174, 255, 0.7),
              0 8px 30px rgba(139, 124, 255, 0.5);
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
""" + "".join(f'[class*="st-key-jtcard{i}_"], [class*="st-key-jtrecent{i}_"] {{ --jt-i: {i}; }}\n' for i in range(13)) + """
</style>
"""


# ---------------------------------------------------------------- small helpers

def board_column(status):
    for name, _, statuses in BOARD:
        if statuses and status in statuses:
            return name
    return "In conversation"


def badge_color(status):
    if status in ("Ghosted", "Withdrawn"):
        return "gray"
    column = board_column(status)
    return next(color for name, color, _ in BOARD if name == column)


def card_key(kind, position, application_id):
    """Container key for a card; its position sets how long the entrance animation waits."""
    return f"{kind}{min(position, 12)}_{application_id}"


def short_date(value):
    stamp = pd.Timestamp(value)
    return f"{stamp:%b} {stamp.day}"


def esc(text):
    return html.escape(str(text or ""))


def percent(value):
    return "–" if value is None else f"{value:.0%}"


def heading(title, subtitle=None, eyebrow=None):
    if eyebrow:
        st.markdown(f"<div class='jt-eyebrow'>{eyebrow}</div>", unsafe_allow_html=True)
    st.title(title)
    if subtitle:
        st.markdown(f"<p class='jt-subtitle'>{subtitle}</p>", unsafe_allow_html=True)


def notify(message, icon=":material/check_circle:"):
    """Show a toast on the next run (after st.rerun)."""
    st.session_state.setdefault("toasts", []).append((message, icon))


def show_notices():
    for message, icon in st.session_state.pop("toasts", []):
        st.toast(message, icon=icon)
    if st.session_state.pop("celebrate", False):
        st.balloons()
    problem = st.session_state.pop("problem", None)
    if problem:
        st.error(problem, icon=":material/error:")


# ---------------------------------------------------------------- database

def open_db():
    """The database for this run, and whether to close it afterwards.
    The demo keeps one in-memory copy of the sample data per browser tab."""
    if st.session_state.get("demo"):
        if "demo_db" not in st.session_state:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            db = jl.Database(conn, "sqlite", "sample data")
            sample_data.build(db)
            st.session_state["demo_db"] = db
        return st.session_state["demo_db"], False
    sample_file = os.environ.get("JOB_TRACKER_TEST_SQLITE")  # automated checks only; the app itself uses MySQL
    if sample_file:
        conn = sqlite3.connect(sample_file)
        conn.execute("PRAGMA foreign_keys = ON")
        return jl.Database(conn, "sqlite", "sample data"), True
    return jl.get_db_connection(), True


def current_db():
    return st.session_state["_db"]


def plain_error(error):
    errno = getattr(error, "errno", None)
    if errno == 1045:
        if not os.environ.get("DB_PASSWORD"):
            return None  # first run: nothing entered yet
        return "MySQL says the user name or password is wrong. Check them below."
    if errno in (2003, 2005):
        return ("Couldn't reach MySQL. Make sure it's running (on Windows, the MySQL80 service) "
                "and that the host and port below are right.")
    return f"Couldn't connect to the database: {error}"


def resume_versions(db):
    return db.query("SELECT resume_version_id, version_name FROM resume_versions ORDER BY resume_version_id")


def last_resume_name(db):
    rows = db.query("SELECT rv.version_name FROM applications a JOIN resume_versions rv "
                    "ON rv.resume_version_id = a.resume_version_id ORDER BY a.application_id DESC LIMIT 1")
    return rows[0][0] if rows else None


def resume_id_for(db, picked, typed):
    """The id for the resume picked from the list, or for a newly typed name (added if it's new)."""
    if typed.strip():
        return jl.resolve_resume_version(db, typed.strip(), {})
    for version_id, name in resume_versions(db):
        if name == picked:
            return version_id
    return None


# ---------------------------------------------------------------- first run

def settings_form(first_run=False):
    """Connection details and the API key, saved to .env. Leaving a password field blank keeps what's saved."""
    env = os.environ
    with st.form("settings"):
        st.markdown("**MySQL**")
        left, right = st.columns(2)
        user = left.text_input("User", env.get("DB_USER", "root"))
        password = right.text_input("Password", type="password",
                                    placeholder="(saved)" if env.get("DB_PASSWORD") else "")
        host_col, port_col, name_col = st.columns([2, 1, 2])
        host = host_col.text_input("Host", env.get("DB_HOST", "127.0.0.1"))
        port = port_col.text_input("Port", env.get("DB_PORT", "3306"))
        name = name_col.text_input("Database", env.get("DB_NAME", "job_search_tracker"))
        st.markdown("**Claude API key** (optional: fills in details some job pages leave out)")
        api_key = st.text_input("Claude API key", type="password", label_visibility="collapsed",
                                placeholder="(saved)" if env.get("ANTHROPIC_API_KEY") else "sk-ant-...")
        st.caption("Saved in the .env file in this folder. It stays on your computer and is never uploaded.")
        if st.form_submit_button("Save and connect" if first_run else "Save", type="primary"):
            if not port.strip().isdigit():
                st.error("The port should be a number, like 3306.")
                return
            values = {"DB_USER": user, "DB_HOST": host, "DB_PORT": port, "DB_NAME": name}
            if password:
                values["DB_PASSWORD"] = password
            if api_key:
                values["ANTHROPIC_API_KEY"] = api_key
            jl.save_env(values)
            notify("Settings saved")
            st.rerun()


def connection_screen(error):
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        heading("Welcome to Job Search Tracker", "Connect your MySQL database to get started. You only do this once.")
        if isinstance(error, ImportError):
            st.error("A Python package is missing. Close this window and double-click start.bat again.")
            return
        if getattr(error, "errno", None) == 1049:  # MySQL is fine, the database doesn't exist yet
            name = os.environ.get("DB_NAME", "job_search_tracker")
            st.info(f"MySQL is running, but it doesn't have a database called '{name}' yet.")
            if st.button(f"Create '{name}'", type="primary"):
                try:
                    jl.get_db_connection(create_database=True).close()
                except Exception as e:
                    st.error(plain_error(e) or str(e))
                else:
                    st.rerun()
            return
        message = plain_error(error)
        if message:
            st.warning(message, icon=":material/warning:")
        settings_form(first_run=True)


def setup_screen(db, tables):
    _, middle, _ = st.columns([1, 2, 1])
    with middle:
        heading("Set up your database", f"Connected to {db.label}. It needs the tracker's tables "
                                         f"({', '.join(tables)}) before you start.")
        if st.button("Create the tables", type="primary", icon=":material/table_chart:"):
            jl.apply_schema_updates(db)
            st.rerun()


def update_banner(db):
    """Offer the one-time updates older databases need."""
    _, columns = jl.missing_schema(db)
    missing_applied = len(db.query(jl.MISSING_APPLIED_SQL))
    if not columns and not missing_applied:
        return
    parts = []
    if columns:
        parts.append(f"add the columns {', '.join(columns)}")
    if missing_applied:
        parts.append(f"give {missing_applied} older applications their 'Applied' status so the dashboard counts them")
    with st.container(border=True):
        text, action = st.columns([5, 1], vertical_alignment="center")
        text.markdown(f"**One-time database update:** {' and '.join(parts)}. Nothing is deleted or changed.")
        if action.button("Update now", type="primary", width="stretch"):
            jl.apply_schema_updates(db)
            jl.backfill_applied_events(db, assume_yes=True)
            notify("Database updated")
            st.rerun()


def demo_banner():
    st.markdown(
        f"<div class='jt-demo'><span class='jt-tag'>Sample data</span>"
        f"<span><b>The companies here are fictional.</b> Try logging a real job link, recording an update, or "
        f"exploring the dashboard. Your changes stay in this tab. <a href='{REPO_URL}' target='_blank'>"
        f"See the code</a></span></div>",
        unsafe_allow_html=True)


# ---------------------------------------------------------------- Log a job

def start_draft(result):
    st.session_state["draft_count"] = st.session_state.get("draft_count", 0) + 1
    st.session_state["draft"] = dict(result, id=st.session_state["draft_count"])


def read_into_draft(reader, value):
    try:
        with st.spinner("Reading the job posting..."):
            start_draft(reader(value))
    except capture.CaptureError as e:
        st.session_state["problem"] = str(e)


def page_log():
    db = current_db()
    incoming = st.query_params.get("url")  # from the "Log this job" bookmark
    if incoming:
        st.query_params.clear()
        read_into_draft(capture.read_link, incoming)
        st.rerun()

    draft = st.session_state.get("draft")
    if draft:
        draft_view(db, draft)
        return

    heading("Log a job", "Paste the link to a job you applied to. The details fill themselves in.", "01 · Log")
    with st.form("link"):
        entry, action = st.columns([5, 1], vertical_alignment="bottom")
        url = entry.text_input("Job link", placeholder="https://careers.example.com/jobs/data-analyst",
                               icon=":material/link:")
        if action.form_submit_button("Read job", type="primary", width="stretch"):
            read_into_draft(capture.read_link, url)
            st.rerun()
    paste_col, manual_col, _ = st.columns([1, 1, 3])
    with paste_col.popover("Paste a description", icon=":material/description:", width="stretch"):
        with st.form("paste", border=False):
            text = st.text_area("Job description", height=220, placeholder="Paste the whole posting here")
            if st.form_submit_button("Read description", type="primary"):
                read_into_draft(capture.read_text, text)
                st.rerun()
    if manual_col.button("Type it in myself", icon=":material/edit_note:", width="stretch"):
        start_draft({"source": ""})
        st.rerun()
    tip = "Works with most career sites, including Greenhouse, LinkedIn, Workday, Lever, Ashby, iCIMS and Oracle."
    if not capture.has_api_key():
        tip += " Add a Claude API key in Settings to fill in details some pages leave out, like the industry."
    st.caption(tip)
    recent_applications(db)


def recent_applications(db):
    rows = db.query(jl.CURRENT_STATUS_SQL)[:5]
    if not rows:
        return
    st.subheader("Recently logged")
    for position, (app_id, company, role, applied, status) in enumerate(rows):
        status = status or "Applied"
        with st.container(border=True, key=card_key("jtrecent", position, app_id)):
            text, badge = st.columns([5, 1], vertical_alignment="center")
            text.markdown(f"<div class='jt-card-title'>{esc(company)}</div>"
                          f"<div class='jt-card-role'>{esc(role)} · applied {short_date(applied)}</div>",
                          unsafe_allow_html=True)
            with badge:
                st.badge(status, color=badge_color(status))


def draft_view(db, draft):
    key = f"draft{draft['id']}"
    source = draft.get("source")
    heading("Check the details",
            f"Filled in from {source}. Change anything that's off, then save." if source else
            "Fill in the job, then save.", "01 · Log")
    if draft.get("warning"):
        st.warning(draft["warning"], icon=":material/info:")
    existing = jl.match_existing(jl.load_existing(db), draft.get("company_name", ""),
                                 draft.get("role_title", ""), draft.get("url", ""))
    if existing:
        st.warning(f"You already logged this job ({existing[0]}, applied {existing[1]}).",
                   icon=":material/content_copy:")
    show_duplicate_box = bool(existing) or st.session_state.get(f"{key}-duplicate")

    guess = jl.guess_channel(draft.get("url"))
    channels = jl.unique(jl.known_channels(db) + [guess])
    resume_options = [name for _, name in resume_versions(db)] + [NO_RESUME]
    last = last_resume_name(db)

    with st.form(key):
        st.markdown("**The job**")
        left, right = st.columns(2)
        company = left.text_input("Company *", draft.get("company_name", ""))
        role = right.text_input("Role *", draft.get("role_title", ""))
        first, second, third = st.columns(3)
        location = first.text_input("Location", draft.get("location", ""))
        pay = second.text_input("Pay", draft.get("salary_range", ""))
        industry = third.text_input("Industry", draft.get("industry", ""))
        url = st.text_input("Job link", draft.get("url", ""))

        st.markdown("**Your application**")
        first, second, third = st.columns(3)
        applied = first.date_input("Date applied", value=date.today(), max_value=date.today(), format="MM/DD/YYYY")
        channel = second.selectbox("Where you found it", channels,
                                   index=channels.index(guess) if guess in channels else 0)
        new_channel = second.text_input("Or a new one", placeholder="e.g. Hospital career site")
        resume = third.selectbox("Resume you sent", resume_options,
                                 index=resume_options.index(last) if last in resume_options else len(resume_options) - 1)
        new_resume = third.text_input("Or a new resume version", max_chars=50, placeholder="e.g. ICU RN")
        notes = st.text_area("Notes", height=80, placeholder="Referral, recruiter's name, salary you asked for...")
        with st.expander("Job description (kept so you have it for interviews)", icon=":material/article:"):
            description = st.text_area("Job description", draft.get("description", ""), height=220,
                                       label_visibility="collapsed")
        save_anyway = st.checkbox("Log it again anyway") if show_duplicate_box else False
        save_col, cancel_col, _ = st.columns([1, 1, 4])
        save = save_col.form_submit_button("Save application", type="primary", icon=":material/check:",
                                           width="stretch")
        cancel = cancel_col.form_submit_button("Start over", width="stretch")

    if cancel:
        st.session_state.pop("draft", None)
        st.rerun()
    if not save:
        return
    if not company.strip() or not role.strip():
        st.error("Company and role are required.")
        return
    duplicate = jl.match_existing(jl.load_existing(db), company, role, url)
    if duplicate and not save_anyway:
        st.session_state[f"{key}-duplicate"] = True
        st.session_state["problem"] = (f"You already logged this job ({duplicate[0]}, applied {duplicate[1]}). "
                                       "Tick 'Log it again anyway' and save again to keep both.")
        st.rerun()
    app_id = jl.insert_application(db, {
        "company_name": company.strip(),
        "role_title": role.strip(),
        "location": location.strip() or None,
        "date_applied": applied,
        "channel": new_channel.strip() or channel,
        "resume_version_id": resume_id_for(db, resume, new_resume),
        "job_posting_url": url.strip() or None,
        "industry": industry.strip() or None,
        "salary_listed": pay.strip() or None,
        "notes": notes.strip() or None,
        "job_description": description.strip() or None,
    })
    st.session_state.pop("draft", None)
    notify(f"Logged {company.strip()} as application #{app_id}")
    st.rerun()


# ---------------------------------------------------------------- Pipeline

def reset_table_selection():
    st.session_state["table_version"] = st.session_state.get("table_version", 0) + 1


@st.dialog("Record an update", on_dismiss=reset_table_selection)
def update_dialog(application_id):
    # Dialogs rerun on their own, after the main run has closed its connection, so open one here
    db, close_after = open_db()
    try:
        rows = db.query("SELECT company_name, role_title, job_posting_url FROM applications "
                        "WHERE application_id = %s", (application_id,))
        if not rows:
            st.error("That application isn't there anymore.")
            return
        company, role, link = rows[0]
        history = db.query("SELECT status, event_date FROM status_events WHERE application_id = %s "
                           "ORDER BY event_date, status_event_id", (application_id,))
        st.markdown(f"**{esc(company)}**<br><span class='jt-card-role'>{esc(role)}</span>", unsafe_allow_html=True)
        if history:
            st.markdown("<div class='jt-history'>" + " → ".join(f"{esc(status)} ({short_date(when)})"
                                                                for status, when in history) + "</div>",
                        unsafe_allow_html=True)
        if link and link.startswith(("http://", "https://")):
            st.link_button("Open the posting", link, icon=":material/open_in_new:")
        with st.form("update-form", border=False):
            status = st.selectbox("What happened", jl.update_statuses(db))
            custom = st.text_input("Or type your own", placeholder="e.g. Skills test")
            when = st.date_input("When", value=date.today(), format="MM/DD/YYYY")
            notes = st.text_area("Notes", height=90, placeholder="Who you talked to, next steps...")
            if st.form_submit_button("Save update", type="primary", width="stretch"):
                status = jl.normalize_status(custom) if custom.strip() else status
                jl.add_status(db, application_id, status, when, notes.strip() or None)
                reset_table_selection()
                if status == "Offer":
                    st.session_state["celebrate"] = True
                    notify(f"An offer from {company}. Congratulations!", icon=":material/celebration:")
                else:
                    notify(f"{company} is now '{status}'")
                st.rerun()
    finally:
        if close_after:
            db.close()


def application_card(row, position):
    with st.container(border=True, key=card_key("jtcard", position, row.application_id)):
        st.markdown(f"<div class='jt-card-title'>{esc(row.company)}</div>"
                    f"<div class='jt-card-role'>{esc(row.role)}</div>", unsafe_allow_html=True)
        st.badge(row.status, color=badge_color(row.status))
        st.caption(f"{short_date(row.date_applied)} · {esc(row.channel)}")
        if st.button("Update", key=f"update-{row.application_id}", icon=":material/edit:", type="tertiary"):
            update_dialog(int(row.application_id))


def pipeline_board(summary):
    summary = summary.assign(column=summary["status"].map(board_column))
    for (name, color, _), area in zip(BOARD, st.columns(len(BOARD), gap="small")):
        rows = summary[summary["column"] == name].sort_values(["status_date", "application_id"], ascending=False)
        with area:
            st.markdown(f"<div class='jt-col-head'><span><span class='jt-dot' style='--c: {DOT_COLORS[color]}'>"
                        f"</span>{name}</span><span class='jt-count'>{len(rows)}</span></div>",
                        unsafe_allow_html=True)
            with st.container(height=680, border=False):
                for position, row in enumerate(rows.head(CARDS_PER_COLUMN).itertuples()):
                    application_card(row, position)
                if len(rows) > CARDS_PER_COLUMN:
                    st.caption(f"+{len(rows) - CARDS_PER_COLUMN} more in the Table view")
                if rows.empty:
                    st.caption("Nothing here yet")


def pipeline_table(summary):
    view = summary.sort_values(["date_applied", "application_id"], ascending=False)
    table = pd.DataFrame({
        "#": view["application_id"],
        "Applied": view["date_applied"].dt.date,
        "Company": view["company"],
        "Role": view["role"],
        "Status": view["status"],
        "Since": view["status_date"].dt.date,
        "Channel": view["channel"],
        "Resume": view["resume_version"],
        "Link": view["link"],
    })
    st.caption("Tick the box at the start of a row to record an update.")
    event = st.dataframe(table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"pipeline-table-{st.session_state.get('table_version', 0)}",
                         column_config={"Link": st.column_config.LinkColumn("Link", display_text="Open"),
                                        "Applied": st.column_config.DateColumn("Applied", format="MMM D, YYYY"),
                                        "Since": st.column_config.DateColumn("Since", format="MMM D")})
    if event.selection.rows:
        update_dialog(int(table.iloc[event.selection.rows[0]]["#"]))


def page_pipeline():
    db = current_db()
    heading("Pipeline", "Every application and where it stands.", "02 · Track")
    apps, events = analytics.load(db)
    if apps.empty:
        st.info("No applications yet. Log your first one on the Log a job page.", icon=":material/inbox:")
        return
    summary = analytics.summarize(apps, events)
    search_col, view_col = st.columns([4, 1], vertical_alignment="bottom")
    search = search_col.text_input("Search", placeholder="Search by company or role", icon=":material/search:",
                                   label_visibility="collapsed")
    view = view_col.segmented_control("View", ["Board", "Table"], default="Board", required=True,
                                      label_visibility="collapsed", key="pipeline-view")
    if search.strip():
        text = (summary["company"] + " " + summary["role"]).str.lower()
        summary = summary[text.str.contains(search.strip().lower(), regex=False)]
        if summary.empty:
            st.caption("No applications match that search.")
            return
    if view == "Table":
        pipeline_table(summary)
    else:
        pipeline_board(summary)


# ---------------------------------------------------------------- Dashboard

def styled(chart, height):
    return (chart.properties(height=height, padding={"left": 8, "right": 8, "top": 4, "bottom": 4})
            .configure(font=FONT, background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=MUTED, titleColor=INK_2, gridColor=GRID, gridWidth=1, domainColor=AXIS,
                            tickColor=AXIS, labelFontSize=12, titleFontWeight="normal"))


def funnel_chart(data):
    data = data.assign(label=[f"{count}  ({share:.0%})" for count, share in zip(data["applications"], data["share"])])
    top = max(1, int(data["applications"].max()))
    base = alt.Chart(data).encode(
        y=alt.Y("stage:N", sort=analytics.FUNNEL, title=None,
                axis=alt.Axis(domain=False, ticks=False, labelPadding=8, labelColor=INK_2)),
        x=alt.X("applications:Q", title=None, scale=alt.Scale(domain=[0, top * 1.35]),
                axis=alt.Axis(format="d", tickMinStep=1, values=list(range(0, top + 1, max(1, top // 4))))),
        tooltip=[alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("applications:Q", title="Applications"),
                 alt.Tooltip("share:Q", title="Share of all", format=".0%")],
    )
    bars = base.mark_bar(size=22, cornerRadiusEnd=4).encode(
        color=alt.Color("stage:N", scale=alt.Scale(domain=analytics.FUNNEL, range=FUNNEL_SHADES), legend=None))
    labels = base.mark_text(align="left", dx=6, color=INK_2).encode(text="label:N")
    return styled(bars + labels, 200)


def weekly_chart(data):
    one_year = data["week"].dt.year.nunique() <= 1
    data = data.assign(label=data["week"].dt.strftime("%b %d" if one_year else "%b %d, %Y"))
    return styled(alt.Chart(data).mark_bar(size=18, cornerRadiusEnd=4, color=ACCENT).encode(
        x=alt.X("label:O", sort=None, title=None, axis=alt.Axis(labelAngle=0, labelOverlap="greedy", grid=False)),
        y=alt.Y("applications:Q", title=None, axis=alt.Axis(format="d", tickMinStep=1)),
        tooltip=[alt.Tooltip("label:N", title="Week of"), alt.Tooltip("applications:Q", title="Applications"),
                 alt.Tooltip("running_total:Q", title="Running total")],
    ), 200)


def rate_chart(data, column):
    data = data.assign(label=[f"{rate:.0%}  ({int(replies)} of {int(total)})"
                              for rate, replies, total in zip(data["response_rate"], data["replies"],
                                                              data["applications"])])
    top = max(0.1, float(data["response_rate"].max()))
    base = alt.Chart(data).encode(
        y=alt.Y(f"{column}:N", sort="-x", title=None,
                axis=alt.Axis(domain=False, ticks=False, labelPadding=8, labelLimit=200, labelColor=INK_2)),
        x=alt.X("response_rate:Q", title=None, scale=alt.Scale(domain=[0, top * 1.45]),
                axis=alt.Axis(format="%", values=[v for v in (0, 0.25, 0.5, 0.75, 1) if v <= top * 1.45])),
        tooltip=[alt.Tooltip(f"{column}:N", title=column.replace("_", " ").capitalize()),
                 alt.Tooltip("applications:Q", title="Applications"),
                 alt.Tooltip("response_rate:Q", title="Response rate", format=".0%"),
                 alt.Tooltip("interview_rate:Q", title="Interview rate", format=".0%")],
    )
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=ACCENT)
    labels = base.mark_text(align="left", dx=6, color=INK_2).encode(text="label:N")
    return styled(bars + labels, 38 * len(data) + 24)


def rates_table(data, label):
    return pd.DataFrame({
        label: data.iloc[:, 0],
        "Applications": data["applications"],
        "Replies": data["replies"],
        "Response rate": data["response_rate"].map(percent),
        "Interviews": data["interviews"],
        "Interview rate": data["interview_rate"].map(percent),
    })


def chart_card(title, chart, table):
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.altair_chart(chart, width="stretch", theme=None)
        with st.expander("Show as a table"):
            st.dataframe(table, hide_index=True, width="stretch")


PERIODS = {"Last 30 days": 30, "Last 90 days": 90, "All time": None}


def page_dashboard():
    db = current_db()
    heading("Dashboard", "What's working in your search.", "03 · Learn")
    apps, events = analytics.load(db)
    if apps.empty:
        st.info("Nothing to show yet. Log a few applications first.", icon=":material/insights:")
        return
    period = st.segmented_control("Period", list(PERIODS), default="All time", required=True,
                                  label_visibility="collapsed", key="dashboard-period")
    days = PERIODS[period]
    if days:
        apps = apps[apps["date_applied"] >= pd.Timestamp(date.today() - timedelta(days=days))]
        events = events[events["application_id"].isin(apps["application_id"])]
        if apps.empty:
            st.info("No applications in this period.", icon=":material/event_busy:")
            return
    summary = analytics.summarize(apps, events)
    numbers = analytics.kpis(summary)
    tiles = st.columns(5)
    tiles[0].metric("Applications", f"{numbers['applications']:,}", border=True)
    tiles[1].metric("This week", numbers["this_week"], border=True)
    tiles[2].metric("Response rate", percent(numbers["response_rate"]), border=True,
                    help="Share of applications where the employer replied in any way, rejections included.")
    tiles[3].metric("Interview rate", percent(numbers["interview_rate"]), border=True,
                    help="Share of applications that reached an interview or an offer.")
    tiles[4].metric("No word in 3+ weeks", numbers["no_word"], border=True,
                    help="Applied 21 or more days ago with no update since.")

    stages = analytics.funnel(summary)
    weeks = analytics.weekly(summary)
    left, right = st.columns(2)
    with left:
        chart_card("How far applications get", funnel_chart(stages),
                   stages.assign(share=stages["share"].map(percent)).rename(
                       columns={"stage": "Stage", "applications": "Applications", "share": "Share of all"}))
    with right:
        chart_card("Applications per week", weekly_chart(weeks),
                   pd.DataFrame({"Week of": weeks["week"].dt.date, "Applications": weeks["applications"],
                                 "Running total": weeks["running_total"]}))
    left, right = st.columns(2)
    for column, title, label, area in (("resume_version", "Response rate by resume", "Resume", left),
                                       ("channel", "Response rate by channel", "Channel", right)):
        rates = analytics.rates_by(summary, column)
        with area:
            chart_card(title, rate_chart(rates, column), rates_table(rates, label))

    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**No word in 3+ weeks**")
        quiet = analytics.waiting(summary)
        if quiet.empty:
            st.caption("Nothing waiting that long.")
        else:
            st.dataframe(pd.DataFrame({"Company": quiet["company"], "Days": quiet["days_since_applied"],
                                       "Applied": quiet["date_applied"].dt.date, "Role": quiet["role"]}),
                         hide_index=True, width="stretch")
            st.caption("Worth a follow-up, or mark them Ghosted on the Pipeline page.")
    with right.container(border=True):
        st.markdown("**Time to first reply**")
        replies = analytics.reply_times(summary)
        if replies.empty:
            st.caption("No replies recorded yet. Add them on the Pipeline page as they come in.")
        else:
            st.dataframe(pd.DataFrame({"Company": replies["company"], "Days": replies["days_to_reply"].astype(int),
                                       "Now": replies["status"], "Role": replies["role"]}),
                         hide_index=True, width="stretch")


# ---------------------------------------------------------------- Settings

def import_preview(db, rows):
    if not rows:
        st.error("That file is empty.")
        return
    plan = jl.plan_import(db, rows)
    if plan["missing"]:
        st.error(f"Couldn't find a column for: {', '.join(plan['missing'])}. "
                 f"The file's columns are: {', '.join(plan['headers'])}")
        return
    st.markdown(f"**{len(plan['new'])} new**, {len(plan['duplicates'])} already logged, "
                f"{len(plan['problems'])} with problems.")
    if plan["duplicates"] or plan["problems"]:
        with st.expander("Details"):
            st.text("\n".join(plan["problems"] + plan["duplicates"]))
    if not plan["new"]:
        return
    default_resume_id = None
    if "resume_version" not in plan["mapping"]:
        options = [name for _, name in resume_versions(db)] + [NO_RESUME]
        picked = st.selectbox("Resume version for these applications", options, index=len(options) - 1)
        default_resume_id = resume_id_for(db, picked, "")
    if st.button(f"Import {len(plan['new'])} applications", type="primary", icon=":material/upload:"):
        try:
            added = jl.run_import(db, plan, default_resume_id)
        except RuntimeError as e:
            st.error(str(e))
        else:
            notify(f"Imported {added} applications")
            st.rerun()


def page_settings():
    db = current_db()
    demo = st.session_state.get("demo")
    heading("Settings", eyebrow="04 · Set up")
    connection, resumes, files, bookmark = st.tabs(["Connection", "Resume versions", "Import & export",
                                                    "Log this job bookmark"])
    with connection:
        if demo:
            st.info(f"This demo runs on made-up data, so there's no database to connect. To track your own "
                    f"search, get the app from [GitHub]({REPO_URL}) and run it on your computer.",
                    icon=":material/science:")
        else:
            st.caption(f"Connected to {db.label}.")
            settings_form()
    with resumes:
        counts = db.query("SELECT rv.version_name, COUNT(a.application_id) FROM resume_versions rv "
                          "LEFT JOIN applications a ON a.resume_version_id = rv.resume_version_id "
                          "GROUP BY rv.resume_version_id, rv.version_name ORDER BY rv.resume_version_id")
        if counts:
            st.dataframe(pd.DataFrame(counts, columns=["Resume version", "Applications"]), hide_index=True)
        with st.form("new-resume", clear_on_submit=True):
            name = st.text_input("Add a resume version", max_chars=50,
                                 placeholder="e.g. Data Analytics, ICU RN, Retail management")
            if st.form_submit_button("Add", icon=":material/add:") and name.strip():
                jl.resolve_resume_version(db, name.strip(), {})
                db.commit()
                notify(f"Added '{name.strip()}'")
                st.rerun()
    with files:
        st.markdown("**Import from a spreadsheet**")
        st.caption("Save your sheet as CSV (Excel: File > Save As > CSV; Google Sheets: File > Download > CSV). "
                   "It needs columns for company, role and date applied; link, channel, pay, status and notes "
                   "are used too.")
        upload = st.file_uploader("CSV file", type=["csv"], label_visibility="collapsed")
        if upload is not None:
            import_preview(db, jl.decode_csv(upload.getvalue()))
        st.divider()
        st.markdown("**Export**")
        rows = jl.export_rows(db)
        st.download_button(f"Download all {len(rows)} applications (CSV)", data=jl.export_csv_bytes(rows),
                           file_name=f"applications_{date.today()}.csv", mime="text/csv", disabled=not rows,
                           icon=":material/download:")
    with bookmark:
        if demo:
            st.caption("The bookmark opens the app running on your own computer, so it isn't part of the demo.")
        else:
            port = st.get_option("server.port") or 8501
            st.write("Make a bookmark in your browser, name it **Log this job**, and paste this as its address. "
                     "Clicking it on any job posting opens the tracker with that job filled in.")
            st.code(f"javascript:void(window.open('http://localhost:{port}/?url='"
                    f"+encodeURIComponent(location.href)))", language="text")


# ---------------------------------------------------------------- main

PAGES = [
    (page_log, "Log a job", ":material/add_link:", "log"),
    (page_pipeline, "Pipeline", ":material/view_kanban:", "pipeline"),
    (page_dashboard, "Dashboard", ":material/insights:", "dashboard"),
    (page_settings, "Settings", ":material/settings:", "settings"),
]


def footer():
    st.markdown(f"<div class='jt-footer'><span>Job Search Tracker · sample data, reset when you close the tab</span>"
                f"<span>Built by <a href='{AUTHOR_URL}' target='_blank'>Mojolaoluwa (David) Babafemi</a> · "
                f"<a href='{REPO_URL}' target='_blank'>Source on GitHub</a></span></div>", unsafe_allow_html=True)


def main(demo=False):
    st.set_page_config(page_title="Job Search Tracker", page_icon=LOGO, layout="wide")
    st.logo(LOGO, size="medium")
    st.markdown(CSS, unsafe_allow_html=True)
    if demo:
        st.session_state["demo"] = True
    jl.load_env()
    try:
        db, close_after = open_db()
    except Exception as e:
        show_notices()
        connection_screen(e)
        return
    try:
        tables, _ = jl.missing_schema(db)
        if tables:
            setup_screen(db, tables)
            return
        jl.load_optional_columns(db)
        st.session_state["_db"] = db
        navigation = st.navigation(
            [st.Page(page, title=title, icon=icon, url_path=path, default=index == 0)
             for index, (page, title, icon, path) in enumerate(PAGES)],
            position="top")
        show_notices()
        if demo:
            demo_banner()
        else:
            update_banner(db)
        navigation.run()
        if demo:
            footer()
    finally:
        if close_after:
            db.close()


if __name__ == "__main__":
    main()

"""
app.py
-------
The Job Search Tracker as a local web app: log a job from its link, record what happens,
and see what's working.

    streamlit run app.py      (or double-click start.bat)
"""

import os
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

import analytics
import capture
import job_logger as jl

st.set_page_config(page_title="Job Search Tracker", layout="wide")

PAGES = ["Log a job", "Pipeline", "Dashboard", "Settings"]
NO_RESUME = "No resume version"

# Chart colors, checked for contrast and color-blind safety on the app's #fcfcfb background
BLUE = "#2a78d6"                                            # the single series color
FUNNEL_BLUES = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab"]  # one hue, light (first stage) to dark
INK_2, MUTED, GRID, AXIS = "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


# ---------------------------------------------------------------- Streamlit version differences

def rerun():
    (getattr(st, "rerun", None) or st.experimental_rerun)()


def query_param(name):
    if hasattr(st, "query_params"):
        return st.query_params.get(name)
    values = st.experimental_get_query_params().get(name)
    return values[0] if values else None


def clear_query_params():
    if hasattr(st, "query_params"):
        st.query_params.clear()
    else:
        st.experimental_set_query_params()


def link_column(label):
    config = getattr(st, "column_config", None)
    return config.LinkColumn(label) if config and hasattr(config, "LinkColumn") else label


def percent(value):
    return "–" if value is None else f"{value:.0%}"


# ---------------------------------------------------------------- database and setup

def connect():
    test_db = os.environ.get("JOB_TRACKER_TEST_SQLITE")  # only for the automated checks; the app uses MySQL
    if test_db:
        import sqlite3

        conn = sqlite3.connect(test_db)
        conn.execute("PRAGMA foreign_keys = ON")
        return jl.Database(conn, "sqlite", "sample data")
    return jl.get_db_connection()


def plain_error(error):
    errno = getattr(error, "errno", None)
    if errno == 1045:
        return "MySQL says the user name or password is wrong. Check them below."
    if errno in (2003, 2005):
        return ("Couldn't reach MySQL. Make sure it's running (on Windows, the MySQL80 service) "
                "and that the host and port below are right.")
    return f"Couldn't connect to the database: {error}"


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
        st.markdown("**Claude API key** (optional: lets the tracker fill in details from job pages)")
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
            st.session_state["notice"] = "Settings saved."
            rerun()


def connection_screen(error):
    st.title("Job Search Tracker")
    st.subheader("Connect to your MySQL database")
    if isinstance(error, ImportError):
        st.error("A Python package is missing. In a terminal in this folder, run: pip install -r requirements.txt")
        return
    if getattr(error, "errno", None) == 1049:  # MySQL is fine, the database doesn't exist yet
        name = os.environ.get("DB_NAME", "job_search_tracker")
        st.info(f"MySQL is running, but it doesn't have a database called '{name}' yet.")
        if st.button(f"Create '{name}'", type="primary"):
            try:
                jl.get_db_connection(create_database=True).close()
            except Exception as e:
                st.error(plain_error(e))
            else:
                rerun()
        return
    st.warning(plain_error(error))
    settings_form(first_run=True)


def setup_screen(db, tables):
    st.title("Job Search Tracker")
    st.subheader("Set up your database")
    st.write(f"Connected to {db.label}, which doesn't have the tracker's tables yet ({', '.join(tables)}).")
    if st.button("Create the tables", type="primary"):
        jl.apply_schema_updates(db)
        rerun()


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
    st.info(f"One-time database update: {' and '.join(parts)}. Nothing is deleted or changed.")
    if st.button("Update now"):
        jl.apply_schema_updates(db)
        jl.backfill_applied_events(db, assume_yes=True)
        rerun()


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


# ---------------------------------------------------------------- Log a job

def start_draft(result):
    st.session_state["draft_count"] = st.session_state.get("draft_count", 0) + 1
    st.session_state["draft"] = dict(result, id=st.session_state["draft_count"])


def read_into_draft(reader, value):
    try:
        with st.spinner("Reading the job posting..."):
            start_draft(reader(value))
    except capture.CaptureError as e:
        st.session_state["notice_error"] = str(e)


def page_log(db):
    st.title("Log a job")
    incoming = query_param("url")  # from the "Log this job" bookmark
    if incoming:
        clear_query_params()
        read_into_draft(capture.read_link, incoming)
        rerun()

    draft = st.session_state.get("draft")
    if draft:
        draft_form(db, draft)
        return

    with st.form("link"):
        url = st.text_input("Job link", placeholder="Paste the link to the job posting")
        if st.form_submit_button("Read the job", type="primary"):
            read_into_draft(capture.read_link, url)
            rerun()
    if not capture.has_api_key():
        st.caption("Most job sites work without an API key. Add a Claude API key in Settings to fill in "
                   "the rest (like the industry) automatically.")
    with st.expander("No link? Paste the job description instead"):
        with st.form("paste"):
            text = st.text_area("Job description", height=200)
            if st.form_submit_button("Read the description"):
                read_into_draft(capture.read_text, text)
                rerun()
    if st.button("Type the details in myself"):
        start_draft({"source": ""})
        rerun()


def draft_form(db, draft):
    key = f"draft{draft['id']}"
    if draft.get("source"):
        st.caption(f"Filled in from {draft['source']}. Check everything before saving.")
    if draft.get("warning"):
        st.warning(draft["warning"])
    existing = jl.match_existing(jl.load_existing(db), draft.get("company_name", ""),
                                 draft.get("role_title", ""), draft.get("url", ""))
    if existing:
        st.warning(f"You already logged this job ({existing[0]}, applied {existing[1]}).")
    show_duplicate_box = bool(existing) or st.session_state.get(f"{key}-duplicate")

    guess = jl.guess_channel(draft.get("url"))
    channels = jl.unique(jl.known_channels(db) + [guess])
    resume_options = [name for _, name in resume_versions(db)] + [NO_RESUME]
    last = last_resume_name(db)

    with st.form(key):
        left, right = st.columns(2)
        company = left.text_input("Company *", draft.get("company_name", ""))
        role = right.text_input("Role *", draft.get("role_title", ""))
        first, second, third = st.columns(3)
        location = first.text_input("Location", draft.get("location", ""))
        pay = second.text_input("Pay", draft.get("salary_range", ""))
        industry = third.text_input("Industry", draft.get("industry", ""))
        url = st.text_input("Job link", draft.get("url", ""))
        first, second, third = st.columns(3)
        applied = first.date_input("Date applied", value=date.today(), max_value=date.today())
        channel = second.selectbox("Where you found it", channels,
                                   index=channels.index(guess) if guess in channels else 0)
        new_channel = second.text_input("Or a new one", placeholder="e.g. Hospital career site")
        resume = third.selectbox("Resume you sent", resume_options,
                                 index=resume_options.index(last) if last in resume_options else len(resume_options) - 1)
        new_resume = third.text_input("Or a new resume version", max_chars=50, placeholder="e.g. ICU RN")
        notes = st.text_area("Notes", height=70, placeholder="Referral, recruiter's name, salary you asked for...")
        description = st.text_area("Job description (kept so you have it for interviews)",
                                   draft.get("description", ""), height=160)
        save_anyway = st.checkbox("Log it again anyway") if show_duplicate_box else False
        save_col, cancel_col, _ = st.columns([1, 1, 4])
        save = save_col.form_submit_button("Save", type="primary")
        cancel = cancel_col.form_submit_button("Start over")

    if cancel:
        st.session_state.pop("draft", None)
        rerun()
    if not save:
        return
    if not company.strip() or not role.strip():
        st.error("Company and role are required.")
        return
    duplicate = jl.match_existing(jl.load_existing(db), company, role, url)
    if duplicate and not save_anyway:
        st.session_state[f"{key}-duplicate"] = True
        st.session_state["notice_error"] = (f"You already logged this job ({duplicate[0]}, applied {duplicate[1]}). "
                                            "Tick 'Log it again anyway' and save again to keep both.")
        rerun()
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
    st.session_state["notice"] = f"Logged {company.strip()} - {role.strip()} as application #{app_id}."
    rerun()


# ---------------------------------------------------------------- Pipeline

def page_pipeline(db):
    st.title("Pipeline")
    apps, events = analytics.load(db)
    if apps.empty:
        st.info("No applications yet. Log your first one on the 'Log a job' page.")
        return
    summary = analytics.summarize(apps, events)

    search_col, status_col = st.columns([2, 3])
    search = search_col.text_input("Search", placeholder="Company or role")
    chosen = status_col.multiselect("Current status", sorted(summary["status"].unique()))
    view = summary.sort_values(["date_applied", "application_id"], ascending=False)
    if search.strip():
        text = (view["company"] + " " + view["role"]).str.lower()
        view = view[text.str.contains(search.strip().lower(), regex=False)]
    if chosen:
        view = view[view["status"].isin(chosen)]

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
    st.dataframe(table, use_container_width=True, hide_index=True, column_config={"Link": link_column("Link")})
    st.caption(f"{len(view)} of {len(summary)} applications")

    st.subheader("Record an update")
    if view.empty:
        st.caption("No applications match the filters above.")
        return
    labels = {int(row.application_id): f"#{row.application_id}  {row.company} - {row.role}  ({row.status})"
              for row in view.itertuples()}
    with st.form("update"):
        application = st.selectbox("Application", list(labels), format_func=labels.get)
        pick_col, date_col, notes_col = st.columns([2, 1, 3])
        status = pick_col.selectbox("What happened", jl.update_statuses(db))
        custom = pick_col.text_input("Or type your own", placeholder="e.g. Skills test")
        when = date_col.date_input("When", value=date.today())
        notes = notes_col.text_area("Notes", height=108, placeholder="Who you talked to, next steps...")
        if st.form_submit_button("Save update", type="primary"):
            status = jl.normalize_status(custom) if custom.strip() else status
            jl.add_status(db, application, status, when, notes.strip() or None)
            row = summary[summary["application_id"] == application].iloc[0]
            st.session_state["notice"] = f"Saved: {row['company']} - {row['role']} is now '{status}' ({when})."
            rerun()

    with st.expander("Recent updates"):
        recent = (events.sort_values(["event_date", "event_id"], ascending=False).head(25)
                  .merge(apps[["application_id", "company", "role"]], on="application_id"))
        st.dataframe(pd.DataFrame({
            "Date": recent["event_date"].dt.date,
            "Company": recent["company"],
            "Role": recent["role"],
            "Status": recent["status"],
            "Notes": recent["notes"].fillna(""),
        }), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------- Dashboard

def styled(chart, height):
    return (chart.properties(height=height)
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
        color=alt.Color("stage:N", scale=alt.Scale(domain=analytics.FUNNEL, range=FUNNEL_BLUES), legend=None))
    labels = base.mark_text(align="left", dx=6, color=INK_2).encode(text="label:N")
    return styled(bars + labels, 200)  # same height as the weekly chart beside it


def weekly_chart(data):
    one_year = data["week"].dt.year.nunique() <= 1
    data = data.assign(label=data["week"].dt.strftime("%b %d" if one_year else "%b %d, %Y"))
    return styled(alt.Chart(data).mark_bar(size=18, cornerRadiusEnd=4, color=BLUE).encode(
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
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=BLUE)
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


def page_dashboard(db):
    st.title("Dashboard")
    apps, events = analytics.load(db)
    if apps.empty:
        st.info("Nothing to show yet. Log a few applications first.")
        return
    summary = analytics.summarize(apps, events)
    numbers = analytics.kpis(summary)
    tiles = st.columns(5)
    tiles[0].metric("Applications", f"{numbers['applications']:,}")
    tiles[1].metric("This week", numbers["this_week"])
    tiles[2].metric("Response rate", percent(numbers["response_rate"]),
                    help="Share of applications where the employer replied in any way, rejections included.")
    tiles[3].metric("Interview rate", percent(numbers["interview_rate"]),
                    help="Share of applications that reached an interview or an offer.")
    tiles[4].metric("No word in 3+ weeks", numbers["no_word"],
                    help="Applied 21 or more days ago with no update since.")

    left, right = st.columns(2)
    with left:
        st.subheader("How far applications get")
        stages = analytics.funnel(summary)
        st.altair_chart(funnel_chart(stages), use_container_width=True, theme=None)
        with st.expander("Show as a table"):
            st.dataframe(stages.assign(share=stages["share"].map(percent)).rename(
                columns={"stage": "Stage", "applications": "Applications", "share": "Share of all"}),
                use_container_width=True, hide_index=True)
    with right:
        st.subheader("Applications per week")
        weeks = analytics.weekly(summary)
        st.altair_chart(weekly_chart(weeks), use_container_width=True, theme=None)
        with st.expander("Show as a table"):
            st.dataframe(pd.DataFrame({"Week of": weeks["week"].dt.date, "Applications": weeks["applications"],
                                       "Running total": weeks["running_total"]}),
                         use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    for column, title, label, area in (("resume_version", "Response rate by resume", "Resume", left),
                                       ("channel", "Response rate by channel", "Channel", right)):
        with area:
            st.subheader(title)
            rates = analytics.rates_by(summary, column)
            st.altair_chart(rate_chart(rates, column), use_container_width=True, theme=None)
            with st.expander("Show as a table"):
                st.dataframe(rates_table(rates, label), use_container_width=True, hide_index=True)

    left, right = st.columns(2)
    with left:
        st.subheader("No word in 3+ weeks")
        quiet = analytics.waiting(summary)
        if quiet.empty:
            st.caption("Nothing waiting that long.")
        else:
            st.dataframe(pd.DataFrame({"Company": quiet["company"], "Days": quiet["days_since_applied"],
                                       "Applied": quiet["date_applied"].dt.date, "Role": quiet["role"]}),
                         use_container_width=True, hide_index=True)
            st.caption("Worth a follow-up, or mark them Ghosted on the Pipeline page.")
    with right:
        st.subheader("Time to first reply")
        replies = analytics.reply_times(summary)
        if replies.empty:
            st.caption("No replies recorded yet. Add them on the Pipeline page as they come in.")
        else:
            st.dataframe(pd.DataFrame({"Company": replies["company"], "Days": replies["days_to_reply"].astype(int),
                                       "Now": replies["status"], "Role": replies["role"]}),
                         use_container_width=True, hide_index=True)


# ---------------------------------------------------------------- Settings

def page_settings(db):
    st.title("Settings")
    st.caption(f"Connected to {db.label}.")

    st.subheader("Connection and API key")
    settings_form()

    st.subheader("Resume versions")
    counts = db.query("SELECT rv.version_name, COUNT(a.application_id) FROM resume_versions rv "
                      "LEFT JOIN applications a ON a.resume_version_id = rv.resume_version_id "
                      "GROUP BY rv.resume_version_id, rv.version_name ORDER BY rv.resume_version_id")
    if counts:
        st.dataframe(pd.DataFrame(counts, columns=["Resume version", "Applications"]), hide_index=True)
    with st.form("new-resume", clear_on_submit=True):
        name = st.text_input("Add a resume version", max_chars=50,
                             placeholder="e.g. Data Analytics, ICU RN, Retail management")
        if st.form_submit_button("Add") and name.strip():
            jl.resolve_resume_version(db, name.strip(), {})
            db.commit()
            st.session_state["notice"] = f"Added the resume version '{name.strip()}'."
            rerun()

    st.subheader("Import from a spreadsheet")
    st.caption("Save your sheet as CSV (Excel: File > Save As > CSV; Google Sheets: File > Download > CSV). "
               "It needs columns for company, role and date applied; link, channel, pay, status and notes are used too.")
    upload = st.file_uploader("CSV file", type=["csv"])
    if upload is not None:
        import_preview(db, jl.decode_csv(upload.getvalue()))

    st.subheader("Export")
    rows = jl.export_rows(db)
    st.download_button(f"Download all {len(rows)} applications (CSV)", data=jl.export_csv_bytes(rows),
                       file_name=f"applications_{date.today()}.csv", mime="text/csv", disabled=not rows)

    st.subheader("'Log this job' bookmark")
    port = st.get_option("server.port") or 8501
    st.write("Make a bookmark in your browser, name it **Log this job**, and paste this as its address. "
             "Clicking it on any job posting opens the tracker with that job filled in.")
    st.code(f"javascript:void(window.open('http://localhost:{port}/?url='+encodeURIComponent(location.href)))",
            language="text")


def import_preview(db, rows):
    if not rows:
        st.error("That file is empty.")
        return
    plan = jl.plan_import(db, rows)
    if plan["missing"]:
        st.error(f"Couldn't find a column for: {', '.join(plan['missing'])}. "
                 f"The file's columns are: {', '.join(plan['headers'])}")
        return
    st.write(f"**{len(plan['new'])} new**, {len(plan['duplicates'])} already logged, "
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
    if st.button(f"Import {len(plan['new'])} applications", type="primary"):
        try:
            added = jl.run_import(db, plan, default_resume_id)
        except RuntimeError as e:
            st.error(str(e))
        else:
            st.session_state["notice"] = f"Imported {added} applications."
            rerun()


# ---------------------------------------------------------------- main

def show_notices():
    notice, problem = st.session_state.pop("notice", None), st.session_state.pop("notice_error", None)
    if notice:
        st.success(notice)
    if problem:
        st.error(problem)


def main():
    jl.load_env()
    if query_param("url"):
        st.session_state["page"] = "Log a job"
    elif query_param("page") in PAGES:  # e.g. http://localhost:8501/?page=Dashboard
        st.session_state["page"] = query_param("page")
        clear_query_params()
    with st.sidebar:
        st.markdown("### Job Search Tracker")
        page = st.radio("Page", PAGES, key="page", label_visibility="collapsed")
    try:
        db = connect()
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
        show_notices()
        update_banner(db)
        {"Log a job": page_log, "Pipeline": page_pipeline, "Dashboard": page_dashboard,
         "Settings": page_settings}[page](db)
        st.sidebar.caption(f"Connected to {db.label}")
    finally:
        db.close()


main()

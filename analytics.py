"""
analytics.py
-------------
The dashboard numbers, computed with pandas from the three tables so the same code runs on MySQL
(the app) and SQLite (the tests). queries.sql has SQL versions for MySQL Workbench and Tableau.

A "response" is any status that isn't in job_logger.NOT_RESPONSES, so statuses you add for your
field ('Skills test', 'Demo lesson', 'Shadow shift') count automatically.
"""

from datetime import date, timedelta

import pandas as pd

from job_logger import NOT_RESPONSES

FUNNEL = ["Applied", "Phone Screen", "Interview", "Offer"]
GHOST_DAYS = 21  # applied this long ago with no update at all


def load(db):
    """(applications, status events) as DataFrames."""
    apps = pd.DataFrame(db.query(
        "SELECT a.application_id, a.company_name, a.role_title, a.date_applied, a.channel, "
        "rv.version_name, a.job_posting_url "
        "FROM applications a LEFT JOIN resume_versions rv ON rv.resume_version_id = a.resume_version_id"),
        columns=["application_id", "company", "role", "date_applied", "channel", "resume_version", "link"])
    events = pd.DataFrame(db.query(
        "SELECT status_event_id, application_id, status, event_date, notes FROM status_events"),
        columns=["event_id", "application_id", "status", "event_date", "notes"])
    apps["date_applied"] = pd.to_datetime(apps["date_applied"])
    events["event_date"] = pd.to_datetime(events["event_date"])
    apps["channel"] = apps["channel"].fillna("(not set)").replace("", "(not set)")
    apps["resume_version"] = apps["resume_version"].fillna("(none)")
    return apps, events


def stage_rank(status):
    """How far down the funnel a status is: 0 applied, 1 first reply, 2 interview, 3 offer."""
    if status in FUNNEL:
        return FUNNEL.index(status)
    if status in NOT_RESPONSES or status == "Rejected":
        return 0
    return 1  # a reply stage you added, such as 'Skills test' or 'Demo lesson'


def summarize(apps, events, today=None):
    """One row per application: current status, whether and when the employer replied, days waiting."""
    today = pd.Timestamp(today or date.today())
    ordered = events.sort_values(["event_date", "event_id"])
    latest = ordered.groupby("application_id").tail(1).set_index("application_id")
    replies = ordered[~ordered["status"].isin(NOT_RESPONSES)]
    first_reply = replies.groupby("application_id")["event_date"].min()
    best_rank = ordered.assign(rank=ordered["status"].map(stage_rank)).groupby("application_id")["rank"].max()
    updated = set(ordered.loc[ordered["status"] != "Applied", "application_id"])

    summary = apps.copy()
    ids = summary["application_id"]
    summary["status"] = ids.map(latest["status"]).fillna("Applied")
    summary["status_date"] = ids.map(latest["event_date"]).fillna(summary["date_applied"])
    summary["replied"] = ids.isin(first_reply.index)
    summary["first_reply"] = ids.map(first_reply)
    summary["days_to_reply"] = (summary["first_reply"] - summary["date_applied"]).dt.days
    summary["stage"] = ids.map(best_rank).fillna(0).astype(int)
    summary["interviewed"] = summary["stage"] >= 2
    summary["days_since_applied"] = (today - summary["date_applied"]).dt.days
    summary["no_word"] = ~ids.isin(updated) & (summary["days_since_applied"] >= GHOST_DAYS)
    return summary


def kpis(summary, today=None):
    today = today or date.today()
    week_start = pd.Timestamp(today - timedelta(days=today.weekday()))
    count = len(summary)
    return {
        "applications": count,
        "this_week": int((summary["date_applied"] >= week_start).sum()),
        "response_rate": float(summary["replied"].mean()) if count else None,
        "interview_rate": float(summary["interviewed"].mean()) if count else None,
        "no_word": int(summary["no_word"].sum()),
    }


def funnel(summary):
    """Applications that reached each stage or went further (an interview also counts as a first reply)."""
    total = len(summary)
    counts = [int((summary["stage"] >= rank).sum()) for rank in range(len(FUNNEL))]
    return pd.DataFrame({
        "stage": FUNNEL,
        "applications": counts,
        "share": [c / total if total else 0.0 for c in counts],
    })


def weekly(summary):
    """Applications per week (weeks start Monday), with empty weeks filled in and a running total."""
    if summary.empty:
        return pd.DataFrame(columns=["week", "applications", "running_total"])
    weeks = summary["date_applied"].dt.to_period("W-SUN").dt.start_time
    counts = weeks.value_counts()
    every_week = pd.date_range(weeks.min(), weeks.max(), freq="7D")
    table = pd.DataFrame({"week": every_week, "applications": counts.reindex(every_week, fill_value=0).values})
    table["running_total"] = table["applications"].cumsum()
    return table


def rates_by(summary, column):
    """Applications, replies and interviews for each value of a column (resume version, channel...)."""
    grouped = summary.groupby(column).agg(
        applications=("application_id", "count"),
        replies=("replied", "sum"),
        interviews=("interviewed", "sum"),
    ).reset_index()
    grouped["response_rate"] = grouped["replies"] / grouped["applications"]
    grouped["interview_rate"] = grouped["interviews"] / grouped["applications"]
    return grouped.sort_values(["response_rate", "applications"], ascending=False).reset_index(drop=True)


def waiting(summary):
    """Applied three weeks or more ago with no update at all, longest wait first."""
    rows = summary[summary["no_word"]].sort_values("days_since_applied", ascending=False)
    return rows[["application_id", "company", "role", "date_applied", "days_since_applied", "channel"]]


def reply_times(summary):
    """Days from applying to the first reply, fastest first."""
    rows = summary[summary["replied"]].sort_values("days_to_reply")
    return rows[["company", "role", "date_applied", "first_reply", "days_to_reply", "status"]]

-- =====================================================
-- JOB SEARCH COMMAND CENTER — Analysis Queries (MySQL)
-- Run `python job_logger.py setup` once first, so older applications get their 'Applied' status.
--
-- A "response" means the employer actually replied: Phone Screen, Interview, Offer or Rejected.
-- 'Applied', 'Viewed' and 'Ghosted' are not responses.
-- =====================================================

USE job_search_tracker;

-- 1. Response and interview rate by resume version
--    (applications with no resume version show up as '(none)' instead of disappearing)
SELECT
    COALESCE(rv.version_name, '(none)') AS resume_version,
    COUNT(DISTINCT a.application_id) AS total_applications,
    COUNT(DISTINCT CASE WHEN se.status IN ('Phone Screen','Interview','Offer','Rejected') THEN a.application_id END) AS got_response,
    ROUND(
        COUNT(DISTINCT CASE WHEN se.status IN ('Phone Screen','Interview','Offer','Rejected') THEN a.application_id END)
        / COUNT(DISTINCT a.application_id) * 100, 1
    ) AS response_rate_pct,
    ROUND(
        COUNT(DISTINCT CASE WHEN se.status IN ('Phone Screen','Interview','Offer') THEN a.application_id END)
        / COUNT(DISTINCT a.application_id) * 100, 1
    ) AS interview_rate_pct
FROM applications a
LEFT JOIN resume_versions rv ON a.resume_version_id = rv.resume_version_id
LEFT JOIN status_events se ON a.application_id = se.application_id
GROUP BY resume_version
ORDER BY response_rate_pct DESC;

-- 2. Days to first real response, per application
WITH first_response AS (
    SELECT
        application_id,
        MIN(event_date) AS first_response_date
    FROM status_events
    WHERE status IN ('Phone Screen','Interview','Offer','Rejected')
    GROUP BY application_id
)
SELECT
    a.company_name,
    a.role_title,
    a.date_applied,
    fr.first_response_date,
    DATEDIFF(fr.first_response_date, a.date_applied) AS days_to_response
FROM applications a
JOIN first_response fr ON a.application_id = fr.application_id
ORDER BY days_to_response;

-- 3. Funnel counts — how many applications reach each stage at all (unknown statuses sort last)
SELECT
    status,
    COUNT(DISTINCT application_id) AS applications_reaching_stage
FROM status_events
GROUP BY status
ORDER BY
    FIELD(status, 'Applied','Viewed','Phone Screen','Interview','Offer','Rejected','Ghosted','Withdrawn') = 0,
    FIELD(status, 'Applied','Viewed','Phone Screen','Interview','Offer','Rejected','Ghosted','Withdrawn');

-- 4. Weekly application volume with a running total (weeks start on Monday)
SELECT
    app_week,
    applications_this_week,
    SUM(applications_this_week) OVER (ORDER BY app_week) AS running_total
FROM (
    SELECT YEARWEEK(date_applied, 1) AS app_week, COUNT(*) AS applications_this_week
    FROM applications
    GROUP BY YEARWEEK(date_applied, 1)
) weekly
ORDER BY app_week;

-- 5. Ghost list — applied 21+ days ago with no update since
SELECT
    a.company_name,
    a.role_title,
    a.date_applied,
    DATEDIFF(CURDATE(), a.date_applied) AS days_since_applied
FROM applications a
LEFT JOIN status_events se ON a.application_id = se.application_id AND se.status <> 'Applied'
WHERE se.status_event_id IS NULL
  AND DATEDIFF(CURDATE(), a.date_applied) >= 21
ORDER BY days_since_applied DESC;

-- 6. Best-performing channel (LinkedIn vs referral vs company site, etc.)
SELECT
    COALESCE(a.channel, '(unknown)') AS channel,
    COUNT(DISTINCT a.application_id) AS total_applications,
    COUNT(DISTINCT CASE WHEN se.status IN ('Phone Screen','Interview','Offer','Rejected') THEN a.application_id END) AS got_response,
    COUNT(DISTINCT CASE WHEN se.status IN ('Interview','Offer') THEN a.application_id END) AS reached_interview_or_offer,
    ROUND(
        COUNT(DISTINCT CASE WHEN se.status IN ('Interview','Offer') THEN a.application_id END)
        / COUNT(DISTINCT a.application_id) * 100, 1
    ) AS interview_rate_pct
FROM applications a
LEFT JOIN status_events se ON a.application_id = se.application_id
GROUP BY channel
ORDER BY interview_rate_pct DESC, total_applications DESC;

-- 7. Every application with its current (latest) status
SELECT
    a.application_id,
    a.date_applied,
    a.company_name,
    a.role_title,
    a.channel,
    (SELECT se.status FROM status_events se
     WHERE se.application_id = a.application_id
     ORDER BY se.event_date DESC, se.status_event_id DESC
     LIMIT 1) AS current_status
FROM applications a
ORDER BY a.date_applied DESC, a.application_id DESC;

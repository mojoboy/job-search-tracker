# Job Search Tracker

Log job applications and what happens to them, without writing SQL. Works for any field: you choose your own resume versions, channels and statuses as you go.

## Setup (once)

1. `pip install anthropic mysql-connector-python`
2. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY` and `DB_PASSWORD` (MySQL 8).
   No API key? Leave it blank and type job details yourself.
3. `python job_logger.py setup`: creates missing tables, adds new columns, and gives older applications their "Applied" status.

## Everyday use

| To do this | Run |
|---|---|
| Log a job you applied to | `python job_logger.py` |
| Record a phone screen, interview, offer or rejection | `python job_logger.py status` |
| Add many applications from a spreadsheet (save it as CSV first) | `python job_logger.py import my_jobs.csv` |
| Save everything to a spreadsheet | `python job_logger.py export` |

Logging a job: paste the posting and type `END`. Check the details Claude fills in (Enter keeps a value), then paste the link and answer three short questions. It warns you if you already logged that job.

## Analysis

`queries.sql` has response rate by resume version, days to first response, the funnel, weekly volume, the ghost list, channel performance, and every application's current status. A "response" means Phone Screen, Interview, Offer or Rejected.

## Tests

`python -m unittest discover -s tests -v` (uses a throwaway in-memory database)

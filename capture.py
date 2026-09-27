"""
capture.py
-----------
Turn a job link into the tracker's fields while spending as little as possible on the Claude API.

1. Greenhouse, LinkedIn and Oracle job pages have public JSON versions, which are read directly.
2. Most other job sites (Workday, Lever, Ashby, iCIMS, SmartRecruiters, many company sites) embed
   schema.org "JobPosting" data for search engines: title, company, location, pay and the description.
3. Whatever is still blank (usually the industry) is filled in by Claude from the description, only
   when an API key is set. Pages with no job data at all are read by Claude from their text.
"""

import html
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import job_logger as jl

# Says plainly what's asking. Sites that refuse it get a "paste the description instead" message.
USER_AGENT = "job-search-tracker/1.0 (personal job application tracker; +https://github.com/mojoboy/job-search-tracker)"
MAX_BYTES = 3_000_000
FIELDS = list(jl.FIELD_LABELS)  # company_name, role_title, location, salary_range, industry
PERIODS = {"HOUR": "/hr", "DAY": "/day", "WEEK": "/wk", "MONTH": "/mo", "YEAR": "/yr"}
CURRENCY_SYMBOLS = {"USD": "$", "CAD": "CA$", "AUD": "A$", "GBP": "£", "EUR": "€"}


class CaptureError(Exception):
    """A job page couldn't be read. The message says what to do instead."""


def fetch(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429, 999):
            raise CaptureError("That site blocks automatic reading. Paste the job description instead.") from e
        if e.code in (404, 410):
            raise CaptureError("That job posting isn't there anymore. Paste the description if you still have it.") from e
        raise CaptureError(f"The site answered with an error ({e.code}). Paste the job description instead.") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise CaptureError(f"Couldn't open that link ({getattr(e, 'reason', e)}).") from e
    return raw.decode(charset, "replace")


def fetch_json(url):
    try:
        return json.loads(fetch(url))
    except ValueError as e:
        raise CaptureError("The job site sent something unexpected. Paste the job description instead.") from e


# ---------------------------------------------------------------- text helpers

BLOCK_TAGS = re.compile(r"</?(p|div|br|ul|ol|li|h[1-6]|tr|section|article|header|footer)\b[^>]*>", re.I)


def html_to_text(markup):
    """Readable plain text from HTML: keeps paragraph and list breaks, drops scripts and styles."""
    markup = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", markup or "", flags=re.S | re.I)
    markup = re.sub(r"<li\b[^>]*>", "\n- ", markup, flags=re.I)
    markup = BLOCK_TAGS.sub("\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clean(value):
    """Tidy a short field: decode entities, collapse spaces, drop Workday-style number prefixes ('001 Acme')."""
    value = html.unescape(str(value or "")).strip()
    return re.sub(r"^\d{2,}\s+", "", re.sub(r"\s+", " ", value))


def format_pay(low=None, high=None, currency="USD", period=""):
    """'$80,000-$100,000/yr' or '$32.50-$41/hr' from numbers; '' when there are none."""
    values = []
    for value in (low, high):
        try:
            number = float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if number > 0:
            values.append(number)
    if not values:
        return ""
    currency = (currency or "USD").upper()
    symbol = CURRENCY_SYMBOLS.get(currency, f"{currency} ")

    def money(number):
        return f"{symbol}{number:,.0f}" if number >= 1000 or number == int(number) else f"{symbol}{number:,.2f}"

    return "-".join(dict.fromkeys(money(v) for v in values)) + PERIODS.get(str(period or "").upper(), "")


# ---------------------------------------------------------------- schema.org JobPosting

def find_job_posting(page):
    """The first schema.org JobPosting object embedded in a page, or None."""
    for blob in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:
            data = json.loads(blob.strip(), strict=False)
        except ValueError:
            continue
        if isinstance(data, dict):
            candidates = data.get("@graph", [data])
        else:
            candidates = data if isinstance(data, list) else []
        for item in candidates:
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type")):
                return item
    return None


def json_ld_location(job):
    places = job.get("jobLocation") or []
    names = []
    for place in places if isinstance(places, list) else [places]:
        address = place.get("address", {}) if isinstance(place, dict) else place
        if isinstance(address, str):
            names.append(clean(address))
            continue
        if not isinstance(address, dict):
            continue
        country = address.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        parts = [clean(address.get("addressLocality")), clean(address.get("addressRegion"))]
        names.append(", ".join(p for p in parts if p) or clean(country))
    names = [name for name in dict.fromkeys(names) if name]
    text = "; ".join(names[:3]) + (f" (+{len(names) - 3} more)" if len(names) > 3 else "")
    if job.get("jobLocationType") == "TELECOMMUTE" and "remote" not in text.lower():
        return f"Remote ({text})" if text else "Remote"
    return text


def json_ld_pay(base):
    if not isinstance(base, dict):
        return ""
    value = base.get("value")
    if isinstance(value, dict):
        low, high = value.get("minValue", value.get("value")), value.get("maxValue")
        period = value.get("unitText") or base.get("unitText")
    elif "minValue" in base or "maxValue" in base:  # some sites (iCIMS) put the range on baseSalary itself
        low, high, period = base.get("minValue"), base.get("maxValue"), base.get("unitText")
    else:
        low, high, period = value, None, base.get("unitText")
    return format_pay(low, high, base.get("currency"), period)


def from_job_posting(job):
    org = job.get("hiringOrganization")
    industry = job.get("industry")
    return {
        "company_name": clean(org.get("name") if isinstance(org, dict) else org),
        "role_title": clean(job.get("title")),
        "location": json_ld_location(job),
        "salary_range": json_ld_pay(job.get("baseSalary")),
        "industry": clean(industry) if isinstance(industry, str) else "",
        "description": html_to_text(html.unescape(str(job.get("description") or ""))),
    }


# ---------------------------------------------------------------- sites with a public JSON version

def read_greenhouse(url):
    match = re.search(r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url)
    query = parse_qs(urlsplit(url).query)
    if match:
        board, job_id = match.groups()
    elif "for" in query and "token" in query:  # embedded form: /embed/job_app?for=acme&token=123
        board, job_id = query["for"][0], query["token"][0]
    else:
        return None
    job = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?pay_transparency=true")
    pay = (job.get("pay_input_ranges") or [{}])[0]
    return {
        "company_name": clean(job.get("company_name")),
        "role_title": clean(job.get("title")),
        "location": clean((job.get("location") or {}).get("name")),
        "salary_range": format_pay((pay.get("min_cents") or 0) / 100, (pay.get("max_cents") or 0) / 100,
                                   pay.get("currency_type")),
        "industry": "",
        "description": html_to_text(html.unescape(job.get("content") or "")),
    }


def read_linkedin(url):
    match = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*?-)?(\d{6,})", url)
    job_id = match.group(1) if match else (parse_qs(urlsplit(url).query).get("currentJobId") or [None])[0]
    if not job_id:
        return None
    page = fetch(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")

    def grab(pattern):
        found = re.search(pattern, page, re.S)
        return clean(html_to_text(found.group(1))) if found else ""

    description = re.search(r"show-more-less-html__markup[^>]*>(.*?)</div>", page, re.S)
    pay = re.sub(r"^base pay range\s*", "", grab(r"compensation__salary[^>]*>(.*?)</div>"), flags=re.I)
    return {
        "company_name": grab(r"topcard__org-name-link[^>]*>(.*?)</a>"),
        "role_title": grab(r"top-card-layout__title[^>]*>(.*?)</h"),
        "location": grab(r"topcard__flavor--bullet[^>]*>(.*?)</span>"),
        "salary_range": re.sub(r"\.00(?=/)", "", pay),
        "industry": "",
        "description": html_to_text(description.group(1)) if description else "",
    }


def read_oracle(url):
    match = re.search(r"https?://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/(?:job|jobs/preview)/(\d+)", url)
    if not match:
        return None
    host, site, job_id = match.groups()
    data = fetch_json(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                      f"?expand=all&onlyData=true&finder=ById;Id=%22{job_id}%22,siteNumber={site}")
    if not data.get("items"):
        raise CaptureError("That job isn't posted anymore. Paste the description if you still have it.")
    job = data["items"][0]
    location = clean(job.get("PrimaryLocation"))
    workplace = clean(job.get("WorkplaceType"))
    if workplace and workplace.lower() not in location.lower():
        location = f"{location} ({workplace})" if location else workplace
    sections = ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr",
                "CorporateDescriptionStr")
    return {
        "company_name": "",  # Oracle's job data doesn't name the employer; Claude finds it in the description
        "role_title": clean(job.get("Title")),
        "location": location,
        "salary_range": "",
        "industry": "",
        "description": "\n\n".join(html_to_text(job[key]) for key in sections if job.get(key)),
    }


SITE_READERS = [
    (re.compile(r"greenhouse\.io/", re.I), read_greenhouse, "Greenhouse's job data"),
    (re.compile(r"linkedin\.com/jobs", re.I), read_linkedin, "LinkedIn's public job page"),
    (re.compile(r"/hcmUI/CandidateExperience/", re.I), read_oracle, "Oracle's job data"),
]


# ---------------------------------------------------------------- entry points

def has_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def complete(result, use_claude):
    """Fill blank fields with Claude from the description, never overwriting what the page itself said."""
    missing = [field for field in FIELDS if not result.get(field)]
    text = result.get("description", "")
    if missing and use_claude and text.strip():
        try:
            extracted = jl.extract_fields(text)
        except Exception as e:  # bad key, no internet, rate limit...
            result["warning"] = f"Claude couldn't fill in the rest ({e}). Type the missing details yourself."
            return result
        for field in missing:
            result[field] = clean(extracted.get(field))
        result["source"] += " + Claude"
    return result


def read_link(url, use_claude=None):
    """The tracker's fields, the description, and how they were found, for a job link.
    Raises CaptureError when the page can't be read."""
    url = (url or "").strip()
    if not url:
        raise CaptureError("Paste a job link first.")
    if "://" not in url:
        url = "https://" + url
    use_claude = has_api_key() if use_claude is None else use_claude

    result = None
    for pattern, reader, source in SITE_READERS:
        if pattern.search(url):
            result = reader(url)
            if result:
                result["source"] = source
            break
    if result is None:
        page_url = url
        if "icims.com" in urlsplit(url).netloc.lower() and "in_iframe" not in url:
            # the version of the page iCIMS serves for embedding on company career sites
            page_url += ("&" if "?" in url else "?") + "in_iframe=1"
        page = fetch(page_url)
        job = find_job_posting(page)
        if job:
            result = from_job_posting(job)
            result["source"] = "the job data built into the page"
        else:
            result = {field: "" for field in FIELDS}
            result["description"] = html_to_text(page)[:40000]
            result["source"] = "the page's text"

    if not result.get("role_title") and len(result.get("description", "")) < 200:
        raise CaptureError("That page shows its job only in a browser, so it can't be read from the link. "
                           "Paste the job description instead.")
    result = complete(result, use_claude)
    if not result.get("role_title"):
        result.setdefault("warning", "Couldn't find the job details on that page. Fill them in below; "
                                     "the page text is saved as the description.")
    result["url"] = url
    return result


def read_text(text, use_claude=None):
    """The same result for a pasted job description."""
    use_claude = has_api_key() if use_claude is None else use_claude
    result = {field: "" for field in FIELDS}
    result.update(description=(text or "").strip(), source="the description you pasted", url="")
    if not use_claude:
        result["warning"] = ("Without a Claude API key the details can't be read from pasted text. "
                             "Type them in below; the description is saved either way.")
    return complete(result, use_claude)

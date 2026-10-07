#!/usr/bin/env python3
"""Hourly Data Analyst job fetcher.

Pulls Data Analyst postings (full-time and contract, newest first, last 30 days)
from the Adzuna API, merges them into jobs.json without duplicates, tags each
one (industry, skills, F-1 signal, urgency), and writes a notification file when
new postings appear.
"""
import html
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
COUNTRY = (os.environ.get("JOB_COUNTRY") or "us").strip().lower()
LOCATION = (os.environ.get("JOB_LOCATION") or "").strip()  # empty = whole country

MAX_DAYS = 30
RESULTS_PER_PAGE = 50
PAGES_FIRST_RUN = 4   # first run builds the 30-day history
PAGES_REGULAR = 2     # hourly runs only need the newest postings
DATA_FILE = "jobs.json"
API = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

# ------------------------------------------------------- work-authorization signals
# Hard blocks: the role needs a US citizen or green card (or clearance).
CITIZEN = [
    r"\bmust\s+be\s+(a\s+)?(u\.?s\.?|united\s+states)\s+(citizen|national)",
    r"\b(u\.?s\.?|united\s+states)\s+citizens?\s+(only|required)",
    r"\bcitizens?\s+(only|required)",
    r"\bcitizenship\s+(is\s+)?required",
    r"\bcitizens?\s+(or|and|&)\s+(lawful\s+)?(permanent\s+residents?|green\s+card)",
    r"\bgreen\s+card\s+(holders?\s+)?(or|and|&)\s+(u\.?s\.?\s+)?citizens?",
    r"\b(gc|green\s+card)\b[^.]{0,25}\b(only|required)",
    r"\bsecurity\s+clearance\b",
    r"\bpublic\s+trust\b",
    r"\bu\.?s\.?\s+persons?\b",
]
# The posting says it will not take F-1 / OPT / CPT / H-1B candidates.
NOF1 = [r"\bno\s+(opt|cpt|f-?1|h-?1b)\b",
        r"\b(not|unable\s+to)\s+(accept|consider|hire)\s+(opt|cpt|f-?1|h-?1b)"]
# Soft block: no sponsorship (an OPT student may still be hireable, H-1B later is the risk).
NOSP = [
    r"\bno\s+(visa\s+|work\s+)?sponsorship",
    r"\b(not|unable\s+to|cannot|can't|won't|will\s+not|does\s+not|do\s+not|doesn't|don't)\s+(be\s+able\s+to\s+)?(provide\s+|offer\s+|support\s+)?(visa\s+|work\s+|employment\s+)?sponsor",
    r"\bwithout\s+(the\s+need\s+for\s+)?(visa\s+|employer\s+|current\s+or\s+future\s+)?sponsorship",
    r"\bsponsorship\s+(is\s+)?(not|unavailable)\s*(available|offered|provided)?",
    r"\bnot\s+eligible\s+for\s+(visa\s+)?sponsorship",
]
POS_F1 = [r"\binternational\s+students?", r"\bh-?1b\s+(transfer|welcome|accepted|candidates)",
          r"\bf-?1\s+(student|visa|candidates)"]
POS_CASE = [r"\bF-?1\b", r"\b(STEM\s+)?OPT\b(?![\s-]*(in|out)\b)", r"\bCPT\b"]
POS_SPONSOR = [
    r"\b(visa\s+)?sponsorship\s+(is\s+)?(available|offered|provided)",
    r"\b(will|can|able\s+to|may)\s+(provide\s+)?sponsor",
    r"\bopen\s+to\s+(international|visa)",
]

URGENT = (r"urgent(ly)?\b|immediate(ly)?\s+(hire|hiring|start|opening|need)|multiple\s+(openings|positions)"
          r"|hiring\s+(now|immediately)|high[- ]volume|ongoing\s+recruit|actively\s+hiring")

INDUSTRY_RULES = [
    ("Education / University", r"universit|college|school|academy|institute"),
    ("Healthcare / Hospital", r"hospital|health|medical|clinic|pharma|biotech|care\b"),
    ("Banking / Finance", r"\bbank|financ|credit\s+union|capital|insurance|invest|mortgage|\btrust\b|wealth|lending"),
    ("Government / Public", r"\bcounty\b|\bcity\s+of\b|\bstate\s+of\b|department\s+of|government|federal|municipal"),
]

SKILLS = {
    "SQL": r"\bsql\b|t-sql|pl/sql|mysql|postgres",
    "Python": r"\bpython\b|\bpandas\b",
    "R": r"\bR\b(?=[,/ )])",
    "Excel": r"\bexcel\b|spreadsheets?",
    "Power BI": r"power\s?bi",
    "Tableau": r"\btableau\b",
    "Looker": r"\blooker\b",
    "Snowflake": r"\bsnowflake\b",
    "Redshift": r"\bredshift\b",
    "BigQuery": r"\bbigquery\b",
    "Databricks": r"\bdatabricks\b|\bspark\b",
    "AWS": r"\baws\b|amazon\s+web\s+services",
    "Azure": r"\bazure\b",
    "GCP": r"\bgcp\b|google\s+cloud",
    "SAS": r"\bsas\b",
    "dbt": r"\bdbt\b",
    "Airflow": r"\bairflow\b",
    "ETL": r"\betl\b|\belt\b|data\s+pipelines?",
    "Data Modeling": r"data\s+model(l)?ing",
    "Data Warehousing": r"data\s+warehous",
    "Data Visualization": r"data\s+visuali[sz]ation|visuali[sz]ations?",
    "Dashboards": r"dashboards?",
    "Reporting": r"\breporting\b|\breports\b",
    "Statistics": r"statistic|regression|hypothesis",
    "A/B Testing": r"a/b\s+test|experimentation",
    "Machine Learning": r"machine\s+learning|\bml\b",
    "Data Quality": r"data\s+quality|data\s+validation|data\s+integrity",
    "Data Governance": r"data\s+governance|metadata",
    "KPIs": r"\bkpis?\b|key\s+performance",
    "Stakeholder Communication": r"stakeholders?",
    "Agile / Jira": r"\bagile\b|\bscrum\b|\bjira\b",
    "Salesforce": r"salesforce",
    "VBA / Macros": r"\bvba\b|macros?",
    "Git": r"\bgit\b|github",
    "HIPAA / Healthcare data": r"hipaa|claims\s+data|\behr\b|\bepic\b",
}


def now():
    return datetime.now(timezone.utc)


def parse_ts(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def snippet(patterns, text, flags=re.I):
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            s, e = max(0, m.start() - 50), min(len(text), m.end() + 50)
            return text[s:e].strip()
    return None


def classify_f1(text):
    """Returns (status, evidence). Status: accepts, caution, citizen_gc, mixed, not_mentioned."""
    nof1 = snippet(NOF1, text)
    if nof1:
        return "citizen_gc", nof1
    cit = snippet(CITIZEN, text)
    nosp = snippet(NOSP, text)
    f1 = snippet(POS_F1, text) or snippet(POS_CASE, text, 0)
    spon = snippet(POS_SPONSOR, text)
    if cit:
        pos = f1 or spon
        return ("mixed", f"{pos}  |  {cit}") if pos else ("citizen_gc", cit)
    if nosp:
        if f1:
            return "accepts", f"{f1}  |  {nosp}"
        if spon:
            return "mixed", f"{spon}  |  {nosp}"
        return "caution", nosp
    if f1 or spon:
        return "accepts", f1 or spon
    return "not_mentioned", ""


SUFFIX = r"\b(inc|llc|l\s?l\s?c|corp|corporation|co|company|ltd|limited|lp|llp|plc|na|pc|pllc|the)\b"


def norm_company(name):
    n = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", " and "))
    n = re.sub(SUFFIX, " ", n)
    return re.sub(r"\s+", " ", n).strip()


def load_sponsors():
    try:
        with open("sponsors.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sponsor_info(company, sponsors):
    if sponsors is None or not norm_company(company):
        return {"status": "unknown"}
    rec = sponsors.get("employers", {}).get(norm_company(company))
    if rec:
        return {"status": "filed", "count": rec[0], "analyst": rec[1]}
    return {"status": "none"}


def detect_industry(company, category):
    for label, pattern in INDUSTRY_RULES:
        if re.search(pattern, company or "", re.I):
            return label
    return category or "Other"


def find_skills(text):
    return [name for name, pat in SKILLS.items()
            if re.search(pat, text, 0 if name == "R" else re.I)]


def is_target(title):
    return bool(re.search(r"data\s+analyst|analyst.{0,20}\bdata\b|\bdata\b.{0,20}analyst|data\s+analytics",
                          title or "", re.I))


def build_job(r, kind):
    title = clean(r.get("title"))
    company = clean((r.get("company") or {}).get("display_name"))
    location = clean((r.get("location") or {}).get("display_name"))
    desc = clean(r.get("description"))
    category = clean((r.get("category") or {}).get("label")).replace(" Jobs", "")
    f1, evidence = classify_f1(f"{title}. {desc}")
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", desc)
    return {
        "id": f"adzuna-{r.get('id')}",
        "title": title,
        "company": company,
        "location": location,
        "created": r.get("created"),
        "first_seen": now().isoformat(timespec="seconds"),
        "url": r.get("redirect_url"),
        "type": "contract" if (kind == "contract" or r.get("contract_type") == "contract") else "full_time",
        "industry": detect_industry(company, category),
        "description": desc,
        "skills": find_skills(f"{title} {desc}"),
        "f1": f1,
        "f1_evidence": evidence,
        "sponsor": {"status": "unknown"},
        "urgent": bool(re.search(URGENT, f"{title} {desc}", re.I)),
        "email": email.group(0) if email else None,
    }


def fetch_page(kind, page):
    params = {
        "app_id": APP_ID, "app_key": APP_KEY,
        "results_per_page": RESULTS_PER_PAGE,
        "what_phrase": "data analyst",
        "max_days_old": MAX_DAYS,
        "sort_by": "date",
        "content-type": "application/json",
        "full_time" if kind == "full_time" else "contract": 1,
    }
    if LOCATION:
        params["where"] = LOCATION
    resp = requests.get(API.format(country=COUNTRY, page=page), params=params, timeout=30)
    if resp.status_code in (401, 403):
        sys.exit("Adzuna rejected the keys. Re-check ADZUNA_APP_ID and ADZUNA_APP_KEY in GitHub secrets.")
    if resp.status_code != 200:
        print(f"Adzuna returned {resp.status_code} for {kind} page {page}; skipping.")
        return None
    return resp.json().get("results", [])


def load():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"updated": None, "jobs": []}


def dedupe_key(j):
    return re.sub(r"\W+", "", f"{j['title']}{j['company']}{j['location']}".lower())


def apply_advice(hours):
    if hours < 24:
        return "Apply today. Early applicants get the most attention."
    if hours < 48:
        return "Apply within 24 hours."
    if hours < 168:
        return "Apply within 2 days."
    return "Apply this week; it may already be filling."


def age_text(hours):
    if hours < 1:
        return "just posted"
    if hours < 24:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


F1_TEXT = {"accepts": "F-1 welcome or sponsorship offered", "caution": "Says no sponsorship",
           "citizen_gc": "Needs US citizen or green card", "mixed": "Mixed F-1 signals",
           "not_mentioned": "F-1 not mentioned"}


def sponsor_text(sp):
    if sp["status"] == "filed":
        return f"Employer has H-1B filings on record ({sp['count']}, {sp['analyst']} with analyst titles)."
    if sp["status"] == "none":
        return "No H-1B record found for this employer."
    return ""


def write_notification(new_jobs, first_run):
    skipped = sum(1 for j in new_jobs if j["f1"] == "citizen_gc")
    new_jobs = sorted([j for j in new_jobs if j["f1"] != "citizen_gc"],
                      key=lambda j: j["created"] or "", reverse=True)
    n = len(new_jobs)
    if n == 0:
        return
    label = "Initial load:" if first_run else "New:"
    title = f"{label} {n} Data Analyst job{'s' if n != 1 else ''} ({now():%b %d, %H:%M} UTC)"
    top_skills = Counter(s for j in new_jobs for s in j["skills"]).most_common(8)
    lines = []
    if top_skills:
        lines.append("**Skills these postings ask for most:** " + ", ".join(f"{s} ({c})" for s, c in top_skills))
        lines.append("")
    for j in new_jobs[:25]:
        created = parse_ts(j["created"])
        hours = (now() - created).total_seconds() / 3600 if created else 999
        kind = "Contract" if j["type"] == "contract" else "Full-time"
        lines += [
            f"### {j['title']} at {j['company'] or 'Company not listed'}",
            f"{j['location'] or 'Location not listed'}. {kind}. Posted {age_text(hours)}. {F1_TEXT[j['f1']]}. {sponsor_text(j['sponsor'])}",
            f"**When to apply:** {apply_advice(hours)}",
            f"[Open posting]({j['url']})",
            "",
        ]
    if n > 25:
        lines.append(f"...and {n - 25} more on your dashboard.")
    if skipped:
        lines.append(f"{skipped} new job(s) were left out because they need a US citizen or green card.")
    with open("new_jobs.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open("new_jobs_title.txt", "w", encoding="utf-8") as f:
        f.write(title)


def main():
    if not APP_ID or not APP_KEY:
        sys.exit("Missing ADZUNA_APP_ID / ADZUNA_APP_KEY. Add them as GitHub repository secrets.")

    data = load()
    cutoff = now() - timedelta(days=MAX_DAYS)
    jobs = []
    for j in data.get("jobs", []):
        created = parse_ts(j.get("created"))
        if created and created >= cutoff:
            jobs.append(j)
    seen_ids = {j["id"] for j in jobs}
    seen_keys = {dedupe_key(j) for j in jobs}
    first_run = not jobs
    pages = PAGES_FIRST_RUN if first_run else PAGES_REGULAR

    new_jobs = []
    for kind in ("full_time", "contract"):
        for page in range(1, pages + 1):
            results = fetch_page(kind, page)
            if results is None:
                break
            for r in results:
                job = build_job(r, kind)
                if not job["url"] or not is_target(job["title"]):
                    continue
                created = parse_ts(job["created"])
                if not created or created < cutoff:
                    continue
                if job["id"] in seen_ids:
                    if kind == "contract":
                        for old in jobs:
                            if old["id"] == job["id"]:
                                old["type"] = "contract"
                    continue
                if dedupe_key(job) in seen_keys:
                    continue
                seen_ids.add(job["id"])
                seen_keys.add(dedupe_key(job))
                jobs.append(job)
                new_jobs.append(job)
            if len(results) < RESULTS_PER_PAGE:
                break

    sponsors = load_sponsors()
    for j in jobs:  # re-run rules on every stored job so rule updates apply to old jobs too
        j["f1"], j["f1_evidence"] = classify_f1(f"{j['title']}. {j['description']}")
        j["sponsor"] = sponsor_info(j["company"], sponsors)

    jobs.sort(key=lambda j: j["created"] or "", reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": now().isoformat(timespec="seconds"), "jobs": jobs},
                  f, ensure_ascii=False, separators=(",", ":"))

    print(f"{len(new_jobs)} new, {len(jobs)} total stored.")
    if new_jobs:
        write_notification(new_jobs, first_run)


if __name__ == "__main__":
    main()

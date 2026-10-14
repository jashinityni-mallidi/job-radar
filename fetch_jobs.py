#!/usr/bin/env python3
"""Hourly Data Analyst job fetcher.

Pulls Analyst postings of every kind (data, business, financial and more; newest first, last 30 days)
from the Adzuna API, merges them into jobs.json without duplicates, tags each
one (industry, skills, F-1 signal, urgency), and writes a notification file when
new postings appear.
"""
import html
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
COUNTRY = (os.environ.get("JOB_COUNTRY") or "us").strip().lower()
LOCATION = (os.environ.get("JOB_LOCATION") or "").strip()  # empty = whole country

MAX_DAYS = 30
RESULTS_PER_PAGE = 50
# Adzuna's free keys allow roughly 1,000 calls a month, so hourly runs stay at 1-3 calls.
PAGES_REGULAR = 3     # hourly: stops early as soon as a page reaches jobs we already have
PAGES_BACKFILL = 30   # first run, or when you tick "backfill": reaches back about a week or more
BACKFILL = os.environ.get("BACKFILL", "").strip().lower() == "true"
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
    "Data Cleaning": r"data\s+(cleaning|cleansing|wrangling)",
    "Power Query / DAX": r"\bdax\b|power\s+query",
    "SSIS / SSRS": r"\bssis\b|\bssrs\b",
    "Alteryx / Qlik": r"alteryx|\bqlik",
    "Anaplan": r"anaplan",
    "Financial Modeling": r"financial\s+model",
    "Forecasting": r"forecast",
    "Budgeting": r"budget",
    "Variance Analysis": r"variance",
    "Reconciliation": r"reconcil",
    "GAAP": r"\bgaap\b",
    "SOX / Internal Controls": r"\bsox\b|internal\s+controls?",
    "Audit": r"\baudit",
    "SAP / Oracle / ERP": r"\bsap\b|\boracle\b|\berp\b|workday",
    "Requirements Gathering": r"requirements?\s+(gathering|analysis|elicitation)|business\s+requirements",
    "User Stories / UAT": r"user\s+stor|\buat\b|acceptance\s+test",
    "Process Mapping": r"process\s+(map|flow)|\bvisio\b",
    "Process Improvement": r"process\s+improvement|\blean\b|six\s+sigma",
    "Project Management": r"project\s+manage|\bpmp\b",
    "CRM": r"\bcrm\b",
    "Google Analytics / SEO": r"google\s+analytics|\bga4\b|\bseo\b",
    "Market Research": r"market\s+research|survey",
    "Risk Assessment": r"risk\s+(assessment|analysis|management)",
    "Compliance": r"compliance|regulatory",
    "Fraud / AML": r"\bfraud|\baml\b|\bkyc\b",
    "Supply Chain": r"supply\s+chain|logistics",
    "Inventory / Demand Planning": r"inventory|demand\s+planning",
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


ROLE_RULES = [
    ("Financial", r"financ|accounting|accountant|treasury|budget|fp&a|credit|investment|audit|\btax\b|actuar|revenue|payroll|pricing|\bloan|mortgage|underwrit"),
    ("Risk / Compliance", r"\brisk|fraud|compliance|\baml\b|\bkyc\b|regulat|security|cyber|threat|investigat"),
    ("Healthcare", r"health|clinical|medical|patient|pharma|claims|hospital|nurs|\behr\b|\bepic\b|biostat"),
    ("Operations / Supply Chain", r"supply|logistics|procure|inventory|operations|planning|demand|sourcing|warehouse|transportation|production|manufactur"),
    ("HR / People", r"\bhr\b|people|talent|recruit|workforce|compensation|benefits"),
    ("Marketing / Research", r"market|research|\bseo\b|digital|customer|consumer|social|campaign|e-?commerce|policy|survey|insight"),
    ("IT / Technical", r"\bit\b|network|software|application|infrastructure|\bqa\b|quality\s+assurance|\btest|devops|cloud|technical|platform|help\s?desk|\bsupport\b|engineer"),
    ("Data / BI", r"\bdata\b|\bbi\b|business\s+intelligence|analytics|reporting|statistic|scientist|database|\bsql\b|tableau|power\s?bi"),
    ("Business / Systems", r"business|system|process|requirements|product|program|project|strategy|management|salesforce|\bcrm\b|workday|functional"),
]


def detect_role(title):
    for label, pattern in ROLE_RULES:
        if re.search(pattern, title or "", re.I):
            return label
    return "Other"


def detect_level(title, text):
    t = (title or "").lower()
    if re.search(r"\b(intern|internship|trainee|co-?op)\b", t):
        return "Intern"
    if re.search(r"\b(lead|principal|staff|manager|director|head|vp|svp)\b", t):
        return "Lead / Manager"
    if re.search(r"\b(sr\.?|senior)\b|\b(iii|iv|3|4)\s*$", t):
        return "Senior"
    if re.search(r"\b(jr\.?|junior|entry|associate)\b|\banalyst\s+(i|1)\b", t):
        return "Entry"
    if re.search(r"\banalyst\s+(ii|2)\b|\bmid[- ]level\b", t):
        return "Mid"
    if re.search(r"entry[- ]level|new\s+grad|recent\s+grad", text or "", re.I):
        return "Entry"
    return "Not stated"


def find_experience(text):
    """Years of experience stated in the posting excerpt. Returns (low, high) or (None, None)."""
    for m in re.finditer(r"(?<![\d$.,])(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:-|\u2013|to)\s*(\d{1,2}))?\s*\+?\s*years?", text or "", re.I):
        ctx = text[max(0, m.start() - 60): m.end() + 80]
        if not re.search(r"experience|\bexp\b|background|professional", ctx, re.I):
            continue
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else None
        if hi is not None and hi < lo:
            lo, hi = hi, lo
        if lo > 20:
            continue
        return lo, hi
    if re.search(r"entry[- ]level|new\s+grad|recent\s+grad|fresher", text or "", re.I):
        return 0, None
    return None, None


STATE_INFO = {
    "CT": ("Connecticut", "Northeast"), "ME": ("Maine", "Northeast"), "MA": ("Massachusetts", "Northeast"),
    "NH": ("New Hampshire", "Northeast"), "RI": ("Rhode Island", "Northeast"), "VT": ("Vermont", "Northeast"),
    "NJ": ("New Jersey", "Northeast"), "NY": ("New York", "Northeast"), "PA": ("Pennsylvania", "Northeast"),
    "IL": ("Illinois", "Midwest"), "IN": ("Indiana", "Midwest"), "MI": ("Michigan", "Midwest"),
    "OH": ("Ohio", "Midwest"), "WI": ("Wisconsin", "Midwest"), "IA": ("Iowa", "Midwest"),
    "KS": ("Kansas", "Midwest"), "MN": ("Minnesota", "Midwest"), "MO": ("Missouri", "Midwest"),
    "NE": ("Nebraska", "Midwest"), "ND": ("North Dakota", "Midwest"), "SD": ("South Dakota", "Midwest"),
    "DE": ("Delaware", "South"), "FL": ("Florida", "South"), "GA": ("Georgia", "South"),
    "MD": ("Maryland", "South"), "NC": ("North Carolina", "South"), "SC": ("South Carolina", "South"),
    "VA": ("Virginia", "South"), "DC": ("District of Columbia", "South"), "WV": ("West Virginia", "South"),
    "AL": ("Alabama", "South"), "KY": ("Kentucky", "South"), "MS": ("Mississippi", "South"),
    "TN": ("Tennessee", "South"), "AR": ("Arkansas", "South"), "LA": ("Louisiana", "South"),
    "OK": ("Oklahoma", "South"), "TX": ("Texas", "South"),
    "AZ": ("Arizona", "West"), "CO": ("Colorado", "West"), "ID": ("Idaho", "West"), "MT": ("Montana", "West"),
    "NV": ("Nevada", "West"), "NM": ("New Mexico", "West"), "UT": ("Utah", "West"), "WY": ("Wyoming", "West"),
    "AK": ("Alaska", "West"), "CA": ("California", "West"), "HI": ("Hawaii", "West"),
    "OR": ("Oregon", "West"), "WA": ("Washington", "West"),
}
NAME_TO_ABBR = {v[0].lower(): k for k, v in STATE_INFO.items()}


def derive_state(location, area):
    """Prefer Adzuna's structured area; fall back to reading the location text."""
    if area and len(area) > 1 and str(area[1]).lower() in NAME_TO_ABBR:
        return NAME_TO_ABBR[str(area[1]).lower()]
    loc = location or ""
    if re.search(r"d\.?c\.?\b", loc, re.I) and "washington" in loc.lower():
        return "DC"
    m = re.search(r",\s*([A-Z]{2})\b", loc)
    if m and m.group(1) in STATE_INFO:
        return m.group(1)
    for name in sorted(NAME_TO_ABBR, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b(?!\s+(county|parish|borough|township))", loc.lower()):
            return NAME_TO_ABBR[name]
    return ""


def place_label(location, area, state):
    city = str(area[-1]) if area and len(area) >= 4 else ""
    if not city and location and "," in location:
        first = location.split(",")[0].strip()
        if first and first.lower() not in NAME_TO_ABBR and first.upper() != "US":
            city = first
    if state and city:
        return f"{city}, {state}"
    if state:
        return STATE_INFO[state][0]
    return location or ""


def detect_industry(company, category):
    for label, pattern in INDUSTRY_RULES:
        if re.search(pattern, company or "", re.I):
            return label
    return category or "Other"


def find_skills(text):
    return [name for name, pat in SKILLS.items()
            if re.search(pat, text, 0 if name == "R" else re.I)]


def is_target(title):
    return bool(re.search(r"analyst|analytics", title or "", re.I))


def detect_type(r, title):
    contract_type = (r.get("contract_type") or "").lower()
    contract_time = (r.get("contract_time") or "").lower()
    if contract_type == "contract" or re.search(r"\bcontract(or)?\b", title, re.I):
        return "contract"
    if contract_time == "full_time" or contract_type == "permanent":
        return "full_time"
    return "unknown"


def build_job(r):
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
        "area": (r.get("location") or {}).get("area") or [],
        "created": r.get("created"),
        "first_seen": now().isoformat(timespec="seconds"),
        "url": r.get("redirect_url"),
        "type": detect_type(r, title),
        "industry": detect_industry(company, category),
        "description": desc,
        "skills": find_skills(f"{title} {desc}"),
        "f1": f1,
        "f1_evidence": evidence,
        "sponsor": {"status": "unknown"},
        "urgent": bool(re.search(URGENT, f"{title} {desc}", re.I)),
        "email": email.group(0) if email else None,
    }


def fetch_page(page):
    params = {
        "app_id": APP_ID, "app_key": APP_KEY,
        "results_per_page": RESULTS_PER_PAGE,
        "title_only": "analyst",
        "max_days_old": MAX_DAYS,
        "sort_by": "date",
        "content-type": "application/json",
    }
    if LOCATION:
        params["where"] = LOCATION
    resp = requests.get(API.format(country=COUNTRY, page=page), params=params, timeout=30)
    if resp.status_code in (401, 403):
        sys.exit("Adzuna rejected the keys. Re-check ADZUNA_APP_ID and ADZUNA_APP_KEY in GitHub secrets.")
    if resp.status_code != 200:
        print(f"Adzuna returned {resp.status_code} for page {page}; stopping.")
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


def enrich(j, sponsors):
    """(Re)compute every derived field from the stored text so rule updates apply to old jobs too."""
    text = f"{j['title']}. {j['description']}"
    j["f1"], j["f1_evidence"] = classify_f1(text)
    j["sponsor"] = sponsor_info(j["company"], sponsors)
    j["skills"] = find_skills(text)
    j["role"] = detect_role(j["title"])
    j["level"] = detect_level(j["title"], j["description"])
    j["exp_min"], j["exp_max"] = find_experience(j["description"])
    j["state"] = derive_state(j.get("location"), j.get("area"))
    j["region"] = STATE_INFO[j["state"]][1] if j["state"] else "Not listed"
    j["place"] = place_label(j.get("location"), j.get("area"), j["state"])


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
            f"{j['place'] or 'Location not listed'}. {kind}. {j['role']}. Level: {j['level']}. Posted {age_text(hours)}. {F1_TEXT[j['f1']]}. {sponsor_text(j['sponsor'])}",
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
    by_id = {j["id"]: j for j in jobs}
    seen_ids = set(by_id)
    seen_keys = {dedupe_key(j) for j in jobs}
    first_run = not jobs
    backfill = first_run or BACKFILL
    pages = PAGES_BACKFILL if backfill else PAGES_REGULAR
    known_ids = set(seen_ids)

    new_jobs = []
    calls = 0
    for page in range(1, pages + 1):
        if page > 1:
            time.sleep(3)  # stay under Adzuna's per-minute limit
        results = fetch_page(page)
        calls += 1
        if results is None:
            break
        reached_known = False
        for r in results:
            job = build_job(r)
            if job["id"] in known_ids:
                reached_known = True
            if not job["url"] or not is_target(job["title"]):
                continue
            created = parse_ts(job["created"])
            if not created or created < cutoff:
                continue
            if job["id"] in by_id:  # already stored: just refresh its location details
                by_id[job["id"]]["area"] = job["area"]
                by_id[job["id"]]["location"] = job["location"]
                continue
            if dedupe_key(job) in seen_keys:
                continue
            seen_ids.add(job["id"])
            by_id[job["id"]] = job
            seen_keys.add(dedupe_key(job))
            jobs.append(job)
            new_jobs.append(job)
        if len(results) < RESULTS_PER_PAGE:
            break
        if not backfill and reached_known:
            break  # newest-first: once we hit a job we already have, everything after is old news

    sponsors = load_sponsors()
    for j in jobs:
        enrich(j, sponsors)

    jobs.sort(key=lambda j: j["created"] or "", reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"updated": now().isoformat(timespec="seconds"), "jobs": jobs},
                  f, ensure_ascii=False, separators=(",", ":"))

    oldest = parse_ts(jobs[-1]["created"]) if jobs else None
    span = f"{(now() - oldest).days} days" if oldest else "n/a"
    print(f"{len(new_jobs)} new, {len(jobs)} total stored, oldest posting {span} old, {calls} Adzuna call(s).")
    notify = new_jobs
    if backfill and not first_run:  # old jobs found by a backfill should not flood your email
        notify = [j for j in new_jobs if (parse_ts(j["created"]) or cutoff) > now() - timedelta(hours=24)]
    if notify:
        write_notification(notify, first_run)


if __name__ == "__main__":
    main()

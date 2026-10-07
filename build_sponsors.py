#!/usr/bin/env python3
"""Builds sponsors.json from the US Department of Labor LCA (H-1B) public disclosure file.

Set the repository variable LCA_URL to the download link of the latest "LCA Programs (H-1B...)"
disclosure file (.xlsx or .csv) from https://www.dol.gov/agencies/eta/foreign-labor/performance
It only re-downloads when LCA_URL changes.
"""
import io
import json
import os
import sys
from datetime import datetime, timezone

import pandas as pd
import requests

from fetch_jobs import norm_company

WANTED = {"EMPLOYER_NAME", "CASE_STATUS", "JOB_TITLE", "VISA_CLASS"}


def main():
    url = os.environ.get("LCA_URL", "").strip()
    if not url:
        print("LCA_URL not set; skipping the H-1B employer list.")
        return
    try:
        with open("sponsors.json", encoding="utf-8") as f:
            current = json.load(f)
    except Exception:
        current = {}
    if current.get("source") == url:
        print("Employer list already built from this file.")
        return

    print("Downloading", url)
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (job-radar)"}, timeout=900)
    resp.raise_for_status()
    buf = io.BytesIO(resp.content)
    pick = lambda c: str(c).strip().upper() in WANTED
    if url.lower().split("?")[0].endswith(".csv"):
        df = pd.read_csv(buf, usecols=pick, dtype=str, low_memory=False)
    else:
        df = pd.read_excel(buf, usecols=pick, dtype=str)
    df.columns = [str(c).strip().upper() for c in df.columns]

    if "EMPLOYER_NAME" not in df.columns:
        sys.exit("That file has no EMPLOYER_NAME column. Use the LCA disclosure file.")
    if "CASE_STATUS" in df.columns:
        df = df[df["CASE_STATUS"].fillna("").str.strip().str.upper() == "CERTIFIED"]
    if "VISA_CLASS" in df.columns:
        df = df[df["VISA_CLASS"].fillna("").str.upper().str.contains("H-1B")]

    df["key"] = df["EMPLOYER_NAME"].fillna("").map(norm_company)
    df = df[df["key"] != ""]
    if "JOB_TITLE" in df.columns:
        df["an"] = df["JOB_TITLE"].fillna("").str.contains("analy", case=False)
    else:
        df["an"] = False
    grouped = df.groupby("key").agg(n=("key", "size"), a=("an", "sum"))
    employers = {k: [int(r.n), int(r.a)] for k, r in grouped.iterrows()}

    with open("sponsors.json", "w", encoding="utf-8") as f:
        json.dump({"source": url, "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "employers": employers}, f, separators=(",", ":"))
    print(f"Saved {len(employers)} employers with certified H-1B applications.")


if __name__ == "__main__":
    main()

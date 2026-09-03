#!/usr/bin/env python3
"""
jobradar — watch startup career pages, report only what's new.

Stdlib only. Reads companies.json, fetches each company's job board, diffs
against a local SQLite of jobs already seen, and writes a digest of the new
ones. Optionally scores them for relevance with `claude -p` and notifies.

  python3 jobradar.py               # fetch, diff, write digest
  python3 jobradar.py --seed        # first run: record everything, no digest
  python3 jobradar.py --filter      # also score new jobs with claude -p
  python3 jobradar.py --notify      # macOS notification if anything new
  python3 jobradar.py --all         # digest of every open job, not just new
  python3 jobradar.py --only telli  # single company (substring match)
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "seen.sqlite3"
COMPANIES = HERE / "companies.json"
DIGEST_DIR = HERE / "digests"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 jobradar"
CHROME = os.environ.get(
    "CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


# ---------------------------------------------------------------- http

def get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url, timeout=25):
    return json.loads(get(url, timeout))


def render(url, timeout=90):
    """Chrome headless DOM dump, for boards that build their list in JS."""
    if not Path(CHROME).exists():
        raise RuntimeError("Chrome not found; needed for provider 'render'")
    out = subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=8000", "--dump-dom", url],
        capture_output=True, timeout=timeout,
    )
    return out.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- providers
# Each returns a list of {id, title, location, url}. `slug` comes from
# companies.json; `url` there is the human-facing careers page.

def p_ashby(c):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    return [{"id": j["id"], "title": j["title"],
             "location": j.get("location") or "", "url": j.get("jobUrl") or c["url"],
             "posted_at": j.get("publishedAt")}
            for j in d.get("jobs", []) if j.get("isListed", True)]


def p_greenhouse(c):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs")
    return [{"id": str(j["id"]), "title": j["title"],
             "location": (j.get("location") or {}).get("name", ""),
             "url": j.get("absolute_url") or c["url"],
             "posted_at": j.get("first_published")}
            for j in d.get("jobs", [])]


def p_lever(c):
    d = get_json(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    return [{"id": j["id"], "title": j["text"],
             "location": (j.get("categories") or {}).get("location", ""),
             "url": j.get("hostedUrl") or c["url"],
             # epoch milliseconds -> ISO date
             "posted_at": (datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc)
                           .isoformat(timespec="seconds") if j.get("createdAt") else None)}
            for j in d]


def p_recruitee(c):
    d = get_json(f"https://{c['slug']}.recruitee.com/api/offers/")
    return [{"id": str(j["id"]), "title": j["title"],
             "location": j.get("location") or "", "url": j.get("careers_url") or c["url"],
             "posted_at": j.get("published_at")}
            for j in d.get("offers", [])]


def p_smartrecruiters(c):
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?limit=100")
    return [{"id": j["id"], "title": j["name"],
             "location": (j.get("location") or {}).get("city", ""),
             "url": j.get("ref") or c["url"],
             "posted_at": j.get("releasedDate")} for j in d.get("content", [])]


def p_workable(c):
    d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{c['slug']}?details=true")
    return [{"id": j["shortcode"], "title": j["title"],
             "location": j.get("location", {}).get("city", "") if isinstance(j.get("location"), dict) else str(j.get("location") or ""),
             "url": j.get("application_url") or j.get("url") or c["url"],
             "posted_at": j.get("published_on")}
            for j in d.get("jobs", [])]


def p_personio(c):
    xml = get(f"https://{c['slug']}.jobs.personio.de/xml")
    jobs = []
    for block in re.findall(r"<position>(.*?)</position>", xml, re.S):
        def tag(t):
            m = re.search(rf"<{t}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", block, re.S)
            return unescape(m.group(1).strip()) if m else ""
        jid = tag("id")
        if jid:
            jobs.append({"id": jid, "title": tag("name"), "location": tag("office"),
                         "url": f"https://{c['slug']}.jobs.personio.de/job/{jid}"})
    return jobs


# JSON-LD JobPosting works on a surprising number of custom career pages.
def _from_jsonld(html, fallback_url):
    jobs = []
    for blob in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(blob)
        except Exception:
            continue
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, list):
                stack.extend(o)
            elif isinstance(o, dict):
                if o.get("@type") == "JobPosting" and o.get("title"):
                    loc = o.get("jobLocation") or {}
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    addr = (loc or {}).get("address") or {}
                    jobs.append({
                        "id": str(o.get("identifier") or o.get("url") or o["title"]),
                        "title": o["title"],
                        "location": addr.get("addressLocality", "") if isinstance(addr, dict) else "",
                        "url": o.get("url") or fallback_url,
                        "posted_at": o.get("datePosted"),
                    })
                stack.extend(o.values())
    return jobs


def p_render(c):
    """Last resort: render the page and scrape job links heuristically."""
    html = render(c["url"])
    jobs = _from_jsonld(html, c["url"])
    if jobs:
        return jobs
    pat = c.get("link_pattern", r"/(jobs?|careers?|positions?|stellen)/[^\"'?#]+")
    seen, out = set(), []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.S | re.I):
        href, inner = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
        title = unescape(re.sub(r"\s+", " ", inner)).strip()
        if not re.search(pat, href) or not (4 < len(title) < 120):
            continue
        if href.startswith("/"):
            base = re.match(r"https?://[^/]+", c["url"])
            href = base.group(0) + href if base else href
        if href in seen:
            continue
        seen.add(href)
        out.append({"id": href, "title": title, "location": "", "url": href})
    return out


PROVIDERS = {
    "ashby": p_ashby, "greenhouse": p_greenhouse, "lever": p_lever,
    "recruitee": p_recruitee, "smartrecruiters": p_smartrecruiters,
    "workable": p_workable, "personio": p_personio, "render": p_render,
}


# ---------------------------------------------------------------- storage

def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS jobs(
        company TEXT, job_id TEXT, title TEXT, location TEXT, url TEXT,
        first_seen TEXT, PRIMARY KEY(company, job_id))""")
    return con


# ---------------------------------------------------------------- relevance

# Personal — see the note in core.py above LOCATION_TIERS for the
# profile_local.py / profile_example.py fallback pattern.
try:
    from profile_local import CLI_FILTER_PROMPT as FILTER_PROMPT
except ImportError:
    from profile_example import CLI_FILTER_PROMPT as FILTER_PROMPT


# Cheap pre-filter so the LLM never sees the obvious noise (Enpal and 1Komma5
# alone post ~500 sales/installer roles). Drops by title only — deliberately
# permissive; the LLM makes the real call.
NOISE = re.compile(
    r"\b(sales|account executive|sdr|bdr|vertrieb|marketing|recruit|talent|hr\b|"
    r"people (ops|&)|finance|accountant|buchhalt|legal|counsel|office manager|"
    r"customer success|support agent|installer|monteur|elektriker|handwerk|"
    r"außendienst|aussendienst|praktikum sales|working student sales|"
    r"technician|mechanic|logistik|lager|fahrer|driver|einkauf|procurement specialist)\b",
    re.I)
SENIOR = re.compile(r"\b(staff|principal|lead|head of|director|vp |chief|manager)\b", re.I)


def prefilter(jobs):
    return [j for j in jobs if not NOISE.search(j["title"]) and not SENIOR.search(j["title"])]


def score(jobs, chunk=60):
    """Ask `claude -p` for APPLY/MAYBE/SKIP per job. Returns {index: (verdict, reason)}."""
    verdicts = {}
    for start in range(0, len(jobs), chunk):
        batch = jobs[start:start + chunk]
        listing = "\n".join(
            f"{start + k}. {j['company']} — {j['title']} ({j['location'] or 'n/a'})"
            for k, j in enumerate(batch))
        try:
            out = subprocess.run(
                ["claude", "-p", FILTER_PROMPT.format(jobs=listing)],
                capture_output=True, text=True, timeout=600)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"  ! relevance filter stopped ({e.__class__.__name__})", file=sys.stderr)
            break
        for line in out.stdout.splitlines():
            m = re.match(r"\s*(\d+)\s*\|\s*(APPLY|MAYBE|SKIP)\s*\|\s*(.*)", line, re.I)
            if m:
                verdicts[int(m.group(1))] = (m.group(2).upper(), m.group(3).strip())
        print(f"  scored {min(start + chunk, len(jobs))}/{len(jobs)}")
    return verdicts


# ---------------------------------------------------------------- output

RANK = {"APPLY": 0, "MAYBE": 1, "": 2, "SKIP": 3}


def digest(jobs, verdicts, errors, title):
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"# {title}", f"_{stamp} — {len(jobs)} posting(s)_", ""]
    ranked = sorted(
        enumerate(jobs),
        key=lambda p: (RANK.get(verdicts.get(p[0], ("", ""))[0], 2), p[1]["company"]))
    for i, j in ranked:
        verdict, reason = verdicts.get(i, ("", ""))
        tag = f"**{verdict}** — {reason}  \n" if verdict else ""
        loc = f" · {j['location']}" if j["location"] else ""
        lines.append(f"- **{j['company']}** — [{j['title']}]({j['url']}){loc}  \n  {tag}".rstrip())
    if errors:
        lines += ["", "## Fetch errors", ""] + [f"- {c}: {e}" for c, e in errors]
    return "\n".join(lines) + "\n"


def notify(text):
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "jobradar"'],
                   capture_output=True)


def email(subject, body_md):
    """Optional. Needs RESEND_API_KEY and JOBRADAR_TO in the environment."""
    key, to = os.environ.get("RESEND_API_KEY"), os.environ.get("JOBRADAR_TO")
    if not (key and to):
        return False
    sender = os.environ.get("JOBRADAR_FROM", "jobradar <onboarding@resend.dev>")
    payload = json.dumps({
        "from": sender, "to": [to], "subject": subject,
        "text": body_md,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25):
            return True
    except urllib.error.HTTPError as e:
        print(f"  ! email failed: {e.code} {e.read()[:200]!r}", file=sys.stderr)
    except Exception as e:
        print(f"  ! email failed: {e}", file=sys.stderr)
    return False


# ---------------------------------------------------------------- main

ATS_HINTS = [
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9._-]+)"),
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9._-]+)"),
    ("personio", r"([A-Za-z0-9._-]+)\.jobs\.personio\.[a-z]+"),
    ("recruitee", r"\b(?!careers-analytics\b)([A-Za-z0-9._-]+)\.recruitee\.com"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9._-]+)"),
    ("smartrecruiters", r"careers\.smartrecruiters\.com/([A-Za-z0-9._-]+)"),
]


def probe(url):
    """Guess which ATS a careers page uses, so it can be added to companies.json."""
    try:
        html = get(url)
    except Exception as e:
        print(f"plain fetch failed ({e}); trying rendered")
        html = ""
    for source in (html, ""):
        if source is None:
            continue
        for name, pat in ATS_HINTS:
            m = re.search(pat, source)
            if m:
                print(json.dumps({"company": "?", "provider": name,
                                  "slug": m.group(1), "url": url}, indent=2))
                return
        if source is html:
            print("no ATS in raw HTML; rendering with Chrome...")
            try:
                source = render(url)
            except Exception as e:
                print(f"render failed: {e}")
                break
            for name, pat in ATS_HINTS:
                m = re.search(pat, source)
                if m:
                    print(json.dumps({"company": "?", "provider": name,
                                      "slug": m.group(1), "url": url}, indent=2))
                    return
    print(json.dumps({"company": "?", "provider": "render", "url": url}, indent=2))
    print('# no known ATS — falls back to rendering. Add "link_pattern" if noisy.')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", metavar="URL",
                    help="detect the ATS behind a careers page and print a companies.json entry")
    ap.add_argument("--seed", action="store_true",
                    help="record current postings without producing a digest")
    ap.add_argument("--all", action="store_true", help="digest every open job, not just new")
    ap.add_argument("--filter", action="store_true", help="score with claude -p")
    ap.add_argument("--notify", action="store_true", help="macOS notification when new")
    ap.add_argument("--only", help="substring match on company name")
    args = ap.parse_args()

    if args.probe:
        return probe(args.probe)

    companies = json.loads(COMPANIES.read_text())
    if args.only:
        companies = [c for c in companies
                     if args.only.lower() in c["company"].lower()]
    companies = [c for c in companies if not c.get("disabled")]

    con, errors, current = db(), [], []
    for c in companies:
        fn = PROVIDERS.get(c["provider"])
        if not fn:
            errors.append((c["company"], f"unknown provider {c['provider']}"))
            continue
        try:
            jobs = fn(c)
        except Exception as e:
            errors.append((c["company"], f"{e.__class__.__name__}: {e}"))
            print(f"  ! {c['company']}: {e}", file=sys.stderr)
            continue
        print(f"  {c['company']}: {len(jobs)} open")
        for j in jobs:
            j["company"] = c["company"]
        current += jobs

    now = datetime.now(timezone.utc).isoformat()
    known = {(r[0], r[1]) for r in con.execute("SELECT company, job_id FROM jobs")}
    new = [j for j in current if (j["company"], j["id"]) not in known]
    con.executemany(
        "INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?)",
        [(j["company"], j["id"], j["title"], j["location"], j["url"], now) for j in current])
    con.commit()

    if args.seed:
        print(f"Seeded {len(current)} postings. Future runs report only new ones.")
        return

    show = current if args.all else new
    if args.filter:
        kept = prefilter(show)
        print(f"\nprefilter: {len(show)} → {len(kept)} worth scoring")
        show = kept
    if not show:
        print("Nothing new.")
        if errors:
            print("Errors:", errors)
        return

    verdicts = score(show) if args.filter else {}
    DIGEST_DIR.mkdir(exist_ok=True)
    label = "All open postings" if args.all else "New postings"
    path = DIGEST_DIR / (datetime.now().strftime("%Y-%m-%d-%H%M") +
                         ("-all" if args.all else "-new") + ".md")
    body = digest(show, verdicts, errors, label)
    path.write_text(body)
    print(f"\n{len(show)} posting(s) → {path}")

    if args.notify and new:
        good = sum(1 for v in verdicts.values() if v[0] == "APPLY")
        summary = f"{len(show)} new posting(s)" + (f", {good} worth applying" if verdicts else "")
        notify(summary)
        if email(f"jobradar: {summary}", body):
            print("  emailed")


if __name__ == "__main__":
    main()

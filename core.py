"""
jobradar core — storage, job-description fetching, and relevance scoring.

Shared by the CLI (jobradar.py) and the web app (app.py). The CLI's provider
functions are imported from jobradar.py so there is one definition of how each
applicant-tracking system is read.
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

import jobradar as jr

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("JOBRADAR_DATA", HERE / "data"))
DATA.mkdir(parents=True, exist_ok=True)

# The CVs, cover letters and answer files. jobradar used to live inside
# cvwork, so this was just HERE.parent; they are siblings now.
CVWORK = Path(os.environ.get("CVWORK_DIR", HERE.parent / "cvwork"))
if not CVWORK.is_dir():
    # Say so now: otherwise /apply just reports nothing is ready, and the
    # missing folder looks like missing paperwork.
    print(f"jobradar: cvwork not found at {CVWORK} -- set CVWORK_DIR",
          file=sys.stderr)
DB_PATH = DATA / "jobradar.sqlite3"

# ------------------------------------------------------------------ location

# Your home-base geography and CV routing are personal, so they live outside
# this file — see profile_local.py (gitignored) and profile_example.py (the
# fallback template, tracked in git). Cloning this repo gets the matching
# engine below, never one person's target cities or identity.
try:
    from profile_local import LOCATION_TIERS, LOCATION_LABEL
except ImportError:
    from profile_example import LOCATION_TIERS, LOCATION_LABEL
ABROAD = re.compile(
    r"\b(london|paris|amsterdam|madrid|barcelona|lisbon|milan|warsaw|prague|"
    r"stockholm|copenhagen|oslo|helsinki|dublin|tallinn|new york|san francisco|"
    r"singapore|toronto|bengaluru|bangalore|s(?:ã|a)o paulo|tel aviv|zagreb|"
    r"bucharest|sofia|athens|brussels|luxembourg|"
    r"palo alto|boston|austin|seattle|chicago|denver|atlanta|los angeles|"
    r"united kingdom|england|scotland|derby|manchester|cambridge, |"
    r"tunis|dubai|hong kong|shanghai|seoul|sydney|melbourne|"
    r"sunnyvale|tokyo|gurugram|lima|buenos aires|santiago|bogot(?:á|a)|almaty|"
    r"washington|arlington|tirana|lisboa|lisbon|malm(?:ö|o)|budapest|nantes|"
    r"levallois|marseille|ia(?:ș|s)i|gda(?:ń|n)sk|krak(?:ó|o)w|wroc(?:ł|l)aw|"
    r"portugal|spain|france|belgium|poland|netherlands|romania|hungary|bulgaria|"
    r"italy|greece|serbia|ukraine|turkey|egypt|kenya|nigeria|vietnam|thailand|"
    r"indonesia|malaysia|taiwan|korea|chile|peru|argentina|colombia|"
    r"bristol|edinburgh|glasgow|leeds|birmingham|uk|"
    r"wien|vienna|linz|graz|salzburg|austria|"
    r"z(?:ü|ue|u)rich|basel|bern|geneva|gen(?:è|e)ve|lausanne|switzerland|"
    r"casablanca|rabat|morocco|tunisia|algeria)\b", re.I)

# Continent-scoped "remote" that is not remote for someone in Germany. Checked
# BEFORE the remote pattern, because "Home Based - Americas" matches both and
# only one of the two readings can be right. A string naming EMEA/Europe as
# well (e.g. "Home Based - Americas; Home based - EMEA") is genuinely open to
# him, so those are excluded here and fall through to the remote tier.
NON_EU_REMOTE = re.compile(
    r"\b(americas|latam|apac|north america|us timezones?|\bus\b|"
    r"united states|usa|canada|mexico|brazil|argentina|colombia|"
    r"philippines|india|japan|australia|new zealand|south africa|"
    # US states and Canadian provinces named without a country word, seen on
    # postings like "Remote, US, Massachusetts" that the bare US/state check
    # above would otherwise miss if written as just the state/province name.
    r"massachusetts|california|texas|colorado|new york|washington state|"
    r"alberta|british columbia|manitoba|nova scotia|ontario|quebec)\b", re.I)
EU_ANCHOR = re.compile(r"\bemea\b|\beurope\b|european union|worldwide|\bglobal\b|"
                       r"germany|deutschland", re.I)


def classify_location(text):
    """-> (tier, rank). Lower rank is better. Multi-location strings win on best."""
    t = (text or "").lower()
    if not t.strip():
        return "unknown", 4
    if NON_EU_REMOTE.search(t) and not EU_ANCHOR.search(t):
        return "abroad", 5
    best = None
    for tier, rank, pattern in LOCATION_TIERS:
        if re.search(pattern, t, re.I):
            if best is None or rank < best[1]:
                best = (tier, rank)
    if best:
        # "Anywhere in France, Belgium, Spain" matches the remote tier's
        # \banywhere\b, but it is not open to him at all — it names specific
        # non-German countries and nothing anchors it back to Germany/EU-wide.
        if best[0] == "remote" and ABROAD.search(t) and not EU_ANCHOR.search(t):
            return "abroad", 5
        return best
    if ABROAD.search(t):
        return "abroad", 5
    return "unknown", 4


# ------------------------------------------------------------------ storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    company     TEXT NOT NULL,
    job_id      TEXT NOT NULL,
    title       TEXT,
    location    TEXT,
    url         TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    closed_at   TEXT,
    loc_tier    TEXT,
    loc_rank    INTEGER,
    jd_text     TEXT,
    jd_fetched  TEXT,
    verdict     TEXT,
    reason      TEXT,
    blockers    TEXT,
    scored_at   TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    note        TEXT,
    status_at   TEXT,
    logistics   TEXT,
    posted_at   TEXT,   -- when the company says it posted the role (per its ATS);
                        -- NULL where the source (Personio, generic render) doesn't say
    PRIMARY KEY(company, job_id)
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS jobs_verdict ON jobs(verdict);

-- What each application form actually asks for, and which documents exist.
-- Filled by hand after reading a form; document presence is checked live on
-- disk, never trusted from this table.
CREATE TABLE IF NOT EXISTS prep(
    company    TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    form_url   TEXT,
    letter     TEXT,          -- optional | none | required
    asks       TEXT,          -- JSON list of what the form demands
    essays     INTEGER DEFAULT 0,
    cv_track   TEXT,          -- frontend | python
    letter_pdf TEXT,          -- path under cvwork/ (e.g. cover_letters/...pdf)
    answers_md TEXT,
    watch      TEXT,          -- the one thing that could sink this application
    checked_at TEXT,
    PRIMARY KEY(company, job_id)
);

CREATE TABLE IF NOT EXISTS sweeps(
    started_at TEXT, finished_at TEXT,
    found INTEGER, new INTEGER, scored INTEGER, errors TEXT
);
"""


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # Column added after the table already existed on disk for early users —
    # CREATE TABLE IF NOT EXISTS above won't retrofit it, so migrate by hand.
    cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)")}
    if "posted_at" not in cols:
        con.execute("ALTER TABLE jobs ADD COLUMN posted_at TEXT")
        con.commit()
    return con


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def companies():
    return [c for c in json.loads((HERE / "companies.json").read_text())
            if not c.get("disabled")]


# ------------------------------------------------------- job descriptions

def fetch_jd(job):
    """Full posting text. This is what the title-only filter was missing."""
    url = job["url"]
    try:
        if "ashbyhq.com" in url:
            m = re.search(r"ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
            if m:
                board = jr.get_json(
                    f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
                for j in board.get("jobs", []):
                    if j["id"] == m.group(2):
                        return strip_html(j.get("descriptionHtml")
                                          or j.get("descriptionPlain") or "")
        if "greenhouse.io" in url:
            m = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
            if m:
                d = jr.get_json("https://boards-api.greenhouse.io/v1/boards/"
                                f"{m.group(1)}/jobs/{m.group(2)}")
                return strip_html(d.get("content", ""))
        if "lever.co" in url:
            m = re.search(r"lever\.co/([^/]+)/([0-9a-f-]{36})", url)
            if m:
                d = jr.get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/"
                                f"{m.group(2)}?mode=json")
                return strip_html(d.get("descriptionPlain") or d.get("description", ""))
        html = jr.get(url)
    except Exception as e:
        return f"[could not fetch: {e.__class__.__name__}]"
    text = strip_html(html)
    # Custom boards render client-side; fall back to Chrome only when needed.
    if len(text) < 400:
        try:
            text = strip_html(jr.render(url))
        except Exception:
            pass
    return text


def strip_html(html):
    if not html:
        return ""
    t = re.sub(r"<(script|style|nav|footer|svg)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<(li|p|br|h[1-6]|div|tr)[^>]*>", "\n", t, flags=re.I)
    t = unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"[ \t ]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()[:14000]


# ------------------------------------------------- free hard-blocker scan

# Some blockers are stated so plainly that a regex reads them more reliably
# than a model does — it quotes rather than paraphrases, and never infers.
# Validated against 323 model-judged postings: 79 flagged, 0 disagreements.
GERMAN_REQ = re.compile(
    r"(deutsch\s+als\s+muttersprache|muttersprachlich|verhandlungssicher\w*\s+deutsch|"
    r"flie(?:ß|ss)end(?:e[nrs]?)?\s+deutsch|deutsch\w*\s*(?:auf\s*)?(?:niveau\s*)?[( ]?(?:c1|c2)|"
    r"(?:fluent|native|business[- ]level|professional)[^.\n]{0,30}\bgerman\b|"
    r"\bgerman\b[^.\n]{0,30}(?:fluent|native|\bc1\b|\bc2\b|business[- ]level|mandatory|required))",
    re.I)
# "3 to 6 years" sets a floor of 3, not 6 — only the floor is a blocker.
YEARS_RANGE = re.compile(
    r"\b([1-9])\s*(?:-|–|to|bis)\s*(?:5|6|7|8|9|10)\s*\+?\s*(?:years|jahre)", re.I)
YEARS_REQ = re.compile(
    r"\b(?:5|6|7|8|9|10|[1-9]\d)\s*\+\s*(?:years|jahre|yrs)\b|"
    r"\b(?:minimum|min\.?|at least|mindestens|über)\s*(?:of\s*)?(?:5|6|7|8|9|10)"
    r"\s*\+?\s*(?:years|jahre)", re.I)


# A posting that's itself written in English, even one line stating "German
# required", tends not to enforce that in practice — English is evidently the
# working language. Only auto-SKIP the German requirement when the posting is
# actually written in German; otherwise let the model weigh it (MAYBE/logistics)
# rather than a free regex bounce.
GERMAN_JD = re.compile(r"\b(und|der|die|das|ist|mit|für|wir|werden|sowie|"
                        r"unser|unsere|sie|dich|deine|erfahrung|kenntnisse)\b", re.I)


def hard_block(jd):
    """A quoted, stated blocker — or None. Saves a model call when it fires."""
    m = GERMAN_REQ.search(jd or "")
    if m and len(GERMAN_JD.findall(jd or "")) >= 5:
        return "German requirement in the posting: " + m.group(0).strip()[:80]
    for m in YEARS_REQ.finditer(jd or ""):
        if YEARS_RANGE.search(jd[max(0, m.start() - 24):m.end() + 4]):
            continue
        return "Experience bar in the posting: " + m.group(0).strip()[:60]
    return None


# ------------------------------------------------------------------ documents

# Three tailored CVs exist (cvwork/cv/out). Which one to send is decidable from
# the posting text already cached, so it is decided here rather than guessed at
# the moment of applying. Whether the *form* demands a cover letter is NOT
# decidable from the posting — that needs the form opened, which is what the
# Apply desk records. These two facts are kept apart on purpose.
CV_FRONTEND = re.compile(
    r"\b(react|next\.?js|typescript|frontend|front[- ]end|tailwind|css|"
    r"vue|angular|svelte|ui engineer|design system|browser)\b", re.I)
CV_PYTHON = re.compile(
    r"\b(python|fastapi|django|flask|pandas|numpy|airflow|llm|rag|openai|"
    r"machine learning|\bml\b|data pipeline|backend|etl|pytorch|scikit)\b", re.I)
CV_STUDENT = re.compile(
    r"\b(werkstudent|working student|intern|internship|praktik\w*|thesis|"
    r"abschlussarbeit|graduate|new grad|student)\b", re.I)


def cv_track(title, jd):
    """-> (track, why). Which of the tailored CVs this posting wants."""
    text = f"{title or ''}\n{jd or ''}"
    # A working-student or internship posting is read for different things than
    # a full-time one, so that variant wins over the stack split when the title
    # says so — the title, not the body, which mentions students in passing.
    if CV_STUDENT.search(title or ""):
        return "werkstudent", "working-student / internship posting"
    # How many *different* technologies are named is the better signal — one
    # stack mentioned ten times is boilerplate, five named once is a real stack.
    # Raw frequency only breaks the ties, which long postings produce often.
    fe = [m.group(0).lower() for m in CV_FRONTEND.finditer(text)]
    py = [m.group(0).lower() for m in CV_PYTHON.finditer(text)]
    fe_k, py_k = (len(set(fe)), len(fe)), (len(set(py)), len(py))
    if fe_k > py_k:
        return "frontend", f"{fe_k[0]} frontend terms ({fe_k[1]} mentions) vs {py_k[0]}"
    if py_k > fe_k:
        return "python", f"{py_k[0]} Python/AI terms ({py_k[1]} mentions) vs {fe_k[0]}"
    return "general", "no clear lean — the one-page general CV"


def letter_for(company):
    """The tailored cover letter already written for this company, or None."""
    d = CVWORK / "cover_letters"
    if not d.is_dir():
        return None
    key = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if not key:
        return None
    for f in sorted(d.glob("*.pdf")):
        stem = re.sub(r"[^a-z0-9]", "", f.stem.lower())
        # "Anschreiben_Jibran_Mughal_TACTO.pdf" -> match on the company token.
        if key and (key in stem or stem.endswith(key)):
            return f.relative_to(CVWORK).as_posix()
    return None


# --------------------------------------------------------------- reachability

# The verdict judges role fit alone — deliberately, so that geography is not
# counted twice. But a shortlist has to be sendable, and some postings are ones
# he cannot take whatever the fit: they need a work permit he does not hold, or
# the German he does not have, or they are already filled. Those are caught here
# instead, on the LOGISTICS line the scorer produced, and are always reported
# with the phrase that triggered them rather than dropped silently.
REACH_CUT = (
    ("needs US work authorisation",
     r"\bU\.?S\.?\s*(?:work\s*)?(?:authoriz|authoris)|authoriz\w*\s+to\s+work\s+in\s+the\s+"
     r"(?:US|U\.S\.|United\s+States)|\bUS[- ]based\b|\bUS\s+Remote\b"),
    # Sponsorship alone is not a cut: he already holds a residence permit here, so
    # it only bites when the role also sits outside the EU. The region patterns
    # below carry that, and they match a stated relocation, never a mere mention
    # of a country in a list of eligible remote locations.
    # A bare city name is not a relocation requirement — a posting may simply
    # mention an office. Require the phrasing that actually binds: "relocation
    # to X", "based in X", "X-based", "Remote - X".
    ("based outside the reachable region",
     r"(?:relocation\s+to\s+(?:the\s+)?|based\s+in\s+(?:the\s+)?|"
     r"Remote\s*(?:[-\u2013\u2014]|in)\s*(?:the\s+)?)"
     r"(?:Bangalore|Charlotte|Raleigh|San\s+Mateo|San\s+Francisco|New\s+York|"
     r"Austin|Sydney|Toronto|Singapore|India|the\s+US\b|the\s+United\s+States|"
     r"the\s+UK\b|the\s+United\s+Kingdom)|"
     r"\b(?:UK|U\.K\.|United\s+Kingdom|Bangalore|San\s+Mateo|San\s+Francisco|"
     r"New\s+York)[- ]based\b"),
    ("requires German beyond A2",
     r"(?:fluen\w+|native|business[- ]level)\s+(?:\w+\s+){0,3}German|"
     r"German\s+(?:\w+\s+){0,2}(?:fluency|required|mandatory)|"
     r"gute[nr]?\s+Deutsch|verhandlungssicher|Deutsch\s+als\s+Muttersprache"),
    ("the posting says the role is filled",
     r"has\s+been\s+filled|no\s+longer\s+(?:accepting|open)|position\s+is\s+closed"),
)
REACH_CUT = tuple((why, re.compile(pat, re.I)) for why, pat in REACH_CUT)


def out_of_reach(row):
    """Why this posting can't be sent, or None. Reads what the scorer recorded."""
    text = " ".join(str(row.get(k) or "") for k in ("logistics", "blockers", "reason"))
    for why, pat in REACH_CUT:
        m = pat.search(text)
        if m:
            return why, m.group(0).strip()
    return None


# ------------------------------------------------------------------ scoring

# Who you are and what counts as a fit is personal too — see the note above
# LOCATION_TIERS. Same fallback pattern: profile_local.py if you have one,
# otherwise the generic template in profile_example.py.
try:
    from profile_local import PROFILE
except ImportError:
    from profile_example import PROFILE

SCORE_PROMPT = """{profile}

Below is one job posting. Judge it against the profile.

Reply with EXACTLY four lines and nothing else:
VERDICT: APPLY or MAYBE or SKIP
BLOCKERS: semicolon-separated hard blockers, quoted from the posting, or NONE
LOGISTICS: what taking this job would require — relocation to a named city,
  full-time only, on-site days, unusual hours — or NONE
REASON: one sentence, max 25 words, on the strongest match or mismatch in SKILLS

Judge the VERDICT on ROLE FIT ALONE: does this person's stack, level and
background make them a credible candidate on the merits?

Location, relocation, on-site requirements, full-time-only and working-student
availability are NOT part of the verdict. They are handled elsewhere and must
never lower it — record them on the LOGISTICS line instead. A perfect skills
match in Berlin is an APPLY with "relocation to Berlin" in LOGISTICS.

APPLY  = a credible candidate on the merits; worth a tailored application.
MAYBE  = plausible but the skills are a stretch, or the posting is too vague.
SKIP   = a hard blocker applies, or the role is the wrong discipline or level.

Only cite a blocker the posting actually states. Do not infer a German
requirement, a clearance requirement, or a seniority bar that is not written.

COMPANY: {company}
TITLE: {title}
LOCATION: {location}

POSTING:
{jd}
"""


def score_job(company, title, location, jd, model="claude-opus-5"):
    """-> (verdict, blockers, reason, logistics). `claude -p`, else the SDK."""
    prompt = SCORE_PROMPT.format(profile=PROFILE, company=company, title=title,
                                 location=location or "not stated", jd=jd[:12000])
    out = _run_claude_cli(prompt, model) if _have_cli() else _run_api(prompt, model)
    if out is None:
        # Four values, like every other path — a caller unpacking this into
        # (verdict, blockers, reason, logistics) must not blow up just because
        # the scorer had a bad minute. A None verdict leaves the row unscored.
        return None, None, None, None
    v = re.search(r"VERDICT:\s*(APPLY|MAYBE|SKIP)", out, re.I)
    b = re.search(r"BLOCKERS:\s*(.+)", out)
    g = re.search(r"LOGISTICS:\s*(.+)", out)
    r = re.search(r"REASON:\s*(.+)", out)
    return (v.group(1).upper() if v else "MAYBE",
            (b.group(1).strip() if b else "") or "NONE",
            r.group(1).strip() if r else "",
            (g.group(1).strip() if g else "") or "NONE")


def scorer():
    """Which scoring backend is reachable: 'cli', 'api', or None."""
    if os.environ.get("JOBRADAR_SCORER") != "api" and _which("claude"):
        return "cli"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    return None


_cli_checked = None


def _have_cli():
    global _cli_checked
    if _cli_checked is None:
        _cli_checked = (os.environ.get("JOBRADAR_SCORER", "auto") != "api"
                        and _which("claude"))
    return _cli_checked


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / name
        if p.exists() and os.access(p, os.X_OK):
            return True
    return False


def _run_claude_cli(prompt, model=None):
    try:
        cmd = ["claude", "-p"]
        if model:
            cmd += ["--model", model]
        r = subprocess.run(cmd + [prompt], capture_output=True,
                           text=True, timeout=300)
        return r.stdout
    except Exception as e:
        print(f"  ! claude CLI: {e}", file=sys.stderr)
        return None


def _run_api(prompt, model):
    """Anthropic SDK path — used inside Docker, where the CLI isn't present."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        print("  ! pip install anthropic (or run scoring on the host)", file=sys.stderr)
        return None
    try:
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            output_config={"effort": "low"},   # a three-line classification
            messages=[{"role": "user", "content": prompt}],
        )
        if msg.stop_reason == "refusal":
            return None
        return "".join(b.text for b in msg.content if b.type == "text")
    except Exception as e:
        print(f"  ! anthropic API: {e}", file=sys.stderr)
        return None


# ------------------------------------------------------------------- sweep

def sweep(fetch_descriptions=True, score=True, only=None, limit=60, log=print):
    """Poll every board, record new postings, fetch + score the new ones.

    Reading a posting costs a fetch plus an LLM call, so each sweep scores at
    most `limit` of them, best location first. The backlog drains over
    subsequent sweeps; nothing is dropped, and the cap is reported.
    """
    started, errors, seen_now, new_rows = now(), [], [], []
    fetched = set()          # companies whose board answered this run
    for c in companies():
        if only and only.lower() not in c["company"].lower():
            continue
        fn = jr.PROVIDERS.get(c["provider"])
        if not fn:
            errors.append(f"{c['company']}: unknown provider")
            continue
        try:
            jobs = fn(c)
        except Exception as e:
            errors.append(f"{c['company']}: {e.__class__.__name__}: {e}")
            log(f"  ! {c['company']}: {e}")
            continue
        fetched.add(c["company"])
        log(f"  {c['company']}: {len(jobs)} open")
        for j in jobs:
            j["company"] = c["company"]
        seen_now += jobs

    con = db()
    known = {(r["company"], r["job_id"]) for r in con.execute(
        "SELECT company, job_id FROM jobs")}
    for j in seen_now:
        key = (j["company"], j["id"])
        tier, rank = classify_location(j.get("location"))
        if key in known:
            # posted_at is filled in only if still missing — it's the ATS's
            # own stated post date, which doesn't change on a re-fetch, and
            # this also backfills rows that predate the posted_at column.
            con.execute("UPDATE jobs SET last_seen=?, closed_at=NULL, location=?,"
                        " loc_tier=?, loc_rank=?,"
                        " posted_at=COALESCE(posted_at, ?) WHERE company=? AND job_id=?",
                        (started, j.get("location", ""), tier, rank,
                         j.get("posted_at"), *key))
        else:
            # Titles that are plainly out of scope are marked SKIP on sight, so
            # they never reach the queue or cost an LLM call. The reason says so.
            noise = (jr.NOISE.search(j["title"]) and "non-engineering role"
                     or jr.SENIOR.search(j["title"]) and "senior/lead level")
            con.execute(
                "INSERT INTO jobs(company, job_id, title, location, url, first_seen,"
                " last_seen, loc_tier, loc_rank, status, verdict, blockers, reason,"
                " scored_at, posted_at) VALUES(?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?)",
                (j["company"], j["id"], j["title"], j.get("location", ""),
                 j["url"], started, started, tier, rank,
                 "SKIP" if noise else None, "NONE" if noise else None,
                 f"Filtered on title: {noise}." if noise else None,
                 started if noise else None, j.get("posted_at")))
            new_rows.append(key)
    # Postings that vanished from a board are closed, not deleted. Only companies
    # actually polled this run are considered — otherwise a filtered sweep would
    # close every posting belonging to the companies it skipped.
    live = {f"{c}|{i}" for c, i in {(j["company"], j["id"]) for j in seen_now}}
    # `fetched`, not the full company list: a board that errored or was filtered
    # out this run tells us nothing about whether its postings are still open.
    for r in con.execute("SELECT company, job_id FROM jobs WHERE closed_at IS NULL"):
        if r["company"] not in fetched:
            continue
        if f"{r['company']}|{r['job_id']}" not in live:
            con.execute("UPDATE jobs SET closed_at=? WHERE company=? AND job_id=?",
                        (started, r["company"], r["job_id"]))
    con.commit()
    log(f"\n{len(seen_now)} open, {len(new_rows)} new")

    scored, deferred = 0, 0
    if fetch_descriptions:
        # Cheap title pre-filter first, so the LLM only sees plausible roles;
        # then best-located first, so remote and Rhein-Main roles are read soonest.
        todo = [r for r in con.execute(
            "SELECT * FROM jobs WHERE scored_at IS NULL AND closed_at IS NULL"
            " AND loc_tier != 'abroad' ORDER BY loc_rank, first_seen DESC")
            if not jr.NOISE.search(r["title"]) and not jr.SENIOR.search(r["title"])]
        log(f"prefilter: {len(todo)} unscored postings worth reading")
        if limit and len(todo) > limit:
            deferred = len(todo) - limit
            todo = todo[:limit]
            log(f"reading {limit} this sweep, {deferred} deferred to the next one")
        for i, r in enumerate(todo, 1):
            jd = fetch_jd(r)
            con.execute("UPDATE jobs SET jd_text=?, jd_fetched=? WHERE company=? AND job_id=?",
                        (jd, now(), r["company"], r["job_id"]))
            con.commit()
            blocked = hard_block(jd) if len(jd) > 200 else None
            if blocked:
                con.execute("UPDATE jobs SET verdict='SKIP', blockers=?, reason=?,"
                            " scored_at=? WHERE company=? AND job_id=?",
                            (blocked, "Ruled out on a stated requirement, no model call.",
                             now(), r["company"], r["job_id"]))
                con.commit()
                log(f"  [{i}/{len(todo)}] free SKIP  {r['company']} — {r['title']}")
                continue
            if score and len(jd) > 200:
                v, b, reason, logi = score_job(r["company"], r["title"],
                                               r["location"], jd)
                if v:
                    con.execute("UPDATE jobs SET verdict=?, blockers=?, reason=?,"
                                " logistics=?, scored_at=? WHERE company=? AND job_id=?",
                                (v, b, reason, logi, now(), r["company"], r["job_id"]))
                    con.commit()
                    scored += 1
            log(f"  [{i}/{len(todo)}] {r['company']} — {r['title']}")

    # Anything the LLM never reached stays unscored rather than silently APPLY.
    con.execute("INSERT INTO sweeps VALUES(?,?,?,?,?,?)",
                (started, now(), len(seen_now), len(new_rows), scored,
                 "\n".join(errors)))
    con.commit()
    con.close()
    return {"found": len(seen_now), "new": len(new_rows), "scored": scored,
            "deferred": deferred, "errors": errors}

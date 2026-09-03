"""
jobradar web — a decision queue for job applications.

Sweeps and scoring are manual: nothing calls a model until you press a button,
and the button says what it will cost.

  uvicorn app:app --host 0.0.0.0 --port 8000
  (or: docker compose up)
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import core
import jobradar as jr

templates = Jinja2Templates(directory=str(core.HERE / "templates"))


def _ago(iso):
    """'3d ago' from an ISO timestamp, for the posted/added-to-app lines."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = int(hours / 24)
    return f"{days}d ago" if days < 60 else dt.date().isoformat()


templates.env.filters["ago"] = _ago

# Measured from the postings already fetched: ~5.1k chars of description plus
# ~1.5k of profile and instructions, at roughly 3.8 chars per token.
TOKENS_PER_JOB = 1900

state = {"running": False, "task": "", "log": [], "last": None,
         "done": 0, "total": 0}


def _log(msg):
    state["log"].append(str(msg))
    del state["log"][:-300]
    print(msg, flush=True)


def _idle():
    state.update(running=False, task="", done=0, total=0)


async def run_sweep(score=False, limit=0):
    """Poll every board. Reads descriptions and scores only when asked to."""
    if state["running"]:
        return
    state.update(running=True, task="Sweeping boards", log=[], done=0, total=0)
    try:
        result = await asyncio.to_thread(
            core.sweep, fetch_descriptions=score, score=score,
            limit=limit, log=_log)
        state["last"] = {"at": core.now(), **result}
    except Exception as e:
        _log(f"Sweep failed: {e!r}")
        state["last"] = {"at": core.now(), "error": repr(e)}
    finally:
        _idle()


async def run_scoring(limit, rescore=False):
    """Read and judge the next `limit` postings. One model call each."""
    if state["running"]:
        return
    state.update(running=True, task=f"Reading {limit} postings",
                 log=[], done=0, total=limit)
    try:
        where = ("closed_at IS NULL AND jd_text IS NOT NULL AND length(jd_text) > 200"
                 if rescore else "scored_at IS NULL AND closed_at IS NULL")
        con = core.db()
        todo = [dict(r) for r in con.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY loc_rank, first_seen DESC"
            f" LIMIT {int(limit)}")]
        con.close()
        state["total"] = len(todo)
        for i, r in enumerate(todo, 1):
            if not state["running"]:            # cancelled from the UI
                _log("Stopped.")
                break
            jd = r.get("jd_text") if rescore else None
            if not jd or len(jd) < 200:
                jd = await asyncio.to_thread(core.fetch_jd, r)
            v, b, reason, logi = await asyncio.to_thread(
                core.score_job, r["company"], r["title"], r["location"], jd)
            con = core.db()
            con.execute("UPDATE jobs SET jd_text=?, jd_fetched=?, verdict=?,"
                        " blockers=?, reason=?, logistics=?, scored_at=?"
                        " WHERE company=? AND job_id=?",
                        (jd, core.now(), v or r.get("verdict"), b, reason, logi,
                         core.now() if v else None, r["company"], r["job_id"]))
            con.commit()
            con.close()
            state["done"] = i
            _log(f"{v or '—':<5}  {r['company']} — {r['title']}")
        state["last"] = {"at": core.now(), "read": state["done"]}
    except Exception as e:
        _log(f"Scoring failed: {e!r}")
    finally:
        _idle()


app = FastAPI(title="jobradar")


# ------------------------------------------------------------------ queries

TABS = {
    "queue": "closed_at IS NULL AND status='new' AND (verdict IS NULL OR verdict!='SKIP')",
    "interested": "status='interested'",
    "applied": "status='applied'",
    "dismissed": "status='dismissed'",
    "skip": "closed_at IS NULL AND verdict='SKIP'",
    "all": "closed_at IS NULL",
}
# A working-student/intern posting is read for local commute range first —
# Darmstadt/Frankfurt, loc_rank 1-2. Full-time roles anywhere in Germany
# (loc_rank up to 'relocate') are fine as-is, so only students/interns whose
# posting sits outside that commute range get pushed down — not cut, since a
# strong one further out is still worth seeing, just not first.
STUDENT_TITLE = (
    "(title LIKE '%werkstudent%' OR title LIKE '%working student%'"
    " OR title LIKE '%intern%' OR title LIKE '%praktik%'"
    " OR title LIKE '%thesis%' OR title LIKE '%abschlussarbeit%'"
    " OR title LIKE '%graduate%' OR title LIKE '%new grad%')")
ORDER = ("CASE verdict WHEN 'APPLY' THEN 0 WHEN 'MAYBE' THEN 1"
         " WHEN 'SKIP' THEN 3 ELSE 2 END,"
         f" CASE WHEN loc_rank NOT IN (1, 2) AND {STUDENT_TITLE}"
         " THEN loc_rank + 3 ELSE loc_rank END, first_seen DESC")
PAGE = 60


def funnel():
    """The reduction this tool performs, as live counts. Also the nav."""
    con = core.db()
    one = lambda q: con.execute(q).fetchone()[0]
    total = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL")
    titled = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL"
                 " AND reason LIKE 'Filtered on title%'")
    read = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL"
               " AND scored_at IS NOT NULL AND reason NOT LIKE 'Filtered on title%'")
    unread = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL AND scored_at IS NULL")
    keep = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL"
               " AND verdict IN ('APPLY','MAYBE')")
    apply_n = one("SELECT COUNT(*) FROM jobs WHERE closed_at IS NULL AND verdict='APPLY'")
    con.close()
    return {"total": total, "titled": titled, "read": read, "unread": unread,
            "keep": keep, "apply": apply_n}


def counts():
    con = core.db()
    out = {k: con.execute(f"SELECT COUNT(*) FROM jobs WHERE {w}").fetchone()[0]
           for k, w in TABS.items()}
    con.close()
    return out


@app.get("/", response_class=HTMLResponse)
def index(request: Request, show: str = "queue", q: str = "",
          verdict: str = "", loc: str = "", company: str = "", posted: str = "",
          page: int = 0):
    where = [TABS.get(show, TABS["queue"])]
    args = []
    if q:
        where.append("(title LIKE ? OR company LIKE ? OR reason LIKE ?"
                     " OR blockers LIKE ?)")
        args += [f"%{q}%"] * 4
    if verdict:
        where.append("verdict IS ?" if verdict == "unread" else "verdict = ?")
        args.append(None if verdict == "unread" else verdict)
    if loc:
        where.append("loc_tier = ?")
        args.append(loc)
    if company:
        where.append("company = ?")
        args.append(company)
    if posted and posted.isdigit():
        # Posted date per the company's own ATS where it states one; falls
        # back to first_seen (when we found it) for sources that don't
        # (Personio, the generic render fallback) rather than hiding them.
        where.append("COALESCE(posted_at, first_seen) >= datetime('now', ?)")
        args.append(f"-{int(posted)} days")
    clause = "WHERE " + " AND ".join(where)

    con = core.db()
    total = con.execute(f"SELECT COUNT(*) FROM jobs {clause}", args).fetchone()[0]
    jobs = [dict(r) for r in con.execute(
        f"SELECT * FROM jobs {clause} ORDER BY {ORDER} LIMIT {PAGE} OFFSET {page * PAGE}",
        args)]
    firms = [r[0] for r in con.execute(
        "SELECT DISTINCT company FROM jobs WHERE closed_at IS NULL ORDER BY company")]
    con.close()

    return templates.TemplateResponse(request, "index.html", {
        "jobs": jobs, "show": show, "counts": counts(), "funnel": funnel(),
        "state": state, "loc_label": core.LOCATION_LABEL, "firms": firms,
        "q": q, "verdict": verdict, "loc": loc, "company": company, "posted": posted,
        "page": page, "total": total, "pages": (total + PAGE - 1) // PAGE,
        "tokens_per_job": TOKENS_PER_JOB, "scorer": core.scorer(),
        "loc_order": ["remote", "local", "commutable", "relocate", "abroad"],
    })


# Which tailored CV to route a posting to is personal — see the note in
# core.py above LOCATION_TIERS for the profile_local.py / profile_example.py
# fallback pattern this follows too.
try:
    from profile_local import CV_TRACKS, DEFAULT_CV
except ImportError:
    from profile_example import CV_TRACKS, DEFAULT_CV


@app.get("/apply", response_class=HTMLResponse)
def apply_desk(request: Request, sort: str = "default"):
    """What each form wants, and which documents actually exist on disk."""
    cvwork = core.CVWORK
    con = core.db()
    rows = [dict(r) for r in con.execute(
        "SELECT p.*, j.title, j.location, j.status, j.verdict, j.url,"
        " j.loc_tier, j.loc_rank"
        " FROM prep p JOIN jobs j USING (company, job_id)"
        " WHERE j.closed_at IS NULL ORDER BY p.company, j.title")]
    con.close()

    for r in rows:
        r["asks"] = json.loads(r["asks"] or "[]")
        # Never trust the table about a file — look.
        r["letter_ok"] = bool(r["letter_pdf"] and (cvwork / r["letter_pdf"]).exists())
        r["answers_ok"] = bool(r["answers_md"] and (cvwork / r["answers_md"]).exists())
        r["cv_name"], cv_file, r["cv_hint"] = CV_TRACKS.get(r["cv_track"], DEFAULT_CV)
        # Check the file this track actually names, not a generic stand-in.
        r["cv_file"] = cv_file
        r["cv_ok"] = (cvwork / cv_file).exists()
        needs_letter = r["letter"] in ("optional", "required")
        r["ready"] = r["cv_ok"] and r["answers_ok"] and (r["letter_ok"] or not needs_letter)
    if sort == "prio":
        # Work through the reachable, ready-to-go ones first: closest location,
        # nothing missing, alphabetical after that just to be stable. A
        # working-student/intern posting outside the local commute range
        # (loc_rank 1/2) sorts as if it were a tier further out — full-time
        # roles anywhere in Germany need no such penalty.
        def prio_key(r):
            rank = r["loc_rank"] if r["loc_rank"] is not None else 9
            if rank not in (1, 2) and core.CV_STUDENT.search(r["title"] or ""):
                rank += 3
            return (r["status"] == "applied", not r["ready"], rank, r["company"])
        rows.sort(key=prio_key)
    else:
        rows.sort(key=lambda r: (r["status"] == "applied", not r["ready"], r["company"]))

    return templates.TemplateResponse(request, "apply.html", {
        "rows": rows, "state": state, "funnel": funnel(), "counts": counts(),
        "loc_label": core.LOCATION_LABEL, "scorer": core.scorer(), "sort": sort,
        "ready": sum(1 for r in rows if r["ready"] and r["status"] != "applied"),
        "sent": sum(1 for r in rows if r["status"] == "applied"),
        "gaps": sum(1 for r in rows if not r["ready"]),
    })


SHORTLIST_TARGET = 40


@app.get("/shortlist", response_class=HTMLResponse)
def shortlist(request: Request, target: int = SHORTLIST_TARGET, sort: str = "default"):
    """A day's sending list: one role per company, best fit first.

    The queue is posting-shaped, but the unit of work is the application — and
    a queue sorted by posting collapses into a handful of employers (Canonical
    alone can hold nine APPLY rows). So this page takes the single best-scoring
    open role at each company, then sets aside the ones that cannot be sent
    whatever the fit. Nothing is hidden: the set-aside rows are listed below the
    list with the phrase from the posting that put them there.
    """
    target = max(1, min(int(target), 200))
    con = core.db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM jobs WHERE closed_at IS NULL AND status='new'"
        " AND verdict IN ('APPLY','MAYBE') AND loc_tier NOT IN ('abroad','unknown')"
        f" ORDER BY {ORDER}")]
    sent = [dict(r) for r in con.execute(
        "SELECT * FROM jobs WHERE status='applied' ORDER BY status_at DESC LIMIT 60")]
    # A company you have already written to is done for the day — otherwise
    # marking one Canonical role sent just promotes the next of its nine onto
    # the list, and the company never leaves.
    done = {r["company"] for r in con.execute(
        "SELECT DISTINCT company FROM jobs WHERE status='applied'")}
    # Rows whose application form has genuinely been opened and recorded.
    prepped = {(r["company"], r["job_id"]): dict(r)
               for r in con.execute("SELECT * FROM prep")}
    con.close()

    best, seen = [], set()
    for r in rows:                       # ORDER already put each company's best first
        if r["company"] in seen or r["company"] in done:
            continue
        seen.add(r["company"])
        best.append(r)

    reachable, cut = [], []
    for r in best:
        why = core.out_of_reach(r)
        if why:
            r["cut_why"], r["cut_quote"] = why
            cut.append(r)
        else:
            reachable.append(r)

    if sort == "found":
        # Newest-found-first — same "one role per company" set as the default
        # view, just re-ordered so what showed up most recently sends first.
        reachable.sort(key=lambda r: r["first_seen"], reverse=True)

    # Which CV to send is decidable from the posting; whether the form wants a
    # cover letter is not, so that stays blank until the form is opened. The
    # letter column reports only what already exists on disk in cvwork/.
    for r in reachable[:target]:
        track, why = core.cv_track(r["title"], r["jd_text"])
        name, path, hint = CV_TRACKS.get(track, CV_TRACKS["general"])
        r["cv_track"], r["cv_why"], r["cv_name"], r["cv_hint"] = track, why, name, hint
        r["cv_ok"] = (core.CVWORK / path).exists()
        r["letter_pdf"] = core.letter_for(r["company"])
        r["checked"] = prepped.get((r["company"], r["job_id"]))

    return templates.TemplateResponse(request, "shortlist.html", {
        "list": reachable[:target], "bench": reachable[target:], "cut": cut,
        "sent": sent, "target": target, "state": state, "funnel": funnel(),
        "counts": counts(), "loc_label": core.LOCATION_LABEL,
        "scorer": core.scorer(), "sort": sort,
        "n_apply": sum(1 for r in reachable[:target] if r["verdict"] == "APPLY"),
        "n_companies": len(seen),
    })


@app.get("/companies", response_class=HTMLResponse)
def companies_view(request: Request):
    con = core.db()
    stats = {r["company"]: dict(r) for r in con.execute(
        "SELECT company, COUNT(*) FILTER (WHERE closed_at IS NULL) AS open,"
        " COUNT(*) FILTER (WHERE verdict='APPLY' AND closed_at IS NULL) AS apply,"
        " COUNT(*) FILTER (WHERE verdict='MAYBE' AND closed_at IS NULL) AS maybe,"
        " COUNT(*) FILTER (WHERE scored_at IS NULL AND closed_at IS NULL) AS unread,"
        " MAX(first_seen) AS latest FROM jobs GROUP BY company")}
    con.close()
    listed = [{**c, **{k: 0 for k in ("open", "apply", "maybe", "unread")},
               **stats.get(c["company"], {})} for c in core.companies()]
    listed.sort(key=lambda c: (-c["apply"], -c["maybe"], -c["open"]))
    return templates.TemplateResponse(request, "companies.html", {
        "companies": listed, "state": state, "funnel": funnel(),
        "counts": counts(), "scorer": core.scorer()})


# ------------------------------------------------------------------ actions

@app.post("/status")
async def set_status(request: Request, company: str = Form(...),
                     job_id: str = Form(...), status: str = Form(...),
                     back: str = Form("/")):
    if status in {"new", "interested", "applied", "dismissed"}:
        con = core.db()
        con.execute("UPDATE jobs SET status=?, status_at=? WHERE company=? AND job_id=?",
                    (status, core.now(), company, job_id))
        con.commit()
        con.close()
    return RedirectResponse(back or "/", status_code=303)


@app.post("/note")
async def set_note(company: str = Form(...), job_id: str = Form(...),
                   note: str = Form(""), back: str = Form("/")):
    con = core.db()
    con.execute("UPDATE jobs SET note=? WHERE company=? AND job_id=?",
                (note.strip() or None, company, job_id))
    con.commit()
    con.close()
    return RedirectResponse(back or "/", status_code=303)


@app.post("/sweep")
async def sweep_now(back: str = Form("/")):
    asyncio.create_task(run_sweep(score=False))
    return RedirectResponse(back or "/", status_code=303)


@app.post("/score")
async def score_now(limit: int = Form(25), rescore: str = Form(""),
                    back: str = Form("/")):
    asyncio.create_task(run_scoring(max(1, min(int(limit), 500)), bool(rescore)))
    return RedirectResponse(back or "/", status_code=303)


@app.post("/stop")
async def stop():
    state["running"] = False
    return RedirectResponse("/", status_code=303)


@app.post("/companies/add")
async def add_company(url: str = Form(...), name: str = Form("")):
    entry = await asyncio.to_thread(_probe_entry, url, name)
    path = core.HERE / "companies.json"
    data = json.loads(path.read_text())
    if not any(c.get("url") == entry["url"] or
               (c.get("slug") and c.get("slug") == entry.get("slug")
                and c.get("provider") == entry["provider"]) for c in data):
        data.append(entry)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return RedirectResponse("/companies", status_code=303)


def _probe_entry(url, name):
    html = ""
    try:
        html = jr.get(url)
    except Exception:
        pass
    for source in (html, None):
        if source is None:
            try:
                source = jr.render(url)
            except Exception:
                break
        for provider, pattern in jr.ATS_HINTS:
            m = re.search(pattern, source)
            if m:
                return {"company": name or m.group(1), "provider": provider,
                        "slug": m.group(1), "url": url}
    return {"company": name or url, "provider": "render", "url": url}


@app.get("/status.json")
def status_json():
    return {"running": state["running"], "task": state["task"],
            "done": state["done"], "total": state["total"],
            "log": state["log"][-25:], "last": state["last"]}

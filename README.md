# jobradar

Watches company career pages directly (not LinkedIn), reads each new posting
in full, scores it against your own profile, and presents what's left as a
decision queue. Runs as a local web app in Docker, or as a CLI.

A full board sweep is pure HTTP — no model calls, no cost — and typically
takes under a minute even watching a couple hundred companies.

**Personal to you, not in this repo:** who you are and what counts as a fit
(`profile_local.py`), your target companies (`companies.json`), and your
application data (`data/`) are all gitignored. Copy `profile_example.py` to
`profile_local.py` and `companies.example.json` to `companies.json` to make
this yours — see each file's comments for what to fill in.

---

## The web app (recommended)

```sh
cd jobradar
export ANTHROPIC_API_KEY=sk-ant-...      # needed for scoring inside the container
docker compose up --build
```

Then open **http://localhost:8420**.

**Without Docker** — same app, and it uses your logged-in `claude` CLI for
scoring instead of an API key:

```sh
pip install -r requirements.txt
uvicorn app:app --port 8420
```

### What the pages do

- **Queue** — open postings you haven't decided on, best fit first
  (APPLY → MAYBE → unread → SKIP, and within each, closest-to-home first).
  Each card carries the verdict, a one-line reason, and any hard blockers found
  in the posting. Buttons: *Applied / Interested / Not for me*.
- **Send list** — a day's sending, sorted by *company* rather than posting: the
  single best-scoring open role at each, strongest fit first. The queue stacks
  up behind big employers with many open roles, which is wrong when the unit of
  work is the application. *Sent* writes straight to the database, and the
  whole company then leaves the list. Set the target with the box in the
  header; the rest wait on a bench below.

  Underneath it sits a second filter the verdict deliberately doesn't apply.
  Scoring judges role fit alone, so a posting needing work authorisation you
  don't hold, a language bar you don't meet, or one already filled can still
  score APPLY. Those are moved to **Set aside** by `REACH_CUT` in `core.py`,
  each row quoting the phrase that matched — it's a heuristic, shown so you
  can overrule it, never a silent drop.
- **Interested / Applied / Dismissed** — your decisions. Dismissing is memory:
  the posting never returns to the queue.
- **Companies** — every watched board, what it currently shows, and a box to add
  a new company by pasting its careers URL (the ATS is detected for you).
  Also has **Re-score everything**, for after you edit the profile.
- **Check boards** — polls every watched board for new postings. Pure HTTP,
  **no model calls and no cost.**
- **Read 10 / 25 / 100** — fetches those postings in full and judges them. One
  model call each; the button shows the estimated token spend before you press
  it. A live meter and the verdict stream show progress, and **Stop** halts the
  run between postings.

**Nothing runs on a timer.** There is no scheduler: the app only calls a model
when you press a button. Set `JOBRADAR_SWEEP_HOURS` if you want it back — it is
currently ignored.

Keyboard triage in the queue: <kbd>j</kbd>/<kbd>k</kbd> move, <kbd>a</kbd>
applied, <kbd>i</kbd> interested, <kbd>x</kbd> pass, <kbd>o</kbd> opens the
posting, <kbd>/</kbd> focuses search.

### State

Everything lives in a SQLite database at `/app/data` inside the container —
postings, full job-description text, verdicts, and your decisions. Postings that
disappear from a board are marked closed, not deleted.

It's a **named Docker volume** (`jobradar-data`), not a bind mount, and that is
deliberate: on macOS a bind-mounted SQLite file is not coherent across the
Docker VM boundary — the container reads it as `database disk image is
malformed` while the host sees the same file as fine. The volume survives
`docker compose down` and rebuilds.

```sh
# back up / inspect on the host
docker cp jobradar:/app/data/jobradar.sqlite3 ./data/backup.sqlite3

# restore into a fresh volume
docker compose up -d && docker cp ./data/backup.sqlite3 jobradar:/app/data/jobradar.sqlite3 && docker compose restart
```

`companies.json` *is* bind-mounted (it's a small text file, no journal), so
adding a company from the web UI edits the file in your repo.

**Don't run a host-side sweep while the container is up** — two writers on one
database is how the corruption above happens. Use the app, or stop the container
first.

---

## How scoring works

Three stages, cheapest first:

1. **Title filter** — sales, marketing, installers, senior/lead/staff/manager.
   Marked SKIP on sight with the reason `Filtered on title: …`, so they never
   cost an LLM call. About 60% of postings stop here.
2. **Fetch the full job description** — via the ATS API where one exists
   (Ashby, Greenhouse, Lever), else the page, else Chrome-rendered.
   **This is the stage that matters.** A posting's title alone routinely hides
   the requirement that would sink it — a language bar, a years-of-experience
   floor, a seniority level — buried three paragraphs into the body. Reading
   the full posting catches those the title filter can't.
3. **Score with Claude** against `PROFILE` in `profile_local.py` — your real
   constraints, whatever they are. Returns APPLY / MAYBE / SKIP, the blocking
   sentence quoted from the posting, and a separate **logistics** line.

   **The verdict judges role fit only** — stack, level, language bar. Location,
   relocation, on-site days and full-time-only never lower it; they go on the
   logistics line, because the card already shows geography on the distance
   rail and counting it twice buried every out-of-town match. A perfect skills
   match in Berlin is an Apply that says "relocation to Berlin" underneath.
   The prompt also forbids inferring a blocker the posting doesn't state —
   an earlier version was demoting every defence role on a guess about
   clearance.

You choose how many to read and when. Postings are queued best-location-first,
so postings near home are read before ones that would mean relocating. If no
scorer is configured the read buttons are disabled and the panel says why,
rather than fetching postings and leaving them silently unjudged.

**Edit `PROFILE` in `profile_local.py` when the facts change**, then hit
*Re-score everything* — it re-judges cached descriptions without re-fetching
any board.

### Which model, and what it costs

Scoring uses `claude-opus-5` at `effort: "low"` — a three-line classification
over ~3–6k tokens of job description. If the `claude` CLI is on PATH it is used
instead (no API key, uses your existing login); inside Docker it falls back to
the Anthropic SDK and `ANTHROPIC_API_KEY`. Force one or the other with
`JOBRADAR_SCORER=api`.

---

## Location ranking

Postings are ranked by how reachable they are from your home base — five
tiers: remote, home city, a reasonable commute, "would need to move", and
outside your target region entirely. Edit `LOCATION_TIERS` and
`LOCATION_LABEL` in `profile_local.py` to set your own geography; see
`profile_example.py` for the shape.

---

## The CLI (still works)

```sh
python3 jobradar.py                     # postings new since the last run
python3 jobradar.py --all --filter      # full scored sweep to a markdown digest
python3 jobradar.py --only wingcopter   # one company
python3 jobradar.py --probe <careers-url>   # detect a company's ATS
python3 jobradar.py --notify            # macOS notification + email via Resend
```

CLI digests land in `digests/` and use a separate `seen.sqlite3`; the web app
has its own database. For email, set `RESEND_API_KEY` and `JOBRADAR_TO`.

Cron it if you prefer the CLI to the web app:

```sh
30 8 * * 1-5 cd /path/to/jobradar && /usr/bin/python3 jobradar.py --filter --notify >> radar.log 2>&1
```

---

## Adding a company

Paste a careers URL into the box on the Companies page, or:

```sh
python3 jobradar.py --probe https://www.somestartup.com/careers
```

Either way the applicant-tracking system is detected and a `companies.json`
entry is produced. Supported directly: **Ashby, Greenhouse, Lever, Personio,
Recruitee, SmartRecruiters, Workable**, plus JSON-LD `JobPosting` and a
Chrome-rendered fallback. That covers most German and Austrian startup boards.

**The ATS is itself a useful signal** in some markets: Ashby / Greenhouse /
Lever boards skew toward English-speaking, remote-friendly companies, while
region-specific ATSes (Personio in the DACH market, for instance) more often
mean the role expects the local language. Worth knowing when you're triaging
which companies to add.

---

## Known limits

- **No LinkedIn.** It needs a login and scraping it risks the account. Outreach
  stays manual.
- **No auto-apply.** The forms have reCAPTCHA, and submitting on your behalf
  isn't something to automate — you always click the final Submit.
- **A board can show 0 open and still be correctly configured.** Some
  companies just aren't hiring right now; that's what a watcher is for —
  catching the day that changes, not guaranteeing something's always there.

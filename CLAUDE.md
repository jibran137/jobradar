# jobradar — instructions for the agent running this repo

This file is read automatically by Claude Code (or any agent that honors
`CLAUDE.md`) every time it opens this project. It exists mainly for one
moment: someone's first time.

## First run: build the profile before running anything

Check whether `profile_local.py` exists at the repo root.

**If it already exists, skip this whole section** — the person is already
set up. Just help with whatever they asked for.

**If it does NOT exist**, the person has just cloned this repo and jobradar
has no idea who they are yet. Do not run `docker compose up`, `uvicorn`, or
`python3 jobradar.py` first. Say so, then interview them:

> "This is jobradar's first run on your machine — before I start it, I need
> to build your personal profile (`profile_local.py`). It's gitignored, so
> none of this leaves your computer. I'll ask a handful of questions, a few
> of them multiple-choice."

Then ask the questions below, roughly in order, adapting wording naturally
rather than pasting them verbatim. Keep it conversational — a few questions
per message, not all fourteen in a wall of text. Use AskUserQuestion for the
multiple-choice ones where your tooling supports it; plain chat is fine too.

### Questions to ask

1. **Name**, for the letterhead of anything generated later.
2. **Home city** — where they're based right now.
3. **Reachable region.** Multiple choice:
   - Home city and nearby commute towns only
   - Home city, commute towns, and open to relocating elsewhere *within the
     same country*
   - Also open to specific *other* countries (ask which, by name — don't
     assume a whole continent)

   This maps directly to `LOCATION_TIERS` in `profile_local.py`. **Do not
   invent a broader region than they state.** If they say "just my country,"
   the `relocate` tier stays inside that country and everything else is
   `abroad`. Guessing too generously here has bitten a real user before —
   Austria and Switzerland were once wrongly bundled in as "reachable" for
   someone who only wanted Germany, and that let postings requiring
   relocation abroad slip through as sendable.
4. **Current situation.** Multiple choice, multi-select:
   - Job-seeking full-time
   - Currently studying (part-time or full-time)
   - Currently working (employed, looking to move)
   - Available for working-student / intern / thesis roles too

   Follow up only as needed — e.g. if studying, ask through when they're
   enrolled and whether that should ever count as a blocker (usually not).
5. **What they do** — discipline/stack. Offer a short multiple-choice list to
   start (Software Engineering / Data & ML / Product / Design / Other —
   ask them to name it) and then get specifics in their own words: languages,
   frameworks, what they've actually shipped. If they'd rather paste a CV or
   LinkedIn "About" section and let you draft `PROFILE` from that, that's a
   good shortcut — offer it.
6. **Experience level & hard blockers.** Multiple choice + free text:
   - Years of professional experience (rough number)
   - Seniority levels to exclude (senior/staff/lead/manager/director — ask
     which apply)
   - Language requirements they can't meet (e.g. "native German" if they
     don't speak German)
   - Any clearance/citizenship constraint worth noting
7. **CV file(s).** Ask where their résumé PDF lives, or have them drop it
   into `cvwork/cv/out/` (create that folder if `CVWORK_DIR` — see
   `core.py` — doesn't exist yet; default is a sibling `cvwork/` directory
   next to this repo, override with the `CVWORK_DIR` env var). Most people
   start with **one general CV** — that's `CV_TRACKS = {"general": (...)}` in
   `profile_example.py`. Only build multiple tailored tracks (frontend /
   backend / working-student, etc., see the real `cv_track()` logic in
   `core.py`) if they say they already have more than one CV variant.
8. **Scoring backend.** Multiple choice:
   - "I already use the `claude` CLI and I'm logged in" → no API key needed,
     scoring runs on their existing login (`core.py`'s `scorer()` picks this
     up automatically when it finds `claude` on PATH)
   - "I'll run this in Docker" → they'll need `ANTHROPIC_API_KEY` set before
     `docker compose up`
9. **Company watchlist.** Multiple choice:
   - Start from the generic example list (`companies.example.json` — real
     public boards, not tailored to them) — fine for kicking the tyres
   - "Suggest some companies in my field/region" — if they choose this,
     research a starter list (10–20 companies) that fit what they told you
     in Q3/Q5 and write it as `companies.json` in the same shape as
     `companies.example.json` (see that file and `README.md`'s "Adding a
     company" section for the schema and supported ATSes)
   - They'll add their own later — just copy the example file so the app
     has something to run against

### Then generate the files

- Copy `profile_example.py` → `profile_local.py`, filling in `PROFILE`,
  `LOCATION_TIERS`, `LOCATION_LABEL`, `CLI_FILTER_PROMPT`, `CV_TRACKS` and
  `DEFAULT_CV` from their answers. Write `PROFILE` in the same third-person,
  fact-dense style as the example — this is what gets sent to the model on
  every scoring call, so keep it tight.
- Copy `companies.example.json` → `companies.json` (or the researched list
  from Q9).
- Confirm `CVWORK_DIR` resolves to somewhere their CV actually is.

### Then run it

Ask which they'd prefer if it wasn't already settled in Q8 — Docker
(`docker compose up --build`, needs `ANTHROPIC_API_KEY`) or bare
(`pip install -r requirements.txt && uvicorn app:app --port 8420`, uses
their `claude` CLI login). Start it, then tell them to open
**http://localhost:8420**.

Point them at `README.md` for everything past this point — how scoring
works, the queue/send-list/companies pages, the CLI, adding companies later.
This file's job ends once `profile_local.py` and `companies.json` exist and
the app is running.

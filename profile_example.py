"""
Example profile — jobradar's fallback when profile_local.py doesn't exist.

Copy this file to profile_local.py and rewrite every section for yourself:
who you are, what makes a posting a fit, and which cities count as home,
a commute, or a move. profile_local.py is gitignored, so nothing you write
there ever gets committed.
"""

PROFILE = """Alex Doe — software engineer, based Springfield, USA.

- B.Sc. Computer Science, State University. Graduated and available now.
- 2 years building web apps: React, TypeScript, Node, Postgres.
- Comfortable with Python for scripting and small services.
- English fluent.

Describe here what should never lower a verdict (e.g. part-time roles,
a specific employment type, an enrolment requirement) and what should.

HARD BLOCKERS — any one of these makes it a SKIP:
- the posting requires a language you don't speak at the level it asks for
- it asks for more years of experience than you have
- it is a seniority level you're not targeting (senior / staff / lead / …)
- it is not the kind of role you do at all
- it requires a clearance or citizenship you don't hold, when the posting
  says so explicitly — never inferred from the industry
"""

# Ordered: first match wins. Replace the city names with your own home base,
# a reasonable commute, and the rest of your country — remote and "abroad"
# are handled elsewhere and don't need editing.
LOCATION_TIERS = [
    ("remote", 0, r"\bremote\b|\banywhere\b|work from home|home ?office|home[- ]based"),
    ("local", 1, r"springfield"),                       # <- your home city
    ("commutable", 2, r"shelbyville|capital city"),      # <- short-drive cities
    ("relocate", 3, r"usa|united states"),                # <- same-country, distant
]
LOCATION_LABEL = {
    "remote": "Remote",
    "local": "Home base",
    "commutable": "Reachable",
    "relocate": "Would need to move",
    "unknown": "Location unclear",
    "abroad": "Outside target region",
}

# The CLI (jobradar.py --filter) uses its own lightweight version of the same
# profile for its cheap pre-LLM screen. Keep this in sync with PROFILE above.
CLI_FILTER_PROMPT = """You are screening job postings for Alex Doe.

Profile: same facts as PROFILE above, condensed to a couple of sentences —
this runs against every job title in the sweep, so keep it short.

For EACH job below output one line:
<index>|<VERDICT>|<one short reason>
VERDICT is APPLY (strong fit, worth a tailored application), MAYBE (plausible
stretch), or SKIP.
SKIP anything that is: non-engineering, a seniority level you're not
targeting, requires a language bar you don't meet, requires more years of
experience than you have, or is a research role needing publications.
Output nothing but those lines.

JOBS:
{jobs}
"""

# Which tailored CV to point at for a given posting, and where the file
# lives under CVWORK_DIR. "general" is the required fallback key.
CV_TRACKS = {
    "general": ("General", "cv/out/Resume.pdf",
                "The one-page resume, for postings that name no particular stack."),
}
DEFAULT_CV = ("General", "cv/out/Resume.pdf", "")

"""
Internal trial-operations (CTMS) layer — a local SQLite database of *our own*
study portfolio and per-site enrollment, plus a natural-language → SQL tool that
the ReAct agent calls alongside the public ClinicalTrials.gov / PubMed MCP tools.

Why this is the purposeful complement: ClinicalTrials.gov is the *public* registry
(what the world is doing) and PubMed is the *published* evidence; neither knows our
internal accrual. The `studies.nct_id` column links our portfolio straight to
ClinicalTrials.gov, so the agent can answer "how is our enrollment tracking vs. the
competitive landscape?" by combining this tool with the public ones.

Design (see plan): a SINGLE self-contained tool — one LLM pass turns the question
into a SQLite SELECT against an embedded schema + few-shot examples, a guard enforces
read-only, it executes against a read-only connection, and (on error) self-corrects
once. No introspection round-trips, no new dependencies (sqlite3 is stdlib; SQL
generation reuses the existing ChatOpenAI model).

The data is fully SYNTHETIC (no PHI). NCT IDs are real ClinicalTrials.gov studies so
the cross-tool demo returns real public data when the agent looks them up.
"""

import logging
import os
import re
import sqlite3

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool

from config import TRIAL_OPS_DB_PATH, NLSQL_MAX_TOKENS

load_dotenv()  # ensure OPENAI_API_KEY is available even if imported standalone

_log = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA_DDL = """
CREATE TABLE studies (
    internal_id            TEXT PRIMARY KEY,   -- our protocol ID, e.g. 'PROT-2023-007'
    nct_id                 TEXT,               -- ClinicalTrials.gov ID (NULL if unregistered)
    title                  TEXT,
    therapeutic_area       TEXT,
    phase                  TEXT,               -- Phase 1 | Phase 2 | Phase 3
    status                 TEXT,               -- Recruiting | Active, not recruiting | Completed
    principal_investigator TEXT,
    target_enrollment      INTEGER,            -- institution-wide target across sites
    start_date             TEXT,               -- YYYY-MM-DD (protocol start)
    enrollment_open_date   TEXT,               -- YYYY-MM-DD (first site activation / FPI window)
    planned_end_date       TEXT,               -- YYYY-MM-DD (target completion)
    sponsor_type           TEXT                -- Industry | Investigator-initiated | Federal
);

CREATE TABLE sites (
    site_id   TEXT PRIMARY KEY,
    site_name TEXT,
    city      TEXT,
    state     TEXT,                            -- 2-letter US code; '' for international
    country   TEXT,                            -- USA | UK
    region    TEXT,                            -- Midwest | Southeast | ... | International
    site_type TEXT                             -- Academic Medical Center | Health System | Community
);

-- Monthly CUMULATIVE enrollment time series: one row per study x site x month.
CREATE TABLE enrollment (
    id          INTEGER PRIMARY KEY,
    internal_id TEXT,    -- FK -> studies.internal_id
    site_id     TEXT,    -- FK -> sites.site_id
    as_of_date  TEXT,    -- 'YYYY-MM-01' monthly snapshot
    screened    INTEGER, -- cumulative screened (>= enrolled)
    enrolled    INTEGER, -- cumulative enrolled
    randomized  INTEGER, -- cumulative randomized (<= enrolled)
    completed   INTEGER, -- cumulative completed (<= randomized)
    withdrawn   INTEGER, -- cumulative withdrawn
    target      INTEGER, -- this site's target for the study (constant over time)
    status      TEXT     -- Ahead | On track | Behind | Complete
);

-- Convenience VIEW: the LATEST snapshot per study x site = the CURRENT state
-- (one row per study x site, the shape current-enrollment questions expect).
CREATE VIEW enrollment_current AS
    SELECT * FROM enrollment
    WHERE as_of_date = (SELECT MAX(as_of_date) FROM enrollment);
"""

# A compact, plain-text version of the schema for the NL→SQL prompt.
SCHEMA_DESCRIPTION = """\
Tables (SQLite):
- studies(internal_id, nct_id, title, therapeutic_area, phase, status,
          principal_investigator, target_enrollment, start_date,
          enrollment_open_date, planned_end_date, sponsor_type)
    therapeutic_area ∈ {Cardiometabolic, Cardiology, Oncology, Neurology, Hematology,
                        Infectious Disease, Respiratory, Nephrology, Rheumatology}
    phase ∈ {Phase 1, Phase 2, Phase 3}
    status is EXACTLY one of these three string literals: 'Recruiting',
      'Active, not recruiting', 'Completed' (the middle is the full literal — there is
      NO value just 'Active'). "active"/"ongoing" = status != 'Completed';
      "recruiting" = status = 'Recruiting'.
    sponsor_type ∈ {Industry, Investigator-initiated, Federal}
    target_enrollment is the institution-wide target (sum of this study's site targets).
    nct_id is NULL for unregistered studies — exclude NULLs for NCT/registered questions.
    Dates are 'YYYY-MM-DD'; "started in <year>" → start_date LIKE '<year>-%'.
    planned_end_date = target completion; enrollment_open_date = recruitment start.
- sites(site_id, site_name, city, state, country, region, site_type)
    state is a 2-LETTER US code ('' for international). The 7 sites:
      ('SITE-RST','Rochester Medical Center','Rochester','MN','USA','Midwest','Academic Medical Center')
      ('SITE-JAX','Jacksonville Medical Center','Jacksonville','FL','USA','Southeast','Academic Medical Center')
      ('SITE-PHX','Arizona Medical Center','Scottsdale','AZ','USA','Southwest','Academic Medical Center')
      ('SITE-MHS','La Crosse Health System','La Crosse','WI','USA','Midwest','Health System')
      ('SITE-AUS','Collaborative Site — Austin','Austin','TX','USA','South','Community')
      ('SITE-SEA','Collaborative Site — Seattle','Seattle','WA','USA','West','Academic Medical Center')
      ('SITE-LON','Partner Site — London','London','','UK','International','Academic Medical Center')
    Map a place to the right column: "Arizona"→state='AZ', "Rochester"→city='Rochester',
    "London"/"international"→country='UK' or region='International'. Prefer site_name LIKE when unsure.
- enrollment(id, internal_id, site_id, as_of_date, screened, enrolled, randomized,
             completed, withdrawn, target, status) — MONTHLY CUMULATIVE TIME SERIES
    (one row per study x site x month; as_of_date = 'YYYY-MM-01'; latest = 2026-05-01).
    status ∈ {Ahead, On track, Behind, Complete}. Funnel: screened ≥ enrolled ≥
    randomized ≥ completed.
- enrollment_current — a VIEW = the latest snapshot per study x site (CURRENT state).

*** ROUTING RULE — VERY IMPORTANT ***
- For CURRENT enrollment ("how many enrolled", "behind target", "by site", totals NOW)
  query the **enrollment_current** view (one row per study x site) — NEVER SUM the raw
  `enrollment` table for current values (it would sum every monthly snapshot = wrong).
- Only use the raw **enrollment** table for OVER-TIME / TREND / "month by month" /
  "velocity" / "since <date>" questions (it is the monthly time series).
Join studies↔enrollment_current on internal_id; enrollment_current↔sites on site_id."""

# ── Synthetic seed data (deterministic — no RNG) ───────────────────────────────
# Flagship NCT IDs are real ClinicalTrials.gov studies; everything else is our own.

# (internal_id, nct_id, title, therapeutic_area, phase, status, PI, target,
#  start_date, enrollment_open_date, planned_end_date, sponsor_type)
STUDIES = [
    ("PROT-2021-014", "NCT03574597", "Semaglutide for Cardiovascular Outcomes in Overweight/Obesity",
     "Cardiometabolic", "Phase 3", "Active, not recruiting", "Dr. Helen Park", 180, "2021-06-15", "2021-08-01", "2025-06-30", "Industry"),
    ("PROT-2021-033", "NCT01994889", "Tafamidis in Transthyretin Amyloid Cardiomyopathy (ATTR-ACT)",
     "Cardiology", "Phase 3", "Completed", "Dr. Mark Reyes", 55, "2021-04-22", "2021-06-01", "2024-03-31", "Industry"),
    ("PROT-2022-009", "NCT03036124", "Dapagliflozin in Heart Failure with Reduced EF (DAPA-HF)",
     "Cardiology", "Phase 3", "Completed", "Dr. Mark Reyes", 110, "2022-02-18", "2022-04-01", "2024-12-31", "Industry"),
    ("PROT-2022-031", "NCT03548935", "Semaglutide 2.4 mg for Weight Management",
     "Cardiometabolic", "Phase 3", "Completed", "Dr. Helen Park", 120, "2022-01-10", "2022-03-01", "2024-06-30", "Industry"),
    ("PROT-2023-007", "NCT02578680", "Pembrolizumab plus Chemotherapy in Metastatic NSCLC",
     "Oncology", "Phase 3", "Active, not recruiting", "Dr. Raj Patel", 90, "2023-03-01", "2023-05-01", "2026-09-30", "Industry"),
    ("PROT-2023-022", "NCT04437511", "Donanemab in Early Symptomatic Alzheimer's Disease",
     "Neurology", "Phase 3", "Recruiting", "Dr. Susan Cole", 75, "2023-09-12", "2023-11-01", "2027-03-31", "Industry"),
    ("PROT-2023-041", "NCT03887455", "Lecanemab in Early Alzheimer's Disease (Clarity AD)",
     "Neurology", "Phase 3", "Active, not recruiting", "Dr. Susan Cole", 85, "2023-11-03", "2024-01-01", "2026-12-31", "Industry"),
    ("PROT-2023-055", "NCT03954834", "Tirzepatide for Glycemic Control in Type 2 Diabetes (SURPASS)",
     "Cardiometabolic", "Phase 3", "Active, not recruiting", "Dr. Helen Park", 95, "2023-06-27", "2023-08-01", "2026-06-30", "Industry"),
    ("PROT-2024-003", "NCT01866319", "Pembrolizumab versus Ipilimumab in Advanced Melanoma",
     "Oncology", "Phase 3", "Completed", "Dr. Raj Patel", 60, "2024-02-05", "2024-04-01", "2025-11-30", "Industry"),
    ("PROT-2024-012", "NCT03434379", "Atezolizumab plus Bevacizumab in Hepatocellular Carcinoma (IMbrave150)",
     "Oncology", "Phase 3", "Active, not recruiting", "Dr. Raj Patel", 70, "2024-03-19", "2024-05-01", "2027-06-30", "Industry"),
    ("PROT-2024-018", "NCT04368728", "mRNA Vaccine Immunogenicity Substudy",
     "Infectious Disease", "Phase 2", "Recruiting", "Dr. Liam Foster", 50, "2024-07-20", "2024-09-01", "2026-12-31", "Federal"),
    ("PROT-2024-027", "NCT02348216", "Axicabtagene Ciloleucel in Refractory Large B-Cell Lymphoma",
     "Hematology", "Phase 2", "Recruiting", "Dr. Anita Rao", 40, "2024-05-14", "2024-07-01", "2026-06-30", "Industry"),
    ("PROT-2024-039", "NCT04381936", "Dexamethasone in Hospitalized COVID-19 (RECOVERY substudy)",
     "Infectious Disease", "Phase 3", "Completed", "Dr. Liam Foster", 65, "2024-01-30", "2024-03-01", "2025-06-30", "Investigator-initiated"),
    ("PROT-2025-002", None, "Investigator-Initiated CAR-NK Cell Therapy in Refractory Solid Tumors",
     "Hematology", "Phase 1", "Recruiting", "Dr. Anita Rao", 24, "2025-01-15", "2025-03-01", "2027-01-31", "Investigator-initiated"),
    # ── added in the 2026-06-05 enrichment (more areas / phases / sponsors / sites) ──
    ("PROT-2022-047", "NCT02856828", "Nivolumab plus Ipilimumab in Advanced Renal Cell Carcinoma (CheckMate)",
     "Oncology", "Phase 3", "Completed", "Dr. Raj Patel", 80, "2022-05-10", "2022-07-01", "2025-03-31", "Industry"),
    ("PROT-2023-064", "NCT03347279", "Tezepelumab in Severe Uncontrolled Asthma (NAVIGATOR)",
     "Respiratory", "Phase 3", "Active, not recruiting", "Dr. Priya Nair", 70, "2023-08-01", "2023-10-01", "2026-10-31", "Industry"),
    ("PROT-2024-051", "NCT03594110", "Empagliflozin in Chronic Kidney Disease (EMPA-KIDNEY)",
     "Nephrology", "Phase 3", "Recruiting", "Dr. Mark Reyes", 100, "2024-09-05", "2024-11-01", "2027-09-30", "Industry"),
    ("PROT-2022-072", "NCT02675426", "Upadacitinib in Moderate-to-Severe Rheumatoid Arthritis (SELECT)",
     "Rheumatology", "Phase 3", "Completed", "Dr. Priya Nair", 65, "2022-09-18", "2022-11-01", "2025-02-28", "Industry"),
    ("PROT-2024-066", "NCT05256134", "Lecanemab Subcutaneous Maintenance Dosing",
     "Neurology", "Phase 2", "Recruiting", "Dr. Susan Cole", 45, "2024-10-22", "2024-12-01", "2026-12-31", "Industry"),
    ("PROT-2025-011", None, "Investigator-Initiated CRISPR Gene Editing in Sickle Cell Disease",
     "Hematology", "Phase 1", "Recruiting", "Dr. Anita Rao", 18, "2025-02-20", "2025-04-01", "2027-02-28", "Investigator-initiated"),
    ("PROT-2023-088", "NCT04944992", "Semaglutide in Non-alcoholic Steatohepatitis (NASH/MASH)",
     "Cardiometabolic", "Phase 2", "Active, not recruiting", "Dr. Helen Park", 55, "2023-12-01", "2024-02-01", "2026-05-31", "Industry"),
    ("PROT-2024-079", "NCT04576988", "Sotatercept in Pulmonary Arterial Hypertension (STELLAR)",
     "Cardiology", "Phase 3", "Recruiting", "Dr. Mark Reyes", 60, "2024-11-14", "2025-01-01", "2028-01-31", "Industry"),
]

# (site_id, site_name, city, state, country, region, site_type)
SITES = [
    ("SITE-RST", "Rochester Medical Center", "Rochester", "MN", "USA", "Midwest", "Academic Medical Center"),
    ("SITE-JAX", "Jacksonville Medical Center", "Jacksonville", "FL", "USA", "Southeast", "Academic Medical Center"),
    ("SITE-PHX", "Arizona Medical Center", "Scottsdale", "AZ", "USA", "Southwest", "Academic Medical Center"),
    ("SITE-MHS", "La Crosse Health System", "La Crosse", "WI", "USA", "Midwest", "Health System"),
    ("SITE-AUS", "Collaborative Site — Austin", "Austin", "TX", "USA", "South", "Community"),
    ("SITE-SEA", "Collaborative Site — Seattle", "Seattle", "WA", "USA", "West", "Academic Medical Center"),
    ("SITE-LON", "Partner Site — London", "London", "", "UK", "International", "Academic Medical Center"),
]

# Current (latest-snapshot) state per study x site — drives the generated time series.
# (internal_id, site_id, site_target, current_screened, current_enrolled, current_status)
# The original 14 studies keep their exact totals (so prior facts/tests hold); new
# studies add the new sites (AUS/SEA/LON) for geographic richness.
_CURRENT = [
    ("PROT-2021-014", "SITE-RST", 90, 142, 96, "Ahead"),
    ("PROT-2021-014", "SITE-JAX", 50, 78,  52, "On track"),
    ("PROT-2021-014", "SITE-PHX", 40, 49,  31, "Behind"),
    ("PROT-2022-031", "SITE-RST", 70, 95,  70, "Complete"),
    ("PROT-2022-031", "SITE-JAX", 50, 63,  50, "Complete"),
    ("PROT-2023-007", "SITE-RST", 45, 61,  38, "Behind"),
    ("PROT-2023-007", "SITE-PHX", 25, 40,  27, "Ahead"),
    ("PROT-2023-007", "SITE-MHS", 20, 22,  14, "Behind"),
    ("PROT-2023-022", "SITE-RST", 35, 55,  24, "Behind"),
    ("PROT-2023-022", "SITE-JAX", 20, 38,  19, "On track"),
    ("PROT-2023-022", "SITE-PHX", 20, 31,  18, "On track"),
    ("PROT-2024-003", "SITE-PHX", 35, 48,  35, "Complete"),
    ("PROT-2024-003", "SITE-RST", 25, 33,  25, "Complete"),
    ("PROT-2024-018", "SITE-RST", 25, 27,  12, "Behind"),
    ("PROT-2024-018", "SITE-MHS", 25, 19,  11, "Behind"),
    ("PROT-2021-033", "SITE-RST", 35, 60,  35, "Complete"),
    ("PROT-2021-033", "SITE-JAX", 20, 35,  20, "Complete"),
    ("PROT-2022-009", "SITE-RST", 60, 92,  60, "Complete"),
    ("PROT-2022-009", "SITE-JAX", 50, 78,  50, "Complete"),
    ("PROT-2023-041", "SITE-RST", 40, 70,  40, "On track"),
    ("PROT-2023-041", "SITE-JAX", 25, 45,  26, "Ahead"),
    ("PROT-2023-041", "SITE-PHX", 20, 32,  16, "Behind"),
    ("PROT-2023-055", "SITE-RST", 50, 80,  50, "On track"),
    ("PROT-2023-055", "SITE-JAX", 30, 55,  33, "Ahead"),
    ("PROT-2023-055", "SITE-MHS", 15, 25,  13, "Behind"),
    ("PROT-2024-012", "SITE-RST", 35, 55,  35, "On track"),
    ("PROT-2024-012", "SITE-JAX", 25, 40,  24, "Behind"),
    ("PROT-2024-012", "SITE-PHX", 10, 18,   9, "Behind"),
    ("PROT-2024-027", "SITE-RST", 25, 38,  18, "Behind"),
    ("PROT-2024-027", "SITE-PHX", 15, 22,  11, "Behind"),
    ("PROT-2024-039", "SITE-RST", 40, 70,  40, "Complete"),
    ("PROT-2024-039", "SITE-MHS", 25, 30,  25, "Complete"),
    ("PROT-2025-002", "SITE-RST", 14, 20,   8, "Behind"),
    ("PROT-2025-002", "SITE-PHX", 10, 12,   5, "Behind"),
    # new studies (introduce AUS / SEA / LON)
    ("PROT-2022-047", "SITE-RST", 45, 70,  45, "Complete"),
    ("PROT-2022-047", "SITE-SEA", 35, 55,  35, "Complete"),
    ("PROT-2023-064", "SITE-RST", 30, 48,  30, "On track"),
    ("PROT-2023-064", "SITE-JAX", 25, 35,  22, "On track"),
    ("PROT-2023-064", "SITE-AUS", 15, 20,  12, "Behind"),
    ("PROT-2024-051", "SITE-RST", 40, 55,  28, "Behind"),
    ("PROT-2024-051", "SITE-SEA", 35, 35,  18, "Behind"),
    ("PROT-2024-051", "SITE-MHS", 25, 22,  12, "Behind"),
    ("PROT-2022-072", "SITE-RST", 40, 60,  40, "Complete"),
    ("PROT-2022-072", "SITE-JAX", 25, 42,  25, "Complete"),
    ("PROT-2024-066", "SITE-RST", 25, 30,  16, "On track"),
    ("PROT-2024-066", "SITE-PHX", 20, 20,  11, "Behind"),
    ("PROT-2025-011", "SITE-RST", 10, 12,   6, "Behind"),
    ("PROT-2025-011", "SITE-SEA", 8,  7,    3, "Behind"),
    ("PROT-2023-088", "SITE-RST", 30, 48,  30, "Ahead"),
    ("PROT-2023-088", "SITE-JAX", 25, 35,  22, "On track"),
    ("PROT-2024-079", "SITE-RST", 30, 32,  18, "Behind"),
    ("PROT-2024-079", "SITE-AUS", 15, 18,  10, "Behind"),
    ("PROT-2024-079", "SITE-LON", 15, 15,   8, "On track"),
]

_AS_OF = "2026-05-01"        # latest monthly snapshot ("current")
_AS_OF_YM = (2026, 5)


def _ease(x: float) -> float:
    """Smoothstep 0..1 — gives accrual curves a gentle S shape."""
    x = 0.0 if x < 0 else 1.0 if x > 1 else x
    return x * x * (3 - 2 * x)


def _build_enrollment_rows():
    """Expand each current (study x site) state into a monthly CUMULATIVE time series
    from the study's enrollment_open_date to the as-of month. Deterministic. The final
    month equals the current values, so enrollment_current reproduces the intended state."""
    studies_by_id = {s[0]: s for s in STUDIES}
    rows, rid = [], 0
    for internal_id, site_id, target, cur_scr, cur_enr, cur_status in _CURRENT:
        st = studies_by_id[internal_id]
        study_status, open_date = st[5], st[9]
        y, m = int(open_date[:4]), int(open_date[5:7])
        months = []
        while (y, m) <= _AS_OF_YM:
            months.append(f"{y:04d}-{m:02d}-01")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        n = len(months)
        # Completed studies reach final ~70% through their window then plateau.
        ramp_to = max(1, round(n * (0.7 if study_status == "Completed" else 1.0)))
        for i, mdate in enumerate(months):
            f = _ease(min(1.0, (i + 1) / ramp_to))
            enrolled = round(cur_enr * f)
            screened = max(enrolled, round(cur_scr * _ease(min(1.0, (i + 1) / ramp_to * 1.08))))
            randomized = round(enrolled * 0.96)
            late = _ease(max(0.0, (i + 1 - ramp_to * 0.45) / max(1, n - ramp_to * 0.45)))
            completed = round(enrolled * (0.85 if study_status == "Completed" else 0.2) * late)
            withdrawn = round(enrolled * 0.05 * f)
            rid += 1
            rows.append((rid, internal_id, site_id, mdate, screened, enrolled,
                         randomized, completed, withdrawn, target, cur_status))
    return rows


def ensure_db(db_path: str = TRIAL_OPS_DB_PATH) -> str:
    """Create and seed the SQLite DB if it does not already exist (idempotent,
    deterministic). Safe to call on every agent build / process boot."""
    if os.path.exists(db_path):
        return db_path
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA_DDL)
        con.executemany("INSERT INTO studies VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", STUDIES)
        con.executemany("INSERT INTO sites VALUES (?,?,?,?,?,?,?)", SITES)
        con.executemany("INSERT INTO enrollment VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        _build_enrollment_rows())
        con.commit()
    finally:
        con.close()
    _log.info("Seeded trial-operations DB at %s", db_path)
    return db_path


# ── NL → SQL generation ────────────────────────────────────────────────────────

FEW_SHOT = """\
-- CURRENT enrollment → use the enrollment_current VIEW (one row per study x site).
Q: How is enrollment tracking across our trials?
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment
     FROM studies s JOIN enrollment_current e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY enrolled DESC;

Q: Which of our sites is furthest behind its enrollment target?   -- "furthest behind" = fill rate, NOT absolute gap
SQL: SELECT si.site_name, SUM(e.enrolled) AS enrolled, SUM(e.target) AS target,
     ROUND(SUM(e.enrolled) * 100.0 / SUM(e.target), 1) AS pct_of_target
     FROM enrollment_current e JOIN sites si ON si.site_id = e.site_id
     GROUP BY e.site_id ORDER BY pct_of_target ASC;

Q: Show our active Phase 3 oncology studies.
SQL: SELECT internal_id, nct_id, title, status, principal_investigator
     FROM studies WHERE phase = 'Phase 3' AND therapeutic_area = 'Oncology'
     AND status != 'Completed';

Q: What is our enrollment on the donanemab trial?   -- a SPECIFIC named trial: return the real IDs + the study TOTAL (never a per-site row)
SQL: SELECT s.internal_id, s.nct_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment
     FROM studies s JOIN enrollment_current e ON e.internal_id = s.internal_id
     WHERE s.title LIKE '%Donanemab%' GROUP BY s.internal_id;

Q: Which of our studies has the worst enrollment?   -- "worst/best/lagging enrollment" = fill rate, NOT absolute count
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment,
     ROUND(SUM(e.enrolled) * 100.0 / s.target_enrollment, 1) AS pct_of_target
     FROM studies s JOIN enrollment_current e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY pct_of_target ASC;

Q: Which PIs have trials that are behind target?   -- count TRIALS, not site rows
SQL: SELECT s.principal_investigator, COUNT(DISTINCT s.internal_id) AS trials_behind
     FROM studies s JOIN enrollment_current e ON e.internal_id = s.internal_id
     WHERE e.status = 'Behind' GROUP BY s.principal_investigator
     ORDER BY trials_behind DESC;

Q: How many active studies do we have?   -- "active/ongoing" = NOT Completed; never status='Active'
SQL: SELECT COUNT(*) AS active_studies FROM studies WHERE status != 'Completed';

Q: How are we doing?   -- vague status question → portfolio enrollment-vs-target summary
SQL: SELECT s.internal_id, s.title, SUM(e.enrolled) AS enrolled, s.target_enrollment,
     ROUND(SUM(e.enrolled) * 100.0 / s.target_enrollment, 1) AS pct_of_target
     FROM studies s JOIN enrollment_current e ON e.internal_id = s.internal_id
     GROUP BY s.internal_id ORDER BY pct_of_target ASC;

Q: What is Dr. Raj Patel researching / working on?   -- a PI's research/work = the studies they lead
SQL: SELECT internal_id, nct_id, title, therapeutic_area, phase, status
     FROM studies WHERE principal_investigator LIKE '%Raj Patel%';

Q: Who is leading / running the donanemab trial?   -- study → its PI (inverse lookup); also for phase/status/dates of a named trial
SQL: SELECT title, principal_investigator, phase, status FROM studies
     WHERE title LIKE '%Donanemab%';

Q: What is happening at our Florida site?   -- a place → its site; list that site's trials + current enrollment
SQL: SELECT s.title, e.enrolled, e.target, e.status
     FROM enrollment_current e JOIN studies s ON s.internal_id = e.internal_id
     JOIN sites si ON si.site_id = e.site_id
     WHERE si.state = 'FL' OR si.city = 'Jacksonville' OR si.site_name LIKE '%Florida%';

Q: How many patients have we enrolled at the Rochester site?
SQL: SELECT si.site_name, SUM(e.enrolled) AS enrolled
     FROM enrollment_current e JOIN sites si ON si.site_id = e.site_id
     WHERE si.city = 'Rochester' GROUP BY e.site_id;

Q: Enrollment by site for NCT02578680.
SQL: SELECT si.site_name, e.screened, e.enrolled, e.target, e.status
     FROM enrollment_current e JOIN studies s ON s.internal_id = e.internal_id
     JOIN sites si ON si.site_id = e.site_id WHERE s.nct_id = 'NCT02578680';

-- OVER-TIME / TREND → use the raw enrollment time series (monthly snapshots).
Q: Show the monthly enrollment trend for the donanemab trial.
SQL: SELECT e.as_of_date, SUM(e.enrolled) AS enrolled
     FROM enrollment e JOIN studies s ON s.internal_id = e.internal_id
     WHERE s.title LIKE '%Donanemab%' GROUP BY e.as_of_date ORDER BY e.as_of_date;

Q: Enrollment over time across all our studies (last 12 months).
SQL: SELECT as_of_date, SUM(enrolled) AS enrolled FROM enrollment
     GROUP BY as_of_date ORDER BY as_of_date DESC LIMIT 12;"""

_SQLGEN_SYSTEM = f"""You translate a question about our organization's INTERNAL trial-operations \
database into ONE SQLite SELECT query.

{SCHEMA_DESCRIPTION}

Examples:
{FEW_SHOT}

Rules:
- Return ONLY the SQL — no prose, no markdown fences, no explanation.
- A SINGLE read-only SELECT (or WITH … SELECT). Never INSERT/UPDATE/DELETE/DDL.
- Always cap rows with LIMIT 50 unless the query already aggregates to few rows.
- Use the nct_id column when the question references a ClinicalTrials.gov / NCT number.
- For a SPECIFIC named trial, SELECT internal_id, nct_id, title AND the totals — so the
  real IDs are in the result and never have to be guessed — and return the study TOTAL
  (GROUP BY internal_id), not an individual site row.
- Match a study by a drug/condition phrase using its SIGNIFICANT TERMS SEPARATELY:
  `title LIKE '%term1%' AND title LIKE '%term2%'`, NEVER the literal multi-word phrase.
  Our titles are descriptive ("Semaglutide for Cardiovascular Outcomes in Overweight/
  Obesity"), so "semaglutide cardiovascular trial" → title LIKE '%Semaglutide%' AND title
  LIKE '%Cardiovascular%' (a literal LIKE '%semaglutide cardiovascular%' wrongly returns
  nothing).
- "worst / best / lagging / leading / how is X tracking" about enrollment = rank by FILL
  RATE (SUM(enrolled)*1.0/target), not absolute count (a small-target study naturally has
  fewer patients).
- **CURRENT enrollment (how many enrolled, behind target, by site, totals NOW) → query the
  `enrollment_current` VIEW.** NEVER SUM the raw `enrollment` table for current values — it's
  a monthly time series, so summing it adds up every snapshot = badly wrong. Use the raw
  `enrollment` table ONLY for over-time / trend / month-by-month / velocity questions.
- Counting trials/studies from enrollment_current uses COUNT(DISTINCT internal_id) —
  it has one row PER SITE, so COUNT(*) over-counts.
- A vague status question about our work ("how are we doing?", "where do we stand?",
  "give me a status update") = the portfolio enrollment-vs-target summary (per study,
  with % of target).
- A question about a named INVESTIGATOR — what they research / work on / their focus /
  which studies they lead — IS in scope: filter `studies` by `principal_investigator
  LIKE '%<name>%'`. Do NOT return NO_MATCH for a PI who appears in the data.
- **Do NOT use a number, target, status, or attribute the USER asserts as a filter or value.**
  If the user says "our trial WITH 500 patients", "the trial that has 1000 enrolled", "our
  Boston site" — those are the user's CLAIMS, not facts. Query by the real identifying terms
  only (drug/condition/PI/site name we actually have) and return the REAL numbers. Never write
  `WHERE target = 500` or otherwise bend the query to the user's asserted figure, and never
  return a study paired with the user's invented number. If nothing matches the real criteria,
  return SELECT 'NO_MATCH'; (we don't run it).
- If the question is truly unrelated to our studies/sites/enrollment, OR references a study/
  site/PI/drug we do NOT have (no real row matches), return exactly:
  SELECT 'NO_MATCH';"""

_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH|PRAGMA|"
    r"VACUUM|REINDEX|TRUNCATE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


def _strip_sql(text: str) -> str:
    """Remove markdown fences / stray prose and keep the SQL statement."""
    s = _FENCE.sub("", text).strip()
    # keep through the first statement terminator if the model added trailing prose
    if ";" in s:
        s = s[: s.index(";") + 1]
    return s.strip()


# Dedicated, more capable model for NL→SQL generation. gpt-4o-mini was too weak at
# text-to-SQL (simple phrasings kept missing → false NO_MATCH / wrong column); gpt-4o
# handles them without per-phrase few-shots. One short call per internal query, so the
# cost/latency is negligible. Lazy-init so it's built after .env is loaded.
_sql_model = None


def _get_sql_model():
    global _sql_model
    if _sql_model is None:
        from langchain_openai import ChatOpenAI
        _sql_model = ChatOpenAI(model="gpt-4o", api_key=os.getenv("OPENAI_API_KEY"),
                                temperature=0, max_tokens=NLSQL_MAX_TOKENS)
    return _sql_model


async def _generate_sql(question: str, error: str = None, prior_sql: str = None) -> str:
    user = question
    if error:
        user = (f"Question: {question}\nYour previous SQL was:\n{prior_sql}\n"
                f"It failed with: {error}\nReturn a corrected single SQLite SELECT.")
    resp = await _get_sql_model().ainvoke(
        [SystemMessage(content=_SQLGEN_SYSTEM), HumanMessage(content=user)]
    )
    return _strip_sql(resp.content if isinstance(resp.content, str) else str(resp.content))


def _assert_read_only(sql: str) -> str:
    """Defense-in-depth guard (the read-only connection is the hard guarantee)."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("multiple statements are not allowed")
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", s):
        raise ValueError("only SELECT/WITH queries are allowed")
    if _FORBIDDEN.search(s):
        raise ValueError("write/DDL keywords are not allowed")
    return s


def _run_readonly(sql: str, db_path: str, limit: int = 50):
    # Text answers cap at 50 rows; charts need more — a monthly time series spans
    # years (e.g. 60 months × studies), so a 50-row cap silently truncates the trend.
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only = ON")
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit)
        return cols, rows
    finally:
        con.close()


def _format_rows(cols, rows) -> str:
    if not rows:
        return "No rows matched in the internal trial-operations database."
    header = " | ".join(cols)
    body = "\n".join(
        " | ".join("" if v is None else str(v) for v in row) for row in rows
    )
    return f"{header}\n{body}"


async def _answer(question: str, db_path: str) -> str:
    ensure_db(db_path)
    sql = await _generate_sql(question)
    try:
        clean = _assert_read_only(sql)
        cols, rows = _run_readonly(clean, db_path)
    except Exception as e1:
        _log.info("NL→SQL first attempt failed (%s); self-correcting once", e1)
        sql = await _generate_sql(question, error=str(e1), prior_sql=sql)
        try:
            clean = _assert_read_only(sql)
            cols, rows = _run_readonly(clean, db_path)
        except Exception as e2:
            _log.warning("NL→SQL retry failed: %s", e2)
            return ("Couldn't answer that from our internal trial-operations data. "
                    "Try rephrasing — e.g. ask about enrollment by site, studies behind "
                    "target, or our active trials by phase or therapeutic area.")
    if rows == [("NO_MATCH",)]:
        return ("That question isn't answerable from our internal trial-operations "
                "database (which holds our studies, sites, and per-site enrollment).")
    # Log the generated SQL for debugging / traceability — but do NOT surface it to
    # the model/UI (the end user has no need to see the raw query; it only clutters
    # the response and the model tends to echo it). LangSmith also captures it.
    _log.info("query_trial_operations SQL: %s", clean)
    table = _format_rows(cols, rows)
    # Tell the model EXACTLY which columns these rows contain, and forbid showing
    # enrolled/target when those columns are absent — the #1 fabrication path is the
    # model inventing "enrolled N/500" from the USER's question when the rows have no
    # such columns (red-team: "our trial with 500 patients" → fake "0/500").
    has_enrolled = any(c.lower() in ("enrolled", "enrolled_count") for c in cols)
    has_target = any("target" in c.lower() for c in cols)
    col_note = (
        f"These rows contain ONLY these columns: {', '.join(cols)}. "
        + ("They DO include an enrolled and a target column — show enrolled/target using the "
           "EXACT values in each row. " if (has_enrolled and has_target) else
           "They do NOT include both an enrolled and a target column, so you MUST NOT show any "
           "'enrolled N/M' enrollment figure for these — describe only the columns present "
           "(e.g. title, phase, status, PI). Inventing an enrollment count or target here "
           "(especially a number from the user's question) is fabrication. ")
    )
    # When the rows carry a % of target column they are a FILL-RATE RANKING in the order
    # the query produced (its ORDER BY encodes the worst↔best intent). gpt-4o-mini tends to
    # re-rank a "worst/best" answer by the raw enrolled count (it called a 9/18 = 50% study
    # "worst" over a 23/50 = 46% one). Structure beats prose: NAME the top-ranked row right in
    # the tool output so the model doesn't pick among 22 rows by the most salient raw number.
    has_pct = any(("pct" in c.lower() or "percent" in c.lower() or "fill" in c.lower())
                  for c in cols)
    rank_note = ""
    if has_pct and len(rows) > 1 and re.search(r"(?i)\border\s+by\b", clean):
        top = " · ".join(f"{c}={'' if v is None else v}" for c, v in zip(cols, rows[0]))
        rank_note = (
            "These rows are ALREADY RANKED by % of target (fill rate) in the order the query "
            f"produced. The TOP-ranked row is: [{top}]. For a single 'worst / lowest / furthest "
            "behind' (or 'best / highest / furthest ahead') study or site, THAT top row IS the "
            "answer — report it and lead with it. Do NOT re-rank by the raw enrolled count: a "
            "study with fewer patients enrolled is NOT automatically the worst (fill rate, not "
            "absolute count, defines worst/best). "
        )
    return (
        "Internal trial operations (CTMS) — OUR private operational data. "
        "Use ONLY the rows below, exactly as given — copy values verbatim; never substitute a "
        "number from the user's question. If the result is a single count/number, state just "
        "that number in one sentence — do NOT render an enrollment list. NEVER invent a study, "
        "a name (e.g. 'Study 1'), an enrollment number, sponsor, status, phase, or eligibility "
        "that is not in the rows below — that is fabrication. If a per-study list IS present, "
        "render it under '## Our Enrollment' (not as a ClinicalTrials.gov card).\n"
        f"{col_note}{rank_note}\n{table}"
    )


_TOOL_DESCRIPTION = (
    "Query our organization's INTERNAL trial-operations database (our OWN study portfolio, "
    "sites, and per-site enrollment vs. target) with a plain-English question. Use this "
    "for anything about OUR trials, OUR enrollment/accrual, OUR sites, recruitment status, "
    "or how our studies are tracking against target. This is private operational data, "
    "distinct from the public ClinicalTrials.gov registry. Input: a natural-language question."
)


def make_trial_ops_tool(db_path: str = TRIAL_OPS_DB_PATH) -> StructuredTool:
    """Build the `query_trial_operations` tool (NL→SQL uses its own gpt-4o model)."""

    async def query_trial_operations(question: str) -> str:
        return await _answer(question, db_path)

    return StructuredTool.from_function(
        coroutine=query_trial_operations,
        name="query_trial_operations",
        description=_TOOL_DESCRIPTION,
    )


# ── Visualization: NL → chart spec → Plotly figure ─────────────────────────────
# The agent calls plot_trial_operations for "chart/visualize/trend/breakdown" asks.
# It returns (text_summary, figure_json) via content_and_artifact: the model sees the
# summary (to write a 1-line insight), app.py renders the figure JSON as a cl.Plotly.

_BRAND = "#0E3293"
_PALETTE = ["#0E3293", "#3B82F6", "#60A5FA", "#93C5FD", "#10B981",
            "#F59E0B", "#EF4444", "#8B5CF6", "#14B8A6", "#6B7280"]
# Red/Amber/Green for portfolio-health coloring (fill rate vs enrollment target).
# Thresholds: on track ≥90% · at risk 70–90% · behind <70%.
_RAG_GREEN, _RAG_AMBER, _RAG_RED = "#16A34A", "#F59E0B", "#DC2626"

import json as _json

_CHART_SYSTEM = f"""You turn a question about our organization's INTERNAL trial-operations \
database into ONE chart. Reply with ONLY a JSON object (no prose, no fences) with keys:
  "sql": a single read-only SQLite SELECT that returns the chart data,
  "chart_type": one of "bar" | "line" | "donut" | "funnel",
  "x": the result column for the category/time axis (the labels column for donut),
  "y": the value column(s) — a string OR a list of strings (e.g. ["enrolled","target"] \
for grouped bars; the ordered stage columns for a funnel),
  "color": (optional) a result column to split series by (e.g. the study name for a \
multi-line trend), else null,
  "title": a short, human chart title,
  "note": (optional) a short, friendly one-line clause to show the user ONLY when you had \
to deviate from a literal reading of their request — e.g. you kept the chart type they named \
but couldn't apply it the way they implied, or you picked a sensible default for a vague \
request. State WHAT you did and offer how to narrow. Omit it (null) when the chart is a \
straightforward match. Example: "A funnel shows one screening pipeline across all studies, \
so it can't split by area — here's our overall funnel; ask for a specific area to filter it."
  "projection": (optional) set true ONLY when the user asks whether a SINGLE study will \
HIT / REACH / MAKE its enrollment target, is ON PACE / ON TRACK to finish, or wants a \
FORECAST / PROJECTION to the planned end date. Then chart_type MUST be "line" and the SQL \
MUST be that one study's monthly trend that ALSO selects its target_enrollment AS target and \
its planned_end_date (constant columns the forecast needs), e.g.:
  SELECT e.as_of_date, SUM(e.enrolled) AS enrolled, s.target_enrollment AS target, \
s.planned_end_date AS planned_end_date FROM enrollment e JOIN studies s ON \
s.internal_id = e.internal_id WHERE s.title LIKE '%Donanemab%' GROUP BY e.as_of_date \
ORDER BY e.as_of_date. Omit it (null) for an ORDINARY trend ("trend / over time / velocity" \
with NO hit-target/on-pace/forecast wording) — a plain trend must NOT set projection.

{SCHEMA_DESCRIPTION}

Chart guidance:
- "enrollment by study / by site", "enrolled vs target" → "bar", x = the NAME column \
(site_name or study title — friendly labels, never raw IDs), y = ["enrolled","target"].
- "trend / over time / month by month / velocity" → "line", x = as_of_date (use the raw \
`enrollment` time series, GROUP BY as_of_date), y = "enrolled", color = the study title \
if comparing multiple studies.
- "breakdown / distribution / mix / by therapeutic area / by phase / by sponsor / by \
region" → "donut", x = the category column, y = "n" where SQL selects COUNT(*) AS n.
- "funnel / screening / screen-to-enroll" → "funnel", y = ["screened","enrolled",\
"randomized","completed"] summed from enrollment_current. A funnel is ONE aggregate \
row of those ordered stage totals — NO GROUP BY a category. If the user asks for a \
funnel "by area / by site / by phase" (a per-category split a funnel CANNOT show): KEEP \
the funnel chart type they asked for (do NOT silently swap to a donut/bar breakdown). If \
they named a specific subset (one area/site), filter the funnel to it. If no single subset \
is implied, render the OVERALL funnel across all studies and SET "note" to explain that a \
funnel is one pipeline that can't split by that category, and how to narrow. NEVER select \
stage SUMs with a GROUP BY whose category column isn't in SELECT.
- CURRENT values → enrollment_current; trends → raw enrollment. Always select friendly \
label columns (site_name, title) for axes, not internal_id/site_id.
- Match a study by a drug/condition phrase using its SIGNIFICANT TERMS SEPARATELY — \
`title LIKE '%term1%' AND title LIKE '%term2%'` — never the literal multi-word phrase \
(our titles are descriptive, so "semaglutide cardiovascular" → \
`title LIKE '%Semaglutide%' AND title LIKE '%Cardiovascular%'`).
- **If the question asks to chart data NOT in the schema — budget, cost, $, demographics, \
age/sex/race, p-values, outcomes/results, safety/adverse events — reply with EXACTLY \
{{"chart_type": "none"}} and nothing else. NEVER substitute a different column (never chart \
enrollment or targets AS "budget").**
- **Vague METRIC ("chart our performance / success / progress / how we're doing") — the \
metric is undefined, but DON'T pick a random one and DON'T decline. ALWAYS default to the \
single most useful operational view: a "bar" of current enrolled vs target by study \
(enrollment_current, x = title, y = ["enrolled","target"]), and SET "note" to state the \
assumption, e.g. "'Performance' can mean a few things — showing current enrollment vs target \
by study; ask for fill rate, completion, or a trend if you meant something else." Use this \
SAME default for all such vague-metric requests so the behavior is consistent.**
- **Vague SCOPE ("chart everything / all our data / a visual overview") — don't decline. \
Default to a "donut" portfolio breakdown by therapeutic area (the most legible one-glance \
overview) and SET "note", e.g. "Showing our portfolio by therapeutic area as a starting \
overview — ask for enrollment, phase, or a trend to go deeper."**
Reply with ONLY the JSON object."""


async def _generate_chart_spec(question: str) -> dict:
    resp = await _get_sql_model().ainvoke(
        [SystemMessage(content=_CHART_SYSTEM), HumanMessage(content=question)]
    )
    txt = resp.content if isinstance(resp.content, str) else str(resp.content)
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        raise ValueError("chart spec model did not return JSON")
    return _json.loads(m.group(0))


def _rag_bar_colors(cd, y, cols):
    """Per-bar red/amber/green colors based on % of enrollment target, or None when this
    chart carries no fill-rate signal (caller then keeps the brand palette). Render-only,
    deterministic. Fires for single-series bars whose value is EITHER a fill-rate/percent
    column OR a count that has a sibling target column to divide by."""
    vals = cd.get(y, [])
    if not vals:
        return None
    name = y.lower()
    if re.search(r"pct|percent|fill|_rate|of_target", name):
        pct = [v if isinstance(v, (int, float)) else None for v in vals]
    else:
        tcol = next((c for c in cols if "target" in c.lower() and c != y), None)
        if not (tcol and re.search(r"enrol|count|^n_|_n$|num", name)):
            return None
        tvals = cd.get(tcol, [])
        pct = [(e * 100.0 / t) if isinstance(e, (int, float))
               and isinstance(t, (int, float)) and t else None
               for e, t in zip(vals, tvals)]
    if all(p is None for p in pct):
        return None

    def _c(p):
        if p is None:
            return _BRAND
        return _RAG_RED if p < 70 else _RAG_AMBER if p < 90 else _RAG_GREEN
    return [_c(p) for p in pct]


def _project_enrollment(cd, x_col, y_col):
    """Deterministic linear enrollment forecast for a SINGLE-study monthly trend. Returns a
    dict of projection facts, or None when the data can't support a forecast (caller then
    renders an ordinary trend). No arithmetic by the model, no external deps — the chart
    overlay AND the insight sentence both read these exact numbers."""
    import math
    xs = cd.get(x_col) or next((v for c, v in cd.items()
                                if re.search(r"date|month|as_of", c.lower())), [])
    ys = cd.get(y_col) or next((v for c, v in cd.items()
                                if re.search(r"enrol", c.lower())), [])

    def _mi(ym):                       # 'YYYY-MM' -> absolute month index
        return int(ym[:4]) * 12 + (int(ym[5:7]) - 1)

    pts = sorted(((str(x)[:7], y) for x, y in zip(xs, ys)
                  if isinstance(y, (int, float)) and re.match(r"\d{4}-\d{2}", str(x or ""))),
                 key=lambda p: _mi(p[0]))
    if len(pts) < 3:
        return None
    base = _mi(pts[0][0])
    months = [_mi(p[0]) - base for p in pts]
    vals = [p[1] for p in pts]
    last_m, last_v = months[-1], vals[-1]
    w = min(6, len(pts) - 1)           # velocity = slope over the last <=6 months
    dm = months[-1] - months[-1 - w]
    vel = ((vals[-1] - vals[-1 - w]) / dm) if dm else 0.0

    def _const(pat, numeric):
        for c, series in cd.items():
            if re.search(pat, c.lower()):
                for v in series:
                    if numeric and isinstance(v, (int, float)):
                        return v
                    if not numeric and isinstance(v, str) and re.match(r"\d{4}-\d{2}", v):
                        return v[:7]
        return None

    target = _const(r"target", True)
    end_ym = _const(r"planned_end|end_date", False)
    if end_ym:
        end_off = _mi(end_ym) - base
    elif target and vel > 0:
        end_off = last_m + math.ceil((target - last_v) / vel)
    else:
        end_off = last_m + 12
    end_off = min(end_off, last_m + 36)          # cap runaway extrapolation
    if end_off <= last_m:
        return None                              # planned end already passed

    def _ym1(off):                               # month offset -> 'YYYY-MM-01'
        idx = base + off
        return f"{idx // 12:04d}-{idx % 12 + 1:02d}-01"

    proj_x = [_ym1(m) for m in range(last_m + 1, end_off + 1)]
    proj_y = [last_v + vel * (m - last_m) for m in range(last_m + 1, end_off + 1)]
    at_end = proj_y[-1] if proj_y else last_v
    return {"target": target, "end_ym": end_ym, "velocity": vel,
            "proj_x": proj_x, "proj_y": proj_y, "proj_at_end": at_end,
            "meets_target": target is not None and at_end >= target,
            "last_v": last_v, "last_x": _ym1(last_m)}


def _build_figure(spec: dict, cols, rows):
    import plotly.graph_objects as go
    cd = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    ctype = (spec.get("chart_type") or "bar").lower()
    x = spec.get("x"); color = spec.get("color"); title = spec.get("title") or ""
    ys = spec.get("y") or []
    ys = [ys] if isinstance(ys, str) else list(ys)
    ys = [y for y in ys if y in cd] or [c for c in cols if c != x][:1]
    fig = go.Figure()

    if ctype in ("donut", "pie"):
        fig.add_trace(go.Pie(labels=cd.get(x, []), values=cd[ys[0]], hole=0.5,
                             marker=dict(colors=_PALETTE)))
    elif ctype == "funnel":
        stages = ys
        vals = [sum(v for v in cd[s] if isinstance(v, (int, float))) for s in stages]
        fig.add_trace(go.Funnel(y=[s.replace("_", " ").title() for s in stages], x=vals,
                                marker=dict(color=_BRAND)))
    elif ctype == "line":
        if color and color in cd and ys:
            groups = {}
            for xi, ci, yi in zip(cd[x], cd[color], cd[ys[0]]):
                groups.setdefault(ci, ([], []))
                groups[ci][0].append(xi); groups[ci][1].append(yi)
            for i, (g, (gx, gy)) in enumerate(groups.items()):
                fig.add_trace(go.Scatter(x=gx, y=gy, mode="lines+markers", name=str(g),
                                         line=dict(color=_PALETTE[i % len(_PALETTE)])))
        else:
            for i, y in enumerate(ys):
                fig.add_trace(go.Scatter(x=cd.get(x, []), y=cd[y], mode="lines+markers",
                                         name=y.replace("_", " ").title(),
                                         line=dict(color=_PALETTE[i % len(_PALETTE)])))
            # Forecast overlay: dashed velocity extrapolation to the planned end, a target
            # line, and a deterministic on-pace / behind verdict in the title subline. Only
            # when the spec asked for it AND the data supports it (else ordinary trend).
            proj = _project_enrollment(cd, x, ys[0]) if spec.get("projection") else None
            if proj:
                fig.add_trace(go.Scatter(
                    x=[proj["last_x"]] + proj["proj_x"],
                    y=[proj["last_v"]] + proj["proj_y"], mode="lines", name="Projected",
                    line=dict(color="#6B7280", dash="dash", width=2)))
                if proj["target"] is not None:
                    fig.add_hline(
                        y=proj["target"], line_dash="dot", line_width=1.5,
                        line_color=(_RAG_GREEN if proj["meets_target"] else _RAG_RED),
                        annotation_text=f"Target {int(proj['target'])}",
                        annotation_position="top left",
                        annotation_font=dict(size=10, color="#6B7280"))
                if proj["end_ym"]:
                    fig.add_vline(
                        x=proj["end_ym"] + "-01", line_dash="dot",
                        line_color="#6B7280", line_width=1, annotation_text="Planned end",
                        annotation_position="top",
                        annotation_font=dict(size=10, color="#6B7280"))
                if proj["target"] is not None:
                    end = int(round(proj["proj_at_end"]))
                    if proj["meets_target"]:
                        v = f"🟢 On pace — projected ≈{end} vs target {int(proj['target'])}"
                    else:
                        gap = int(round(proj["target"] - proj["proj_at_end"]))
                        v = (f"🔴 Behind pace — projected ≈{end}, ~{gap} short of "
                             f"target {int(proj['target'])}")
                    title = ((title + "<br>") if title else "") + (
                        f"<span style='font-size:11px;color:#6B7280'>{v} "
                        "at current velocity</span>")
    else:  # bar / grouped bar (default)
        labels = ["" if v is None else str(v) for v in cd.get(x, [])]
        # Many categories or long labels (e.g. 22 study titles) make VERTICAL bars
        # unreadable: rotated ticks eat most of the fixed height and truncate, bars
        # become slivers. Flip to HORIZONTAL bars in that case — titles read naturally
        # and the chart grows in height instead of squeezing.
        horizontal = len(labels) > 8 or (labels and max(len(l) for l in labels) > 24)
        # Color single-series bars red/amber/green by fill rate vs target, so "who's
        # behind" reads at a glance instead of from the numbers. None for grouped/
        # non-enrollment bars → keep the brand palette unchanged.
        rag = _rag_bar_colors(cd, ys[0], cols) if len(ys) == 1 else None
        # A fill-rate/percent axis has a meaningful fixed reference: 100% = target met.
        pct_axis = bool(rag) and bool(
            re.search(r"pct|percent|fill|_rate|of_target", ys[0].lower()))
        if horizontal:
            short = [l if len(l) <= 32 else l[:31] + "…" for l in labels]
            for i, y in enumerate(ys):
                fig.add_trace(go.Bar(y=short, x=cd[y], orientation="h",
                                     hovertext=labels,
                                     name=y.replace("_", " ").title(),
                                     marker_color=(rag or _PALETTE[i % len(_PALETTE)])))
            row_px = 22 if len(ys) == 1 else 16 * len(ys)
            fig.update_layout(
                barmode="group",
                height=min(900, max(380, 120 + row_px * len(labels))),
                # keep the SQL's row order top-down (row 1 = top bar) — it often
                # encodes a deliberate ranking (e.g. worst fill rate first)
                yaxis=dict(autorange="reversed"),
            )
        else:
            for i, y in enumerate(ys):
                fig.add_trace(go.Bar(x=labels, y=cd[y],
                                     name=y.replace("_", " ").title(),
                                     marker_color=(rag or _PALETTE[i % len(_PALETTE)])))
            fig.update_layout(barmode="group")
        if rag:
            # one Bar trace with per-point colors — the auto legend swatch would be
            # meaningless, so drop it and explain the colors in a title subline.
            fig.update_layout(showlegend=False)
            title = ((title + "<br>") if title else "") + (
                "<span style='font-size:11px;color:#6B7280'>🟢 on track (≥90% of "
                "target)  ·  🟡 at risk (70–90%)  ·  🔴 behind (&lt;70%)</span>")
        if pct_axis:
            # Dashed reference line at 100% = enrollment target met. The bars read as
            # distance from this line (target on the value axis: x for horizontal bars).
            line = dict(x=100) if horizontal else dict(y=100)
            (fig.add_vline if horizontal else fig.add_hline)(
                line_dash="dash", line_color="#6B7280", line_width=1.5,
                annotation_text="Target (100%)", annotation_position="top",
                annotation_font=dict(size=10, color="#6B7280"), **line)

    fig.update_layout(title=dict(text=title, font=dict(size=16, color=_BRAND)),
                      template="plotly_white", colorway=_PALETTE,
                      font=dict(family="Inter, system-ui, sans-serif", size=12),
                      margin=dict(t=48, l=12, r=12, b=12),
                      legend=dict(orientation="h", y=-0.18))
    # Default height only where the bar branch didn't already set a dynamic one.
    if not fig.layout.height:
        fig.update_layout(height=380)
    return fig


async def _plot_answer(question: str, db_path: str):
    ensure_db(db_path)
    spec = await _generate_chart_spec(question)
    if (spec.get("chart_type") or "").lower() == "none" or not spec.get("sql"):
        return ("That isn't something our internal trial-operations data tracks (it covers "
                "enrollment, sites, study status, phase, therapeutic area — not budget, "
                "demographics, or outcomes). I can chart enrollment by study or site, trends "
                "over time, recruitment funnels, or portfolio breakdowns.", None)
    clean = _assert_read_only(spec.get("sql", ""))
    cols, rows = _run_readonly(clean, db_path, limit=2000)   # charts need the full series
    if not rows:
        return ("No internal data matched that chart request — try our enrollment by "
                "study/site, a trend over time, or a portfolio breakdown.", None)
    fig = _build_figure(spec, cols, rows)
    _log.info("plot_trial_operations chart=%s SQL: %s", spec.get("chart_type"), clean)
    # Show the insight model the MOST RECENT rows (a trend is oldest-first, so the
    # tail holds the current values — the first 30 would be stale early months).
    table = _format_rows(cols, rows[-30:])
    # "assume-and-state": if the spec deviated from a literal reading of the request,
    # the model set a `note` — surface it FIRST so the user knows what we did and why.
    note = (spec.get("note") or "").strip()
    note_line = (
        f'IMPORTANT — the chart deviates from a literal reading of the request, so START your '
        f'reply with this clause, rephrased naturally, BEFORE the insight: "{note}". Then add '
        f'the insight sentence.\n' if note else ""
    )
    # Forecast verdict: feed the model the SAME deterministic numbers the chart drew, so its
    # sentence matches the picture and invents nothing.
    proj_line = ""
    if spec.get("projection"):
        cd = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
        yk = spec.get("y"); yk = yk[0] if isinstance(yk, list) else yk
        p = _project_enrollment(cd, spec.get("x"), yk)
        if p and p["target"] is not None:
            verdict = "ON PACE to meet" if p["meets_target"] else "projected to FALL SHORT of"
            proj_line = (
                f"PROJECTION (deterministic, at current velocity — state THESE numbers, "
                f"invent no others): projected ≈{int(round(p['proj_at_end']))} enrolled by "
                f"the planned end{f' ({p['end_ym']})' if p['end_ym'] else ''} versus a target of "
                f"{int(p['target'])} — {verdict} target.\n")
    summary = (
        note_line + proj_line +
        f"A {spec.get('chart_type', 'bar')} chart titled \"{spec.get('title', '')}\" has "
        "been rendered for the user from OUR internal trial-operations data. Add ONE short "
        "sentence of insight using ONLY the exact category labels and numbers in the data "
        "below. Do NOT rename a category (e.g. never write 'Cardiovascular' for 'Cardiology'); "
        "do NOT add a qualifier the data doesn't state (e.g. don't call a plain count 'active' "
        "unless a column says so); do NOT re-list every row; do NOT invent any value. If two "
        "categories tie, say they tie. For a TREND / over-time chart, describe the LATEST "
        "(most recent / final) value and the overall direction — not an arbitrary mid-point. "
        "Do NOT write any markdown image or link (no ![...](...) and no attachment://) — the "
        f"chart is already displayed above your text.\nChart data (most recent rows last):\n{table}"
    )
    return (summary, fig.to_json())


_PLOT_TOOL_DESCRIPTION = (
    "Render a CHART / visualization of our organization's INTERNAL trial-operations data — "
    "enrollment by study or site, enrollment trends over time, recruitment funnels, or "
    "portfolio breakdowns (by therapeutic area, phase, sponsor, region). Use this whenever "
    "the user asks to chart / visualize / graph / plot / show a trend, breakdown, or "
    "distribution of OUR data. Input: a natural-language question."
)


def make_plot_tool(db_path: str = TRIAL_OPS_DB_PATH) -> StructuredTool:
    """Build the `plot_trial_operations` tool (returns text + a Plotly figure artifact)."""

    async def plot_trial_operations(question: str):
        try:
            return await _plot_answer(question, db_path)
        except Exception as e:
            _log.warning("plot_trial_operations failed (%r)", e)
            return ("Couldn't build that chart — try rephrasing, e.g. 'chart our "
                    "enrollment by site' or 'show the donanemab enrollment trend'.", None)

    return StructuredTool.from_function(
        coroutine=plot_trial_operations,
        name="plot_trial_operations",
        description=_PLOT_TOOL_DESCRIPTION,
        response_format="content_and_artifact",
    )

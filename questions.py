"""
Suggested search starters shown on the welcome screen.

TWO MODES (flip with MODE below):
  - "rotate"  → exploration / confidence mode. A balanced, ROTATING set is drawn from the
                16-question pool: one question from each of the four capability buckets,
                always in the same arc order (our data → a chart → the field → synthesis),
                but the specific question within each bucket rotates per page load. So every
                welcome screen exercises ALL FOUR capabilities, with variety across reloads.
  - "static"  → demo mode. The fixed STATIC four, identical on every reload (predictability
                over variety). We finalize WHICH four alongside the demo script later.

The 16 are all phrased as confident, one-click demo prompts and lean on known-good paths
(ground-truth-anchored internal queries, the new fill-rate + projection chart beats, public
landscape, literature, and our-data-vs-the-field synthesis).

Badge key:
  "🔍"        → Our internal trial-operations data (CTMS)
  "📊"        → Chart / visualization of our data
  "🏥"        → ClinicalTrials.gov (public registry)
  "📚"        → PubMed / published literature
  "🏥 + 📚"  → Public trials + literature together
  "🔍 + 🏥"  → Our data vs. the public landscape
"""
import random

# ── The four capability buckets (4 questions each = 16) ─────────────────────────
# Bucket order IS the demo arc and is preserved on every rotated render.

# A — Our portfolio (internal CTMS, text answers). Ground-truth anchored.
OUR_PORTFOLIO = [
    ("🔍", "How is enrollment tracking across our trials?"),
    ("🔍", "Which of our studies is furthest behind on enrollment?"),
    ("🔍", "Which of our sites is lagging most against its target?"),
    ("🔍", "How many of our trials are still recruiting?"),
]

# B — Charts of our data. Four distinct chart types, including the new fill-rate
# (red/amber/green + target line) and enrollment-projection (forecast) beats.
OUR_CHARTS = [
    ("📊", "Chart our studies ranked by fill rate"),                # RAG bars + 100% target line
    ("📊", "Will the donanemab trial hit its target by its planned end date?"),  # projection
    ("📊", "Break down our portfolio by therapeutic area"),         # donut
    ("📊", "Show our donanemab enrollment trend over time"),        # line
]

# C — The field (public registry + published literature).
THE_FIELD = [
    ("🏥", "How many Alzheimer's disease trials are recruiting nationally?"),
    ("🏥", "Find recruiting Phase 3 trials for pancreatic cancer"),
    ("📚", "What does recent literature say about donanemab in early Alzheimer's?"),
    ("📚", "Latest evidence on tirzepatide for type 2 diabetes?"),
]

# D — Our data vs. the field (multi-tool synthesis — the climax of the arc).
SYNTHESIS = [
    ("🔍 + 🏥", "How does our donanemab enrollment compare to the field?"),
    ("🔍 + 🏥", "How does our pembrolizumab NSCLC accrual compare to the landscape?"),
    ("🏥 + 📚", "CAR-T in DLBCL — trials and long-term outcomes?"),
    ("🏥 + 📚", "Checkpoint inhibitors in NSCLC — sponsors and evidence?"),
]

BUCKETS = [OUR_PORTFOLIO, OUR_CHARTS, THE_FIELD, SYNTHESIS]

# The full 16-question rotation pool (kept as a flat list for reference / testing).
ROTATION_POOL = [pair for bucket in BUCKETS for pair in bucket]

# ── Demo static four (finalized with the script later) ──────────────────────────
# A sensible default arc for now; swap freely when we lock the script.
STATIC = [
    OUR_PORTFOLIO[0],   # How is enrollment tracking across our trials?
    OUR_CHARTS[0],      # Chart each study's fill rate against target
    SYNTHESIS[0],       # How does our donanemab enrollment compare to the field?
    THE_FIELD[2],       # What does recent literature say about donanemab...?
]

# Active mode. Demo day → set to "static".
MODE = "rotate"


def starter_pairs():
    """Return the four (badge, question) pairs to render on the welcome screen,
    honoring MODE. In rotate mode, pick one from each bucket (fixed arc order)."""
    if MODE == "static":
        return list(STATIC)
    return [random.choice(bucket) for bucket in BUCKETS]


# Backwards-compatible alias (older imports expected a flat pool whose first 4 showed).
QUESTION_POOL = ROTATION_POOL

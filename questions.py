"""
Suggested search starters shown on the welcome screen.

The first 4 (SHOWCASE) are DETERMINISTIC — always shown, in this order — so the
landing screen is identical on every reload (demo predictability). They cover the
full capability arc: our internal data → charts → us-vs-the-field → public evidence.

Badge key:
  "🔍"        → Our internal trial-operations data (CTMS)
  "📊"        → Chart / visualization of our data
  "🏥"        → ClinicalTrials.gov (public registry)
  "📚"        → PubMed / published literature
  "🏥 + 📚"  → Public trials + literature together
  "🔍 + 🏥"  → Our data vs. the public landscape
"""

# Always shown, in this order — the strongest one-click demo arc.
SHOWCASE = [
    ("🔍", "How is enrollment tracking across our trials?"),
    ("📊", "Chart our enrollment by site"),
    ("🔍 + 🏥", "How does our donanemab trial compare to the field?"),
    ("🏥 + 📚", "CAR-T in DLBCL — trials and long-term outcomes?"),
]

# Alternates — kept for reference / future rotation; not currently displayed.
ALTERNATES = [
    # ── Our portfolio (internal CTMS) ─────────────────────────────────────────
    ("🔍", "Which of our sites is furthest behind target?"),
    ("🔍", "Which of our studies has the worst enrollment?"),
    ("🔍", "What is Dr. Raj Patel researching?"),

    # ── Charts of our data ────────────────────────────────────────────────────
    ("📊", "Show our enrollment trend over time"),
    ("📊", "Visualize our portfolio by therapeutic area"),
    ("📊", "Show our recruitment funnel"),

    # ── Public trial registry (ClinicalTrials.gov) ────────────────────────────
    ("🏥", "Recruiting Phase 3 trials for pancreatic cancer?"),
    ("🏥", "Which sponsors lead Alzheimer's trial activity?"),
    ("🏥", "Trials for a 52-year-old with BRCA1 ovarian cancer?"),

    # ── Published literature (PubMed) ─────────────────────────────────────────
    ("📚", "Latest evidence on semaglutide and CV outcomes?"),
    ("📚", "What's the MeSH classification for HFpEF?"),

    # ── Trials + literature together ──────────────────────────────────────────
    ("🏥 + 📚", "Checkpoint inhibitors in NSCLC — sponsors and evidence?"),
]

# Backwards-compatible name used by app.py — first 4 are the deterministic showcase.
QUESTION_POOL = SHOWCASE + ALTERNATES

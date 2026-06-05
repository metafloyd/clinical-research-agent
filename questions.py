"""
Suggested search pool — concise starters shown 4 at a time on the welcome screen.

One line each, easy to scan. Weighted toward the differentiators (our internal
trial-operations data + on-demand charts) alongside the public sources.

Badge key:
  "🔍"        → Our internal trial-operations data (CTMS)
  "📊"        → Chart / visualization of our data
  "🏥"        → ClinicalTrials.gov (public registry)
  "📚"        → PubMed / published literature
  "🏥 + 📚"  → Public trials + literature together
  "🔍 + 🏥"  → Our data vs. the public landscape
"""

QUESTION_POOL = [
    # ── Our portfolio (internal CTMS) ─────────────────────────────────────────
    ("🔍", "How is enrollment tracking across our trials?"),
    ("🔍", "Which of our sites is furthest behind target?"),
    ("🔍", "Which of our studies has the worst enrollment?"),
    ("🔍", "What is Dr. Raj Patel researching?"),

    # ── Charts of our data ────────────────────────────────────────────────────
    ("📊", "Chart our enrollment by site"),
    ("📊", "Show our enrollment trend over time"),
    ("📊", "Visualize our portfolio by therapeutic area"),
    ("📊", "Show our recruitment funnel"),

    # ── Our data vs. the world (cross-source) ─────────────────────────────────
    ("🔍 + 🏥", "How does our donanemab trial compare to the field?"),

    # ── Public trial registry (ClinicalTrials.gov) ────────────────────────────
    ("🏥", "Recruiting Phase 3 trials for pancreatic cancer?"),
    ("🏥", "Which sponsors lead Alzheimer's trial activity?"),
    ("🏥", "Trials for a 52-year-old with BRCA1 ovarian cancer?"),

    # ── Published literature (PubMed) ─────────────────────────────────────────
    ("📚", "Latest evidence on semaglutide and CV outcomes?"),
    ("📚", "What's the MeSH classification for HFpEF?"),

    # ── Trials + literature together ──────────────────────────────────────────
    ("🏥 + 📚", "CAR-T in DLBCL — trials and long-term outcomes?"),
    ("🏥 + 📚", "Checkpoint inhibitors in NSCLC — sponsors and evidence?"),
]

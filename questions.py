"""
Suggested search pool — concise questions shown 4 at a time on the welcome screen.

Kept short (one line each) so the starter cards stay compact and the welcome
layout doesn't dominate the screen. Covers the full tool spectrum across both
data sources, for investigators, research coordinators, and clinicians.

Badge key:
  "🏥"        → ClinicalTrials.gov tools only
  "📚"        → PubMed tools only
  "🏥 + 📚"  → Combined query across both sources
"""

QUESTION_POOL = [
    # ── Eligibility matching ──────────────────────────────────────────────────
    ("🏥", "Recruiting trials for a 65-year-old with treatment-resistant hypertension?"),
    ("🏥", "Trials for a 52-year-old with early-stage ovarian cancer and a BRCA1 mutation?"),
    ("🏥", "Trials for relapsed multiple myeloma after two prior lines of therapy?"),

    # ── Landscape and counts ──────────────────────────────────────────────────
    ("🏥", "How many Phase 2–3 pancreatic cancer trials are recruiting in the US?"),
    ("🏥", "Which sponsors are most active in Alzheimer's trials, and in what phases?"),
    ("🏥", "What phases and regions dominate CRISPR gene-therapy trials globally?"),

    # ── Completed trial results ───────────────────────────────────────────────
    ("🏥", "What did completed Phase 3 semaglutide cardiovascular trials report?"),
    ("🏥 + 📚", "Completed CAR-T trials for DLBCL — results and long-term outcomes?"),

    # ── Literature depth ──────────────────────────────────────────────────────
    ("📚", "Papers related to the landmark CAR-T results in paediatric ALL?"),
    ("📚", "APA citations for the top papers on GLP-1 agonists and CV outcomes."),
    ("📚", "MeSH classification for HFpEF and related search terms?"),

    # ── Combined trials + literature ──────────────────────────────────────────
    ("🏥 + 📚", "KRAS-targeted therapy in pancreatic cancer — trials, evidence, and gaps?"),
    ("🏥 + 📚", "Semaglutide for Type 2 Diabetes — active trials and published evidence?"),
    ("🏥 + 📚", "CAR-T in paediatric ALL — eligibility, safety, and recent literature."),
    ("🏥 + 📚", "HFpEF — active trials and what completed studies reported?"),
    ("🏥 + 📚", "SGLT2 inhibitors in heart failure — trial landscape and outcomes."),
    ("🏥 + 📚", "Checkpoint inhibitors in NSCLC — leading sponsors and the evidence?"),
]

# Demo Script & Rehearsal Plan — Mayo Clinical Research Assistant

**Context:** Internal buy-in demo for **Quantiphi leadership** (technical architects, account
manager). Goal: prove this is a real, working, Mayo-aligned product worth pursuing — and
earn *you* visibility as the person who built it. Audience is **tech-savvy but NOT domain
experts**; they *do* know the Mayo account. ~10–15 min, **driven live with safety rails**.

> **The four things they must believe by the end:**
> 1. It's real and it works (live, grounded, no hand-waving).
> 2. It's genuinely aligned to the Mayo account (CTMS + registry + literature — their world).
> 3. You thought about the hard parts (grounding, multi-source composition, engineering judgment).
> 4. There's a real roadmap here — worth pursuing.

---

## ⚙️ PRE-DEMO RITUAL (do this every time — non-negotiable)

1. **Warm the app ~3 min before** you present. Open it, send one throwaway query
   (`how many active studies do we have`). This wakes the Render worker + primes the cache
   so your live queries are fast. **Cold first query = ~30–60s; warm = a few seconds.**
   *(Optional: an UptimeRobot ping every 5 min keeps it warm the whole session.)*
2. **Open a fresh chat** (don't reload an old one on stage — reloads are fine now but a fresh
   chat is the cleanest start).
3. Have this script + the **fallback screenshots** (see end) on a second screen / phone.
4. Close other heavy tabs (the app is lighter that way).

---

## 🎬 THE ARC (each step builds on the last — don't shuffle)

The order is deliberate: **establish credibility → show the unique data → land the hero
cross-source moment → visual wow → close on the roadmap.** ~10–12 min of talking.

### 0. One-line framing (15 sec, before you type anything)
> *"This is a research assistant I built for the Mayo account. The interesting part isn't
> that it searches clinical databases — it's that it reasons over THREE sources at once:
> the public trial registry, the published literature, AND Mayo's own internal trial
> operations data — and composes them in a single answer. That last one is what no
> off-the-shelf tool can do."*

This sentence does the heavy lifting — it tells architects *why it's hard* and ties it to Mayo.

### 1. Establish it's real + grounded (≈1.5 min)
**Type:** `Find recent Phase 3 trials for semaglutide in cardiovascular disease`
- **Narrate while it runs:** "It's calling ClinicalTrials.gov live, pulling structured trial
  cards — NCT IDs, phase, sponsor, status — all real, all cited. Nothing is made up; if a
  field isn't in the data, it says 'Not specified' rather than inventing it."
- **Point at:** the trial cards + citations. *Credibility anchor.*

### 2. Show the UNIQUE data — internal CTMS (≈1.5 min)
**Type:** `which of our sites is furthest behind target?`
- **Expected:** "Partner Site — London, 8/15, 53.3% of target" (real internal data).
- **Narrate:** "Now it's querying Mayo's *internal* trial operations — our studies, our sites,
  our enrollment vs target. This is private operational data, and notice it ranked by
  *fill rate*, not raw count — it understands what 'behind target' actually means."
- **Why this lands:** ops leaders' daily question; architects see the NL→SQL working.

### 3. 🌟 THE HERO MOMENT — cross-source composition (≈2.5 min)
**Type:** `how does our donanemab enrollment compare to the field?`
- **Expected:** a "## Our Enrollment" block (MAYO-2023-022, 61/75) + a "## Competitive
  Landscape" block (real ClinicalTrials.gov donanemab trials), clearly separated.
- **Narrate slowly — this is the moment:** "One question just fanned out to TWO sources —
  our internal accrual AND the public competitive landscape — and kept them clearly
  separated, never conflating our 61/75 with the registry's global numbers. *This* is the
  composition no single tool gives you. For a CRO or sponsor, this is 'how are we doing
  versus everyone else,' answered in one shot."
- **This is the slide they'll remember.** Let it breathe.

### 4. 📊 Visual wow — charts (≈2 min)
**Type:** `chart our enrollment by site`
- **Expected:** an interactive Plotly bar chart (enrolled vs target by site) + a one-line insight.
- **Narrate:** "And it visualizes the internal data on demand — this is live, interactive
  (hover, zoom), drawn straight from the SQL results, so zero hallucination risk. It built
  a dashboard from a sentence."
- **Optional 2nd chart if time:** `show the donanemab enrollment trend over time` (a line
  chart — shows the time-series depth).

### 5. (Optional, if technical questions come) Show judgment (≈1 min)
If an architect probes, show the **clarification behavior** — type something vague:
**Type:** `chart our performance`
- **Expected:** it picks a sensible default (enrollment vs target) AND *states the assumption*.
- **Narrate:** "Notice it didn't guess silently — it picked a reasonable default and told me
  what it assumed, so I can correct it. It knows what it doesn't know." *(Architects love this.)*

### 6. Close — the roadmap (30 sec, no typing)
> *"So today: three live sources, grounded answers, internal-vs-field composition, and
> on-demand visualization — all aligned to the Mayo account. Where this goes next: [pick 1–2
> — a proactive portfolio-risk briefing; a 4th data source like drug-safety signals;
> patient-to-trial eligibility matching]. I'd love to take this further."*

---

## 🛟 SAFETY RAILS (live-demo insurance)

**Use these EXACT phrasings** — they're tested to route correctly:
- `Find recent Phase 3 trials for semaglutide in cardiovascular disease` → ClinicalTrials.gov
- `which of our sites is furthest behind target?` → internal (fill-rate ranking)
- `how does our donanemab enrollment compare to the field?` → internal + registry (HERO)
- `chart our enrollment by site` → bar chart
- `show the donanemab enrollment trend over time` → line chart
- `what is our portfolio status?` → grounded portfolio read (good safe filler)

**If a query is slow (heavy/cold):** keep narrating — "it's composing across sources live,
so it's doing real work here" — talk over the ~10–20s. Never click-and-stare in silence.

**If a query misroutes or looks off:** don't fight it live. Say "let me rephrase that" and
use the tested phrasing above. Move on — don't debug on stage.

**Known edges to AVOID typing live** (they're fine, just not crisp):
- Don't ask for budget/cost/demographics/p-values as a chart (it correctly declines, but
  it's an anticlimax mid-demo).
- Don't reload a chart-heavy old chat on stage if you skipped the warm-up.

---

## 📸 FALLBACK SCREENSHOTS (prep before demo day)

Capture these while the app is warm, keep them on a second device. If the live app stalls,
flip to the screenshot and keep narrating — the story still lands:
1. A trial-card answer (step 1).
2. The "furthest behind target" internal answer (step 2).
3. **The donanemab cross-source answer** — the two-block "Our Enrollment / Competitive
   Landscape" (step 3 — the most important one to have backed up).
4. The enrollment-by-site chart (step 4).

---

## 🔁 REHEARSAL CHECKLIST (run through twice before the real thing)

- [ ] Warm-up ritual done; first query was fast.
- [ ] Said the **one-line framing** before typing (the "three sources" hook).
- [ ] Ran the 4-step arc in order; each answer rendered correctly.
- [ ] Narrated *over* the latency on the hero query — no dead silence.
- [ ] The donanemab cross-source answer showed BOTH blocks, clearly separated.
- [ ] At least one chart rendered + you mentioned "live, interactive, zero hallucination."
- [ ] Closed with the roadmap line.
- [ ] Total time under your slot with ~2 min buffer for questions.
- [ ] Fallback screenshots captured and on a second screen.

**Time the rehearsal.** If you're over, cut step 1 to 30s and drop the 2nd chart — protect
the hero moment (step 3) and the close.

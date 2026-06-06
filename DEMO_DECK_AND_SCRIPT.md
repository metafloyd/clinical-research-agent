# Demo Deck + Word-for-Word Script — Mayo Clinical Research Assistant

**Audience:** Quantiphi leadership — technical architects + account manager. Tech-savvy,
NOT domain experts, aware of the Mayo account.
**Format:** Slides FRAME, live demo CARRIES (~10–15 min). Driven live with safety rails.
**Dual goal:** (1) buy-in to pursue the project, (2) personal visibility — a credible,
thoughtful build that reflects well on you.

> **Design principle:** the deck earns trust and tells the "why"; the **live app is the proof.**
> Slides are sparse — one idea each, big visuals, few words. You talk; slides don't.

---

## PART A — THE SLIDE DECK (7 slides + 2 backup)

> Format notes: keep ≤15 words of body text per slide. Use the Mayo-navy (#0E3293) + white.
> Big type, lots of whitespace. Architects distrust busy slides. Every slide = one takeaway.

### SLIDE 1 — Title
- **Title:** *Mayo Clinical Research Assistant*
- **Subtitle:** *A multi-source research agent for clinical trial intelligence*
- Your name + role, date. A small "built on" line (Claude / LangGraph / Chainlit) is fine —
  signals real engineering.
- *Purpose: set the frame. You're presenting a built thing, not a concept.*

### SLIDE 2 — The problem (why this matters for Mayo)
- **Headline:** *Clinical research intelligence is fragmented across three silos.*
- Three icons/boxes: **Public trial registry** (ClinicalTrials.gov) · **Published literature**
  (PubMed) · **Internal trial operations** (CTMS — our studies, sites, enrollment).
- One line: *"No single tool answers a question that spans all three."*
- *Purpose: frame the gap. This is the setup for your whole differentiator.*

### SLIDE 3 — What I built (the one-liner)
- **Headline:** *One agent that reasons across all three — and composes them in a single answer.*
- A simple diagram: a user question → agent → 3 sources → one grounded answer (+ charts).
- *Purpose: the "what." Architects now know the shape before they see it run.*

### SLIDE 4 — 🎬 LIVE DEMO (transition slide — minimal)
- Just: **"Live Demo"** + the app URL. You switch to the browser here.
- *This is where the deck steps back and the app takes over (~6–8 min).*
- *(The detailed live script is PART B below.)*

### SLIDE 5 — How it works (architecture — for the architects)
- **Headline:** *Grounded, multi-source, single round-trip per source.*
- A clean architecture diagram (pull from `TECH_STACK.md` §1):
  Browser → Agent (LangGraph ReAct, gpt-4o-mini) → [ClinicalTrials.gov MCP · PubMed MCP ·
  Internal CTMS NL→SQL → Plotly charts] → grounded answer. Auth (Google OAuth), persistence
  (Supabase).
- 3 bullets that earn architect respect:
  - *Anti-fabrication guardrails — only states retrieved values, "Not specified" otherwise.*
  - *NL→SQL over internal CTMS — natural language to a read-only query, two-layer safety.*
  - *Charts drawn directly from SQL results — zero hallucination risk.*
- *Purpose: show you thought about the HARD parts. This is the credibility slide.*

### SLIDE 6 — Mayo alignment + why it's differentiated
- **Headline:** *Built for the Mayo account — not a generic chatbot.*
- Left: *Speaks Mayo's world* — internal studies, sites (Rochester/Jacksonville/Scottsdale),
  PIs, enrollment vs target.
- Right: *The unlock* — internal accrual **vs** the public competitive landscape, in one answer.
- One line: *"This composition is what off-the-shelf tools can't do."*
- *Purpose: tie it explicitly to the account + restate the moat.*

### SLIDE 7 — Roadmap + the ask
- **Headline:** *Where this goes next.*
- 3 forward bullets (PICK what you want to commit to — examples):
  - *Proactive portfolio-risk briefings (which trials are slipping, before you ask).*
  - *A 4th source — drug-safety signals (openFDA) or patient-to-trial eligibility matching.*
  - *Productionize: real CTMS connector, role-based access, audit trail.*
- **The ask (explicit):** *"I'd like buy-in to take this from prototype to a Mayo-account asset."*
- *Purpose: convert. Don't end on the demo — end on the decision you want.*

### BACKUP SLIDES (have ready, don't present unless asked)
- **B1 — Reliability/quality:** "We measure groundedness" — mention the eval suite + the
  stress-testing (37-case ambiguity battery, regression smokes). Architects ask "how do you
  know it's not hallucinating?" — this is your answer.
- **B2 — Tech stack table:** the full stack from `TECH_STACK.md` for deep technical Q&A.

---

## PART B — WORD-FOR-WORD LIVE DEMO SCRIPT

> Run after SLIDE 4. Driven live in the browser. **Warm the app 3 min before (see ritual).**
> Italics = what you SAY. Plain = what you DO. Bracketed = stage direction.

### [Switch to browser — a fresh, warm chat is already open]

**[Framing — say this BEFORE typing anything]**
> *"Okay — this is the assistant, running live. Before I type anything: the interesting part
> here isn't that it searches clinical databases. It's that it reasons over three sources at
> once — the public trial registry, the published literature, and Mayo's own internal trial
> operations — and composes them into one grounded answer. That third one, our internal data,
> is what no off-the-shelf tool can touch. Let me show you, building up to that."*

---

### BEAT 1 — Grounded & real (≈1.5 min)
**[Type]:** `Find recent Phase 3 trials for semaglutide in cardiovascular disease`

**[While it runs, say]:**
> *"It's calling ClinicalTrials.gov live right now — pulling structured trial cards: real NCT
> IDs, phase, sponsor, status. Everything you see is retrieved and cited. And it's built to
> NOT make things up — if a field isn't in the source data, it says 'Not specified' rather
> than inventing it. For a clinical audience, that discipline matters more than anything."*

**[Point at the cards + the citation links.]**

---

### BEAT 2 — The unique data: internal CTMS (≈1.5 min)
**[Type]:** `which of our sites is furthest behind target?`

**[Expected: "Partner Site — London, 8/15, 53.3% of target." While/after it runs, say]:**
> *"Now it switched sources — this is Mayo's INTERNAL trial operations. Our studies, our sites,
> our enrollment against target. This is private operational data, the kind that lives in a
> CTMS. And notice — it ranked by fill RATE, percent of target, not raw headcount. It
> understands that 'behind target' is a ratio, not a number. That's the natural-language-to-SQL
> layer doing real reasoning, safely and read-only."*

---

### BEAT 3 — 🌟 THE HERO MOMENT: cross-source composition (≈2.5 min)
**[Type]:** `how does our donanemab enrollment compare to the field?`

**[This is the centerpiece. Slow down. Expected: a "## Our Enrollment" block (MAYO-2023-022,
61/75) AND a "## Competitive Landscape" block with real registry trials.]**

**[Say, deliberately]:**
> *"Watch what one question just did. It went to our internal data — our donanemab study,
> 61 of 75 enrolled — AND to the public registry for the competitive landscape, the other
> donanemab trials in the field. Two sources, one question. And look — it keeps them clearly
> separated. It never blends our internal 61/75 with the registry's global numbers, because
> those mean different things."*

**[Pause. Then the value line:]**
> *"For a sponsor or a CRO, THIS is the question that matters — 'how are we doing versus
> everyone else?' — and it's answered in one shot, grounded, in seconds. That composition is
> the whole point. It's what you can't buy off the shelf."*

---

### BEAT 4 — 📊 Visual wow: charts (≈2 min)
**[Type]:** `chart our enrollment by site`

**[Expected: an interactive Plotly bar chart + a one-line insight. Say]:**
> *"And it visualizes on demand. This chart is live — I can hover, zoom — and it's drawn
> directly from the SQL results, so there's zero hallucination risk: the chart can't show a
> number the data doesn't contain. It effectively built a dashboard from one sentence."*

**[If time, ONE more — type]:** `show the donanemab enrollment trend over time`
> *"Or a trend over time — same idea, time-series from our accrual data."*

---

### BEAT 5 — (ONLY if an architect probes "how does it handle ambiguity / bad input")
**[Type]:** `chart our performance`

**[Expected: it picks enrollment-vs-target AND states the assumption. Say]:**
> *"Notice it didn't just guess silently. 'Performance' is vague, so it picked a sensible
> default — enrollment versus target — and told me what it assumed, so I can redirect it. It
> knows what it doesn't know. We stress-tested that behavior across dozens of ambiguous and
> adversarial inputs."*

---

### [Switch back to SLIDE 5 — architecture]
> *"So that's it live. Let me quickly show what's under the hood..."*
**[Now present slides 5 → 6 → 7.]**

---

### CLOSE (on Slide 7, no typing)
> *"To recap: three live sources, grounded answers, our-accrual-versus-the-field in one shot,
> and visualization on demand — all built around the Mayo account. Where I want to take it
> next is [your 1–2 roadmap items]. I built this to prove it's real and it works — and I'd
> like buy-in to take it from prototype to a Mayo-account asset."*

**[Stop. Let the ask sit. Take questions.]**

---

## PART C — SAFETY RAILS (live-demo insurance)

**Pre-demo ritual (every time):**
1. **Warm the app ~3 min before** — one throwaway query (`how many active studies do we have`).
   Cold first query = 30–60s; warm = a few seconds.
2. Fresh chat open. Script + fallback screenshots on a second screen. Heavy tabs closed.

**Tested phrasings (these route correctly — verified):**
| Beat | Exact phrasing |
|---|---|
| 1 | `Find recent Phase 3 trials for semaglutide in cardiovascular disease` |
| 2 | `which of our sites is furthest behind target?` |
| 3 (HERO) | `how does our donanemab enrollment compare to the field?` |
| 4 | `chart our enrollment by site` |
| 4b | `show the donanemab enrollment trend over time` |
| 5 | `chart our performance` |
| safe filler | `what is our portfolio status?` |

**If a query is SLOW:** keep narrating ("it's composing across sources live — real work here") —
talk over the ~10–20s. Never click-and-stare in silence.

**If a query MISROUTES / looks off:** don't debug live. "Let me rephrase," use the tested
phrasing, move on.

**AVOID typing live:** budget/cost/demographics/p-value charts (it correctly declines, but it's
an anticlimax); don't reload a chart-heavy old chat if you skipped the warm-up.

**Fallback screenshots to capture (while warm, keep on phone/2nd screen):**
1. Trial-card answer (Beat 1) · 2. "Furthest behind target" (Beat 2) ·
3. **The donanemab cross-source two-block answer (Beat 3 — most important backup)** ·
4. Enrollment-by-site chart (Beat 4).

---

## PART D — REHEARSAL CHECKLIST (run twice, timed)

- [ ] Warm-up done; first query fast.
- [ ] Said the framing hook BEFORE typing.
- [ ] 4-beat arc in order; each rendered correctly.
- [ ] Narrated over latency on the hero query — no dead air.
- [ ] Donanemab answer showed BOTH blocks, separated.
- [ ] ≥1 chart rendered + said "live, interactive, zero hallucination."
- [ ] Presented slides 5–6–7 after the demo.
- [ ] Closed with the explicit ASK.
- [ ] Under slot with ~2 min buffer. Fallback screenshots ready.

**If over time:** cut Beat 1 to 30s, drop the 2nd chart. PROTECT the hero moment (Beat 3) and
the close. Never rush the ask.

---

## SLIDE CHEAT-SHEET (what MUST be on slides)

If you build nothing else, these are the non-negotiables:
1. **The 3-source diagram** (Slide 2/3) — your differentiator, visualized. *Most important slide.*
2. **The architecture diagram** (Slide 5) — earns the architects' respect. Pull from TECH_STACK.md.
3. **Mayo-alignment slide** (Slide 6) — explicitly ties it to the account.
4. **Roadmap + the ASK** (Slide 7) — converts the demo into a decision.
5. **A "we measure quality" backup** (B1) — for the inevitable "how do you trust it?" question.

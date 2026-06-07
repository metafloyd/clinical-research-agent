# Demo Deck + Script — Mayo Clinical Research Assistant (CREDIBILITY-FIRST)

**Audience:** Quantiphi leadership — technical architects + account manager. Tech-savvy,
NOT domain experts, aware of the Mayo account.
**Format:** Slides FRAME, live demo CARRIES (~12–15 min). Live, with safety rails.

> **PRIMARY GOAL (60–70%): credibility / visibility.** The room should walk out thinking:
> *"This person is an expert. They diagnosed the real problem, did the thought leadership,
> made defensible engineering decisions, and pressure-tested their own work."*
> **SECONDARY (30%): greenlight** to pursue further — which follows naturally once they believe the above.

> **THE CORE PRINCIPLE:** Expertise shows in **DECISIONS and JUDGMENT**, not features.
> Anyone can demo a feature. Only someone who *understands the domain and the engineering*
> can defend a tradeoff and admit where their first version broke. Optimize every beat for
> "why I did it this way," not just "look what it does."

---

## THE TWO SIGNATURE MOVES (what makes this a credibility play)

1. **"Key Decisions" slide** — 3–4 sharp engineering tradeoffs you made, each "I chose X over
   Y because Z." This is the single highest-credibility artifact in the deck. Architects
   respect *defended choices* far more than features.
2. **"I tried to break my own system" rigor story** — you adversarially stress-tested it,
   found it fabricated under pressure, and fixed it structurally. Intellectual honesty +
   rigor = the rarest, most convincing expert signal. Most people show the happy path; you
   show that you *hunted for the failure modes*.

---

## PART A — THE SLIDE DECK (8 slides + 2 backup)

> Style: ≤15 words body text per slide. Mayo-navy (#0E3293) + white. Big type, whitespace.
> Architects distrust busy slides. One takeaway each. YOU talk; slides anchor.

### SLIDE 1 — Title
- *Mayo Clinical Research Assistant* — *A multi-source research agent for clinical trial intelligence.*
- Your name/role/date. Small "built with Claude · LangGraph · Chainlit · Supabase" line —
  signals a real, modern build.

### SLIDE 2 — The insight (thought leadership — YOUR diagnosis)
- **Headline:** *The problem isn't access to data. It's that the data lives in three silos
  that can't be queried together.*
- Three boxes: **Public registry** (ClinicalTrials.gov) · **Literature** (PubMed) ·
  **Internal trial ops** (CTMS).
- One line: *"The real question — 'how are WE doing versus the field?' — spans all three.
  No tool answers it."*
- ***Say:*** *"Before I wrote any code, this was the insight: the gap isn't search, it's
  composition across silos. That's the problem I set out to solve."*
- *Purpose: positions you as someone who diagnosed the problem, not just coded a solution.*

### SLIDE 3 — What I built
- **Headline:** *One agent that reasons across all three — and composes them into a single,
  grounded answer.*
- Simple diagram: question → agent → 3 sources → one answer (+ charts).
- *Purpose: the "what," in one breath, before they see it run.*

### SLIDE 4 — 🎬 LIVE DEMO (transition — minimal)
- Just **"Live Demo"** + URL. Switch to browser. (Script in PART B; ~6–8 min.)

### SLIDE 5 — Key engineering decisions ⭐ (THE credibility slide)
- **Headline:** *The decisions that made it reliable — not just functional.*
- 3–4 tradeoffs, each as "choice → why" (these are REAL from the build — see PART B for how
  to narrate):
  - **Single-agent, not multi-agent** → *tool round-trips are the #1 latency driver;
    predictability beats cleverness.*
  - **Model-per-task** → *gpt-4o-mini routes + writes; gpt-4o only for NL→SQL (the cheap model
    was too weak at text-to-SQL). Cost-tuned to where capability is actually needed.*
  - **Charts drawn from SQL results, not generated** → *a chart literally cannot show a number
    the data doesn't contain. Zero hallucination by construction.*
  - **Structural guardrails over prompt-instructions** → *stress-testing showed prompts alone
    don't hold under pressure; safety must be in code.*
- *Purpose: THIS is where they think "this person made real engineering judgments." Narrate
  the reasoning — that's the expertise.*

### SLIDE 6 — How I pressured it ⭐ (the rigor / intellectual-honesty slide)
- **Headline:** *I red-teamed my own system — then hardened it.*
- The story, in 3 beats (these are REAL — from the red-team battery):
  - *Attacked it: prompt injection, fabricated premises, social pressure to change a grounded
    number, forcing it to combine incompatible numbers, and medical-advice requests.*
  - *Most held — it refused jailbreaks, kept the real enrollment number even when told "you're
    wrong, it's 200," and declined dosing/diagnosis. But 5 things broke.*
  - *Fixed each — and the most important finding: a prompt rule alone couldn't stop it from
    fabricating enrollment from a user's fake number. I had to fix it in CODE, in the data layer.*
- **The killer line — ***say this***:** *"The single biggest lesson: prompt instructions don't
  hold under pressure. When a user asserted a trial had 500 patients, my agent invented enrollment
  numbers to match — and no amount of prompt wording fixed it reliably. I had to enforce it
  structurally: the data tool now declares exactly what columns it returned and refuses to show a
  figure that isn't there. **That's the difference between a demo and a system you can trust** —
  safety lives in code, not in a prompt."*
- *(Have a slide note with the concrete tally for Q&A: HELD — jailbreaks, authority pressure,
  fake PI/drug, dosing/diagnosis, multi-turn manipulation. FIXED — injection, false-premise
  fabrication [structural], false presupposition, enrollment-decision deferral, cross-source sum.)*
- *Purpose: THE rarest credibility signal. An engineer who adversarially tests their own work AND
  is honest that prompts weren't enough is dramatically more convincing than a happy-path demo.*

### SLIDE 7 — Architecture + Mayo alignment
- **Headline:** *Grounded, multi-source, aligned to the Mayo account.*
- Architecture diagram (from `TECH_STACK.md` §1): Browser → Agent (LangGraph ReAct) →
  [ClinicalTrials.gov MCP · PubMed MCP · Internal CTMS NL→SQL → Plotly] → grounded answer;
  Auth (Google OAuth), persistence (Supabase).
- One line: *"Speaks Mayo's world — our studies, sites, PIs, enrollment vs target — and
  composes it with the public landscape."*
- *Purpose: the architects' map + the explicit account tie-in.*

### SLIDE 8 — Roadmap + the ask
- **Headline:** *Where this goes next.*
- 3 forward bullets (PICK 1–2 to commit to):
  - *Proactive portfolio-risk briefings — which trials are slipping, before you ask.*
  - *A 4th source — drug-safety signals, or patient-to-trial eligibility matching.*
  - *Productionize — real CTMS connector, role-based access, audit trail.*
- **The ask:** *"I built this to prove the concept is real and the engineering is sound. I'd
  like buy-in to take it from prototype to a Mayo-account asset."*

### BACKUP SLIDES (ready, present only if asked)
- **B1 — Quality numbers:** eval scores (groundedness), the regression smoke counts, the
  ambiguity battery. For "how do you know it's not hallucinating?"
- **B2 — Full tech stack table** (from `TECH_STACK.md`) for deep technical Q&A.

---

## PART B — WORD-FOR-WORD LIVE SCRIPT (after SLIDE 4)

> Warm the app 3 min before (ritual in PART C). *Italics = SAY.* Plain = DO. **The credibility
> comes from the REASONING you narrate — not the clicks.**

### [Browser — fresh, warm chat open]

**[Framing — BEFORE typing]**
> *"This is the assistant, live. One thing up front: the interesting part isn't that it
> searches clinical databases — plenty of tools do that. It's that it reasons over three
> sources at once — the public registry, the literature, and Mayo's own internal trial
> operations — and composes them into one grounded answer. Composing across those silos is
> the hard part, and it's the whole point. Let me build up to it."*

### BEAT 1 — Grounded & real (≈1.5 min)
**[Type]:** `Find recent Phase 3 trials for semaglutide in cardiovascular disease`
> *"Live call to ClinicalTrials.gov — real trial cards, NCT IDs, phase, sponsor, all cited.
> And here's a design choice I made: it's built to NOT fabricate. If a field isn't in the
> source, it says 'Not specified' rather than guessing. For a clinical tool, that discipline
> is non-negotiable — and I'll come back to how I enforced it."*

### BEAT 2 — The unique data: internal CTMS (≈1.5 min)
**[Type]:** `which of our sites is furthest behind target?`
> *"Now it's on Mayo's INTERNAL operations — our sites, our enrollment vs target. And notice
> it ranked by fill RATE, not headcount — it understands 'behind target' is a ratio. Under the
> hood that's a natural-language-to-SQL layer, read-only, with two safety checks so a generated
> query can never mutate data. I'll flag a decision there: I use a stronger model JUST for this
> step, because the cheaper one I use elsewhere was too weak at text-to-SQL."*

### BEAT 3 — 🌟 THE HERO: cross-source composition (≈2.5 min)
**[Type]:** `how does our donanemab enrollment compare to the field?`
> *"Watch what one question does. It goes to our internal data — our donanemab study, 61 of 75
> — AND to the public registry for the competitive landscape. Two sources, one question. And
> critically, it keeps them SEPARATED — it never blends our internal 61/75 with the registry's
> global numbers, because those mean different things. That separation was a deliberate
> guardrail; an earlier version conflated them, and I caught it in testing."*

**[Pause — the value line:]**
> *"For a sponsor or CRO, this is THE question — 'how are we doing versus everyone else?' —
> answered in one grounded shot. That composition is what you can't buy off the shelf."*

### BEAT 4 — 📊 Charts (≈1.5 min)
**[Type]:** `chart our enrollment by site`
> *"Visualizes on demand — live, interactive. And a deliberate design point: the chart is drawn
> directly from the SQL results, not generated by the model. So it literally cannot show a
> number the data doesn't contain — zero hallucination, by construction, not by hope."*

### BEAT 5 — (ONLY if asked about ambiguity / bad input)
**[Type]:** `chart our performance`
> *"'Performance' is vague — so it picked a sensible default, enrollment vs target, and TOLD me
> what it assumed. It doesn't guess silently. I stress-tested that across dozens of ambiguous
> and adversarial inputs to make it consistent."*

### [Switch back to SLIDE 5]
> *"That's it live. Now the part I actually care about — the decisions behind it..."*
**[Present Slide 5 (decisions) → Slide 6 (rigor) → Slide 7 (architecture) → Slide 8 (ask).]**
**[Slides 5 & 6 are where you EARN the room — slow down, narrate the reasoning.]**

### CLOSE (Slide 8)
> *"Three live sources, grounded, our-accrual-vs-the-field in one shot, visualized on demand —
> built around the Mayo account, and hardened by trying to break it. Next I'd take it to [your
> 1–2 roadmap items]. I built this to show the concept is real and the engineering is sound —
> and I'd like buy-in to take it further."*

**[Stop. Let the ask sit. Questions.]**

---

## PART C — SAFETY RAILS

**Ritual (every time):** warm the app ~3 min before (one throwaway query); fresh chat; script +
fallback screenshots on a 2nd screen; heavy tabs closed.

**Tested phrasings (route correctly — verified):**
| Beat | Phrasing |
|---|---|
| 1 | `Find recent Phase 3 trials for semaglutide in cardiovascular disease` |
| 2 | `which of our sites is furthest behind target?` |
| 3 HERO | `how does our donanemab enrollment compare to the field?` |
| 4 | `chart our enrollment by site` |
| 4b | `show the donanemab enrollment trend over time` |
| 5 | `chart our performance` |
| filler | `what is our portfolio status?` |

**Slow query:** narrate over it ("real work — composing across sources"). Never click-and-stare.
**Misroute/off:** "let me rephrase," use the tested phrasing, move on. Don't debug live.
**Avoid live:** budget/cost/demographics/p-value charts (declines — anticlimax); don't reload a
chart-heavy old chat if you skipped the warm-up.
**Fallback screenshots (capture warm):** trial cards · furthest-behind · **donanemab two-block
(most important)** · enrollment-by-site chart.

---

## PART D — REHEARSAL CHECKLIST (run twice, timed)

- [ ] Warm-up done; first query fast.
- [ ] Framing hook said BEFORE typing.
- [ ] 4-beat arc in order; each rendered.
- [ ] Narrated a DECISION/tradeoff in ≥2 beats (not just features).
- [ ] Donanemab answer showed BOTH blocks, separated.
- [ ] Presented Slide 5 (decisions) + Slide 6 (rigor) with the reasoning — slowly.
- [ ] Closed with the explicit ASK.
- [ ] Under slot with ~2 min buffer; fallback screenshots ready.

**If over time:** trim Beat 1, drop the 2nd chart. **NEVER cut Slides 5 & 6 (decisions + rigor)
— those are the credibility, the whole point.** Protect the hero moment + the ask.

---

## THE "EXPERT SIGNALS" CHEAT-SHEET (memorize these — they're what land "this person knows")

Drop these naturally; each one says "I understand this deeply":
1. *"Tool round-trips are the #1 latency driver — so I kept it single-agent on purpose."*
2. *"I cost-tuned the models per task — the cheap one routes, the strong one only does NL→SQL."*
3. *"The charts can't hallucinate — they're drawn from the SQL rows, not generated."*
4. *"Prompt instructions don't hold under pressure — real safety has to be in code."* (← the big one)
5. *"I went hunting for where it would fabricate, because a clinical tool that lies is worse than none."*
6. *"Internal-vs-public are two different views of the same trial — never conflate them."*

> These six lines are your credibility. The demo is the stage; THESE are the performance.

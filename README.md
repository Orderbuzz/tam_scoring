# Industrial TAM Scoring Engine — TigerData

A scoring engine for TigerData's actual TAM: heavy industrial, logistics, and manufacturing accounts. Not generic ABM. The signals, weights, and guardrails are tuned for buyers who don't show up on G2 and don't surge on Bombora the way a Series B SaaS company does.

**Run it:**
```bash
pip install -r requirements.txt
python signals.py     # generate 45 mock accounts + 147 signals
python scoring.py     # score them, print top 10 / bottom 5
python tiering.py     # tier them, print Tier 1 + Tier 4 (human review) lists
python value_props.py # show wedge match per account
python email_gen.py   # show fallback emails for top Tier 1 accounts
streamlit run app.py  # interactive UI with tunable weights + drafted outreach
```
The fallback email generator runs offline. For LLM-upgraded drafts, set `ANTHROPIC_API_KEY` in the environment before running Streamlit.

---

### Why this problem

TigerData sells a time-series database to industrial buyers — power utilities, manufacturers, logistics fleets. Generic ABM tools don't work well for this TAM. The signals that actually matter aren't G2 page views or "data warehouse" topic surges; they're new data-platform leadership hires, JD keywords like "OSIsoft PI" or "InfluxDB cardinality," and press releases about IIoT modernization. Most ABM scoring is built for the SaaS-selling-to-SaaS world. This isn't that.

The high-leverage problem isn't "score more accounts." It's "score fewer accounts correctly, refuse to auto-act on the ones where we'd burn the relationship, and when we do act, write a message that sounds like an AE who has actually shipped industrial data infrastructure — not a vendor pitch." That's what the system does.

### What I built

Three modules, all judgment exposed in CONFIG dicts at the top of each file so a reviewer can grep the opinions in 30 seconds:

- **`signals.py`** — 45 mock accounts across 3 archetypes (heavy industrial, logistics, manufacturing) with 6 signal types: new-in-seat hires, team hiring volume, JD keyword content, tech stack with displacement risk, news/events, and AI website-scrape extracts. Deliberately messy: dupes, missing fields, conflicting signals, free-text titles.
- **`scoring.py`** — Score = `ICP_fit × (signal_sum × stack_multiplier + high_conviction_pair_bonuses + special_bonuses)`. Per-signal recency half-lives differ (a new VP hire decays over 180 days; a press release over 45). High-conviction pairs (e.g., new VP + team hiring + JD keywords = buying-committee fingerprint) get explicit additive bonuses on top of the multiplier. Every account gets a full reasoning trace.
- **`tiering.py`** — Six guardrails that route accounts to **Tier 4 (human review, do not auto-act)** regardless of score. The most expensive failure mode of an ABM system is auto-firing outreach to a 100-score account that's actually mid-layoff. In the demo run, **PrecisionWorks Manufacturing scored 100 and was caught by the conflicting-news guardrail.** That's the centerpiece.
- **`value_props.py`** — Maps signals to one of TigerData's four GTM wedges (sensor data scale outgrown, historian modernization, Postgres consolidation, IIoT program acceleration). The wedge match determines the angle the outreach takes.
- **`email_gen.py`** — Fallback-first outreach generator. The deterministic fallback composes a per-wedge first-touch email + 3 discovery questions from hand-tuned phrase functions, no API needed. The LLM upgrade path calls Claude with the fallback as a style guardrail (not a template) and a long ban-list of AI-email tells. Only fires for Tier 1 accounts.

### What I deliberately didn't build

- **Real signal ingestion.** No live job board, news feed, or LinkedIn scraping. The mock data is realistic enough to test the scoring logic; the wiring to real sources is plumbing, and the brief is explicit that polish on plumbing is a bad tradeoff.
- **An n8n / orchestration layer.** Tempting because the brief mentions n8n, but the judgment is the product here, not the orchestration. Production version would wrap this engine as an n8n custom node — that's noted in "next 10 hours" below.
- **Persistence / feedback loop.** No database, no rep-outcome logging. In a real deployment, every Tier 1 fire and Tier 4 review needs to write back so weights can be tuned from data, not vibes.

### Where it breaks at 10x data

- **The conflicting-news guardrail over-fires.** In the demo, 14 of 45 accounts hit Tier 4, mostly from negative news. Real production needs **conflict severity scoring** — an 8% layoff at a 30K-employee utility shouldn't kill a 100-score account the way "CEO departure + earnings delay" should. Right now any negative news within 60 days flips the bit.
- **Signal source reliability isn't modeled.** Two job board scrapers can produce the same signal with very different fidelity. We'd need per-source confidence priors and source-disagreement logic.
- **The signal stack multiplier is naive.** It rewards distinct signal *types* but doesn't penalize within-source correlation (5 job postings from the same company aren't 5 independent signals). At 10x volume this would inflate scores systematically.
- **No deduplication on entity resolution.** I plant duplicate accounts in the mock data (Westhaus Power Systems / WESTHAUS POWER) but the engine doesn't merge them. At scale, fuzzy matching on domain + name + employee-count band is a prerequisite, not a nice-to-have.

### What I'd do next with another 10 hours

1. **Wrap the engine as an n8n custom node.** Webhook trigger from signal sources → score → tier → route. Tier 1 to Slack + Salesforce task, Tier 2 to Outreach sequence enrollment, Tier 4 to a human review queue with the full reasoning trace attached.
2. **Rep voice matching for the outreach drafts.** The current email generator produces an on-tone industrial-AE voice but it's a single voice. Production version takes the AE's last 20 sent emails and tunes the prompt per rep — same wedge logic, different cadence, contractions, and sign-off style.
3. **Replace the conflict guardrail with severity scoring.** LLM-classify negative news on a 1-5 severity scale, weight by company size, decay by recency. Layoff at 5% of headcount at a 30K-employee company is a 2; CEO departure mid-quarter at a 5K-employee company is a 5.
4. **Per-rep weight overrides.** Different AEs have different judgment about which signals matter for their territory. Let them adjust the CONFIG sliders for their accounts and persist it.
5. **Outcome feedback loop.** Every Tier 1 fire and Tier 4 review writes back: did the meeting happen, did the deal progress, was the human reviewer's call right? Use it to tune weights monthly.

### Mock data

Generated by `signals.py`. 45 accounts, 147 signals. Reproducible — `random.seed(42)` is set. Dupes, missing fields, and conflicting signals are deliberately injected; see the docstring at the top of `signals.py` for the full list of mess.

"""
app.py
------
Streamlit view layer for the TigerData scoring engine.

Design principles:
- The UI exists to expose JUDGMENT, not to look pretty. Every control on
  this page either (a) lets a reviewer tune a weight to see how the tiers
  shift, or (b) shows the reasoning trace for why an account landed where
  it did.
- Tier-first ordering: Tier 1 first, then Tier 4 (the human-review catches),
  then Tier 2 / 3 / ignore. The Tier 4 catches are the most distinctive
  thing this engine does and they should be visible.
- No file uploads, no multi-page nav, no charts beyond what's earned.
"""

import streamlit as st
import copy
import os

import scoring
import tiering
from signals import generate_all_accounts, to_jsonable
from value_props import top_value_prop, rank_value_props, VALUE_PROP_SUMMARIES
from email_gen import generate_fallback_email, generate_with_llm


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="TigerData TAM Scoring",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("Industrial TAM Scoring Engine")
st.caption(
    "Scoring + tiering for TigerData's actual TAM — heavy industrial, "
    "logistics, and manufacturing. The judgment lives in the sidebar weights "
    "and the guardrails. Tune anything and watch the tiers shift."
)


# ---------------------------------------------------------------------------
# Sidebar - the tunable weights (this is where the judgment is exposed)
# ---------------------------------------------------------------------------

st.sidebar.header("Signal weights")
st.sidebar.caption(
    "Raw points per signal type, before recency decay and confidence. "
    "Defaults reflect my opinion about which signals convert for industrial "
    "buyers."
)

w_new_in_seat = st.sidebar.slider(
    "New-in-seat (data leadership hire)", 0, 40,
    scoring.CONFIG["signal_weights"]["new_in_seat"],
    help="Highest-converting signal in B2B. New leaders buy."
)
w_team_hiring = st.sidebar.slider(
    "Team hiring (open data/platform reqs)", 0, 40,
    scoring.CONFIG["signal_weights"]["team_hiring"],
)
w_jd_content = st.sidebar.slider(
    "JD keyword content (TSDB / InfluxDB / OSIsoft etc.)", 0, 40,
    scoring.CONFIG["signal_weights"]["job_posting_content"],
    help="JD keywords are a buying-committee tell."
)
w_tech_stack = st.sidebar.slider(
    "Tech stack + displacement risk", 0, 40,
    scoring.CONFIG["signal_weights"]["tech_stack"],
)
w_news = st.sidebar.slider(
    "News & events (press, conferences, M&A)", 0, 40,
    scoring.CONFIG["signal_weights"]["news_events"],
)
w_scrape = st.sidebar.slider(
    "Website scrape (LLM-extracted)", 0, 40,
    scoring.CONFIG["signal_weights"]["website_scrape"],
)

st.sidebar.divider()
st.sidebar.header("Tier thresholds")
t1_threshold = st.sidebar.slider(
    "Tier 1 minimum score", 40, 100,
    tiering.TIER_THRESHOLDS["tier_1"],
    help="Score >= this AND no guardrails fired = Tier 1 (act now)."
)
t2_threshold = st.sidebar.slider(
    "Tier 2 minimum score", 15, 80,
    tiering.TIER_THRESHOLDS["tier_2"],
)

st.sidebar.divider()
st.sidebar.header("Guardrails")
st.sidebar.caption(
    "Toggle off to see what gets through without the safety net. "
    "Tier 4 only exists because of these."
)

guardrails_on = {}
guardrails_on["conflicting_news"] = st.sidebar.checkbox(
    "Conflicting news (negative news within 60d)", value=True
)
guardrails_on["single_source_high_score"] = st.sidebar.checkbox(
    "Single-source high score (one signal type, score ≥30)", value=True
)
guardrails_on["low_confidence_signals_only"] = st.sidebar.checkbox(
    "Low-confidence signals only (max conf <0.6)", value=True
)
guardrails_on["low_icp_fit_high_score"] = st.sidebar.checkbox(
    "Low ICP fit + high score (probable false positive)", value=True
)
guardrails_on["missing_critical_data"] = st.sidebar.checkbox(
    "Missing critical data (domain or employee count)", value=True
)
guardrails_on["stale_signals_only"] = st.sidebar.checkbox(
    "Stale signals only (freshest >120d old)", value=True
)


# ---------------------------------------------------------------------------
# Apply sidebar overrides to a working copy of CONFIG
# ---------------------------------------------------------------------------

# Make sure we don't mutate the imported CONFIG across reruns.
working_config = copy.deepcopy(scoring.CONFIG)
working_config["signal_weights"] = {
    "new_in_seat": w_new_in_seat,
    "team_hiring": w_team_hiring,
    "job_posting_content": w_jd_content,
    "tech_stack": w_tech_stack,
    "news_events": w_news,
    "website_scrape": w_scrape,
}
scoring.CONFIG = working_config

working_thresholds = dict(tiering.TIER_THRESHOLDS)
working_thresholds["tier_1"] = t1_threshold
working_thresholds["tier_2"] = t2_threshold
tiering.TIER_THRESHOLDS = working_thresholds

# Filter guardrails to only the enabled ones
all_checks = dict(tiering.GUARDRAIL_CHECKS)
tiering.GUARDRAIL_CHECKS = {
    name: fn for name, fn in all_checks.items() if guardrails_on.get(name, True)
}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@st.cache_data
def get_accounts():
    return to_jsonable(generate_all_accounts())

accounts = get_accounts()
breakdowns = scoring.score_all(accounts)
tier_assignments = tiering.tier_all(accounts, breakdowns)

# Restore tiering.GUARDRAIL_CHECKS for next rerun (don't leak the filter)
tiering.GUARDRAIL_CHECKS = all_checks

# Index for lookup
accounts_by_name = {a["name"]: a for a in accounts}
assignments_by_name = {t.account_name: t for t in tier_assignments}


# ---------------------------------------------------------------------------
# Top-line metrics
# ---------------------------------------------------------------------------

tier_counts = {}
for t in tier_assignments:
    tier_counts[t.tier] = tier_counts.get(t.tier, 0) + 1

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Tier 1 — Act now", tier_counts.get("tier_1", 0))
c2.metric("Tier 4 — Human review", tier_counts.get("tier_4", 0),
          help="Guardrails caught these. High score does NOT mean act.")
c3.metric("Tier 2 — Nurture", tier_counts.get("tier_2", 0))
c4.metric("Tier 3 — Monitor", tier_counts.get("tier_3", 0))
c5.metric("Ignore", tier_counts.get("ignore", 0))

st.divider()


# ---------------------------------------------------------------------------
# Tier-first list
# ---------------------------------------------------------------------------

TIER_LABELS = {
    "tier_1": ("Tier 1 — Act now", "Executive outbound, draft tailored first-touch."),
    "tier_4": ("Tier 4 — Human review", "Guardrails fired. Do not auto-act."),
    "tier_2": ("Tier 2 — Nurture", "Multi-touch sequence, revisit on new signal."),
    "tier_3": ("Tier 3 — Monitor", "Watch only, no outreach."),
    "ignore": ("Ignore", "Insufficient signal or fit."),
}

TIER_ORDER = ["tier_1", "tier_4", "tier_2", "tier_3", "ignore"]


def render_tier_section(tier_key: str):
    label, blurb = TIER_LABELS[tier_key]
    items = [t for t in tier_assignments if t.tier == tier_key]
    items.sort(key=lambda t: t.final_score, reverse=True)

    with st.expander(f"{label}  ({len(items)})", expanded=(tier_key in ("tier_1", "tier_4"))):
        st.caption(blurb)
        if not items:
            st.write("_No accounts in this tier with current weights._")
            return
        for t in items:
            account = accounts_by_name[t.account_name]
            cols = st.columns([3, 1, 2, 1])
            cols[0].markdown(f"**{t.account_name}**  \n"
                             f"_{account['industry']} · "
                             f"{account.get('employee_count') or '?'} employees_")
            cols[1].markdown(f"### {t.final_score}")
            if t.guardrails_fired:
                rules = ", ".join(r[0] for r in t.guardrails_fired)
                cols[2].markdown(f":red[**Caught by:**] {rules}")
            else:
                n_sigs = t.breakdown.distinct_signal_types
                cols[2].markdown(f"{n_sigs} distinct signal types")
            if cols[3].button("View trace", key=f"trace_{t.account_name}"):
                st.session_state["selected"] = t.account_name


for tier_key in TIER_ORDER:
    render_tier_section(tier_key)


# ---------------------------------------------------------------------------
# Detail panel - the reasoning trace
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Reasoning trace")

selected = st.session_state.get("selected")
if not selected:
    st.caption("Click 'View trace' on any account above to see the full "
               "reasoning: ICP fit breakdown, per-signal contributions, "
               "stack multiplier, special bonuses, and any guardrails that fired.")
else:
    t = assignments_by_name[selected]
    b = t.breakdown
    account = accounts_by_name[selected]

    head_l, head_r = st.columns([2, 1])
    head_l.markdown(f"### {selected}")
    head_l.caption(f"{account['industry']} · {account['archetype']} · "
                   f"{account.get('employee_count') or 'unknown'} employees")
    head_r.markdown(f"## Score: {b.final_score}")
    head_r.markdown(f"**{TIER_LABELS[t.tier][0]}**")

    # Guardrails first if they fired - this is the most important info
    if t.guardrails_fired:
        st.error(
            "**Guardrails fired — routed to human review:**\n\n"
            + "\n\n".join(f"- **{name}**: {reason}"
                          for name, reason in t.guardrails_fired)
        )

    # ICP fit breakdown
    st.markdown("**ICP fit**")
    icp = b.icp_breakdown
    st.write(
        f"`{icp['archetype']}` × {icp['archetype_weight']}  "
        f"× `{icp['industry']}` modifier {icp['industry_modifier']}  "
        f"× size [{icp['size_reason']}] {icp['size_modifier']}  "
        f"= **{icp['final_icp_fit']}**"
    )

    # Signal contributions table
    st.markdown("**Signal contributions**")
    if not b.signal_contributions:
        st.write("_No signals._")
    else:
        # Sort by contribution descending
        sigs = sorted(b.signal_contributions,
                      key=lambda s: s["contribution"], reverse=True)
        rows = []
        for s in sigs:
            rows.append({
                "Type": s["type"],
                "Signal": s["value"],
                "Raw weight": s["raw_weight"],
                "Days ago": s["days_ago"],
                "Recency factor": s["recency_factor"],
                "Confidence": s["confidence"],
                "Contribution": s["contribution"],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # Multiplier and bonuses
    bonus_l, bonus_r = st.columns(2)
    with bonus_l:
        st.markdown("**Stack multiplier**")
        st.write(f"{b.distinct_signal_types} distinct signal type(s) "
                 f"→ ×{b.stack_multiplier}")
    with bonus_r:
        st.markdown("**Bonuses**")
        if b.high_conviction_bonus:
            st.write(f"High-conviction pairs: +{b.high_conviction_bonus}")
            for pair in b.special_bonuses["high_conviction_pairs_fired"]:
                st.write(f"  · {pair}")
        if b.special_bonuses["special_breakdown"]:
            for k, v in b.special_bonuses["special_breakdown"].items():
                st.write(f"{k}: +{v}")
        if (not b.high_conviction_bonus
                and not b.special_bonuses["special_breakdown"]):
            st.write("_None_")

    # Notes
    if account.get("notes"):
        st.markdown("**Notes (CRM free-text)**")
        st.info(account["notes"])

    # ----------------------------------------------------------------------
    # Value prop match + email draft (Tier 1 only - other tiers don't get
    # auto-drafted outreach because either (a) we shouldn't be acting on
    # them yet, or (b) a human needs to look first)
    # ----------------------------------------------------------------------
    st.divider()
    st.markdown("**Top value prop match**")

    wedge = top_value_prop(account, b)
    if wedge is None:
        st.write("_No wedge match - signals don't map to a TigerData "
                 "value proposition. Account may need enrichment or a "
                 "different positioning angle._")
    else:
        wedge_summary = VALUE_PROP_SUMMARIES[wedge.value_prop_id]["short"]
        st.write(f"`{wedge.value_prop_id}` ({wedge.score:.0f}) — {wedge_summary}")
        with st.expander("Why this wedge"):
            for r in wedge.reasons:
                st.write(f"- {r}")

        # Email drafting only for Tier 1
        if t.tier == "tier_1":
            st.markdown("**Outreach draft**")
            st.caption("Pre-drafted first-touch + 3 discovery questions, "
                       "keyed to the matched wedge. Fallback runs offline; "
                       "set ANTHROPIC_API_KEY for the LLM-upgraded version.")

            col_l, col_r = st.columns(2)
            use_llm = col_r.checkbox(
                "Use LLM upgrade",
                value=False,
                key=f"llm_{selected}",
                disabled=(not os.getenv("ANTHROPIC_API_KEY")),
                help=("Calls Claude API. Disabled when ANTHROPIC_API_KEY "
                      "is not set; fallback runs deterministically."),
            )

            if use_llm and os.getenv("ANTHROPIC_API_KEY"):
                with st.spinner("Calling Claude..."):
                    email = generate_with_llm(account, wedge, is_tier_1=True)
            else:
                email = generate_fallback_email(account, wedge, is_tier_1=True)

            st.markdown(f"**Subject:** {email['email_subject']}")
            st.text_area(
                "Body",
                value=email["email_body"],
                height=240,
                key=f"body_{selected}",
            )
            st.markdown("**Discovery questions**")
            for i, q in enumerate(email["discovery_questions"], 1):
                st.write(f"{i}. {q}")
            st.caption(f"Source: `{email['source']}` · "
                       f"Wedge: `{email['wedge']}`")
        elif t.tier == "tier_4":
            st.info("Tier 4 - human review required. No outreach drafted "
                    "until a human clears the guardrails.")
        else:
            st.caption(f"Tier `{t.tier}` - no outreach drafted. "
                       "Drafting only runs for Tier 1.")

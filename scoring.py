"""
scoring.py
----------
The judgment-encoded-in-software piece. Every weight, threshold, and decay
constant lives in CONFIG at the top of this file with a comment explaining
WHY it has the value it does. If a reviewer disagrees with a number, they
should be able to find it in 30 seconds.

Score composition (0-100 scale):
    final = ICP_fit * (signal_strength_weighted_sum * stack_multiplier)

Where:
    ICP_fit             - 0.0 to 1.0 multiplier based on industry, size,
                          asset intensity. Bad ICP fit zeroes everything else.
                          (We don't care how strong the signals are if they're
                          not in our TAM.)
    signal_strength     - sum of (per-signal weight * recency factor *
                          confidence) across all signals.
    stack_multiplier    - bonus when correlated signals fire together.
                          Three independent signals > sum of parts.
                          A new VP hire + team hiring + JD keywords is a
                          buying-committee fingerprint, not three coincidences.
"""

from datetime import datetime
import math
from dataclasses import dataclass, field
from typing import Optional


# ===========================================================================
# CONFIG - the judgment, made legible
# ===========================================================================

CONFIG = {
    # -----------------------------------------------------------------------
    # ICP fit weights
    # -----------------------------------------------------------------------
    # TigerData's strongest TAM is heavy industrial with massive sensor data
    # volume. Manufacturing is a close second. Logistics is solid but the
    # buyer profile is more fragmented (sometimes IT, sometimes ops, sometimes
    # the fleet team). We weight accordingly.
    "archetype_fit": {
        "heavy_industrial": 1.00,   # core TAM - power/utility/nuclear/oil & gas
        "manufacturing":    0.95,   # strong TAM - factory floor, IIoT, OEMs
        "logistics_fleet":  0.85,   # solid TAM, more buyer-profile variance
    },

    # Industry-level overrides for archetype. Some sub-industries punch above
    # their archetype weight (utility/grid is the canonical TigerData buyer);
    # others under-perform (defense mfg has procurement cycles measured in
    # geological time).
    "industry_modifier": {
        "utility/grid":         1.10,   # canonical buyer; sensor data per
                                        # substation is enormous
        "nuclear/power":        1.05,
        "oil & gas":            1.05,
        "auto Tier-1":          1.05,
        "shipping/ports":       1.00,
        "trucking/logistics":   1.00,
        "rail freight":         1.00,
        "renewables":           0.95,
        "aerospace":            0.90,
        "defense mfg":          0.75,   # great TAM, awful sales cycle
        "pharma mfg":           0.85,
        "building materials":   0.80,
        "metals/heavy":         0.95,
        "chemicals":            0.95,
        "plastics/polymers":    0.85,
        "pulp/paper":           0.80,
        "energy storage mfg":   0.95,
        "industrial OEM":       1.00,
        "air freight":          0.95,
        "3PL/warehousing":      0.90,
        "last mile":            0.80,   # buyer is usually too operational
                                        # to be a data platform purchaser
    },

    # Employee count: TigerData's product is most differentiated for orgs
    # with real sensor data scale. Tiny companies don't have the data volume
    # to need us; massive companies have it but the sales cycle is brutal.
    # Sweet spot is 2K-30K employees - big enough to have real IIoT, small
    # enough to move.
    "size_curve": {
        # (min_employees, max_employees, multiplier)
        "tiers": [
            (0,      500,    0.60),  # too small for sensor data scale
            (500,    2000,   0.85),
            (2000,   10000,  1.00),  # sweet spot lower end
            (10000,  30000,  1.05),  # sweet spot
            (30000,  100000, 0.95),  # great TAM but slow sales cycle
            (100000, 1e9,    0.85),  # F500+ - usually multi-year cycle
        ],
        "missing_employee_count": 0.85,  # don't penalize hard for missing
                                         # data; flag for enrichment instead
    },

    # -----------------------------------------------------------------------
    # Per-signal weights (raw points before recency / confidence adjust)
    # -----------------------------------------------------------------------
    # The numbers reflect actual conversion data from B2B GTM research and
    # my own scoring experience. New-in-seat is consistently the highest-
    # converting signal type for industrial buyers because new leaders
    # have budget, mandate, and willingness to switch vendors.
    "signal_weights": {
        "new_in_seat":         25,   # highest - new buyers buy
        "team_hiring":         15,   # building a team = real investment
        "job_posting_content": 22,   # JD keywords = buying committee tell
        "tech_stack":          18,   # current stack reveals fit + risk
        "news_events":         10,   # broad signal, lots of noise
        "website_scrape":      12,   # high-fidelity but lower coverage
    },

    # -----------------------------------------------------------------------
    # Per-signal recency half-life (days)
    # -----------------------------------------------------------------------
    # Different signals decay at different rates. A new VP hire is hot for
    # ~6 months because new execs do their assessment in the first 90 days
    # and start buying in 90-180. A press release decays fast - news from
    # 6 months ago is irrelevant. Tech stack decays slowly because stacks
    # don't change overnight.
    "recency_halflife_days": {
        "new_in_seat":         180,  # ~6 months - the buying window
        "team_hiring":         60,   # open reqs go stale fast
        "job_posting_content": 90,   # similar to team_hiring
        "tech_stack":          365,  # stacks don't change overnight
        "news_events":         45,   # news is a fast-decay signal
        "website_scrape":      30,   # websites change often; freshness matters
    },

    # -----------------------------------------------------------------------
    # Signal stack multiplier
    # -----------------------------------------------------------------------
    # When multiple high-quality signals fire together, the account is
    # meaningfully more likely to be in-market than the sum suggests.
    # Three correlated signals (new VP + team hiring + JD keywords) is a
    # buying-committee fingerprint - we multiply.
    "stack_multiplier": {
        # number of distinct signal types present -> multiplier
        1: 0.85,   # single-source weak signal -> penalize
        2: 1.00,   # baseline
        3: 1.15,   # genuine pattern emerging
        4: 1.30,   # buying-committee fingerprint
        5: 1.40,
        6: 1.45,   # diminishing returns past 4
    },

    # Bonus for "high-conviction" signal pairs that are MORE than the sum.
    # These are correlated patterns that I've seen convert at much higher
    # rates than either signal alone.
    "high_conviction_pairs": [
        # (signal_type_a, signal_type_b, bonus_points)
        ("new_in_seat",         "team_hiring",          8),
        ("new_in_seat",         "job_posting_content",  10),
        ("team_hiring",         "job_posting_content",  6),
        ("tech_stack",          "job_posting_content",  5),
        ("website_scrape",      "job_posting_content",  4),
    ],

    # -----------------------------------------------------------------------
    # Special-case bonuses
    # -----------------------------------------------------------------------
    # An explicit competitor (or our own product) mention in a JD is the
    # rarest, highest-converting signal in B2B. We bonus it hard.
    "explicit_competitor_or_us_bonus": 15,

    # Displacement risk on the current stack is a strong fit signal.
    "displacement_risk_bonus": {
        "displacement_risk_high":   8,   # InfluxDB cardinality complaints
        "displacement_risk_medium": 5,
        "displacement_risk_low":    2,
        "expansion_opportunity":    6,   # already on Postgres - upsell path
    },
}


# ===========================================================================
# Scoring functions
# ===========================================================================

@dataclass
class ScoreBreakdown:
    """Full reasoning trace for a scored account. This is what shows up in
    the Streamlit detail panel and what makes the judgment legible."""
    account_name: str
    final_score: float
    icp_fit: float
    icp_breakdown: dict           # archetype, industry mod, size mod
    signal_contributions: list    # per-signal: type, raw, recency_factor, etc.
    stack_multiplier: float
    high_conviction_bonus: float
    special_bonuses: dict
    flags: list                   # human-readable flags for the trace
    distinct_signal_types: int


def compute_recency_factor(days_ago: int, halflife: int) -> float:
    """Exponential decay. A signal at exactly the halflife has 0.5x weight."""
    return math.pow(0.5, days_ago / halflife)


def compute_size_modifier(emp_count: Optional[int]) -> tuple:
    """Returns (modifier, reason). Reason is human-readable for the trace."""
    if emp_count is None:
        return (CONFIG["size_curve"]["missing_employee_count"],
                "missing employee count - flagged for enrichment")
    for lo, hi, mod in CONFIG["size_curve"]["tiers"]:
        if lo <= emp_count < hi:
            return (mod, f"{emp_count:,} employees (tier: {lo:,}-{hi:,})")
    return (1.0, f"{emp_count:,} employees")


def compute_icp_fit(account: dict) -> tuple:
    """Returns (icp_fit_multiplier, breakdown_dict)."""
    archetype_w = CONFIG["archetype_fit"].get(account["archetype"], 0.5)
    industry_mod = CONFIG["industry_modifier"].get(account["industry"], 1.0)
    size_mod, size_reason = compute_size_modifier(account["employee_count"])

    fit = archetype_w * industry_mod * size_mod
    # Cap at 1.2 so a perfect-fit account doesn't blow past the 0-100 scale
    fit = min(fit, 1.2)

    breakdown = {
        "archetype": account["archetype"],
        "archetype_weight": archetype_w,
        "industry": account["industry"],
        "industry_modifier": industry_mod,
        "size_reason": size_reason,
        "size_modifier": size_mod,
        "final_icp_fit": round(fit, 3),
    }
    return fit, breakdown


def score_signal(signal: dict) -> dict:
    """Score one signal. Returns a dict with the full breakdown."""
    sig_type = signal["type"]
    raw = CONFIG["signal_weights"].get(sig_type, 0)
    halflife = CONFIG["recency_halflife_days"].get(sig_type, 90)
    recency = compute_recency_factor(signal["days_ago"], halflife)
    confidence = signal["confidence"]

    contribution = raw * recency * confidence

    return {
        "type": sig_type,
        "value": signal["value"],
        "raw_weight": raw,
        "days_ago": signal["days_ago"],
        "halflife_days": halflife,
        "recency_factor": round(recency, 3),
        "confidence": confidence,
        "contribution": round(contribution, 2),
    }


def compute_high_conviction_bonus(signals: list) -> tuple:
    """Returns (bonus_points, list_of_pairs_that_fired)."""
    types_present = {s["type"] for s in signals}
    bonus = 0
    fired_pairs = []
    for a, b, points in CONFIG["high_conviction_pairs"]:
        if a in types_present and b in types_present:
            bonus += points
            fired_pairs.append(f"{a} + {b} (+{points})")
    return bonus, fired_pairs


def compute_special_bonuses(signals: list) -> tuple:
    """Returns (total_bonus, breakdown_dict)."""
    breakdown = {}
    total = 0
    for s in signals:
        meta = s.get("metadata") or {}
        # Explicit competitor or TimescaleDB mention in JD
        if meta.get("explicit_competitor_or_us"):
            pts = CONFIG["explicit_competitor_or_us_bonus"]
            total += pts
            breakdown["explicit_jd_mention"] = pts
        # Displacement risk on current stack
        risk = meta.get("risk_class")
        if risk and risk in CONFIG["displacement_risk_bonus"]:
            pts = CONFIG["displacement_risk_bonus"][risk]
            total += pts
            breakdown[f"displacement_{risk}"] = pts
    return total, breakdown


def score_account(account: dict) -> ScoreBreakdown:
    """Score a single account end-to-end. Returns full reasoning trace."""
    flags = []

    # ICP fit
    icp_fit, icp_breakdown = compute_icp_fit(account)
    if icp_fit < 0.5:
        flags.append("LOW_ICP_FIT")

    # Score each signal
    signal_contribs = [score_signal(s) for s in account["signals"]]
    raw_signal_sum = sum(c["contribution"] for c in signal_contribs)

    # Stack multiplier based on number of distinct signal types
    distinct_types = len({c["type"] for c in signal_contribs})
    stack_mult = CONFIG["stack_multiplier"].get(distinct_types, 1.0)
    if distinct_types == 0:
        flags.append("NO_SIGNALS")
    elif distinct_types == 1:
        flags.append("SINGLE_SOURCE_SIGNAL")

    # High-conviction pair bonuses
    hc_bonus, fired_pairs = compute_high_conviction_bonus(account["signals"])

    # Special bonuses (explicit JD mention, displacement risk)
    special_total, special_breakdown = compute_special_bonuses(account["signals"])

    # Compose final score
    pre_icp = (raw_signal_sum * stack_mult) + hc_bonus + special_total
    final = pre_icp * icp_fit

    # Cap at 100 for a clean scale
    final = min(final, 100.0)

    return ScoreBreakdown(
        account_name=account["name"],
        final_score=round(final, 1),
        icp_fit=round(icp_fit, 3),
        icp_breakdown=icp_breakdown,
        signal_contributions=signal_contribs,
        stack_multiplier=stack_mult,
        high_conviction_bonus=hc_bonus,
        special_bonuses={
            "high_conviction_pairs_fired": fired_pairs,
            "special_breakdown": special_breakdown,
            "special_total": special_total,
        },
        flags=flags,
        distinct_signal_types=distinct_types,
    )


def score_all(accounts: list) -> list:
    return [score_account(a) for a in accounts]


if __name__ == "__main__":
    import json
    from signals import generate_all_accounts, to_jsonable

    accounts = to_jsonable(generate_all_accounts())
    breakdowns = score_all(accounts)
    breakdowns.sort(key=lambda b: b.final_score, reverse=True)

    print(f"\n{'='*70}")
    print(f"TOP 10 ACCOUNTS BY SCORE")
    print(f"{'='*70}")
    for b in breakdowns[:10]:
        flags = f" [{', '.join(b.flags)}]" if b.flags else ""
        print(f"  {b.final_score:5.1f}  {b.account_name:<35} "
              f"sigs={b.distinct_signal_types}{flags}")

    print(f"\n{'='*70}")
    print(f"BOTTOM 5 (sanity check)")
    print(f"{'='*70}")
    for b in breakdowns[-5:]:
        flags = f" [{', '.join(b.flags)}]" if b.flags else ""
        print(f"  {b.final_score:5.1f}  {b.account_name:<35} "
              f"sigs={b.distinct_signal_types}{flags}")

    # Score distribution
    scores = [b.final_score for b in breakdowns]
    print(f"\nScore distribution: min={min(scores):.1f} "
          f"median={sorted(scores)[len(scores)//2]:.1f} "
          f"max={max(scores):.1f}")

"""
tiering.py
----------
Turns a ScoreBreakdown into an action tier with explicit reasoning.

The point of this module is the GUARDRAILS, not the thresholds. Anyone can
write `if score > 70: tier_1`. The interesting question is: when do we
explicitly REFUSE to auto-act?

Four tiers:

    TIER_1 - "Act now, executive outbound"
        High score, multiple signals, no conflicts, ICP fit strong.
        AE gets a Slack ping; account gets executive-level outreach drafted
        with specific reason-to-believe pulled from winning signals.

    TIER_2 - "Nurture sequence"
        Real signals, but not enough density or recency to justify a
        high-touch motion. Drop into a multi-touch nurture; revisit when
        new signals fire.

    TIER_3 - "Monitor, do not touch"
        Weak/stale signals or marginal ICP fit. We watch, we don't act.

    TIER_4 - "HUMAN REVIEW - do not auto-act"   <-- the centerpiece
        Score might be high, but something is wrong. Conflicting signals,
        confidence too low, or the account is in a state where cold
        outbound burns the relationship. A human has to look.

Why Tier 4 matters: the most expensive failure mode for an ABM scoring
system is NOT a missed account. It's auto-firing outreach to a high-score
account that's actually in turmoil (post-layoff), in transition (champion
just left), or being misread (a single noisy signal). Burning the account
costs more than the missed outreach saves. So we'd rather let a human
look at it.
"""

from dataclasses import dataclass


# ===========================================================================
# Threshold and guardrail config
# ===========================================================================

TIER_THRESHOLDS = {
    # (min_score_inclusive, tier_name)
    # Conservative thresholds - we'd rather miss a marginal account than
    # false-positive into one. Tier 1 demands a near-perfect score AND clean
    # guardrails; below 25 we don't bother monitoring at all.
    "tier_1": 90,
    "tier_2": 50,
    "tier_3": 25,
    # below tier_3 = do not score, ignore
}

# Guardrail rules - each is a function that returns (triggered, reason).
# If any guardrail fires, the account goes to TIER_4 regardless of score.

GUARDRAIL_RULES = [
    # (rule_name, description)
    ("conflicting_news",
     "Recent negative news (layoff/restructure/exec departure) within "
     "60 days conflicts with positive buying signals."),
    ("single_source_high_score",
     "Score is high but only one signal type is firing. Risk of false "
     "positive from a noisy source."),
    ("low_confidence_signals_only",
     "All signals have confidence < 0.6. Likely noise, not real intent."),
    ("low_icp_fit_high_score",
     "Score is high but ICP fit is weak. Probably a hiring blip, not a "
     "real buyer."),
    ("missing_critical_data",
     "Missing employee count or domain - can't verify the account is "
     "real before reaching out."),
    ("stale_signals_only",
     "All signals are >120 days old. Whatever was happening has passed."),
]


# ===========================================================================
# Guardrail implementations
# ===========================================================================

def _check_conflicting_news(account: dict, breakdown) -> tuple:
    """Negative news within 60 days = conflict."""
    for s in account["signals"]:
        meta = s.get("metadata") or {}
        if (meta.get("polarity") == "negative" and s["days_ago"] <= 60):
            return True, (f"Negative news {s['days_ago']}d ago: '{s['value']}' "
                          f"contradicts positive signals.")
    return False, None


def _check_single_source_high_score(account: dict, breakdown) -> tuple:
    if breakdown.distinct_signal_types <= 1 and breakdown.final_score >= 30:
        return True, (f"Score {breakdown.final_score} based on only "
                      f"{breakdown.distinct_signal_types} signal type(s). "
                      f"Need corroboration before auto-acting.")
    return False, None


def _check_low_confidence_signals_only(account: dict, breakdown) -> tuple:
    if not account["signals"]:
        return False, None
    confidences = [s["confidence"] for s in account["signals"]]
    if max(confidences) < 0.6:
        return True, (f"All {len(confidences)} signals have confidence < 0.6 "
                      f"(max={max(confidences):.2f}). Likely noise.")
    return False, None


def _check_low_icp_fit_high_score(account: dict, breakdown) -> tuple:
    if breakdown.icp_fit < 0.55 and breakdown.final_score >= 40:
        return True, (f"ICP fit {breakdown.icp_fit:.2f} is below threshold "
                      f"despite score {breakdown.final_score}. Hiring blip "
                      f"or wrong-buyer profile.")
    return False, None


def _check_missing_critical_data(account: dict, breakdown) -> tuple:
    """Only flag this guardrail when it actually matters - i.e., we'd
    otherwise be acting on the account."""
    if breakdown.final_score < 40:
        return False, None
    missing = []
    if not account.get("domain"):
        missing.append("domain")
    if account.get("employee_count") is None:
        missing.append("employee_count")
    if missing:
        return True, (f"Missing critical fields: {', '.join(missing)}. "
                      f"Verify account exists before outreach.")
    return False, None


def _check_stale_signals_only(account: dict, breakdown) -> tuple:
    if not account["signals"]:
        return False, None
    min_age = min(s["days_ago"] for s in account["signals"])
    if min_age > 120:
        return True, (f"Freshest signal is {min_age}d old. Whatever was "
                      f"happening is over.")
    return False, None


GUARDRAIL_CHECKS = {
    "conflicting_news":           _check_conflicting_news,
    "single_source_high_score":   _check_single_source_high_score,
    "low_confidence_signals_only": _check_low_confidence_signals_only,
    "low_icp_fit_high_score":     _check_low_icp_fit_high_score,
    "missing_critical_data":      _check_missing_critical_data,
    "stale_signals_only":         _check_stale_signals_only,
}


# ===========================================================================
# Tiering
# ===========================================================================

@dataclass
class TierAssignment:
    account_name: str
    tier: str                  # "tier_1", "tier_2", "tier_3", "tier_4", "ignore"
    final_score: float
    action: str                # human-readable next action
    reasoning: list            # list of strings explaining the assignment
    guardrails_fired: list     # list of (rule_name, reason) tuples
    breakdown: object          # the original ScoreBreakdown


TIER_ACTIONS = {
    "tier_1": "ACT NOW - executive outbound, draft tailored first-touch",
    "tier_2": "NURTURE - enroll in multi-touch sequence, revisit on new signal",
    "tier_3": "MONITOR - watch only, no outreach",
    "tier_4": "HUMAN REVIEW - do not auto-act, route to ops queue",
    "ignore": "IGNORE - insufficient signal or fit",
}


def assign_tier(account: dict, breakdown) -> TierAssignment:
    """Score -> tier with guardrail checks."""
    reasoning = []
    guardrails_fired = []

    # Run all guardrails first - any fire = tier 4
    for rule_name, check_fn in GUARDRAIL_CHECKS.items():
        triggered, reason = check_fn(account, breakdown)
        if triggered:
            guardrails_fired.append((rule_name, reason))

    if guardrails_fired:
        reasoning.append(
            f"{len(guardrails_fired)} guardrail(s) fired - routed to human review."
        )
        for rule_name, reason in guardrails_fired:
            reasoning.append(f"  [{rule_name}] {reason}")
        return TierAssignment(
            account_name=breakdown.account_name,
            tier="tier_4",
            final_score=breakdown.final_score,
            action=TIER_ACTIONS["tier_4"],
            reasoning=reasoning,
            guardrails_fired=guardrails_fired,
            breakdown=breakdown,
        )

    # No guardrails fired - assign by score
    score = breakdown.final_score
    if score >= TIER_THRESHOLDS["tier_1"]:
        tier = "tier_1"
        reasoning.append(f"Score {score} >= {TIER_THRESHOLDS['tier_1']}; "
                         f"all guardrails clear.")
    elif score >= TIER_THRESHOLDS["tier_2"]:
        tier = "tier_2"
        reasoning.append(f"Score {score} in nurture range "
                         f"({TIER_THRESHOLDS['tier_2']}-{TIER_THRESHOLDS['tier_1']}).")
    elif score >= TIER_THRESHOLDS["tier_3"]:
        tier = "tier_3"
        reasoning.append(f"Score {score} weak - monitor only.")
    else:
        tier = "ignore"
        reasoning.append(f"Score {score} below threshold; ignore.")

    return TierAssignment(
        account_name=breakdown.account_name,
        tier=tier,
        final_score=score,
        action=TIER_ACTIONS[tier],
        reasoning=reasoning,
        guardrails_fired=guardrails_fired,
        breakdown=breakdown,
    )


def tier_all(accounts: list, breakdowns: list) -> list:
    """accounts and breakdowns must be aligned by index."""
    return [assign_tier(a, b) for a, b in zip(accounts, breakdowns)]


if __name__ == "__main__":
    from signals import generate_all_accounts, to_jsonable
    from scoring import score_all

    accounts = to_jsonable(generate_all_accounts())
    breakdowns = score_all(accounts)
    tiers = tier_all(accounts, breakdowns)

    # Sort by tier, then by score descending
    tier_order = {"tier_1": 0, "tier_4": 1, "tier_2": 2, "tier_3": 3, "ignore": 4}
    tiers.sort(key=lambda t: (tier_order[t.tier], -t.final_score))

    counts = {}
    for t in tiers:
        counts[t.tier] = counts.get(t.tier, 0) + 1

    print(f"\n{'='*72}")
    print(f"TIER DISTRIBUTION")
    print(f"{'='*72}")
    for tier in ["tier_1", "tier_2", "tier_3", "tier_4", "ignore"]:
        print(f"  {tier:<10} {counts.get(tier, 0):>3}  - {TIER_ACTIONS[tier]}")

    print(f"\n{'='*72}")
    print(f"TIER 1 - ACT NOW")
    print(f"{'='*72}")
    for t in [x for x in tiers if x.tier == "tier_1"]:
        print(f"  {t.final_score:5.1f}  {t.account_name}")

    print(f"\n{'='*72}")
    print(f"TIER 4 - HUMAN REVIEW (the guardrail catches)")
    print(f"{'='*72}")
    for t in [x for x in tiers if x.tier == "tier_4"]:
        rule_names = [r[0] for r in t.guardrails_fired]
        print(f"  {t.final_score:5.1f}  {t.account_name:<35} -> {', '.join(rule_names)}")

"""
value_props.py
--------------
Ranks TigerData's value props for an account based on which signals fired.

Four wedges, mapped to the signal patterns that should trigger each:

    sensor_data_scale_outgrown      - Current TSDB hitting cardinality/latency
                                      walls. Triggers on InfluxDB displacement,
                                      custom Cassandra builds, JD mentions of
                                      "high-cardinality time-series."

    historian_modernization         - Legacy historians (PI, Wonderware) that
                                      can't feed modern analytics. Triggers on
                                      OSIsoft PI in stack/JD, utility/grid
                                      industry, "SCADA modernization" mentions.

    postgres_consolidation          - Already on Postgres - extending to TSDB
                                      means one less system. Triggers on
                                      Postgres in stack, "Postgres at scale"
                                      JD content, website mentions of Postgres.

    iiot_program_acceleration       - New IIoT/predictive maintenance/digital
                                      twin initiative needs infra that won't
                                      become the bottleneck. Triggers on news
                                      about IIoT rollouts, new digital
                                      leadership hires, predictive maintenance
                                      mentions in scrape/news.
"""

from dataclasses import dataclass


VALUE_PROP_SUMMARIES = {
    "sensor_data_scale_outgrown": {
        "short": "rebuilding time-series infrastructure that's outgrown the current TSDB",
        "diagnostic_focus": "cardinality limits, query latency at volume, "
                            "and what breaks first as sensor count grows",
    },
    "historian_modernization": {
        "short": "modernizing legacy historians so plant data can feed analytics and ML",
        "diagnostic_focus": "middleware costs, data accessibility outside the "
                            "control room, and how new plants get onboarded",
    },
    "postgres_consolidation": {
        "short": "extending the existing Postgres footprint to time-series instead "
                "of running a parallel TSDB",
        "diagnostic_focus": "operational overhead of running two database "
                            "stacks, hiring constraints, and where the "
                            "duplication is most painful",
    },
    "iiot_program_acceleration": {
        "short": "giving a new IIoT or predictive maintenance program data "
                "infrastructure that won't become the bottleneck",
        "diagnostic_focus": "what the program needs to prove in year one, "
                            "where the data infra decision sits in the "
                            "rollout, and which constraints are already visible",
    },
}


@dataclass
class ValuePropMatch:
    value_prop_id: str
    score: float           # 0-100, how well this wedge matches
    reasons: list          # list of strings explaining why it ranked
    signals_supporting: list  # list of signal types that supported the match


# ---------------------------------------------------------------------------
# Per-wedge scoring functions
# ---------------------------------------------------------------------------

def _score_sensor_data_scale_outgrown(account: dict, breakdown) -> ValuePropMatch:
    score = 0.0
    reasons = []
    sigs = []

    for s in account["signals"]:
        meta = s.get("metadata") or {}
        # InfluxDB displacement is the canonical trigger
        if meta.get("current_stack") == "InfluxDB":
            score += 35
            reasons.append("InfluxDB detected as current stack (cardinality wall risk)")
            sigs.append(s["type"])
        elif meta.get("current_stack") == "custom Cassandra":
            score += 22
            reasons.append("Custom Cassandra build (operational burden)")
            sigs.append(s["type"])
        elif meta.get("risk_class") == "displacement_risk_high":
            score += 25
            reasons.append("High displacement risk on current stack")
            sigs.append(s["type"])

        # JD keywords for cardinality / TSDB
        for kw in meta.get("high_value_keywords", []):
            if kw in ("high-cardinality time-series", "InfluxDB", "TimescaleDB"):
                score += 15
                reasons.append(f"JD mentions '{kw}'")
                sigs.append(s["type"])

        # Website scrape extraction
        if "cardinality" in s["value"].lower():
            score += 20
            reasons.append("Engineering blog discusses cardinality issues")
            sigs.append(s["type"])

    # Manufacturing and logistics archetypes generate the most sensor volume
    if account["archetype"] in ("manufacturing", "logistics_fleet"):
        score *= 1.1

    return ValuePropMatch(
        value_prop_id="sensor_data_scale_outgrown",
        score=min(score, 100),
        reasons=reasons,
        signals_supporting=list(set(sigs)),
    )


def _score_historian_modernization(account: dict, breakdown) -> ValuePropMatch:
    score = 0.0
    reasons = []
    sigs = []

    for s in account["signals"]:
        meta = s.get("metadata") or {}
        # OSIsoft PI is the canonical trigger
        if meta.get("current_stack") == "OSIsoft PI":
            score += 40
            reasons.append("OSIsoft PI detected as current stack")
            sigs.append(s["type"])

        # JD keyword tells
        for kw in meta.get("high_value_keywords", []):
            if kw in ("OSIsoft PI", "PI Historian", "SCADA modernization"):
                score += 20
                reasons.append(f"JD mentions '{kw}'")
                sigs.append(s["type"])

        # Website scrape
        if "historian" in s["value"].lower() or "legacy" in s["value"].lower():
            score += 15
            reasons.append("Website signals legacy historian modernization")
            sigs.append(s["type"])

    # Industries where historians dominate
    if account["industry"] in ("utility/grid", "nuclear/power", "oil & gas",
                                "metals/heavy", "chemicals", "pulp/paper"):
        score *= 1.15
        if score > 0:
            reasons.append(f"Industry ({account['industry']}) is historian-heavy")

    return ValuePropMatch(
        value_prop_id="historian_modernization",
        score=min(score, 100),
        reasons=reasons,
        signals_supporting=list(set(sigs)),
    )


def _score_postgres_consolidation(account: dict, breakdown) -> ValuePropMatch:
    score = 0.0
    reasons = []
    sigs = []

    for s in account["signals"]:
        meta = s.get("metadata") or {}
        # Already on Postgres
        if meta.get("current_stack") == "self-hosted Postgres":
            score += 40
            reasons.append("Already on self-hosted Postgres - upsell path")
            sigs.append(s["type"])

        # JD content
        for kw in meta.get("high_value_keywords", []):
            if kw in ("Postgres at scale", "Kafka + Postgres"):
                score += 25
                reasons.append(f"JD mentions '{kw}'")
                sigs.append(s["type"])

        # Website scrape
        if "postgres" in s["value"].lower():
            score += 15
            reasons.append("Engineering content references Postgres")
            sigs.append(s["type"])

    return ValuePropMatch(
        value_prop_id="postgres_consolidation",
        score=min(score, 100),
        reasons=reasons,
        signals_supporting=list(set(sigs)),
    )


def _score_iiot_program_acceleration(account: dict, breakdown) -> ValuePropMatch:
    score = 0.0
    reasons = []
    sigs = []

    for s in account["signals"]:
        meta = s.get("metadata") or {}

        # News and events about IIoT initiatives
        if s["type"] == "news_events" and meta.get("polarity") == "positive":
            text = s["value"].lower()
            if any(k in text for k in ("digital transformation", "predictive maintenance",
                                        "iiot", "modernization")):
                score += 25
                reasons.append(f"News: '{s['value']}'")
                sigs.append(s["type"])

        # New digital/data leadership hires
        if s["type"] == "new_in_seat":
            title = (meta.get("title_raw") or "").lower()
            if any(k in title for k in ("digital", "iiot", "data platform")):
                score += 20
                reasons.append(f"New leadership: {meta.get('title_raw')}")
                sigs.append(s["type"])

        # JD content for IIoT / predictive maintenance
        for kw in meta.get("high_value_keywords", []):
            if kw in ("IIoT", "predictive maintenance", "digital twin",
                      "sensor data pipelines"):
                score += 15
                reasons.append(f"JD mentions '{kw}'")
                sigs.append(s["type"])

        # Website scrape
        text = s["value"].lower()
        if "predictive maintenance" in text or "sensor data" in text:
            score += 15
            reasons.append("Website signals active IIoT program")
            sigs.append(s["type"])

    return ValuePropMatch(
        value_prop_id="iiot_program_acceleration",
        score=min(score, 100),
        reasons=reasons,
        signals_supporting=list(set(sigs)),
    )


WEDGE_SCORERS = [
    _score_sensor_data_scale_outgrown,
    _score_historian_modernization,
    _score_postgres_consolidation,
    _score_iiot_program_acceleration,
]


def rank_value_props(account: dict, breakdown) -> list:
    """Returns all four wedges ranked by match score, descending."""
    matches = [scorer(account, breakdown) for scorer in WEDGE_SCORERS]
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def top_value_prop(account: dict, breakdown):
    """Returns the single best wedge match. None if no wedge has any signal."""
    matches = rank_value_props(account, breakdown)
    if matches[0].score == 0:
        return None
    return matches[0]


if __name__ == "__main__":
    from signals import generate_all_accounts, to_jsonable
    from scoring import score_all

    accounts = to_jsonable(generate_all_accounts())
    breakdowns = score_all(accounts)

    print(f"\n{'='*72}")
    print(f"VALUE PROP MATCHES (top wedge per account)")
    print(f"{'='*72}")
    for a, b in zip(accounts, breakdowns):
        top = top_value_prop(a, b)
        if top:
            print(f"  {a['name']:<35}  -> {top.value_prop_id:<32} "
                  f"({top.score:.0f})")
        else:
            print(f"  {a['name']:<35}  -> NO WEDGE MATCH")

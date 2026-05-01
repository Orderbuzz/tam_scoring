"""
email_gen.py
------------
Outreach email + 3 discovery questions for a Tier 1 account, keyed to the
top matched value prop.

Architecture (modeled on a known-good Rula equivalent):

  1. generate_fallback_email()  - deterministic, no API needed. Composes a
     specific, on-tone email from per-wedge phrase functions. This is the
     baseline output - the LLM is an UPGRADE, not a dependency.

  2. generate_with_llm()        - calls Anthropic API with the fallback
     passed in as a style guardrail (not a template). Model is told to
     produce something in the same voice but tuned to the specific account.
     If the API key is missing or the call fails, returns the fallback.

The voice we're going for: a sharp AE who has actually sold to industrial
buyers. Not a vendor pitch. Not a "we partner with leading manufacturers"
landing page. Specific, low-pressure, points at one operational reality
the buyer recognizes.

The negative phrase list at the bottom is the single most important part
of the LLM prompt - it bans the AI-email tells that get this kind of
message marked as spam.
"""

import json
import os
from typing import Optional

from value_props import VALUE_PROP_SUMMARIES, ValuePropMatch


# ---------------------------------------------------------------------------
# Anthropic client (optional - works without)
# ---------------------------------------------------------------------------

_anthropic_client = None
try:
    if os.getenv("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        _anthropic_client = Anthropic()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_lower(value: Optional[str]) -> str:
    return (value or "").lower()


def _signal_summary(account: dict) -> dict:
    """Pull out the specific signal facts the email should reference."""
    summary = {
        "new_hire_title": None,
        "open_reqs": None,
        "jd_keywords": [],
        "current_stack": None,
        "stack_risk": None,
        "news_event": None,
        "scrape_finding": None,
        "explicit_jd_mention": False,
    }
    for s in account["signals"]:
        meta = s.get("metadata") or {}
        if s["type"] == "new_in_seat" and not summary["new_hire_title"]:
            summary["new_hire_title"] = meta.get("title_raw")
        if s["type"] == "team_hiring" and not summary["open_reqs"]:
            summary["open_reqs"] = meta.get("req_count")
        if s["type"] == "job_posting_content":
            summary["jd_keywords"].extend(meta.get("high_value_keywords", []))
            if meta.get("explicit_competitor_or_us"):
                summary["explicit_jd_mention"] = True
        if s["type"] == "tech_stack":
            summary["current_stack"] = meta.get("current_stack")
            summary["stack_risk"] = meta.get("risk_class")
        if s["type"] == "news_events" and meta.get("polarity") == "positive":
            summary["news_event"] = s["value"]
        if s["type"] == "website_scrape" and not summary["scrape_finding"]:
            summary["scrape_finding"] = s["value"].replace("LLM extract: ", "")
    return summary


# ---------------------------------------------------------------------------
# Per-wedge fallback components
# ---------------------------------------------------------------------------

def _subject_line(account: dict, top_match: ValuePropMatch, sig: dict) -> str:
    company = account["name"]

    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        if sig["current_stack"] == "InfluxDB":
            return f"InfluxDB at {company} scale"
        return f"Time-series data at {company}"

    if top_match.value_prop_id == "historian_modernization":
        return f"Getting plant data out of the historian at {company}"

    if top_match.value_prop_id == "postgres_consolidation":
        return f"One database stack at {company}"

    return f"Data infrastructure for the IIoT program at {company}"


def _opening_observation(account: dict, top_match: ValuePropMatch, sig: dict) -> str:
    """Lead with one specific, conversational observation tied to the signals."""
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        if sig["current_stack"] == "InfluxDB" and sig["open_reqs"]:
            return (f"Saw the {sig['open_reqs']} open data platform reqs and "
                    f"that you're running InfluxDB")
        if sig["current_stack"] == "InfluxDB":
            return "Saw you're running InfluxDB for the sensor pipeline"
        if sig["new_hire_title"]:
            return f"Noticed {sig['new_hire_title']} joined recently"
        return "Saw the recent hiring around the data platform team"

    if top_match.value_prop_id == "historian_modernization":
        if sig["current_stack"] == "OSIsoft PI":
            return "Saw OSIsoft PI is still doing the heavy lifting on the plant side"
        return "Given the historian-heavy setup most plants in the industry still run"

    if top_match.value_prop_id == "postgres_consolidation":
        if sig["current_stack"] == "self-hosted Postgres":
            return "Saw you're already running self-hosted Postgres"
        if sig["jd_keywords"] and any("Postgres" in k for k in sig["jd_keywords"]):
            return "Saw the Postgres-heavy hiring in the data platform reqs"
        return "Given the Postgres footprint already in place"

    # iiot_program_acceleration
    if sig["news_event"]:
        return f"Saw the announcement about {sig['news_event'].lower()}"
    if sig["new_hire_title"] and "digital" in sig["new_hire_title"].lower():
        return f"Noticed {sig['new_hire_title']} joined recently"
    return "Given the IIoT program ramping up"


def _problem_interpretation(account: dict, top_match: ValuePropMatch, sig: dict) -> str:
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        if sig["current_stack"] == "InfluxDB":
            return ("I'd guess the team is starting to feel the cardinality "
                    "wall as the sensor count grows")
        return ("I'd imagine the question is whether the current TSDB still "
                "fits where the data volume is heading")

    if top_match.value_prop_id == "historian_modernization":
        return ("I'd imagine the harder part is getting that data out to "
                "anything beyond the control room without rebuilding "
                "middleware every time")

    if top_match.value_prop_id == "postgres_consolidation":
        return ("I'd guess running a separate TSDB alongside Postgres is "
                "becoming more operational overhead than it's worth")

    return ("I'd imagine the question is whether the data infrastructure "
            "underneath the program will keep up as it scales past the pilot")


def _operational_consequence(account: dict, top_match: ValuePropMatch,
                             sig: dict, is_tier_1: bool) -> str:
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        return ("That usually shows up as queries getting slower at the "
                "exact moment the data team needs them, downsampling that "
                "loses the resolution analysts actually wanted, or engineers "
                "rebuilding the same pipeline patches over and over.")

    if top_match.value_prop_id == "historian_modernization":
        return ("That tends to show up as data scientists waiting on extracts, "
                "every new analytics use case requiring a custom integration, "
                "and the historian becoming the bottleneck for anything "
                "downstream of operations.")

    if top_match.value_prop_id == "postgres_consolidation":
        return ("That usually means two on-call rotations, two backup "
                "strategies, and a hiring profile that has to cover both "
                "stacks at every level.")

    # iiot_program_acceleration
    return ("That tends to show up as the pilot working great on a few "
            "lines, then the rollout hitting a wall when the data layer "
            "can't keep up with the next 10 plants or the next 50 "
            "thousand sensors.")


def _reframe_line(account: dict, top_match: ValuePropMatch, sig: dict) -> str:
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        return ("The real question usually isn't whether to scale the "
                "current TSDB - it's whether the architecture is the right "
                "one for where the workload is headed.")

    if top_match.value_prop_id == "historian_modernization":
        return ("The real question is usually less about replacing the "
                "historian and more about what sits next to it so the "
                "rest of the business can actually use the data.")

    if top_match.value_prop_id == "postgres_consolidation":
        return ("In setups like that, the wedge isn't a new database - "
                "it's making the one you already operate handle the "
                "workload you've been running somewhere else.")

    return ("The real question is usually less about the use case and "
            "more about whether the infra underneath it will still work "
            "at 10x the data.")


def _close_question(account: dict, top_match: ValuePropMatch, sig: dict) -> str:
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        if sig["current_stack"] == "InfluxDB":
            return ("Curious - is the cardinality story showing up yet, or "
                    "is it more query latency at this point?")
        return "Where is the current TSDB feeling the most pressure right now?"

    if top_match.value_prop_id == "historian_modernization":
        return ("Where is the historian creating the most friction today - "
                "getting data out, onboarding new plants, or feeding the "
                "analytics layer?")

    if top_match.value_prop_id == "postgres_consolidation":
        return ("Is the team already thinking about consolidating, or is "
                "the second stack still pulling its weight?")

    return ("As the program ramps, where do you see the data infrastructure "
            "decision sitting in the rollout sequence?")


def _build_discovery_questions(account: dict, top_match: ValuePropMatch,
                                sig: dict) -> list:
    """Three questions: diagnosis, segmentation, decision criteria."""
    if top_match.value_prop_id == "sensor_data_scale_outgrown":
        return [
            "Where is the current TSDB hitting limits first - "
            "ingest rate, query latency, or storage cost at retention?",
            "Are certain workloads (real-time dashboards vs historical "
            "analytics) feeling the pressure more than others?",
            "When the team weighs scaling the current system vs migrating, "
            "what's the criteria that actually matters - cost, engineering "
            "effort, or risk to the production workload?",
        ]

    if top_match.value_prop_id == "historian_modernization":
        return [
            "How does data currently get from the historian to the "
            "analytics or ML teams who need it downstream?",
            "Are some plants or sites harder to onboard into the modern "
            "data stack than others, and what's driving that?",
            "When you weigh modernizing the historian vs adding a layer "
            "alongside it, what's pulling each direction?",
        ]

    if top_match.value_prop_id == "postgres_consolidation":
        return [
            "What's the current TSDB doing that Postgres can't, and how "
            "much of that gap still exists?",
            "Where does the operational overhead of running two stacks "
            "show up most - on-call, hiring, backup, something else?",
            "If consolidation were on the table, would the bigger blocker "
            "be technical risk to the production workload, or "
            "organizational ownership of the migration?",
        ]

    return [
        "What does the IIoT program need to prove in year one for the "
        "infrastructure investment to be considered successful?",
        "Are there specific plants, lines, or use cases driving the "
        "current rollout, or is it broader than that?",
        "How is the team thinking about the data layer - build, buy, "
        "or extend something already in place?",
    ]


# ---------------------------------------------------------------------------
# Fallback composer
# ---------------------------------------------------------------------------

def _first_name(contact: Optional[str]) -> Optional[str]:
    if not contact:
        return None
    return contact.split()[0].strip()


def generate_fallback_email(account: dict, top_match: ValuePropMatch,
                            is_tier_1: bool) -> dict:
    """Deterministic fallback. Composes a usable email without any API calls."""
    sig = _signal_summary(account)
    first_name = _first_name(account.get("contact"))
    greeting = f"Hi {first_name}," if first_name else "Hi,"

    subject = _subject_line(account, top_match, sig)
    opening = _opening_observation(account, top_match, sig)
    problem = _problem_interpretation(account, top_match, sig)
    consequence = _operational_consequence(account, top_match, sig, is_tier_1)
    reframe = _reframe_line(account, top_match, sig)
    close = _close_question(account, top_match, sig)
    questions = _build_discovery_questions(account, top_match, sig)

    body = (
        f"{greeting}\n\n"
        f"{opening}, {problem}.\n"
        f"{consequence}\n\n"
        f"{reframe}\n\n"
        f"{close}"
    )

    return {
        "email_subject": subject,
        "email_body": body,
        "discovery_questions": questions,
        "wedge": top_match.value_prop_id,
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# LLM upgrade path
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are writing outbound emails for an AE selling TigerData (a time-series database built on Postgres) to engineering, data platform, and digital leaders at industrial companies - power utilities, manufacturers, logistics fleets, oil & gas, chemicals.

The buyer is technical. They run sensor data infrastructure, plant historians, IIoT programs, fleet telematics. They have read every "leverage your data" landing page in existence and they hate them.

Write:
1. one first-touch email
2. exactly 3 discovery questions

CORE RULE:
The email must sound like an AE who has actually shipped time-series infrastructure at industrial scale - someone who knows what InfluxDB cardinality issues feel like, what OSIsoft PI is, why running two database stacks hurts. Not a vendor pitch. Not a marketing voice.

EMAIL GOAL:
Earn a reply by showing a specific, technically-credible point of view based on the account's actual signals.

TONE:
For Tier 1 (strong fit, high score, no guardrails):
- be directional and confident
- make a sharper inference based on the signals
- sound like someone who has seen this exact problem before

EMAIL STRUCTURE:
1. Opening
- Begin with one specific, conversational observation tied to a real signal from the account
- It should sound like something said to a colleague, not written for a website
- Soft language is fine ("I'd guess", "I'd imagine") but not required
- Do NOT list the signals. Refer to ONE concrete fact.

2. Interpretation
- Translate that observation into one likely engineering or operational problem
- Stay grounded. Don't speculate beyond what the signals support.

3. Operational consequence
- Describe one concrete way the problem shows up in the engineering team's day-to-day
- Make it visual: queries getting slower, on-call rotations doubling, pipelines getting rebuilt, data scientists waiting on extracts, the historian becoming a bottleneck
- This is the part that proves you understand the buyer's world.

4. Reframe
- Position the underlying issue (architecture fit, operational overhead, infrastructure ceiling) - not the product
- Introduce the matched value proposition INDIRECTLY
- One wedge only. Don't list the others.

5. Close
- One low-friction, technically-grounded question
- No meeting ask
- No "would love to chat"

STYLE RULES:
- 90 to 140 words for the body
- Use contractions
- Prefer "the team" over "your organization"
- Specific over abstract every time

DO NOT use any of these (they are AI-email tells and will get the message ignored):
- "I noticed that..."
- "leverage your data"
- "unlock insights"
- "digital transformation journey"
- "real-time visibility into your operations"
- "single source of truth"
- "next-generation"
- "in today's data-driven landscape"
- "we've seen", "we often see", "many companies"
- "worth comparing notes"
- "it felt worth reaching out"
- "there may be a fit around"
- "I hope this finds you well"
- exclamation points
- em dashes used as drama
- bullet points
- adjectives that don't add information ("powerful", "robust", "scalable", "innovative")

DO NOT:
- invent facts not in the signals
- claim the account "should" do anything
- mention TigerData product features by name
- mention competitors by name unless they appear in the signals
- summarize the whole account
- explain multiple value propositions

DISCOVERY QUESTION RULES:
Exactly 3 questions, in this order:
- Q1: diagnosis (what is actually happening technically?)
- Q2: segmentation (where is it most visible - which workloads, plants, teams?)
- Q3: decision criteria (how is the buyer evaluating the tradeoff?)

Each question must:
- be specific to the matched value prop
- reflect at least one fact from the account signals when possible
- be one clean idea
- not start with "what are your priorities" or any vague broad framing

OUTPUT:
Return valid JSON with exactly these keys:
{
  "email_subject": "...",
  "email_body": "...",
  "discovery_questions": ["...", "...", "..."]
}
"""


def generate_with_llm(account: dict, top_match: ValuePropMatch,
                      is_tier_1: bool) -> dict:
    """LLM-upgraded version. Falls back to deterministic on any failure."""
    fallback = generate_fallback_email(account, top_match, is_tier_1)

    if _anthropic_client is None:
        return fallback

    payload = {
        "account": {
            "name": account["name"],
            "domain": account.get("domain"),
            "industry": account["industry"],
            "archetype": account["archetype"],
            "employee_count": account.get("employee_count"),
            "notes": account.get("notes"),
        },
        "signals": account["signals"],
        "tier_1": is_tier_1,
        "top_value_prop": {
            "id": top_match.value_prop_id,
            "summary": VALUE_PROP_SUMMARIES[top_match.value_prop_id]["short"],
            "diagnostic_focus": VALUE_PROP_SUMMARIES[top_match.value_prop_id]["diagnostic_focus"],
            "match_score": top_match.score,
            "reasons_matched": top_match.reasons,
        },
        "fallback_for_style_reference_only": fallback,
    }

    user_prompt = (
        "Write one first-touch email and exactly 3 discovery questions for "
        "the account below. Lead with the top matched value prop only. "
        "Use the fallback as a style reference for voice and structure, "
        "not as a template to copy verbatim. Stay grounded in the actual "
        "signals.\n\n"
        f"Input:\n{json.dumps(payload, indent=2, default=str)}"
    )

    try:
        response = _anthropic_client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract the text content
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        # The model may wrap the JSON in prose or code fences - strip them.
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:].strip()
            text = text.rsplit("```", 1)[0].strip()

        parsed = json.loads(text)

        questions = parsed.get("discovery_questions", [])
        if not isinstance(questions, list) or len(questions) != 3:
            questions = fallback["discovery_questions"]

        return {
            "email_subject": parsed.get("email_subject", fallback["email_subject"]),
            "email_body": parsed.get("email_body", fallback["email_body"]),
            "discovery_questions": questions,
            "wedge": top_match.value_prop_id,
            "source": "llm",
        }

    except Exception as e:
        print(f"LLM call failed, using fallback: {e}")
        return fallback


if __name__ == "__main__":
    from signals import generate_all_accounts, to_jsonable
    from scoring import score_all
    from tiering import tier_all
    from value_props import top_value_prop

    accounts = to_jsonable(generate_all_accounts())
    breakdowns = score_all(accounts)
    tiers = tier_all(accounts, breakdowns)

    # Find tier 1 accounts and show fallback emails
    tier_1 = [(a, b, t) for a, b, t in zip(accounts, breakdowns, tiers)
              if t.tier == "tier_1"]

    print(f"\nGenerating fallback emails for {len(tier_1)} Tier 1 accounts.\n")
    for a, b, t in tier_1[:3]:
        wedge = top_value_prop(a, b)
        if not wedge:
            continue
        email = generate_fallback_email(a, wedge, is_tier_1=True)
        print(f"{'='*72}")
        print(f"  {a['name']}  (wedge: {wedge.value_prop_id})")
        print(f"{'='*72}")
        print(f"Subject: {email['email_subject']}\n")
        print(email["email_body"])
        print("\nDiscovery questions:")
        for i, q in enumerate(email["discovery_questions"], 1):
            print(f"  {i}. {q}")
        print()

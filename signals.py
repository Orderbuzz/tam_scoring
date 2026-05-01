"""
signals.py
----------
Generates ~45 mock industrial accounts for TigerData's TAM with realistic,
deliberately imperfect signal data.

Three archetypes (because TigerData's ICP is NOT "Series B SaaS"):
  1. heavy_industrial  - Westinghouse-style: nuclear, power, energy, grid
  2. logistics_fleet   - FedEx/Maersk-style: fleet telematics, supply chain
  3. manufacturing     - Tier-1 auto / industrial OEMs: factory floor, IIoT

Six signal types (these are the signals that ACTUALLY matter for industrial
buyers - not G2 page views, not Bombora topic surges for "data warehouse"):
  1. new_in_seat       - Recent leadership hire in data/platform/digital roles
  2. team_hiring       - Open reqs for data eng / platform / IIoT engineers
  3. job_posting_content - JD mentions of TSDB, InfluxDB, OSIsoft PI, etc.
  4. tech_stack        - Detected current stack + displacement risk
  5. news_events       - Press releases, conference speakers, IIoT initiatives
  6. website_scrape    - LLM-extracted signals from careers/eng blog pages

Mess we deliberately inject:
  - Duplicates (same company, slightly different name)
  - Missing fields (some accounts have no website, no employee count, etc.)
  - Conflicting signals (new VP hire + recent layoff news = Tier 4 trigger)
  - Free-text job titles that need normalization
  - Stale signals (recency decay testing)
  - Single-source weak signals (testing the "do not auto-act" guardrail)
"""

from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Optional
import random
import json

# Seed so the demo is reproducible. Reviewers can change this to see variance.
random.seed(42)

# Reference date for all "days ago" calculations. Pinned so signals are
# deterministic across runs.
TODAY = datetime(2026, 4, 30)


# ---------------------------------------------------------------------------
# Archetype templates
# ---------------------------------------------------------------------------
# These are not real companies - names are scrambled or fictional. The shapes
# are realistic for TigerData's TAM.

HEAVY_INDUSTRIAL = [
    ("Westhaus Power Systems", "westhaus-power.com", "nuclear/power", 38000),
    ("Northgrid Energy", "northgridenergy.com", "utility/grid", 22000),
    ("Cascadia Hydro", "cascadiahydro.com", "utility/grid", 4500),
    ("Atlas Nuclear Services", "atlasnuclear.com", "nuclear/power", 11000),
    ("Meridian Power & Light", None, "utility/grid", 31000),  # missing website
    ("Boreal Energy Group", "borealenergy.com", "oil & gas", 18000),
    ("Stratus Wind Holdings", "stratuswind.com", "renewables", 2800),
    ("Continental Refining Co", "contirefining.com", "oil & gas", 9400),
    ("Pacific Rim Geothermal", "pacrimgeo.com", "renewables", 1200),
    ("Ironside Power", "ironsidepower.com", "utility/grid", 7600),
    ("WESTHAUS POWER", "westhaus-power.com", "nuclear/power", None),  # dupe + missing
    ("Helios Solar Industrial", "heliossolar-ind.com", "renewables", 3300),
    ("Granite State Electric", "granitestate-elec.com", "utility/grid", 5100),
    ("Vanguard Reactor Tech", "vanguardreactor.com", "nuclear/power", 2200),
    ("Polaris Grid Services", "polarisgrid.com", "utility/grid", 14000),
]

LOGISTICS_FLEET = [
    ("Redline Freight Systems", "redlinefreight.com", "trucking/logistics", 28000),
    ("Maritime Cargo Holdings", "maritimecargo.com", "shipping/ports", 41000),
    ("SkyBridge Air Cargo", "skybridgeair.com", "air freight", 12000),
    ("ContainerPath Logistics", "containerpath.com", "shipping/ports", 8800),
    ("Northbound Trucking", "northboundtrucking.com", "trucking/logistics", 6200),
    ("GlobalLane Express", None, "trucking/logistics", 19000),  # missing website
    ("Sentinel Rail Freight", "sentinelrail.com", "rail freight", 24000),
    ("Coastline Shipping Co", "coastlineshipping.com", "shipping/ports", 15500),
    ("AeroLogix", "aerologix.io", "air freight", 4100),
    ("Lattice Supply Chain", "latticesupply.com", "3PL/warehousing", 7700),
    ("Redline Freight", "redlinefreight.com", "trucking/logistics", 28000),  # dupe
    ("Pinnacle Last Mile", "pinnaclelastmile.com", "last mile", 3400),
    ("Atlantic Container Group", "atlanticcontainer.com", "shipping/ports", 11200),
    ("Summit Cold Chain", "summitcoldchain.com", "3PL/warehousing", 2600),
]

MANUFACTURING = [
    ("Apex Drivetrain Systems", "apexdrivetrain.com", "auto Tier-1", 17000),
    ("Forge & Foundry Industries", "forgefoundry.com", "metals/heavy", 9300),
    ("PrecisionWorks Manufacturing", "precisionworks-mfg.com", "auto Tier-1", 13500),
    ("Crestline Aerospace Components", "crestlineaero.com", "aerospace", 21000),
    ("Bedrock Cement Group", "bedrockcement.com", "building materials", 8400),
    ("Ironclad Steel Works", "ironcladsteel.com", "metals/heavy", 14000),
    ("Vector Robotics Industrial", "vectorrobotics.com", "industrial OEM", 1800),
    ("Hartwell Pharma Manufacturing", "hartwellpharma.com", "pharma mfg", 6700),
    ("Nordic Pulp & Paper", None, "pulp/paper", 5500),  # missing website
    ("Coronado Chemicals", "coronadochem.com", "chemicals", 11800),
    ("Apex Drivetrain", "apexdrivetrain.com", "auto Tier-1", None),  # dupe + missing
    ("Helix Plastics Manufacturing", "helixplastics.com", "plastics/polymers", 4200),
    ("Fortress Defense Systems", "fortressdefense.com", "defense mfg", 23000),
    ("Quarry Stone Industries", "quarrystone.com", "building materials", 3100),
    ("Voltaic Battery Mfg", "voltaicbattery.com", "energy storage mfg", 2900),
    ("Kestrel Engine Works", "kestrelengine.com", "auto Tier-1", 7800),
]


# ---------------------------------------------------------------------------
# Signal vocabularies
# ---------------------------------------------------------------------------

# Job titles for "new in seat" - intentionally messy free-text variants
DATA_LEADER_TITLES = [
    "VP of Data Platform",
    "vp data platform",  # lowercase variant - tests normalization
    "Chief Digital Officer",
    "Head of Data Engineering",
    "Director, Data Platform & Infrastructure",
    "VP Data & Analytics",
    "Chief Data Officer",
    "Director of IIoT & Sensor Platforms",
    "SVP Engineering, Data Platform",
    "Head of Industrial Data",
    "VP, Digital Transformation",  # weaker signal - more buzzword than buyer
    "Director - Platform Engineering",
]

# Engineering reqs for "team hiring"
DATA_ENG_REQS = [
    "Senior Data Engineer",
    "Staff Platform Engineer",
    "Principal Engineer, Time-Series Infrastructure",
    "Sr. Data Platform Engineer",
    "IIoT Data Engineer",
    "SRE - Data Platform",
    "Lead Engineer, Sensor Data Pipelines",
]

# Job posting content - the keywords that fire signal_strength
HIGH_VALUE_JD_KEYWORDS = [
    "TimescaleDB", "time-series database", "InfluxDB", "OSIsoft PI",
    "PI Historian", "sensor data pipelines", "IIoT", "predictive maintenance",
    "digital twin", "Postgres at scale", "high-cardinality time-series",
    "SCADA modernization", "Kafka + Postgres",
]

MEDIUM_VALUE_JD_KEYWORDS = [
    "real-time analytics", "data platform", "streaming data",
    "telemetry", "observability data", "industrial analytics",
]

# News & events templates
POSITIVE_NEWS = [
    "announces $200M digital transformation initiative",
    "launches predictive maintenance program across {n} facilities",
    "selects {vendor} for IIoT platform modernization",
    "speaking at Hannover Messe on industrial data architecture",
    "ARC Industry Forum keynote on sensor data scaling",
    "PostgresConf industrial track presentation announced",
    "completes acquisition of {target} for fleet telematics",
]

NEGATIVE_NEWS = [
    "announces 8% workforce reduction",
    "delays Q3 earnings, restructuring announced",
    "CEO departure, board reviewing strategy",
    "facility closure announced in {region}",
]

# Tech stack signals - displacement risk is the gold one
TECH_STACK_OPTIONS = [
    ("InfluxDB", "displacement_risk_high"),  # known cardinality complaints
    ("OSIsoft PI", "displacement_risk_medium"),  # legacy historian
    ("self-hosted Postgres", "expansion_opportunity"),  # already on Postgres
    ("custom Cassandra", "displacement_risk_medium"),
    ("AWS Timestream", "displacement_risk_low"),
    ("unknown", None),
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """One observed signal for one account."""
    type: str           # one of the 6 signal types
    value: str          # human-readable signal content
    source: str         # where it came from (e.g., "linkedin", "job_board")
    days_ago: int       # recency
    confidence: float   # 0.0-1.0, how sure we are this signal is real
    metadata: dict = field(default_factory=dict)


@dataclass
class Account:
    """A target account with its observed signals."""
    name: str
    domain: Optional[str]
    industry: str
    archetype: str
    employee_count: Optional[int]
    signals: list = field(default_factory=list)
    # Free-text "notes" field - imperfect, sometimes contradicts signals
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Signal generators
# ---------------------------------------------------------------------------

def _maybe(prob: float) -> bool:
    return random.random() < prob


def gen_new_in_seat(archetype: str) -> Optional[Signal]:
    """New leadership hire signal. Highest-converting B2B signal type."""
    if not _maybe(0.55):
        return None
    title = random.choice(DATA_LEADER_TITLES)
    days_ago = random.choices(
        [random.randint(0, 30), random.randint(31, 90),
         random.randint(91, 180), random.randint(181, 365)],
        weights=[3, 4, 2, 1]
    )[0]
    # "VP, Digital Transformation" is buzzword-heavy, lower confidence
    confidence = 0.6 if "Digital Transformation" in title else 0.9
    return Signal(
        type="new_in_seat",
        value=f"Hired: {title}",
        source="linkedin",
        days_ago=days_ago,
        confidence=confidence,
        metadata={"title_raw": title},
    )


def gen_team_hiring(archetype: str) -> Optional[Signal]:
    """Open data/platform engineering reqs."""
    if not _maybe(0.65):
        return None
    n_reqs = random.choices([1, 2, 3, 4, 6], weights=[3, 3, 2, 1, 1])[0]
    titles = random.sample(DATA_ENG_REQS, k=min(n_reqs, len(DATA_ENG_REQS)))
    return Signal(
        type="team_hiring",
        value=f"{n_reqs} open data/platform reqs: {', '.join(titles[:3])}"
              + ("..." if n_reqs > 3 else ""),
        source="job_board_aggregator",
        days_ago=random.randint(0, 60),
        confidence=0.85,
        metadata={"req_count": n_reqs, "titles": titles},
    )


def gen_job_posting_content(archetype: str) -> Optional[Signal]:
    """Specific keyword content in JDs - the highest-fidelity signal."""
    if not _maybe(0.45):
        return None
    # Mix high-value and medium-value keywords
    n_high = random.choices([0, 1, 2, 3], weights=[3, 4, 2, 1])[0]
    n_med = random.choices([0, 1, 2], weights=[2, 3, 2])[0]
    if n_high == 0 and n_med == 0:
        return None
    high_kws = random.sample(HIGH_VALUE_JD_KEYWORDS, k=n_high)
    med_kws = random.sample(MEDIUM_VALUE_JD_KEYWORDS, k=min(n_med, len(MEDIUM_VALUE_JD_KEYWORDS)))
    keywords = high_kws + med_kws
    # The "TimescaleDB experience a plus" tell - rare and very high signal
    explicit_mention = "TimescaleDB" in high_kws and _maybe(0.4)
    return Signal(
        type="job_posting_content",
        value=f"JD keywords: {', '.join(keywords)}"
              + (" [EXPLICIT TIMESCALEDB MENTION]" if explicit_mention else ""),
        source="job_board_scrape",
        days_ago=random.randint(0, 90),
        confidence=0.95 if explicit_mention else 0.8,
        metadata={
            "high_value_keywords": high_kws,
            "medium_value_keywords": med_kws,
            "explicit_competitor_or_us": explicit_mention,
        },
    )


def gen_tech_stack(archetype: str) -> Optional[Signal]:
    """Detected current stack + displacement risk classification."""
    if not _maybe(0.5):
        return None
    stack, risk = random.choice(TECH_STACK_OPTIONS)
    if risk is None:
        return None
    return Signal(
        type="tech_stack",
        value=f"Detected: {stack} ({risk})",
        source="builtwith_plus_jd_inference",
        days_ago=random.randint(0, 180),
        confidence=0.7,
        metadata={"current_stack": stack, "risk_class": risk},
    )


def gen_news_events(archetype: str) -> Optional[Signal]:
    """Press, conference, M&A. Mix of positive and contradicting negative."""
    if not _maybe(0.55):
        return None
    polarity = random.choices(["positive", "negative"], weights=[7, 2])[0]
    if polarity == "positive":
        template = random.choice(POSITIVE_NEWS)
        text = template.format(
            n=random.choice([3, 12, 47]),
            vendor=random.choice(["AVEVA", "Cognite", "C3.ai", "PTC ThingWorx"]),
            target=random.choice(["FleetIQ", "SensorMesh Inc", "TelemetryCo"]),
        )
    else:
        template = random.choice(NEGATIVE_NEWS)
        text = template.format(region=random.choice(["Ohio", "Bavaria", "Guangdong"]))
    return Signal(
        type="news_events",
        value=text,
        source="press_release_feed",
        days_ago=random.randint(0, 120),
        confidence=0.85,
        metadata={"polarity": polarity},
    )


def gen_website_scrape(archetype: str) -> Optional[Signal]:
    """LLM extraction from careers / eng blog. Sometimes low-confidence."""
    if not _maybe(0.4):
        return None
    extractions = [
        ("careers page emphasizes 'scaling sensor data infrastructure'", 0.85),
        ("engineering blog discusses cardinality issues with current TSDB", 0.9),
        ("careers page mentions 'modernizing legacy historian systems'", 0.8),
        ("about page generic - no clear data platform signal", 0.3),
        ("engineering page mentions Postgres + sensor pipelines", 0.85),
        ("blog references predictive maintenance pilot at scale", 0.75),
        ("careers page lists 'time-series' as core competency", 0.9),
    ]
    extraction, conf = random.choice(extractions)
    return Signal(
        type="website_scrape",
        value=f"LLM extract: {extraction}",
        source="ai_scrape",
        days_ago=random.randint(0, 30),
        confidence=conf,
        metadata={"raw_extraction": extraction},
    )


SIGNAL_GENERATORS = [
    gen_new_in_seat,
    gen_team_hiring,
    gen_job_posting_content,
    gen_tech_stack,
    gen_news_events,
    gen_website_scrape,
]


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_account(name: str, domain: Optional[str], industry: str,
                  archetype: str, emp_count: Optional[int]) -> Account:
    signals = []
    for gen in SIGNAL_GENERATORS:
        sig = gen(archetype)
        if sig is not None:
            signals.append(sig)

    # Inject some accounts with deliberately conflicting signals - this is
    # what trips the Tier 4 "human review" guardrail in tiering.py.
    # ~15% chance for any account to get a contradiction.
    if _maybe(0.15) and signals:
        signals.append(Signal(
            type="news_events",
            value="announces 8% workforce reduction",
            source="press_release_feed",
            days_ago=random.randint(0, 45),
            confidence=0.9,
            metadata={"polarity": "negative", "injected_conflict": True},
        ))

    # Optional free-text notes - sometimes empty, sometimes useful
    notes = None
    if _maybe(0.3):
        notes = random.choice([
            "Met at Hannover Messe 2025, expressed interest in TSDB POC.",
            "Champion left for competitor. Cold restart needed.",
            "AE flagged as 'never returns calls' - 3 attempts in 2024.",
            "Procurement-led buyer. Long cycle.",
            "",  # empty string - tests robustness
        ])

    return Account(
        name=name, domain=domain, industry=industry, archetype=archetype,
        employee_count=emp_count, signals=signals, notes=notes,
    )


def generate_all_accounts() -> list:
    accounts = []
    for n, d, i, e in HEAVY_INDUSTRIAL:
        accounts.append(build_account(n, d, i, "heavy_industrial", e))
    for n, d, i, e in LOGISTICS_FLEET:
        accounts.append(build_account(n, d, i, "logistics_fleet", e))
    for n, d, i, e in MANUFACTURING:
        accounts.append(build_account(n, d, i, "manufacturing", e))
    return accounts


def to_jsonable(accounts: list) -> list:
    """For dumping to JSON / displaying in Streamlit."""
    return [asdict(a) for a in accounts]


if __name__ == "__main__":
    accounts = generate_all_accounts()
    print(f"Generated {len(accounts)} accounts across 3 archetypes.")
    print(f"  heavy_industrial: {sum(1 for a in accounts if a.archetype == 'heavy_industrial')}")
    print(f"  logistics_fleet:  {sum(1 for a in accounts if a.archetype == 'logistics_fleet')}")
    print(f"  manufacturing:    {sum(1 for a in accounts if a.archetype == 'manufacturing')}")
    total_signals = sum(len(a.signals) for a in accounts)
    print(f"Total signals generated: {total_signals} (avg {total_signals/len(accounts):.1f}/account)")
    # Detect duplicates we deliberately planted
    domains = [a.domain for a in accounts if a.domain]
    dupe_domains = {d for d in domains if domains.count(d) > 1}
    print(f"Duplicate domains (deliberate): {dupe_domains}")
    # Save for inspection
    with open("mock_accounts.json", "w") as f:
        json.dump(to_jsonable(accounts), f, indent=2, default=str)
    print("Wrote mock_accounts.json")

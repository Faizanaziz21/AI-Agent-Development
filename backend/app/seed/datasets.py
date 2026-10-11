"""Deterministic synthetic datasets for the local development adapters.

All companies, people, domains and vendors here are fictional (domains use the reserved
`.example` TLD). Production deployments replace the local directory with real data
providers (Companies House, enrichment APIs, search APIs) behind the same adapter interfaces.
"""

from __future__ import annotations

import random
from functools import lru_cache

INDUSTRIES = [
    "Financial Services", "Healthcare", "Legal Services", "Manufacturing", "Logistics", "Retail",
    "Education", "Energy & Utilities", "Professional Services", "Technology", "Public Sector",
    "Pharmaceuticals", "Construction", "Media",
]
REGIONS = {
    "London": ["London"], "South East": ["Reading", "Guildford", "Brighton", "Oxford"],
    "North West": ["Manchester", "Liverpool", "Preston"], "West Midlands": ["Birmingham", "Coventry"],
    "Yorkshire": ["Leeds", "Sheffield", "York"], "Scotland": ["Edinburgh", "Glasgow", "Aberdeen"],
    "Wales": ["Cardiff", "Swansea"], "East of England": ["Cambridge", "Norwich"],
    "South West": ["Bristol", "Exeter"], "North East": ["Newcastle", "Durham"],
    "Northern Ireland": ["Belfast"],
}
NON_UK = [("Ireland", "Dublin"), ("Germany", "Munich"), ("Netherlands", "Amsterdam"), ("United States", "Boston")]
PREFIX = [
    "Albion", "Northbridge", "Thames", "Pennine", "Caledon", "Harbour", "Kestrel", "Meridian", "Oakridge",
    "Severn", "Granite", "Beacon", "Lumen", "Hawthorn", "Cobalt", "Ashdown", "Brightwater", "Clearview",
    "Fenwick", "Highgate", "Ironbridge", "Juniper", "Larkspur", "Marlow", "Newhaven", "Orchard", "Portland",
    "Quayside", "Redwood", "Sterling", "Tidewater", "Upland", "Vantage", "Westgate", "Yarrow", "Bramble",
    "Castlegate", "Dunmore", "Eastleigh", "Foxglove", "Greystone", "Holloway", "Ivybridge", "Kingsway",
]
SUFFIX = {
    "Financial Services": ["Capital", "Wealth", "Insurance", "Finance", "Mutual"],
    "Healthcare": ["Health", "Care", "Clinics", "Medical"],
    "Legal Services": ["Legal", "Law", "Chambers", "Solicitors"],
    "Manufacturing": ["Engineering", "Industries", "Manufacturing", "Precision"],
    "Logistics": ["Logistics", "Freight", "Distribution", "Transport"],
    "Retail": ["Retail", "Stores", "Brands", "Outfitters"],
    "Education": ["Academy Trust", "Learning", "College Group"],
    "Energy & Utilities": ["Energy", "Power", "Water", "Utilities"],
    "Professional Services": ["Consulting", "Advisory", "Partners"],
    "Technology": ["Systems", "Software", "Digital", "Labs"],
    "Public Sector": ["Council Services", "Housing Association", "Authority"],
    "Pharmaceuticals": ["Pharma", "Biosciences", "Therapeutics"],
    "Construction": ["Construction", "Builders", "Developments"],
    "Media": ["Media", "Studios", "Publishing"],
}
LEGAL = ["Ltd", "plc", "Group", "Holdings", "Ltd", "Ltd"]
TECH = ["Microsoft 365", "Google Workspace", "Salesforce", "ServiceNow", "Microsoft Intune", "Jamf",
        "CrowdStrike", "SentinelOne", "Okta", "Citrix", "AWS", "Azure", "SAP", "Workday", "Zscaler"]
TECH_SOURCES = ["job postings", "website", "DNS records", "press release", "case study"]
SIGNALS = {
    "ISO 27001 certified": 8, "Cyber Essentials Plus": 7, "Hybrid workforce": 6, "FCA regulated": 9,
    "Handles patient data": 9, "Recent data incident disclosed": 10, "Hiring security engineers": 7,
    "Removable media policy published": 5, "Large field workforce": 6, "On-site contractors": 5,
    "Handles payment card data": 8, "Government contracts (OFFICIAL-SENSITIVE)": 9,
}
INDUSTRY_SIGNALS = {
    "Financial Services": ["FCA regulated", "Handles payment card data"],
    "Healthcare": ["Handles patient data"], "Pharmaceuticals": ["Handles patient data"],
    "Public Sector": ["Government contracts (OFFICIAL-SENSITIVE)"], "Legal Services": ["ISO 27001 certified"],
    "Retail": ["Handles payment card data"], "Logistics": ["Large field workforce"],
    "Manufacturing": ["On-site contractors"], "Construction": ["Large field workforce"],
}
FIRST = ["Olivia", "James", "Amelia", "Oliver", "Isla", "Harry", "Ava", "George", "Mia", "Noah", "Sophie",
         "Jack", "Grace", "Thomas", "Emily", "William", "Hannah", "Arjun", "Priya", "Daniel", "Chloe",
         "Samuel", "Fatima", "Liam", "Zara", "Ethan", "Ruby", "Mohammed", "Evie", "Lucas"]
LAST = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Patel", "Wright",
        "Walker", "Thompson", "Robinson", "Khan", "Evans", "Hughes", "Green", "Hall", "Wood", "Clarke",
        "Murray", "Campbell", "Shah", "Edwards", "Turner", "Bennett", "Morgan", "Reid", "Fraser", "Lewis"]
TITLES_BY_SIZE = [
    (0, ["Managing Director", "IT Manager", "Operations Director", "Finance Director"]),
    (500, ["Chief Executive Officer", "Head of IT", "IT Director", "Head of Information Security", "Finance Director"]),
    (1500, ["Chief Executive Officer", "Chief Information Officer", "Chief Information Security Officer",
            "Head of IT Infrastructure", "Data Protection Officer", "Chief Financial Officer"]),
]


def _employees(rng: random.Random) -> int:
    band = rng.random()
    if band < 0.12:
        return rng.randint(15, 190)
    if band < 0.85:
        return int(rng.choice([rng.randint(200, 900), rng.randint(900, 2500), rng.randint(2500, 5000)]))
    return rng.randint(5200, 22000)


@lru_cache
def company_directory() -> list[dict]:
    rng = random.Random(20240521)
    seen: set[str] = set()
    companies: list[dict] = []
    while len(companies) < 420:
        industry = rng.choice(INDUSTRIES)
        name = f"{rng.choice(PREFIX)} {rng.choice(SUFFIX[industry])}"
        if name in seen:
            continue
        seen.add(name)
        legal = rng.choice(LEGAL)
        full = f"{name} {legal}"
        slug = name.lower().replace(" ", "").replace("&", "")
        domain = f"{slug}.example"
        uk = rng.random() > 0.14
        if uk:
            region = rng.choice(list(REGIONS))
            city = rng.choice(REGIONS[region])
            country = "United Kingdom"
        else:
            country, city = rng.choice(NON_UK)
            region = country
        employees = _employees(rng)
        tech = []
        for t in rng.sample(TECH, rng.randint(2, 6)):
            tech.append({"name": t, "confidence": round(rng.uniform(0.35, 0.98), 2), "source": rng.choice(TECH_SOURCES)})
        signals = []
        for s in set(rng.sample(list(SIGNALS), rng.randint(1, 3)) + INDUSTRY_SIGNALS.get(industry, [])):
            signals.append({"signal": s, "confidence": round(rng.uniform(0.5, 0.97), 2), "weight": SIGNALS[s]})
        sites = 1 + (employees // rng.randint(250, 900))
        titles = next(t for threshold, t in reversed(TITLES_BY_SIZE) if employees >= threshold)
        people = []
        for title in titles:
            fn, ln = rng.choice(FIRST), rng.choice(LAST)
            people.append({"name": f"{fn} {ln}", "title": title, "email": f"{fn.lower()}.{ln.lower()}@{domain}",
                           "seniority": "C-level" if title.startswith("Chief") else "Director/Head"})
        record = {
            "id": f"co_{len(companies):04d}",
            "name": full, "domain": domain, "industry": industry, "country": country, "hq_city": city,
            "region": region, "employees": employees,
            "uk_sites": sites if uk else 0,
            "founded": rng.randint(1890, 2019),
            "revenue_band": rng.choice(["£10m-£50m", "£50m-£250m", "£250m-£1bn", "£1bn+"]),
            "tech_indicators": tech, "security_signals": signals, "people": people,
            "description": (
                f"{full} is a {industry.lower()} organisation headquartered in {city}"
                f"{', ' + country if not uk else ''} with around {employees:,} employees."
            ),
            "url": f"https://www.{domain}",
        }
        # incomplete records exercise enrichment delegation (registry lookup fills the gap)
        record["registry_employees"] = employees
        if rng.random() < 0.12:
            record["employees"] = None
        companies.append(record)
    return companies


@lru_cache
def web_corpus() -> list[dict]:
    """Small fictional 'public web' used by the local search provider."""
    vendors = [
        ("Lockwell DeviceGuard", "lockwell.example", 6.5, "Endpoint device control with USB whitelisting and audit trails."),
        ("PortSentinel", "portsentinel.example", 4.0, "Port and peripheral control for Windows and macOS fleets."),
        ("Curtain Endpoint Suite", "curtain.example", None, "DLP-led device control bundled with endpoint protection."),
        ("Vaultline Peripheral Control", "vaultline.example", 8.0, "Enterprise peripheral control with zero-trust policies."),
        ("NimbusDLP", "nimbusdlp.example", None, "Cloud DLP with optional removable media module."),
    ]
    docs = []
    for name, domain, price, blurb in vendors:
        docs.append({
            "id": f"web_{domain}", "title": f"{name} — Product overview", "url": f"https://{domain}/product",
            "text": f"{name}. {blurb} Supports policy-based blocking of removable media, Bluetooth and MTP devices.",
            "kind": "vendor", "vendor": name,
        })
        pricing = (f"{name} pricing starts at ${price:.2f} per endpoint per month billed annually."
                   if price else f"{name} pricing is available on request. Contact sales for a quote.")
        docs.append({"id": f"web_{domain}_pricing", "title": f"{name} — Pricing", "url": f"https://{domain}/pricing",
                     "text": pricing, "kind": "pricing", "vendor": name, "price_per_endpoint": price})
        if price is None:
            estimate = round((sum(map(ord, domain)) % 40) / 10 + 5, 1)
            docs.append({
                "id": f"web_{domain}_review", "title": f"Analyst note: {name} deal sizes",
                "url": f"https://analyst-weekly.example/{domain}",
                "text": f"Buyers report {name} quotes of roughly ${estimate} per endpoint per month for 1,000+ seats.",
                "kind": "analyst", "vendor": name, "price_per_endpoint": estimate,
            })
    docs.append({
        "id": "web_market_report", "title": "UK Endpoint Security Market 2026",
        "url": "https://analyst-weekly.example/uk-endpoint-2026",
        "text": "Device control demand in the UK is driven by hybrid work, removable-media data loss incidents "
                "and regulatory pressure in financial services and healthcare.",
        "kind": "report",
    })
    return docs

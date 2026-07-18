#!/usr/bin/env python3
"""
Industrial Design Job Scanner v3.0
- LinkedIn + leManoosh + RemoteOK
- 130+ company watchlist
- DAILY DIFF: only reports NEW jobs since yesterday
- Rich Telegram messages with collapsible <details> per job
- Runs on AWS server via systemd timer, zero trading impact
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime
import json
import re
import os
import hashlib
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "seen_jobs.json")

# ═══════════════════════════════════════════════════════════════
# SCORING
# ═══════════════════════════════════════════════════════════════

ID_KEYWORDS = {
    # CORE industrial/product design (highest weight)
    "industrial design": 5, "industrial designer": 5, "product design": 5,
    "product designer": 5, "design industriel": 5, "designer industriel": 5,
    "designer produit": 5, "design engineer": 4,
    # Adjacent fields (medium weight)
    "transportation design": 2, "furniture design": 2,
    "packaging design": 2, "consumer product": 2, "cabin interior": 1,
    "cmf designer": 1, "cmf design": 1, "color material": 1,
    # Software/tools (signal of real ID work)
    "rhino": 2, "grasshopper": 2, "keyshot": 2, "solidworks": 2,
    "fusion 360": 2, "blender": 1, "alias": 2, "catia": 2, "vred": 2,
    # Junior-friendly
    "junior": 1, "stage": 1, "alternance": 1, "entry level": 1,
    "graduate": 1, "internship": 1, "apprentice": 1,
    "stagiaire": 1, "jeune diplômé": 1, "apprentissage": 1,
    # Negative: NOT industrial design
    "graphic design": -2, "graphic designer": -2,
    "ux design": -2, "ui design": -2, "ux/ui": -2,
    "web design": -2, "web designer": -2,
    "frontend": -3, "backend": -3, "fullstack": -3,
    "social media": -3, "data analyst": -3, "content creator": -2,
    "video editor": -2, "devops": -3, "software engineer": -3,
    "software developer": -3, "data scientist": -3,
    # Automotive-specific: penalize unless combined with industrial/product
    "automotive designer": -2, "car designer": -2,
    "vehicle designer": -1, "automotive design": -1,
}

# Premium companies — bonus points for known industrial design employers
PREMIUM_COMPANIES = {
    # Design-led brands
    "ideo": 2, "frog design": 2, "smart design": 2, "seymourpowell": 2,
    "priestmangoode": 2, "layer": 2, "dca design": 2, "kinneir dufort": 2,
    "tangerine": 2, "design partners": 2, "pdd": 2, "native design": 2,
    "map project office": 2, "barber osgerby": 1, "teenage engineering": 2,
    # Premium brands known for design
    "apple": 3, "dyson": 3, "bang & olufsen": 3, "bose": 2, "sonos": 2,
    "nike": 2, "lego": 3, "ikea": 2, "philips": 2, "braun": 2,
    "hermès": 2, "rolex": 2, "cartier": 2, "logitech": 2, "devialet": 2,
    "nothing": 2, "framework": 1,
    # Automotive design studios (still ID-relevant)
    "bmw": 1, "porsche": 1, "ferrari": 1, "polestar": 1, "nio": 1,
    "renault": 1, "peugeot": 1, "citroën": 1, "volvo cars": 1,
    "volvo group": 1, "mercedes-benz": 1, "audi": 1,
    # French design employers
    "decathlon": 2, "seb": 2, "tefal": 1, "schneider electric": 1,
    "saint-gobain": 1, "l'oréal": 1, "chanel": 2, "lv": 1, "lvmh": 2,
    "dassault": 1, "airbus": 1, "safran": 1, "thales": 1, "valeo": 1,
    "michelin": 1, "somfy": 1, "faurecia": 1, "forvia": 1,
    "jcdecaux": 2, "saint laurent": 2, "balenciaga": 2, "givenchy": 2,
    # Swiss
    "nestlé": 1, "audemars piguet": 2, "patek philippe": 2, "breitling": 2,
    "victorinox": 2, "freitag": 2, "on running": 1,
    # European
    "electrolux": 1, "adidas": 1, "puma": 1, "salomon": 1,
    "samsung": 1, "huawei": 1, "xiaomi": 1, "microsoft": 1, "google": 1,
    "amazon": 1, "meta": 1,
}

def score_job(title, company="", description=""):
    text = f"{title} {company} {description}".lower()
    score = 0
    matched = []
    # Keyword scoring
    for kw, weight in ID_KEYWORDS.items():
        if kw in text:
            score += weight
            if weight > 0:
                matched.append(kw)
    # Premium company bonus
    for brand, bonus in PREMIUM_COMPANIES.items():
        if brand in text:
            score += bonus
            matched.append(f"🏷️{brand}(+{bonus})")
            break  # Only apply the best match
    return score, matched

LEVEL_PATTERNS = {
    "Junior": r'\b(junior|entry.level|graduate|jeune.diplômé|stagiaire|intern|stage)\b',
    "Alternance": r'\b(alternance|apprentissage|contrat.pro)\b',
    "Confirmé": r'\b(confirmé|mid.level|senior|lead|expert|director)\b',
}
CONTRACT_PATTERNS = {
    "CDI": r'\b(CDI|temps plein|permanent|full.time)\b',
    "CDD": r'\b(CDD|temps.déterminé|fixed.term)\b',
    "Stage": r'\b(stage|intern|internship|stagiaire)\b',
    "Alternance": r'\b(alternance|apprentissage)\b',
    "Freelance": r'\b(freelance|consultant|indépendant)\b',
}

def extract_metrics(title, company=""):
    text = f"{title} {company}"
    metrics = {"level": [], "contract": []}
    for level, pattern in LEVEL_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            metrics["level"].append(level)
    for contract, pattern in CONTRACT_PATTERNS.items():
        if re.search(pattern, text, re.IGNORECASE):
            metrics["contract"].append(contract)
    return metrics


# ═══════════════════════════════════════════════════════════════
# DEDUP / DIFF LOGIC
# ═══════════════════════════════════════════════════════════════

def job_hash(job):
    """Stable hash for dedup based on title+company+link."""
    key = f"{job.get('title','').lower().strip()[:60]}|{job.get('company','').lower().strip()[:30]}|{job.get('link','')[-40:]}"
    return hashlib.md5(key.encode()).hexdigest()

def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_seen(seen):
    # Keep last 500 entries max
    if len(seen) > 500:
        # Sort by timestamp, keep newest 500
        sorted_items = sorted(seen.items(), key=lambda x: x[1].get("ts", ""), reverse=True)
        seen = dict(sorted_items[:500])
    with open(STATE_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def filter_new(all_jobs, seen):
    """Split jobs into new vs already-seen."""
    new_jobs = []
    for job in all_jobs:
        h = job_hash(job)
        if h not in seen:
            job["is_new"] = True
            new_jobs.append(job)
        else:
            job["is_new"] = False
    return new_jobs


# ═══════════════════════════════════════════════════════════════
# SOURCES (same as v2)
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# SOURCE 1: LINKEDIN GUEST API — expanded European coverage
# ═══════════════════════════════════════════════════════════════

LINKEDIN_SEARCHES = [
    # ── France ──
    {"kw": "industrial design", "locs": ["France", "Remote"]},
    {"kw": "product designer", "locs": ["France"]},
    {"kw": "design industriel", "locs": ["France"]},
    {"kw": "designer industriel", "locs": ["France"]},
    {"kw": "designer produit", "locs": ["France"]},
    {"kw": "design engineer industrial", "locs": ["France"]},
    {"kw": "furniture designer", "locs": ["France"]},
    # ── Switzerland (FR/DE/IT) ──
    {"kw": "industrial design", "locs": ["Switzerland"]},
    {"kw": "product designer", "locs": ["Switzerland"]},
    {"kw": "design industriel", "locs": ["Switzerland"]},
    {"kw": "designer industriel", "locs": ["Switzerland"]},
    {"kw": "designer produkt", "locs": ["Switzerland"]},    # German
    {"kw": "industrial designer", "locs": ["Switzerland"]},
    # ── Germany ──
    {"kw": "industrial design", "locs": ["Germany"]},
    {"kw": "produktdesigner", "locs": ["Germany"]},
    {"kw": "industrial designer", "locs": ["Germany"]},
    {"kw": "produktdesign", "locs": ["Germany"]},
    {"kw": "design engineer", "locs": ["Germany"]},
    # ── Italy ──
    {"kw": "industrial design", "locs": ["Italy"]},
    {"kw": "design industriale", "locs": ["Italy"]},
    {"kw": "product designer", "locs": ["Italy"]},
    {"kw": "designer prodotto", "locs": ["Italy"]},
    # ── Spain ──
    {"kw": "industrial design", "locs": ["Spain"]},
    {"kw": "design industrial", "locs": ["Spain"]},
    {"kw": "designer producto", "locs": ["Spain"]},
    # ── UK ──
    {"kw": "industrial design", "locs": ["United Kingdom"]},
    {"kw": "product designer", "locs": ["United Kingdom"]},
    {"kw": "design engineer", "locs": ["United Kingdom"]},
    {"kw": "furniture designer", "locs": ["United Kingdom"]},
    # ── Benelux ──
    {"kw": "industrial design", "locs": ["Netherlands"]},
    {"kw": "product designer", "locs": ["Netherlands"]},
    {"kw": "industrieel ontwerp", "locs": ["Netherlands"]},
    {"kw": "industrial design", "locs": ["Belgium"]},
    {"kw": "product designer", "locs": ["Belgium"]},
    # ── Scandinavia ──
    {"kw": "industrial design", "locs": ["Sweden"]},
    {"kw": "industrial design", "locs": ["Denmark"]},
    # ── Pan-European ──
    {"kw": "CMF designer", "locs": ["Europe"]},
    {"kw": "transportation design", "locs": ["Europe"]},
    {"kw": "packaging design", "locs": ["Europe"]},
]

def scrape_linkedin(keywords, location, max_results=25):
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {"keywords": keywords, "location": location, "f_TP": "1,2", "start": 0}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        jobs = []
        for li in soup.find_all("li"):
            title_el = li.find("h3", class_="base-search-card__title")
            if not title_el: continue
            company_el = li.find("h4", class_="base-search-card__subtitle")
            loc_el = li.find("span", class_="job-search-card__location")
            link_el = li.find("a", class_="base-card__full-link")
            time_el = li.find("time")
            jobs.append({
                "title": title_el.get_text(strip=True),
                "company": company_el.get_text(strip=True) if company_el else "",
                "location": loc_el.get_text(strip=True) if loc_el else location,
                "link": link_el["href"].split("?")[0] if link_el else "",
                "date": time_el.get("datetime", "") if time_el else "",
                "source": "LinkedIn",
            })
            if len(jobs) >= max_results: break
        return jobs
    except Exception as e:
        print(f"      ⚠️ LinkedIn: {e}")
        return []

def scrape_lemanoosh():
    try:
        resp = requests.get("https://www.lemanoosh.com/jobs", headers=HEADERS, timeout=15)
        pattern = r'\{"id":\d+,"title":"[^"]+","location":"[^"]*"\}'
        raw_jobs = re.findall(pattern, resp.text)
        jobs = []
        for raw in raw_jobs:
            try:
                data = json.loads(raw)
                jobs.append({
                    "title": data["title"].replace("\\/", "/"),
                    "company": "—",
                    "location": data.get("location", "").replace("\\/", "/"),
                    "link": f"https://www.lemanoosh.com/jobs",
                    "date": "", "source": "leManoosh",
                })
            except: pass
        return jobs
    except Exception as e:
        print(f"      ⚠️ leManoosh: {e}")
        return []

def scrape_remoteok():
    try:
        resp = requests.get("https://remoteok.com/api", headers=HEADERS, timeout=15)
        data = resp.json()
        jobs = []
        for item in data:
            if not isinstance(item, dict) or "slug" not in item: continue
            title = item.get("position", "")
            tags = item.get("tags", [])
            text = f"{title} {' '.join(tags)}".lower()
            if not any(kw in text for kw in ["design", "industrial", "product", "creative"]): continue
            jobs.append({
                "title": title,
                "company": item.get("company", "—"),
                "location": item.get("location", "Remote") or "Remote",
                "link": f"https://remoteok.com/remote-jobs/{item.get('slug', '')}",
                "date": item.get("date", ""), "source": "RemoteOK",
            })
        return jobs
    except Exception as e:
        print(f"      ⚠️ RemoteOK: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# COMPANY WATCHLIST
# ═══════════════════════════════════════════════════════════════

COMPANIES = {
    "Decathlon": "https://decathlon.welcometothejungle.com/en",
    "SEB Group": "https://welcome.welcometothejungle.com/en",
    "Schneider Electric": "https://schneider-electric.welcometothejungle.com/en",
    "Dassault Systèmes": "https://3ds.welcometothejungle.com/en",
    "L'Oréal": "https://loreal.welcometothejungle.com/en",
    "Saint-Gobain": "https://saint-gobain.welcometothejungle.com/en",
    "Renault Group": "https://renault.welcometothejungle.com/en",
    "JCDecaux": "https://jcdecaux.welcometothejungle.com",
    "Faurecia (Forvia)": "https://www.forvia.com/careers",
    "Plastic Omnium": "https://careers.plasticomnium.com/",
    "Michelin": "https://career.michelin.com/",
    "Stellantis": "https://careers.stellantis.com/",
    "Legrand": "https://careers.legrand.com/",
    "Somfy": "https://careers.somfy.com/",
    "Thales": "https://careers.thalesgroup.com/",
    "Airbus": "https://www.airbus.com/en/careers",
    "Safran": "https://careers.safran.com/",
    "Valeo": "https://valeo-careers.com/",
    "EssilorLuxottica": "https://www.essilorluxottica.com/en/careers",
    "Hermès": "https://recrutement.hermes.com/",
    "Chanel": "https://careers.chanel.com/",
    "LVMH": "https://www.lvmh.com/talents/",
    "Richemont": "https://www.richemont.com/en/careers",
    "Cartier": "https://careers.cartier.com/",
    "Rolex": "https://www.rolex.com/careers",
    "Parrot": "https://www.parrot.com/careers",
    "Devialet": "https://devialet.com/en/jobs/",
    "Withings": "https://www.withings.com/jobs",
    "Fairphone": "https://www.fairphone.com/en/jobs/",
    "RCP Design Global": "https://www.rcp-design.com/fr/recrutement/",
    "5.5 Designers": "https://www.5et5.com/fr/recrutement",
    "Wedge": "https://www.wedge.fr/recrutement",
    "Eliumstudio": "https://www.eliumstudio.fr/",
    "Blast Design": "https://www.blast-design.com/",
    "Klip Design": "https://www.klipdesign.fr/",
    "Logitech": "https://careers.jobvite.com/logitech/",
    "Nestlé": "https://www.nestle.com/careers",
    "Victorinox": "https://www.victorinox.com/en/careers",
    "Audemars Piguet": "https://www.audemarspiguet.com/careers",
    "Breitling": "https://www.breitling.com/careers/",
    "Freitag": "https://www.freitag.ch/en/jobs",
    "On Running": "https://onrunning.com/en/careers",
    "BMW Group": "https://www.bmwgroup.jobs/",
    "Volkswagen": "https://www.volkswagen-careers.com/",
    "Mercedes-Benz": "https://group.mercedes-benz.com/careers/",
    "Audi": "https://www.audi.com/careers/",
    "Porsche": "https://careers.porsche.com/",
    "Volvo Cars": "https://careers.volvocars.com/",
    "Ferrari": "https://careers.ferrari.com/",
    "NIO": "https://www.nio.com/careers",
    "Polestar": "https://polestar.com/careers/",
    "Philips": "https://www.careers.philips.com/",
    "Electrolux": "https://careers.electrolux.com/",
    "Dyson": "https://careers.dyson.com/",
    "Bang & Olufsen": "https://career.bang-olufsen.com/",
    "Bose": "https://careers.bose.com/",
    "Sonos": "https://www.sonos.com/en/careers",
    "IKEA": "https://www.ikea.com/en/careers/",
    "LEGO Group": "https://www.lego.com/en-us/careers/",
    "Nike": "https://jobs.nike.com/",
    "Adidas": "https://careers.adidas-group.com/",
    "Puma": "https://about.puma/careers",
    "Salomon": "https://careers.salomon.com/",
    "IDEO": "https://www.ideo.com/careers",
    "frog Design": "https://www.frog.co/careers",
    "Smart Design": "https://smartdesign.com/careers/",
    "Seymourpowell": "https://www.seymourpowell.com/careers",
    "PriestmanGoode": "https://priestmangoode.com/careers/",
    "Layer": "https://layer.design/",
    "DCA Design": "https://www.dca-design.com/",
    "Kinneir Dufort": "https://www.kinneirdufort.com/careers",
    "Tangerine": "https://www.tangerinedream.com/careers",
    "Design Partners": "https://www.designpartners.com/careers",
    "PDD Innovations": "https://www.pdd.co.uk/careers/",
    "Apple": "https://www.apple.com/careers/",
    "Google": "https://careers.google.com/",
    "Amazon Lab126": "https://www.amazon.jobs/en/",
    "Samsung Design": "https://design.samsung.com/",
    "Nothing": "https://nothing.tech/careers",
    "Teenage Engineering": "https://teenage.engineering/careers",
    "Framework": "https://frame.work/careers",
}

def check_companies():
    results = []
    for company, url in COMPANIES.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            text = resp.text.lower() if resp.status_code == 200 else ""
            has_design = any(kw in text for kw in ["design", "designer", "industrial", "produit"])
            results.append({"company": company, "url": url, "status": resp.status_code, "has_design": has_design and resp.status_code == 200})
        except:
            results.append({"company": company, "url": url, "status": "ERR", "has_design": False})
    return results


# ═══════════════════════════════════════════════════════════════
# TELEGRAM RICH MESSAGE
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# TELEGRAM RICH MESSAGE with emoji & country/domain markers
# ═══════════════════════════════════════════════════════════════

COUNTRY_EMOJI = {
    "france": "🇫🇷", "paris": "🇫🇷", "lyon": "🇫🇷", "marseille": "🇫🇷",
    "toulouse": "🇫🇷", "bordeaux": "🇫🇷", "lille": "🇫🇷", "nice": "🇫🇷",
    "nantes": "🇫🇷", "strasbourg": "🇫🇷", "rennes": "🇫🇷", "île-de-france": "🇫🇷",
    "annecy": "🇫🇷", "biarritz": "🇫🇷", "grenoble": "🇫🇷",
    "switzerland": "🇨🇭", "suisse": "🇨🇭", "zurich": "🇨🇭", "geneva": "🇨🇭",
    "lausanne": "🇨🇭", "bern": "🇨🇭", "basel": "🇨🇭",
    "germany": "🇩🇪", "allemagne": "🇩🇪", "berlin": "🇩🇪", "munich": "🇩🇪",
    "hamburg": "🇩🇪", "stuttgart": "🇩🇪", "wolfsburg": "🇩🇪",
    "united kingdom": "🇬🇧", "uk": "🇬🇧", "london": "🇬🇧", "england": "🇬🇧",
    "italy": "🇮🇹", "italie": "🇮🇹", "milan": "🇮🇹", "turin": "🇮🇹",
    "spain": "🇪🇸", "espagne": "🇪🇸", "barcelona": "🇪🇸", "madrid": "🇪🇸",
    "netherlands": "🇳🇱", "amsterdam": "🇳🇱", "eindhoven": "🇳🇱",
    "sweden": "🇸🇪", "stockholm": "🇸🇪", "gothenburg": "🇸🇪",
    "denmark": "🇩🇰", "copenhagen": "🇩🇰",
    "belgium": "🇧🇪", "bruxelles": "🇧🇪", "brussels": "🇧🇪",
    "austria": "🇦🇹", "vienna": "🇦🇹",
    "usa": "🇺🇸", "united states": "🇺🇸", "california": "🇺🇸",
    "new york": "🇺🇸", "san francisco": "🇺🇸", "portland": "🇺🇸",
    "canada": "🇨🇦", "montreal": "🇨🇦", "toronto": "🇨🇦",
    "china": "🇨🇳", "shenzhen": "🇨🇳", "shanghai": "🇨🇳",
    "japan": "🇯🇵", "tokyo": "🇯🇵",
    "remote": "🌐", "worldwide": "🌐", "europe": "🇪🇺",
}

def _country_flag(location):
    """Return flag emoji for a location string."""
    if not location:
        return "🌍"
    loc_lower = location.lower().strip()
    for key, emoji in COUNTRY_EMOJI.items():
        if key in loc_lower:
            return emoji
    return "🌍"

def _domain_emoji(job):
    """Infer domain emoji from title + company."""
    title = (job.get("title") or "").lower()
    company = (job.get("company") or "").lower()
    text = f"{title} {company}"

    # ── Company-specific overrides (most accurate) ──
    OVERRIDES = {
        "decathlon": "⚽", "salomon": "⚽", "nike": "👟", "adidas": "👟", "puma": "👟",
        "on running": "👟", "arc'teryx": "🏔️", "fjällräven": "🏔️",
        "lego": "🧸", "ikea": "🪑", "vitra": "🪑",
        "dyson": "🍳", "seb": "🍳", "tefal": "🍳", "electrolux": "🍳",
        "bose": "🎧", "sonos": "🎧", "bang & olufsen": "🎧", "devialet": "🎧",
        "apple": "📱", "nothing": "📱", "samsung": "📱",
        "philips": "🏥", "braun": "🏥",
        "rolex": "💎", "cartier": "💎", "audemars piguet": "💎",
        "patek philippe": "💎", "breitling": "💎", "omega": "💎",
        "hermès": "💎", "chanel": "💎", "lvmh": "💎", "richemont": "💎",
        "saint laurent": "💎", "balenciaga": "💎", "givenchy": "💎",
        "renault": "🚗", "peugeot": "🚗", "citroën": "🚗", "stellantis": "🚗",
        "bmw": "🚗", "porsche": "🚗", "ferrari": "🚗", "lamborghini": "🚗",
        "nio": "🚗", "polestar": "🚗", "volvo": "🚗", "mercedes": "🚗",
        "audi": "🚗", "volkswagen": "🚗", "jaguar": "🚗", "rivian": "🚗",
        "airbus": "✈️", "dassault aviation": "✈️", "safran": "✈️",
        "l'oréal": "📦", "sephora": "📦",
        "ideo": "🏢", "frog design": "🏢", "smart design": "🏢",
        "seymourpowell": "🏢", "design partners": "🏢", "wedge": "🏢",
        "rcp design": "🏢", "5.5 design": "🏢", "klip design": "🏢",
        "michelin": "🛞", "faurecia": "🚗", "forvia": "🚗", "valeo": "🚗",
        "logitech": "🖱️", "victorinox": "🔪", "freitag": "🎒",
        "nestlé": "🍫", "lindt": "🍫",
        "schneider electric": "⚡", "legrand": "⚡", "somfy": "🏠",
        "thales": "🛰️", "saint-gobain": "🏗️",
    }
    for key, emoji in OVERRIDES.items():
        if key in text:
            return emoji

    if any(kw in text for kw in ["automotive", "car ", "vehicle", "bmw", "porsche", "ferrari", "lambo", "renault", "peugeot", "citroën", "nissan", "toyota"]):
        return "🚗"
    if any(kw in text for kw in ["watch", "rolex", "cartier", "omega", "audemars", "patek", "breitling", "horlog", "luxury", "jewel"]):
        return "💎"
    if any(kw in text for kw in ["furniture", "meuble", "chair", "table", "ikea", "lamp", "luminaire"]):
        return "🪑"
    if any(kw in text for kw in ["sport", "nike", "adidas", "salomon", "decathlon", "arc", "outdoor", "bike"]):
        return "⚽"
    if any(kw in text for kw in ["toy", "lego", "jouet", "game", "nintendo"]):
        return "🧸"
    if any(kw in text for kw in ["medical", "health", "surgical", "pharma", "device"]):
        return "🏥"
    if any(kw in text for kw in ["packaging", "emballage", "cosmetic", "beauty", "loreal"]):
        return "📦"
    if any(kw in text for kw in ["kitchen", "cuisine", "appliance", "electrolux", "dyson", "seb", "tefal"]):
        return "🍳"
    if any(kw in text for kw in ["lighting", "lamp", "éclairage", "led"]):
        return "💡"
    if any(kw in text for kw in ["shoe", "chaussure", "footwear", "sneaker"]):
        return "👟"
    if any(kw in text for kw in ["audio", "speaker", "headphone", "sound", "bose", "sonos", "bang", "devialet"]):
        return "🎧"
    if any(kw in text for kw in ["phone", "smartphone", "apple", "samsung", "nothing", "fairphone"]):
        return "📱"
    if any(kw in text for kw in ["aerospace", "aviation", "airbus", "boeing", "drone", "satellite"]):
        return "✈️"
    if any(kw in text for kw in ["agency", "consult", "studio", "ideo", "frog", "wedge", "design firm"]):
        return "🏢"
    return "🏭"  # generic industry

def _level_badge(level_list):
    """Return level badge: 🟢 junior, 🟡 mid, 🔴 senior, ⚪ unknown."""
    if not level_list:
        return "⚪"
    text = " ".join(level_list).lower()
    if any(kw in text for kw in ["junior", "stage", "intern", "graduate", "stagiaire", "alternance"]):
        return "🟢"
    if any(kw in text for kw in ["senior", "lead", "expert", "director"]):
        return "🔴"
    if any(kw in text for kw in ["confirmé", "mid"]):
        return "🟡"
    return "⚪"


def _load_bot_token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        env_path = "/etc/mon-terminal-quant/hybrid_realtime.env"
        if os.path.exists(env_path):
            try:
                with open(env_path) as f:
                    for line in f:
                        if "BOT_TOKEN" in line and "=" in line:
                            token = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            except: pass
    return token

def send_telegram_rich(new_jobs, all_count, new_count, today):
    """Send rich Telegram message to MTQ Tickers with collapsible job panels."""
    token = _load_bot_token()
    # MTQ Tickers channel — not the busy Status channel
    chat_id = os.environ.get("TELEGRAM_TICKERS_CHAT_ID", "-5136300760")

    if not token:
        print("📭 Telegram skipped (no token)")
        return

    new_top = [j for j in new_jobs if j.get("score", 0) >= 5]
    new_good = [j for j in new_jobs if 3 <= j.get("score", 0) < 5]
    new_okay = [j for j in new_jobs if 1 <= j.get("score", 0) < 3]

    html_parts = []

    if new_count == 0:
        html_parts.append(f"🔍 <b>Job Scan — {today}</b>")
        html_parts.append(f"📊 {all_count} offres scannées · 0 nouvelle")
        html_parts.append("")
        html_parts.append("<i>Pas de nouvelle offre aujourd'hui.</i>")
    else:
        top_icon = "🟢" if len(new_top) >= 5 else "🟡" if len(new_top) >= 2 else "⚪"
        html_parts.append(f"🔍 <b>ID Job Scan · {today}</b>")
        html_parts.append(f"{top_icon} <b>{new_count} novelle(s)</b> | {all_count} scannées | 🔥{len(new_top)} ⭐{len(new_good)} 📌{len(new_okay)}")
        html_parts.append("")

        # ── TOP PRIORITY  ──
        if new_top:
            html_parts.append("<b>━━━ 🔥 TOP PRIORITY ━━━</b>")
            html_parts.append("")
            for j in new_top[:12]:
                flag = _country_flag(j.get("location", ""))
                domain = _domain_emoji(j)
                level_badge = _level_badge(j.get("level", []))
                title = _escape_html(j["title"])
                company = _escape_html(j.get("company", "—"))
                location = _escape_html(j.get("location", "—"))
                link = j.get("link", "")
                source = j.get("source", "?")
                matched = ", ".join(j.get("matched", [])[:3])
                level_text = "/".join(j.get("level", [])) or "—"

                # Compact header line
                header = f"{domain} 🔥{j['score']} · {flag} {title[:45]}"
                if company and company != "—":
                    header += f" · {company[:20]}"
                header += f" · {level_badge}"

                html_parts.append(f"<details>")
                html_parts.append(f"<summary>{header}</summary>")
                html_parts.append("")
                html_parts.append(f"<b>{domain} {title}</b>")
                html_parts.append(f"🏢 <b>{company}</b>")
                html_parts.append(f"{flag} <b>{location}</b>")
                html_parts.append(f"🎯 <b>Niveau:</b> {level_badge} {level_text}")
                html_parts.append(f"📡 <b>Source:</b> {source}")
                if matched:
                    html_parts.append(f"🏷️ <b>Match:</b> {matched}")
                if link:
                    html_parts.append(f'📩 <a href="{link}">Postuler / Voir l\'offre</a>')
                html_parts.append("")
                html_parts.append("</details>")
            html_parts.append("")

        # ── GOOD MATCHES  ──
        if new_good:
            html_parts.append("<b>━━━ ⭐ BON MATCH ━━━</b>")
            html_parts.append("")
            for j in new_good[:10]:
                flag = _country_flag(j.get("location", ""))
                domain = _domain_emoji(j)
                level_badge = _level_badge(j.get("level", []))
                title = _escape_html(j["title"][:45])
                company = _escape_html(j.get("company", "—")[:20])
                location = _escape_html(j.get("location", "—")[:25])
                link = j.get("link", "")

                line = f"{domain} ⭐{j['score']} {flag} {level_badge} {title} · {company} · {location}"
                if link:
                    line = f'<a href="{link}">{line}</a>'
                html_parts.append(f"· {line}")
            html_parts.append("")

        # ── PARTIAL ──
        if new_okay:
            html_parts.append(f"<details>")
            html_parts.append(f"<summary>📌 <b>Matchs partiels ({len(new_okay)})</b></summary>")
            html_parts.append("")
            for j in new_okay[:15]:
                flag = _country_flag(j.get("location", ""))
                title = _escape_html(j["title"][:45])
                company = _escape_html(j.get("company", "—")[:20])
                html_parts.append(f"· 📌{j['score']} {flag} {title} · {company}")
            html_parts.append("")
            html_parts.append("</details>")

    html = "\n".join(html_parts)

    try:
        url = f"https://api.telegram.org/bot{token}/sendRichMessage"
        payload = {"chat_id": chat_id, "rich_message": {"markdown": html}}
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            print(f"📨 Telegram → MTQ Tickers ({new_count} new jobs)")
        else:
            print(f"📨 Telegram error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"📨 Telegram failed: {e}")


def _escape_html(text):
    """Escape HTML special chars for Telegram."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ═══════════════════════════════════════════════════════════════
# GITHUB PUSH — auto-publish daily reports
# ═══════════════════════════════════════════════════════════════

GITHUB_REPO = "victorebaguette/id-job-scan"
GITHUB_BRANCH = "main"

def push_to_github(report_path, today):
    """Push the daily report to GitHub via API (no git needed on server)."""
    token = os.environ.get("GITHUB_TOKEN", "")
    # Try loading from gh CLI config on local machine
    if not token:
        try:
            import subprocess
            result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                token = result.stdout.strip()
        except:
            pass
    # Try loading from token file on server
    if not token:
        token_file = os.path.join(BASE_DIR, ".github_token")
        if os.path.exists(token_file):
            try:
                with open(token_file) as f:
                    token = f.read().strip()
            except:
                pass

    if not token:
        print("📦 GitHub push skipped (no token)")
        return False

    try:
        # Read the report
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()

        import base64
        content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")

        # Check if file already exists (need SHA to update)
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/reports/Job_Scan_{today}.md"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

        # Try to get existing file SHA
        existing_sha = None
        try:
            r = requests.get(api_url, headers=headers, timeout=10)
            if r.status_code == 200:
                existing_sha = r.json().get("sha")
        except:
            pass

        # Create or update file
        payload = {
            "message": f"🔍 Job Scan {today}",
            "content": content_b64,
            "branch": GITHUB_BRANCH,
        }
        if existing_sha:
            payload["sha"] = existing_sha

        r = requests.put(api_url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            print(f"📦 GitHub: pushed reports/Job_Scan_{today}.md")
            return True
        else:
            print(f"📦 GitHub error: {r.status_code} {r.text[:150]}")
            return False
    except Exception as e:
        print(f"📦 GitHub failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# MARKDOWN OUTPUT (Obsidian / server file)
# ═══════════════════════════════════════════════════════════════

def generate_markdown(jobs, company_results, new_jobs):
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%d/%m/%Y à %H:%M")

    seen = set()
    unique = []
    for job in sorted(jobs, key=lambda x: x.get("score", 0), reverse=True):
        key = job["title"].lower()[:50] + job.get("company", "").lower()[:20]
        if key in seen: continue
        seen.add(key)
        unique.append(job)

    top = [j for j in unique if j["score"] >= 5]
    good = [j for j in unique if 3 <= j["score"] < 5]
    okay = [j for j in unique if 1 <= j["score"] < 3]

    new_count = len([j for j in unique if j.get("is_new")])
    new_top = [j for j in top if j.get("is_new")]

    md = f"""---
tags: [job-scan, industrial-design]
date: {today}
---

# 🔍 Job Scan — Junior Industrial Design
> Auto-généré le {now} | {len(unique)} offres | 🆕 {new_count} nouvelles | Sources: LinkedIn, leManoosh, RemoteOK, {len(COMPANIES)} entreprises

"""
    if new_top:
        md += f"## 🆕 NOUVELLES — Top Priority ({len(new_top)})\n\n| Score | Poste | Entreprise | Lieu | Niveau | Source | Lien |\n|-------|-------|------------|------|--------|--------|------|\n"
        for j in new_top[:30]:
            level = "/".join(j.get("level", [])) or "—"
            md += f"| 🆕🔥{j['score']} | **{j['title'][:40]}** | {j['company'][:20]} | {j.get('location','—')[:20]} | {level} | {j['source']} | [→]({j['link']}) |\n"
        md += "\n"

    if top:
        md += f"## 🔥 Top Priority — All ({len(top)})\n\n| Score | Poste | Entreprise | Lieu | Niveau | Source | Lien |\n|-------|-------|------------|------|--------|--------|------|\n"
        for j in top[:40]:
            level = "/".join(j.get("level", [])) or "—"
            new_icon = "🆕" if j.get("is_new") else "  "
            md += f"| {new_icon}🔥{j['score']} | **{j['title'][:40]}** | {j['company'][:20]} | {j.get('location','—')[:20]} | {level} | {j['source']} | [→]({j['link']}) |\n"
        md += "\n"

    if good:
        md += f"## ⭐ Bon match ({len(good)})\n\n| Score | Poste | Entreprise | Lieu | Source | Lien |\n|-------|-------|------------|------|--------|------|\n"
        for j in good[:30]:
            new_icon = "🆕" if j.get("is_new") else ""
            md += f"| {new_icon}⭐{j['score']} | {j['title'][:40]} | {j['company'][:20]} | {j.get('location','—')[:20]} | {j['source']} | [→]({j['link']}) |\n"
        md += "\n"

    if okay:
        md += f"<details><summary>📌 Matchs partiels ({len(okay)})</summary>\n\n| Score | Poste | Entreprise | Lieu | Source | Lien |\n|-------|-------|------------|------|--------|------|\n"
        for j in okay:
            md += f"| {j['score']} | {j['title'][:40]} | {j['company'][:20]} | {j.get('location','—')[:20]} | {j['source']} | [→]({j['link']}) |\n"
        md += "\n</details>\n\n"

    active = [c for c in company_results if c.get("has_design")]
    md += f"## 🏢 Watchlist ({len(active)}/{len(company_results)} actives)\n\n"
    md += "| Entreprise | Design? | Career Page |\n|------------|---------|------------|\n"
    for c in sorted(company_results, key=lambda x: (not x.get("has_design"), x["company"].lower())):
        icon = "✅" if c.get("has_design") else "⚪"
        md += f"| {c['company']} | {icon} | [→]({c['url']}) |\n"

    md += f"\n_...ID Job Scanner v3.0 — {today}_\n"
    return md


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    today = datetime.now().strftime("%Y-%m-%d")
    all_jobs = []

    print("=" * 60)
    print(f"🔍 ID Job Scanner v3.0 — {today}")
    print("=" * 60)

    # --- Source 1: LinkedIn ---
    print("\n📡 LinkedIn...")
    seen_links = set()
    for search in LINKEDIN_SEARCHES:
        for loc in search["locs"]:
            print(f"  → '{search['kw']}' / {loc}", end="... ", flush=True)
            jobs = scrape_linkedin(search["kw"], loc)
            for job in jobs:
                if job["link"] not in seen_links:
                    seen_links.add(job["link"])
                    score, matched = score_job(job["title"], job["company"])
                    metrics = extract_metrics(job["title"], job["company"])
                    job["score"] = score
                    job["matched"] = matched
                    job["level"] = metrics["level"]
                    job["contract"] = metrics["contract"]
                    all_jobs.append(job)
            print(f"{len(jobs)}")
            time.sleep(0.4)

    # --- Source 2: leManoosh ---
    print("📡 leManoosh...", end="... ", flush=True)
    lm_jobs = scrape_lemanoosh()
    for job in lm_jobs:
        score, matched = score_job(job["title"])
        metrics = extract_metrics(job["title"])
        job["score"] = score; job["matched"] = matched
        job["level"] = metrics["level"]; job["contract"] = metrics["contract"]
        all_jobs.append(job)
    print(f"{len(lm_jobs)} jobs")

    # --- Source 3: RemoteOK ---
    print("📡 RemoteOK...", end="... ", flush=True)
    ro_jobs = scrape_remoteok()
    for job in ro_jobs:
        score, matched = score_job(job["title"], job.get("company", ""))
        metrics = extract_metrics(job["title"], job.get("company", ""))
        job["score"] = score; job["matched"] = matched
        job["level"] = metrics["level"]; job["contract"] = metrics["contract"]
        all_jobs.append(job)
    print(f"{len(ro_jobs)} jobs")

    # --- Diff: filter new jobs ---
    print("\n🔄 Computing diff...", end=" ", flush=True)
    seen = load_seen()
    new_jobs = filter_new(all_jobs, seen)
    print(f"{len(new_jobs)} new out of {len(all_jobs)}")

    # --- Company watchlist ---
    print(f"🏢 Checking {len(COMPANIES)} companies...", end=" ", flush=True)
    company_results = check_companies()
    active = sum(1 for c in company_results if c.get("has_design"))
    print(f"{active}/{len(company_results)} active")

    # --- Save state for next run ---
    now_ts = datetime.now().isoformat()
    for job in all_jobs:
        h = job_hash(job)
        seen[h] = {"ts": now_ts, "title": job.get("title", "")[:50]}
    save_seen(seen)

    # --- Generate Obsidian markdown ---
    md = generate_markdown(all_jobs, company_results, new_jobs)
    vault_path = os.path.expanduser("~/Documents/Obsidian Vault")
    server_path = "/opt/mon_terminal_quant/job_scanner/output"
    if os.path.exists(vault_path):
        output_dir = os.path.join(vault_path, "01_Projects", "Recherche_Emploi")
    elif os.path.exists("/opt/mon_terminal_quant"):
        output_dir = server_path
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = os.path.expanduser("~/job_scans")
        os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"Job_Scan_{today}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)

    # --- Push to GitHub ---
    push_to_github(output_path, today)

    # --- Send Telegram rich message ---
    send_telegram_rich(new_jobs, len(all_jobs), len(new_jobs), today)

    # --- Summary ---
    top_count = len([j for j in all_jobs if j.get("score", 0) >= 5])
    new_top = len([j for j in new_jobs if j.get("score", 0) >= 5])
    print(f"\n{'=' * 60}")
    print(f"✅ {len(all_jobs)} total | 🆕 {len(new_jobs)} new | 🔥{top_count} top ({new_top} new)")
    print(f"📄 {output_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

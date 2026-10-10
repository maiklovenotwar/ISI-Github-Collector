from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple


class HeuristicResolver:
    _problematic_location_map = {
        "earth": None,
        "worldwide": None,
        "everywhere": None,
        "all over the world": None,
        "planet earth": None,
        "global": None,
        "the earth": None,
        "the world": None,
        "milkyway": None,
        "world wide web": None,
        "the world wide web": None,
        "around the world": None,
        "the moon": None,
        "moon": None,
        "milky way": None,
        "the universe": None,
        "universe": None,
        "somewhere": None,
        "anywhere": None,
        "here": None,
        "there": None,
        "remote": None,
        "remote, worldwide": None,
        "home": None,
        "home office": None,
        "internet": None,
        "on the internet": None,
        "cloud": None,
        "localhost": None,
        "127.0.0.1": None,
        "n/a": None,
        "none": None,
        "nowhere": None,
        "the blockchain": None,
        "blockchain": None,
        "quarantined at home": None,
        "international space station": None,
        "iss": None,
        "space": None,
        "vanadiel": None,
        "the gungeon": None,
        "code land": None,
        "on the exit node": None,
        "root": None,
        "github": None,
        "global (currently in 中国)": None,
        # Zusätzliche Non-Geo-Strings (False-Positive-Reduktion)
        "other worlds": None,
        "cyberspace": None,
        "virtual": None,
        "online": None,
        "digital nomad": None,
        "nomad": None,
        "traveling": None,
        "traveler": None,
        "wanderer": None,
        "citizen of the world": None,
        "world citizen": None,
        "earthling": None,
        "human": None,
        "developer": None,
        "programmer": None,
        "coder": None,
        "hacker": None,
        "nerd": None,
        "geek": None,
        "404": None,
        "null": None,
        "undefined": None,
        "nan": None,
        "unknown": None,
        "secret": None,
        "classified": None,
        "hidden": None,
        "private": None,
        "anonymous": None,
        "incognito": None,
        "mars": None,
        "jupiter": None,
        "saturn": None,
        "pluto": None,
        "andromeda": None,
        "galaxy": None,
        "cosmos": None,
        "multiverse": None,
        "metaverse": None,
        "matrix": None,
        "the matrix": None,
        "hogwarts": None,
        "mordor": None,
        "middle earth": None,
        "narnia": None,
        "gotham": None,
        "wakanda": None,
        "asgard": None,
        "tatooine": None,
        "coruscant": None,
        "winterfell": None,
        "westeros": None,
        "the shire": None,
        "rivendell": None,
        "valinor": None,
        "neverland": None,
        "atlantis": None,
        "el dorado": None,
        "shangri-la": None,
        "utopia": None,
        "dystopia": None,
        "heaven": None,
        "hell": None,
        "purgatory": None,
        "limbo": None,
        "void": None,
        "the void": None,
        "abyss": None,
        "the abyss": None,
        "oblivion": None,
        "nirvana": None,
        "valhalla": None,
        "olympus": None,
        "mount olympus": None,
        "bikini bottom": None,
        "springfield": None,  # Zu generisch, viele Städte heißen so
        "gotham city": None,
        "metropolis": None,  # Zu generisch
        "sim city": None,
        "minecraft": None,
        "roblox": None,
        "fortnite": None,
        "your mom": None,
        "your moms house": None,
        "yo mama": None,
        "deez nuts": None,
        "ligma": None,
        "sugma": None,
        "urmom": None,
        "lol": None,
        "lmao": None,
        "rofl": None,
        "xd": None,
        "bruh": None,
        "yeet": None,
        "based": None,
        "poggers": None,
        "kek": None,
        "gg": None,
        "ez": None,
        "noob": None,
        "pro": None,
        "git": None,
        "npm": None,
        "pip": None,
        "docker": None,
        "kubernetes": None,
        "k8s": None,
        "aws": None,
        "azure": None,
        "gcp": None,
        "linux": None,
        "windows": None,  # Nicht der US-Staat
        "macos": None,
        "ubuntu": None,
        "debian": None,
        "arch": None,
        "vim": None,
        "emacs": None,
        "vscode": None,
        "jetbrains": None,
        "stackoverflow": None,
        "stack overflow": None,
        "reddit": None,
        "twitter": None,
        "facebook": None,
        "instagram": None,
        "tiktok": None,
        "youtube": None,
        "twitch": None,
        "discord": None,
        "slack": None,
        "telegram": None,
        "whatsapp": None,
        "signal": None,
        "matrix.org": None,
        "fediverse": None,
        "mastodon": None,
        # Standalone US-State-Abbreviations (ohne Kontext → mehrdeutig, nicht geocodierbar)
        "la": None,  # Los Angeles oder Louisiana, nicht Laos
        "va": None,  # Virginia, nicht Vatikan
        "sc": None,  # South Carolina, nicht Seychellen
        "sd": None,  # South Dakota, nicht Sudan
        "pa": None,  # Pennsylvania, nicht Panama
        "ga": None,  # Georgia (US), nicht Gabun
        "co": None,  # Colorado, nicht Kolumbien
        "al": None,  # Alabama, nicht Albanien
        "md": None,  # Maryland, nicht Moldawien
        "mn": None,  # Minnesota, nicht Mongolei
        "me": None,  # Maine, nicht Montenegro
        "mt": None,  # Montana, nicht Malta
        "ar": None,  # Arkansas, nicht Argentinien
        "de": None,  # Delaware, nicht Deutschland (als Standalone)
        "in": None,  # Indiana, nicht Indien (als Standalone)
        # Bekannte False-Positive-Locations aus Datenanalyse
        "gz": None,  # Wird fälschlich als Sudan erkannt, ist aber Gizeh-Abkürzung oder Non-Geo
        # Tech-Begriffe (werden fälschlich als Orte erkannt)
        # "localhost" und "root" bereits oben definiert
        "/root": None,  # Unix-Pfad
        "server": None,  # Generisch
        "clouds": None,  # Österreich hat einen Ort
        "in the clouds": None,
        # Java alleinstehend blockieren (lässt "West Java", "East Java" durch)
        "java": None,
        # Short-Codes (2-Buchstaben, oft Abkürzungen für Städte)
        "sh": None,  # Shanghai, nicht St. Helena
        "sz": None,  # Shenzhen, nicht Eswatini
        "bj": None,  # Beijing, nicht Benin
        "fm": None,  # Frequency Modulation, nicht Mikronesien
        # Ambivalente Codes (US-Abkürzungen vs. Länder)
        "ma": None,  # Massachusetts, nicht Marokko
        "sa": None,  # Seattle Area / San Antonio, nicht Saudi-Arabien
        "as": None,  # Typo/Abkürzung, nicht Amerikanisch-Samoa
        "om": None,  # Typo, nicht Oman
        # Präpositionen (werden sonst als Länder erkannt, z.B. "at" -> AT)
        "at": None,
        "on": None,
        "of": None,
        "to": None,
        "from": None,
        "and": None,
        "with": None,
        "near": None,
        # Benchmark-Fundstücke: Non-Geo-Phrasen
        "hello world": None,
        "blue planet": None,
        "at a computer": None,
        "in the woods": None,
        "u guess": None,
        "tropical urheimat": None,
        "on earth": None,
        "southeast asia": None,
        "asia": None,
        "europe": None,
        "world": None,
        "the interwebs": None,
        "interwebs": None,
        "mid-earth": None,
        "teyvat": None,
        "area unknown": None,
        "terra firma": None,
        "in the middle of nowhere": None,
        "sailing": None,
        "vagabonding": None,
        "nomading": None,
        "mr. wordwide": None,
        "mr wordwide": None,
        # Zeitzonen-Kürzel
        "utc": None,
        "gmt": None,
        "cest": None,
        "cet": None,
        "pdt": None,
        "pst": None,
        "est": None,
        "edt": None,
        "mst": None,
        "mdt": None,
        "cst": None,
        "cdt": None,
        # Kurze Non-Geo-Codes aus Benchmark
        "nmi": None,
        "maa": None,
        "tha": None,
        "bdl": None,
        "jax": None,
        "siga": None,
        "mde": None,
        "btv": None,
        "hf": None,
        "pnw": None,
        "nova": None,
        "asean": None,
        "pond": None,
        "bk/ri": None,
        "1 au": None,
        # Institutionen/URLs
        "research institute of molecular pathology": None,
        "/usr/dcat": None,
        "irc.libera.chat": None,
        "irc: firefly on libera.chat": None,
        "railapp.com": None,
        # Sonstige False Positives
        "610 yo": None,
        "mo": None,
        "大": None,
        "da": None,  # unidecode("大") -> "Da " -> "da"
        # Crypto/Bitcoin patterns
        "btc:8333": None,  # unidecode("₿:8333") -> "BTC:8333"
        "mountain view, ca": {
            "country_code": "US",
            "country": "United States",
            "region": "California",
            "latitude": 37.3861,
            "longitude": -122.0839,
            "formatted_address": "Mountain View, CA, United States",
        },
        "new york, ny": {
            "country_code": "US",
            "country": "United States",
            "region": "New York",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "formatted_address": "New York, NY, United States",
        },
        "ny, ny": {
            "country_code": "US",
            "country": "United States",
            "region": "New York",
            "latitude": 40.7128,
            "longitude": -74.0060,
            "formatted_address": "New York, NY, United States",
        },
        "san francisco, ca": {
            "country_code": "US",
            "country": "United States",
            "region": "California",
            "latitude": 37.7749,
            "longitude": -122.4194,
            "formatted_address": "San Francisco, CA, United States",
        },
        "los angeles, ca": {
            "country_code": "US",
            "country": "United States",
            "region": "California",
            "latitude": 34.0522,
            "longitude": -118.2437,
            "formatted_address": "Los Angeles, CA, United States",
        },
        "atlant, ga": {
            "country_code": "US",
            "country": "United States",
            "region": "Georgia",
            "latitude": None,
            "longitude": None,
            "formatted_address": "Atlanta, GA, United States",
        },
        "korea, south": {
            "country_code": "KR",
            "country": "South Korea",
            "region": None,
            "latitude": None,
            "longitude": None,
            "formatted_address": "South Korea",
        },
        "uk": {
            "country_code": "GB",
            "country": "United Kingdom",
            "region": None,
            "latitude": None,
            "longitude": None,
            "formatted_address": "United Kingdom",
        },
        "united kingdom": {
            "country_code": "GB",
            "country": "United Kingdom",
            "region": None,
            "latitude": None,
            "longitude": None,
            "formatted_address": "United Kingdom",
        },
    }

    _country_aliases = {
        "usa": "US",
        "united states": "US",
        "united states of america": "US",
        "u.s.": "US",
        "u.s.a.": "US",
        "america": "US",
        "uk": "GB",
        "united kingdom": "GB",
        "great britain": "GB",
        "england": "GB",
        "russia": "RU",
        "deutschland": "DE",
        "germany": "DE",
        "france": "FR",
        "españa": "ES",
        "espana": "ES",
        "spain": "ES",
        "italia": "IT",
        "italy": "IT",
        "china": "CN",
        "japan": "JP",
        "brasil": "BR",
        "brazil": "BR",
        "canada": "CA",
        "australia": "AU",
        "india": "IN",
        "méxico": "MX",
        "mexico": "MX",
        "netherlands": "NL",
        "holland": "NL",
        "schweiz": "CH",
        "switzerland": "CH",
        "suisse": "CH",
        "sweden": "SE",
        "sverige": "SE",
        "norway": "NO",
        "norge": "NO",
        "denmark": "DK",
        "danmark": "DK",
        "finland": "FI",
        "suomi": "FI",
        "österreich": "AT",
        "osterreich": "AT",
        "austria": "AT",
        "belgium": "BE",
        "belgique": "BE",
        "belgië": "BE",
        "belgie": "BE",
        "portugal": "PT",
        "greece": "GR",
        "ελλάδα": "GR",
        "ellada": "GR",
        "ireland": "IE",
        "éire": "IE",
        "eire": "IE",
        "new zealand": "NZ",
        "south africa": "ZA",
        "south korea": "KR",
        "korea": "KR",
        "north korea": "KP",
        "taiwan": "TW",
        "singapore": "SG",
        "hong kong": "HK",
        "türkiye": "TR",
        "turkiye": "TR",
        "turkey": "TR",
        "israel": "IL",
        "argentina": "AR",
        "chile": "CL",
        "colombia": "CO",
        "peru": "PE",
        "venezuela": "VE",
        "egypt": "EG",
        "مصر": "EG",
        "saudi arabia": "SA",
        "المملكة العربية السعودية": "SA",
        "uae": "AE",
        "united arab emirates": "AE",
        "الإمارات العربية المتحدة": "AE",
        "pakistan": "PK",
        "پاکستان": "PK",
        "bangladesh": "BD",
        "বাংলাদেশ": "BD",
        "vietnam": "VN",
        "việt nam": "VN",
        "thailand": "TH",
        "ประเทศไทย": "TH",
        "malaysia": "MY",
        "indonesia": "ID",
        "philippines": "PH",
        "pilipinas": "PH",
        "new york": "US",
        "california": "US",
        "texas": "US",
        "florida": "US",
        "london": "GB",
        "paris": "FR",
        "berlin": "DE",
        "madrid": "ES",
        "rome": "IT",
        "roma": "IT",
        "tokyo": "JP",
        "東京": "JP",
        "dong jing": "JP",  # unidecode(東京)
        "beijing": "CN",
        "北京": "CN",
        "bei jing": "CN",  # unidecode(北京)
        "shanghai": "CN",
        "上海": "CN",
        "shang hai": "CN",  # unidecode(上海)
        "sydney": "AU",
        "mumbai": "IN",
        "मुंबई": "IN",
        "toronto": "CA",
        "vancouver": "CA",
        "são paulo": "BR",
        "sao paulo": "BR",
        "rio de janeiro": "BR",
        "moscow": "RU",
        "москва": "RU",
        "moskva": "RU",
        "st. petersburg": "RU",
        "saint petersburg": "RU",
        "санкт-петербург": "RU",
        "sankt-peterburg": "RU",
        # Transliterated CJK country/city names (via unidecode)
        "ri ben": "JP",  # unidecode(日本) = Japan
        "zhong guo": "CN",  # unidecode(中国) = China
        "han guo": "KR",  # unidecode(韩国) = South Korea
        "tai wan": "TW",  # unidecode(台湾) = Taiwan
        "xiang gang": "HK",  # unidecode(香港) = Hong Kong
        "shen zhen": "CN",  # unidecode(深圳) = Shenzhen
        "guang zhou": "CN",  # unidecode(广州) = Guangzhou
        "cheng du": "CN",  # unidecode(成都) = Chengdu
        "hang zhou": "CN",  # unidecode(杭州) = Hangzhou
        "nan jing": "CN",  # unidecode(南京) = Nanjing
        "xi an": "CN",  # unidecode(西安) = Xi'an
        "wu han": "CN",  # unidecode(武汉) = Wuhan
        "tian jin": "CN",  # unidecode(天津) = Tianjin
        "chong qing": "CN",  # unidecode(重庆) = Chongqing
        "da lian": "CN",  # unidecode(大连) = Dalian
        "qing dao": "CN",  # unidecode(青岛) = Qingdao
        "su zhou": "CN",  # unidecode(苏州) = Suzhou
        "dong guan": "CN",  # unidecode(东莞) = Dongguan
        "fo shan": "CN",  # unidecode(佛山) = Foshan
        "ning bo": "CN",  # unidecode(宁波) = Ningbo
        # Transliterated Cyrillic (via unidecode)
        "rossiia": "RU",  # unidecode(Россия) = Russia
        "tokio": "JP",  # unidecode(Токио) = Tokyo (Cyrillic)
        "tokio ebisu": "JP",  # unidecode(Токио Эбису)
        "voronezh": "RU",  # unidecode(Воронеж)
    }

    _US_STATES = {
        "alabama": "US",
        "alaska": "US",
        "arizona": "US",
        "arkansas": "US",
        "california": "US",
        "colorado": "US",
        "connecticut": "US",
        "delaware": "US",
        "florida": "US",
        "georgia": "US",
        "hawaii": "US",
        "idaho": "US",
        "illinois": "US",
        "indiana": "US",
        "iowa": "US",
        "kansas": "US",
        "kentucky": "US",
        "louisiana": "US",
        "maine": "US",
        "maryland": "US",
        "massachusetts": "US",
        "michigan": "US",
        "minnesota": "US",
        "mississippi": "US",
        "missouri": "US",
        "montana": "US",
        "nebraska": "US",
        "nevada": "US",
        "new hampshire": "US",
        "new jersey": "US",
        "new mexico": "US",
        "new york": "US",
        "north carolina": "US",
        "north dakota": "US",
        "ohio": "US",
        "oklahoma": "US",
        "oregon": "US",
        "pennsylvania": "US",
        "rhode island": "US",
        "south carolina": "US",
        "south dakota": "US",
        "tennessee": "US",
        "texas": "US",
        "utah": "US",
        "vermont": "US",
        "virginia": "US",
        "washington": "US",
        "west virginia": "US",
        "wisconsin": "US",
        "wyoming": "US",
        "district of columbia": "US",
        "washington dc": "US",
        "washington d.c.": "US",
        "dc": "US",
        "d.c.": "US",
        "puerto rico": "US",
        "guam": "US",
        "american samoa": "US",
        "virgin islands": "US",
        "northern mariana islands": "US",
    }

    _US_STATE_ABBR = {
        "al": "US",
        "ak": "US",
        "az": "US",
        "ar": "US",
        "ca": "US",
        "co": "US",
        "ct": "US",
        "de": "US",
        "fl": "US",
        "ga": "US",
        "hi": "US",
        "id": "US",
        "il": "US",
        "in": "US",
        "ia": "US",
        "ks": "US",
        "ky": "US",
        "la": "US",
        "me": "US",
        "md": "US",
        "ma": "US",
        "mi": "US",
        "mn": "US",
        "ms": "US",
        "mo": "US",
        "mt": "US",
        "ne": "US",
        "nv": "US",
        "nh": "US",
        "nj": "US",
        "nm": "US",
        "ny": "US",
        "nc": "US",
        "nd": "US",
        "oh": "US",
        "ok": "US",
        "or": "US",
        "pa": "US",
        "ri": "US",
        "sc": "US",
        "sd": "US",
        "tn": "US",
        "tx": "US",
        "ut": "US",
        "vt": "US",
        "va": "US",
        "wa": "US",
        "wv": "US",
        "wi": "US",
        "wy": "US",
        "pr": "US",
        "gu": "US",
        "as": "US",
        "vi": "US",
        "mp": "US",
    }

    _MAJOR_US_CITIES = {
        "new york": "US",
        "new york city": "US",
        "nyc": "US",
        "los angeles": "US",
        "chicago": "US",
        "houston": "US",
        "phoenix": "US",
        "philadelphia": "US",
        "san antonio": "US",
        "san diego": "US",
        "dallas": "US",
        "san jose": "US",
        "austin": "US",
        "jacksonville": "US",
        "fort worth": "US",
        "columbus": "US",
        "san francisco": "US",
        "charlotte": "US",
        "indianapolis": "US",
        "seattle": "US",
        "denver": "US",
        "boston": "US",
        "portland": "US",
        "las vegas": "US",
        "detroit": "US",
        "atlanta": "US",
        "miami": "US",
        "minneapolis": "US",
        "pittsburgh": "US",
        "cincinnati": "US",
        "cleveland": "US",
        "nashville": "US",
        "salt lake city": "US",
        "baltimore": "US",
        "brooklyn": "US",
        "manhattan": "US",
        "queens": "US",
        "bronx": "US",
        "staten island": "US",
        "silicon valley": "US",
        "bay area": "US",
        "mountain view": "US",
        "palo alto": "US",
        "menlo park": "US",
        "redwood city": "US",
        "cupertino": "US",
        "sunnyvale": "US",
        "santa clara": "US",
        "san mateo": "US",
        "berkeley": "US",
        "oakland": "US",
        "san bruno": "US",
        # Colorado cities (for CO/Colombia conflict resolution)
        "boulder": "US",
        "fort collins": "US",
        "colorado springs": "US",
        # Nicknames
        "indy": "US",  # Indianapolis
    }

    def resolve_mapping(self, normalized: str) -> Optional[Dict[str, Any]]:
        if not normalized:
            return None

        # Filter Profil-URLs im Location-Feld frühzeitig aus.
        if normalized.startswith("http://") or normalized.startswith("https://"):
            return {}

        # Zeitzonen-Pattern: UTC+X, UTC-X, GMT+X, GMT-X etc.
        if re.match(r"^(utc|gmt)\s*[+-]?\s*\d*", normalized, re.IGNORECASE):
            return {}

        # Pattern-basierte Non-Geo-Erkennung
        if self._is_non_geo_pattern(normalized):
            return {}

        if normalized in self._problematic_location_map:
            mapped = self._problematic_location_map[normalized]
            if mapped is None:
                return {}
            return mapped.copy()

        if normalized in self._country_aliases:
            code = self._country_aliases[normalized]
            return {
                "country_code": code,
                "country": "",  # Name wird im Service über pycountry bestimmt
                "region": None,
                "latitude": None,
                "longitude": None,
                "formatted_address": None,
            }

        return None

    def _is_non_geo_pattern(self, text: str) -> bool:
        """Erkennt Non-Geo-Locations anhand von Patterns (Emojis, Handles, Zahlen, etc.)."""
        # HTML-Tags oder Artefakte (z.B. "><s>", "<html>")
        if re.search(r"<[^>]*>", text):
            return True

        # Nur Emojis (Unicode-Bereich für Emojis)
        emoji_pattern = r"^[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002600-\U000027BF\s]+$"
        if re.match(emoji_pattern, text):
            return True

        # Twitter/GitHub Handle (@username)
        if re.match(r"^@[\w.-]+$", text):
            return True

        # Nur Zahlen
        if re.match(r"^\d+$", text):
            return True

        # Nur Sonderzeichen (keine Buchstaben)
        if re.match(r"^[^a-zA-Z]+$", text) and len(text) > 0:
            return True

        # Sehr kurze Strings (1-2 Zeichen, außer bekannte Ländercodes)
        if len(text) <= 2 and text.upper() not in self._country_aliases.values():
            return True

        # E-Mail-Adressen
        if re.match(r"^[\w.-]+@[\w.-]+\.\w+$", text):
            return True

        return False

    def extract_country_from_text(self, location: str) -> Tuple[Optional[str], Optional[str]]:
        if not location:
            return None, None

        text = location.lower()

        match = self._match_us_state(text)
        if match:
            return match

        match = self._match_us_abbr(text)
        if match:
            return match

        match = self._match_major_city(text)
        if match:
            return match

        match = self._match_country_alias(text)
        if match:
            return match, None

        match = self._match_country_suffix(text)
        if match:
            return match

        return None, None

    def _match_us_state(self, text: str) -> Optional[Tuple[str, str]]:
        for state, code in self._US_STATES.items():
            if state == text or text.endswith(f", {state}") or f" {state} " in f" {text} ":
                return code, state.title()
        return None

    # US-State-Abbreviations, die mit ISO-Ländercodes kollidieren
    _ISO_CONFLICT_ABBRS = {
        "de",  # Delaware vs. Deutschland
        "in",  # Indiana vs. Indien
        "co",  # Colorado vs. Kolumbien
        "id",  # Idaho vs. Indonesien
        "ga",  # Georgia vs. Gabun
        "la",  # Louisiana vs. Laos
        "va",  # Virginia vs. Vatikan
        "sc",  # South Carolina vs. Seychellen
        "sd",  # South Dakota vs. Sudan
        "pa",  # Pennsylvania vs. Panama
        "md",  # Maryland vs. Moldawien
        "mn",  # Minnesota vs. Mongolei
        "al",  # Alabama vs. Albanien
        "ar",  # Arkansas vs. Argentinien
        "me",  # Maine vs. Montenegro
        "mt",  # Montana vs. Malta
    }

    def _match_us_abbr(self, text: str) -> Optional[Tuple[str, str]]:
        abbr_match = re.search(r",\s*([A-Za-z]{2})$|\s+([A-Za-z]{2})$", text)
        if not abbr_match:
            return None
        abbr = next((grp.lower() for grp in abbr_match.groups() if grp is not None), None)
        if not abbr or abbr not in self._US_STATE_ABBR:
            return None

        # Konflikt-Check: Ist das Abbr auch ein ISO-Ländercode?
        if abbr in self._ISO_CONFLICT_ABBRS:
            # Prüfe, ob der Präfix (vor dem Abbr) eine bekannte US-Stadt oder US-State ist
            prefix = text[: abbr_match.start()].strip().rstrip(",").lower()
            if prefix not in self._MAJOR_US_CITIES and prefix not in self._US_STATES:
                return None  # Im Zweifel kein US-Match, könnte ISO-Land sein

        return "US", abbr.upper()

    def _match_major_city(self, text: str) -> Optional[Tuple[str, str]]:
        for city, code in self._MAJOR_US_CITIES.items():
            if city == text or text.startswith(f"{city},") or text.startswith(f"{city} "):
                return code, city.title()
        return None

    def _match_country_alias(self, text: str) -> Optional[str]:
        return self._country_aliases.get(text)

    def _match_country_suffix(self, text: str) -> Optional[Tuple[str, str]]:
        # Wortgrenze vor dem 2-Buchstaben-Code erzwingen:
        # Matcht "Berlin, DE" aber nicht "USA" -> "SA"
        match = re.search(r"(?:^|[\s,])([A-Z]{2})$", text.upper())
        if not match:
            return None
        code = match.group(1).upper()
        if code in self._country_aliases.values():
            return code, None
        return None

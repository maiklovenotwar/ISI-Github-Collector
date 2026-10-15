"""Gebiete mit eigenem ISO-3166-1-Code (Entscheidung E15).

Nominatim führt Hongkong und Macau unter China, Puerto Rico und Guam unter den USA und die
französischen Überseegebiete unter Frankreich, Taiwan, Jersey oder Grönland dagegen unter
ihrem eigenen Code. Die Textregeln zählen Puerto Rico und Guam zu den US-Bundesstaaten.
Damit ein Gebiet unabhängig von der Quelle gleich gezählt wird, bekommt jedes Ergebnis den
eigenen Code des Gebiets, wenn Region oder ein Teil der Adresse genau dessen Namen trägt.
"""

from __future__ import annotations

from typing import Any, Dict

import pycountry

# Staat, wie Nominatim ihn meldet -> Name des Gebiets (klein) -> eigener ISO-Code
TERRITORIES: Dict[str, Dict[str, str]] = {
    "CN": {
        "hong kong": "HK",
        "new territories": "HK",
        "kowloon": "HK",
        "macau": "MO",
        "macao": "MO",
    },
    "US": {
        "puerto rico": "PR",
        "guam": "GU",
        "united states virgin islands": "VI",
        "u.s. virgin islands": "VI",
        "american samoa": "AS",
        "northern mariana islands": "MP",
    },
    "FR": {
        "réunion": "RE",
        "reunion": "RE",
        "guadeloupe": "GP",
        "martinique": "MQ",
        "french guiana": "GF",
        "mayotte": "YT",
        "french polynesia": "PF",
        "new caledonia": "NC",
        "saint pierre and miquelon": "PM",
        "wallis and futuna": "WF",
        "saint barthélemy": "BL",
        "saint martin (france)": "MF",
    },
    "NL": {
        "aruba": "AW",
        "curaçao": "CW",
        "curacao": "CW",
        "sint maarten": "SX",
        "caribbean netherlands": "BQ",
        "bonaire": "BQ",
        "sint eustatius": "BQ",
        "saba": "BQ",
    },
    "AU": {
        "christmas island": "CX",
        "norfolk island": "NF",
        "cocos (keeling) islands": "CC",
    },
    "NO": {
        "svalbard": "SJ",
        "jan mayen": "SJ",
    },
}


def apply_territory(result: Dict[str, Any]) -> Dict[str, Any]:
    """Ergebnis mit dem eigenen Code des Gebiets; unverändert, wenn keines passt."""
    code = (result.get("country_code") or "").upper()
    names = TERRITORIES.get(code)
    if not names:
        return result
    parts = [result.get("region") or ""]
    parts += (result.get("formatted_address") or "").split(",")
    for part in parts:
        territory = names.get(part.strip().lower())
        if territory:
            country = pycountry.countries.get(alpha_2=territory)
            return {
                **result,
                "country_code": territory,
                "country": getattr(country, "name", result.get("country")),
            }
    return result

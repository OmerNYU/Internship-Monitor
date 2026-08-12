"""Project-maintained country-to-region taxonomy for geographic classification."""

from __future__ import annotations

import re

EMEA_COUNTRIES = (
    "Albania",
    "Algeria",
    "Andorra",
    "Angola",
    "Armenia",
    "Austria",
    "Azerbaijan",
    "Bahrain",
    "Belarus",
    "Belgium",
    "Benin",
    "Bosnia and Herzegovina",
    "Botswana",
    "Bulgaria",
    "Burkina Faso",
    "Burundi",
    "Cabo Verde",
    "Cameroon",
    "Central African Republic",
    "Chad",
    "Comoros",
    "Croatia",
    "Cyprus",
    "Czechia",
    "Democratic Republic of the Congo",
    "Denmark",
    "Djibouti",
    "Egypt",
    "Equatorial Guinea",
    "Eritrea",
    "Estonia",
    "Eswatini",
    "Ethiopia",
    "Finland",
    "France",
    "Gabon",
    "Gambia",
    "Georgia",
    "Germany",
    "Ghana",
    "Greece",
    "Guinea",
    "Guinea-Bissau",
    "Hungary",
    "Iceland",
    "Iran",
    "Iraq",
    "Ireland",
    "Israel",
    "Italy",
    "Ivory Coast",
    "Jordan",
    "Kenya",
    "Kosovo",
    "Kuwait",
    "Latvia",
    "Lebanon",
    "Lesotho",
    "Liberia",
    "Libya",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Madagascar",
    "Malawi",
    "Mali",
    "Malta",
    "Mauritania",
    "Mauritius",
    "Moldova",
    "Monaco",
    "Montenegro",
    "Morocco",
    "Mozambique",
    "Namibia",
    "Netherlands",
    "Niger",
    "Nigeria",
    "North Macedonia",
    "Norway",
    "Oman",
    "Palestine",
    "Poland",
    "Portugal",
    "Qatar",
    "Republic of the Congo",
    "Romania",
    "Rwanda",
    "San Marino",
    "Saudi Arabia",
    "Senegal",
    "Serbia",
    "Seychelles",
    "Sierra Leone",
    "Slovakia",
    "Slovenia",
    "Somalia",
    "South Africa",
    "South Sudan",
    "Spain",
    "Sudan",
    "Sweden",
    "Switzerland",
    "Syria",
    "Tanzania",
    "Togo",
    "Tunisia",
    "Turkey",
    "Uganda",
    "Ukraine",
    "United Arab Emirates",
    "United Kingdom",
    "Vatican City",
    "Yemen",
    "Zambia",
    "Zimbabwe",
)

APAC_COUNTRIES = (
    "Afghanistan",
    "Australia",
    "Bangladesh",
    "Bhutan",
    "Brunei",
    "Cambodia",
    "China",
    "Fiji",
    "India",
    "Indonesia",
    "Japan",
    "Kiribati",
    "Laos",
    "Malaysia",
    "Maldives",
    "Marshall Islands",
    "Micronesia",
    "Mongolia",
    "Myanmar",
    "Nauru",
    "Nepal",
    "New Zealand",
    "North Korea",
    "Pakistan",
    "Palau",
    "Papua New Guinea",
    "Philippines",
    "Samoa",
    "Singapore",
    "Solomon Islands",
    "South Korea",
    "Sri Lanka",
    "Taiwan",
    "Thailand",
    "Timor-Leste",
    "Tonga",
    "Tuvalu",
    "Vanuatu",
    "Vietnam",
)

AMERICAS_COUNTRIES = (
    "Argentina",
    "Brazil",
    "Canada",
    "Chile",
    "Colombia",
    "Mexico",
    "Peru",
    "United States",
    "Uruguay",
)

COUNTRY_ALIASES = {
    "czech republic": "Czechia",
    "democratic republic of congo": "Democratic Republic of the Congo",
    "drc": "Democratic Republic of the Congo",
    "england": "United Kingdom",
    "great britain": "United Kingdom",
    "hong kong": "China",
    "macau": "China",
    "south korea": "South Korea",
    "uae": "United Arab Emirates",
    "uk": "United Kingdom",
    "united states of america": "United States",
    "usa": "United States",
    "us": "United States",
    "viet nam": "Vietnam",
}


def _normalize(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


def _build_regions() -> dict[str, str]:
    regions: dict[str, str] = {}
    for country in EMEA_COUNTRIES:
        regions[_normalize(country)] = "EMEA"
    for country in APAC_COUNTRIES:
        regions[_normalize(country)] = "APAC"
    for country in AMERICAS_COUNTRIES:
        regions[_normalize(country)] = "Americas"
    return regions


REGION_BY_COUNTRY = _build_regions()


def country_from_location(location: str | None) -> str | None:
    """Find the longest known country or configured alias present in a listing location."""
    if location is None:
        return None
    normalized_location = f" {_normalize(location)} "
    aliases = {**{country: country for country in REGION_BY_COUNTRY}, **COUNTRY_ALIASES}
    matches = [alias for alias in aliases if f" {alias} " in normalized_location]
    if not matches:
        return None
    canonical = aliases[max(matches, key=len)]
    return next(
        country
        for country in (*EMEA_COUNTRIES, *APAC_COUNTRIES, *AMERICAS_COUNTRIES)
        if _normalize(country) == canonical
    )


def region_for_country(country: str | None) -> str | None:
    """Return the project taxonomy's region for a canonical country name."""
    if country is None:
        return None
    return REGION_BY_COUNTRY.get(_normalize(country))

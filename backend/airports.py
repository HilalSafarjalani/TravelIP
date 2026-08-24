"""Reverse-DNS hostname -> city inference, via IATA airport codes.

ISP and backbone router hostnames very often embed the IATA code of the
nearest major airport as a standalone, dot- or hyphen-delimited label --
e.g. "ae0.cr1.lax1.us.example.net" (Los Angeles) or "0/0/0.gw.par1.example.net"
(Paris). This is a well-known convention among network engineers manually
reading traceroutes, and it's the cheapest, most reliable signal available
for a hop that normal IP geolocation couldn't place (interconnection/
backbone router IPs are notoriously inaccurate in GeoIP databases).

This is a best-effort heuristic, not a database lookup: it can both miss
(no code in the hostname, or an unlisted airport) and false-positive (a
label that happens to match a code by coincidence). The table below is
deliberately limited to major hub airports/metro codes, and deliberately
excludes a handful of real IATA codes that collide with extremely common
networking terms (e.g. "GIG" for Rio de Janeiro, which collides with
"gigabitethernet" abbreviations like "gig0") to keep the false-positive
rate low. Anything this produces is surfaced as `inferred`, never as a
measured fact.
"""

from __future__ import annotations

import re
from typing import Optional, TypedDict


class AirportHit(TypedDict):
    city: str
    country: str
    country_code: str
    lat: float
    lon: float


# code -> (city, country, country_code, lat, lon). Deliberately not
# exhaustive -- covers major internet backbone hub metros only.
AIRPORT_CODES: dict[str, AirportHit] = {
    # North America
    "LAX": {"city": "Los Angeles", "country": "United States", "country_code": "US", "lat": 33.9425, "lon": -118.4080},
    "SFO": {"city": "San Francisco", "country": "United States", "country_code": "US", "lat": 37.6213, "lon": -122.3790},
    "SJC": {"city": "San Jose", "country": "United States", "country_code": "US", "lat": 37.3639, "lon": -121.9289},
    "SEA": {"city": "Seattle", "country": "United States", "country_code": "US", "lat": 47.4502, "lon": -122.3088},
    "PDX": {"city": "Portland", "country": "United States", "country_code": "US", "lat": 45.5898, "lon": -122.5951},
    "DEN": {"city": "Denver", "country": "United States", "country_code": "US", "lat": 39.8561, "lon": -104.6737},
    "ORD": {"city": "Chicago", "country": "United States", "country_code": "US", "lat": 41.9742, "lon": -87.9073},
    "DFW": {"city": "Dallas", "country": "United States", "country_code": "US", "lat": 32.8998, "lon": -97.0403},
    "IAH": {"city": "Houston", "country": "United States", "country_code": "US", "lat": 29.9902, "lon": -95.3368},
    "ATL": {"city": "Atlanta", "country": "United States", "country_code": "US", "lat": 33.6407, "lon": -84.4277},
    "MIA": {"city": "Miami", "country": "United States", "country_code": "US", "lat": 25.7959, "lon": -80.2870},
    "JFK": {"city": "New York", "country": "United States", "country_code": "US", "lat": 40.6413, "lon": -73.7781},
    "EWR": {"city": "Newark", "country": "United States", "country_code": "US", "lat": 40.6895, "lon": -74.1745},
    "BOS": {"city": "Boston", "country": "United States", "country_code": "US", "lat": 42.3656, "lon": -71.0096},
    "IAD": {"city": "Washington DC", "country": "United States", "country_code": "US", "lat": 38.9531, "lon": -77.4565},
    "PHL": {"city": "Philadelphia", "country": "United States", "country_code": "US", "lat": 39.8744, "lon": -75.2424},
    "MSP": {"city": "Minneapolis", "country": "United States", "country_code": "US", "lat": 44.8848, "lon": -93.2223},
    "DTW": {"city": "Detroit", "country": "United States", "country_code": "US", "lat": 42.2124, "lon": -83.3534},
    "PHX": {"city": "Phoenix", "country": "United States", "country_code": "US", "lat": 33.4352, "lon": -112.0101},
    "LAS": {"city": "Las Vegas", "country": "United States", "country_code": "US", "lat": 36.0840, "lon": -115.1537},
    "SLC": {"city": "Salt Lake City", "country": "United States", "country_code": "US", "lat": 40.7899, "lon": -111.9791},
    "STL": {"city": "St. Louis", "country": "United States", "country_code": "US", "lat": 38.7487, "lon": -90.3700},
    "CLE": {"city": "Cleveland", "country": "United States", "country_code": "US", "lat": 41.4117, "lon": -81.8498},
    "CVG": {"city": "Cincinnati", "country": "United States", "country_code": "US", "lat": 39.0489, "lon": -84.6678},
    "RDU": {"city": "Raleigh", "country": "United States", "country_code": "US", "lat": 35.8801, "lon": -78.7880},
    "CLT": {"city": "Charlotte", "country": "United States", "country_code": "US", "lat": 35.2144, "lon": -80.9473},
    "MCO": {"city": "Orlando", "country": "United States", "country_code": "US", "lat": 28.4312, "lon": -81.3081},
    "TPA": {"city": "Tampa", "country": "United States", "country_code": "US", "lat": 27.9755, "lon": -82.5332},
    "SAN": {"city": "San Diego", "country": "United States", "country_code": "US", "lat": 32.7338, "lon": -117.1933},
    "PIT": {"city": "Pittsburgh", "country": "United States", "country_code": "US", "lat": 40.4915, "lon": -80.2329},
    "BWI": {"city": "Baltimore", "country": "United States", "country_code": "US", "lat": 39.1774, "lon": -76.6684},
    "YYZ": {"city": "Toronto", "country": "Canada", "country_code": "CA", "lat": 43.6777, "lon": -79.6248},
    "YVR": {"city": "Vancouver", "country": "Canada", "country_code": "CA", "lat": 49.1967, "lon": -123.1815},
    "YUL": {"city": "Montreal", "country": "Canada", "country_code": "CA", "lat": 45.4706, "lon": -73.7408},
    "YOW": {"city": "Ottawa", "country": "Canada", "country_code": "CA", "lat": 45.3225, "lon": -75.6692},
    "YYC": {"city": "Calgary", "country": "Canada", "country_code": "CA", "lat": 51.1215, "lon": -114.0076},
    "MEX": {"city": "Mexico City", "country": "Mexico", "country_code": "MX", "lat": 19.4363, "lon": -99.0721},
    # South America
    "GRU": {"city": "Sao Paulo", "country": "Brazil", "country_code": "BR", "lat": -23.4356, "lon": -46.4731},
    "EZE": {"city": "Buenos Aires", "country": "Argentina", "country_code": "AR", "lat": -34.8222, "lon": -58.5358},
    "SCL": {"city": "Santiago", "country": "Chile", "country_code": "CL", "lat": -33.3930, "lon": -70.7858},
    "BOG": {"city": "Bogota", "country": "Colombia", "country_code": "CO", "lat": 4.7016, "lon": -74.1469},
    "LIM": {"city": "Lima", "country": "Peru", "country_code": "PE", "lat": -12.0219, "lon": -77.1143},
    "UIO": {"city": "Quito", "country": "Ecuador", "country_code": "EC", "lat": -0.1292, "lon": -78.3575},
    "CCS": {"city": "Caracas", "country": "Venezuela", "country_code": "VE", "lat": 10.6013, "lon": -66.9911},
    # Europe
    "LHR": {"city": "London", "country": "United Kingdom", "country_code": "GB", "lat": 51.4700, "lon": -0.4543},
    "LGW": {"city": "London", "country": "United Kingdom", "country_code": "GB", "lat": 51.1481, "lon": -0.1903},
    "CDG": {"city": "Paris", "country": "France", "country_code": "FR", "lat": 49.0097, "lon": 2.5479},
    "ORY": {"city": "Paris", "country": "France", "country_code": "FR", "lat": 48.7233, "lon": 2.3794},
    "AMS": {"city": "Amsterdam", "country": "Netherlands", "country_code": "NL", "lat": 52.3105, "lon": 4.7683},
    "FRA": {"city": "Frankfurt", "country": "Germany", "country_code": "DE", "lat": 50.0379, "lon": 8.5622},
    "MUC": {"city": "Munich", "country": "Germany", "country_code": "DE", "lat": 48.3538, "lon": 11.7861},
    "BER": {"city": "Berlin", "country": "Germany", "country_code": "DE", "lat": 52.3667, "lon": 13.5033},
    "HAM": {"city": "Hamburg", "country": "Germany", "country_code": "DE", "lat": 53.6304, "lon": 9.9882},
    "DUS": {"city": "Dusseldorf", "country": "Germany", "country_code": "DE", "lat": 51.2895, "lon": 6.7668},
    "MAD": {"city": "Madrid", "country": "Spain", "country_code": "ES", "lat": 40.4983, "lon": -3.5676},
    "BCN": {"city": "Barcelona", "country": "Spain", "country_code": "ES", "lat": 41.2974, "lon": 2.0833},
    "MXP": {"city": "Milan", "country": "Italy", "country_code": "IT", "lat": 45.6306, "lon": 8.7281},
    "FCO": {"city": "Rome", "country": "Italy", "country_code": "IT", "lat": 41.8003, "lon": 12.2389},
    "ZRH": {"city": "Zurich", "country": "Switzerland", "country_code": "CH", "lat": 47.4647, "lon": 8.5492},
    "GVA": {"city": "Geneva", "country": "Switzerland", "country_code": "CH", "lat": 46.2381, "lon": 6.1090},
    "VIE": {"city": "Vienna", "country": "Austria", "country_code": "AT", "lat": 48.1103, "lon": 16.5697},
    "BRU": {"city": "Brussels", "country": "Belgium", "country_code": "BE", "lat": 50.9014, "lon": 4.4844},
    "LUX": {"city": "Luxembourg", "country": "Luxembourg", "country_code": "LU", "lat": 49.6233, "lon": 6.2044},
    "ARN": {"city": "Stockholm", "country": "Sweden", "country_code": "SE", "lat": 59.6519, "lon": 17.9186},
    "OSL": {"city": "Oslo", "country": "Norway", "country_code": "NO", "lat": 60.1939, "lon": 11.1004},
    "CPH": {"city": "Copenhagen", "country": "Denmark", "country_code": "DK", "lat": 55.6180, "lon": 12.6560},
    "HEL": {"city": "Helsinki", "country": "Finland", "country_code": "FI", "lat": 60.3172, "lon": 24.9633},
    "DUB": {"city": "Dublin", "country": "Ireland", "country_code": "IE", "lat": 53.4213, "lon": -6.2701},
    "WAW": {"city": "Warsaw", "country": "Poland", "country_code": "PL", "lat": 52.1657, "lon": 20.9671},
    "PRG": {"city": "Prague", "country": "Czechia", "country_code": "CZ", "lat": 50.1008, "lon": 14.2632},
    "BUD": {"city": "Budapest", "country": "Hungary", "country_code": "HU", "lat": 47.4298, "lon": 19.2611},
    "ATH": {"city": "Athens", "country": "Greece", "country_code": "GR", "lat": 37.9364, "lon": 23.9445},
    "IST": {"city": "Istanbul", "country": "Turkey", "country_code": "TR", "lat": 41.2753, "lon": 28.7519},
    "LIS": {"city": "Lisbon", "country": "Portugal", "country_code": "PT", "lat": 38.7742, "lon": -9.1342},
    "OPO": {"city": "Porto", "country": "Portugal", "country_code": "PT", "lat": 41.2481, "lon": -8.6814},
    "SOF": {"city": "Sofia", "country": "Bulgaria", "country_code": "BG", "lat": 42.6952, "lon": 23.4062},
    "OTP": {"city": "Bucharest", "country": "Romania", "country_code": "RO", "lat": 44.5711, "lon": 26.0850},
    "ZAG": {"city": "Zagreb", "country": "Croatia", "country_code": "HR", "lat": 45.7429, "lon": 16.0688},
    "KBP": {"city": "Kyiv", "country": "Ukraine", "country_code": "UA", "lat": 50.3450, "lon": 30.8947},
    "RIX": {"city": "Riga", "country": "Latvia", "country_code": "LV", "lat": 56.9236, "lon": 23.9711},
    "TLL": {"city": "Tallinn", "country": "Estonia", "country_code": "EE", "lat": 59.4133, "lon": 24.8328},
    "VNO": {"city": "Vilnius", "country": "Lithuania", "country_code": "LT", "lat": 54.6341, "lon": 25.2858},
    "LED": {"city": "St. Petersburg", "country": "Russia", "country_code": "RU", "lat": 59.8003, "lon": 30.2625},
    "SVO": {"city": "Moscow", "country": "Russia", "country_code": "RU", "lat": 55.9736, "lon": 37.4125},
    # Middle East / Africa
    "DXB": {"city": "Dubai", "country": "United Arab Emirates", "country_code": "AE", "lat": 25.2532, "lon": 55.3657},
    "AUH": {"city": "Abu Dhabi", "country": "United Arab Emirates", "country_code": "AE", "lat": 24.4330, "lon": 54.6511},
    "DOH": {"city": "Doha", "country": "Qatar", "country_code": "QA", "lat": 25.2731, "lon": 51.6080},
    "TLV": {"city": "Tel Aviv", "country": "Israel", "country_code": "IL", "lat": 32.0114, "lon": 34.8867},
    "CAI": {"city": "Cairo", "country": "Egypt", "country_code": "EG", "lat": 30.1219, "lon": 31.4056},
    "JNB": {"city": "Johannesburg", "country": "South Africa", "country_code": "ZA", "lat": -26.1392, "lon": 28.2460},
    "CPT": {"city": "Cape Town", "country": "South Africa", "country_code": "ZA", "lat": -33.9715, "lon": 18.6021},
    "LOS": {"city": "Lagos", "country": "Nigeria", "country_code": "NG", "lat": 6.5774, "lon": 3.3212},
    "NBO": {"city": "Nairobi", "country": "Kenya", "country_code": "KE", "lat": -1.3192, "lon": 36.9278},
    "ADD": {"city": "Addis Ababa", "country": "Ethiopia", "country_code": "ET", "lat": 8.9779, "lon": 38.7993},
    "CMN": {"city": "Casablanca", "country": "Morocco", "country_code": "MA", "lat": 33.3675, "lon": -7.5900},
    # Asia-Pacific
    "SIN": {"city": "Singapore", "country": "Singapore", "country_code": "SG", "lat": 1.3644, "lon": 103.9915},
    "HKG": {"city": "Hong Kong", "country": "Hong Kong", "country_code": "HK", "lat": 22.3080, "lon": 113.9185},
    "NRT": {"city": "Tokyo", "country": "Japan", "country_code": "JP", "lat": 35.7720, "lon": 140.3929},
    "HND": {"city": "Tokyo", "country": "Japan", "country_code": "JP", "lat": 35.5494, "lon": 139.7798},
    "ICN": {"city": "Seoul", "country": "South Korea", "country_code": "KR", "lat": 37.4602, "lon": 126.4407},
    "PVG": {"city": "Shanghai", "country": "China", "country_code": "CN", "lat": 31.1443, "lon": 121.8083},
    "PEK": {"city": "Beijing", "country": "China", "country_code": "CN", "lat": 40.0799, "lon": 116.6031},
    "CAN": {"city": "Guangzhou", "country": "China", "country_code": "CN", "lat": 23.3924, "lon": 113.2988},
    "SZX": {"city": "Shenzhen", "country": "China", "country_code": "CN", "lat": 22.6393, "lon": 113.8107},
    "TPE": {"city": "Taipei", "country": "Taiwan", "country_code": "TW", "lat": 25.0797, "lon": 121.2342},
    "BKK": {"city": "Bangkok", "country": "Thailand", "country_code": "TH", "lat": 13.6900, "lon": 100.7501},
    "KUL": {"city": "Kuala Lumpur", "country": "Malaysia", "country_code": "MY", "lat": 2.7456, "lon": 101.7099},
    "CGK": {"city": "Jakarta", "country": "Indonesia", "country_code": "ID", "lat": -6.1256, "lon": 106.6559},
    "MNL": {"city": "Manila", "country": "Philippines", "country_code": "PH", "lat": 14.5086, "lon": 121.0194},
    "DEL": {"city": "New Delhi", "country": "India", "country_code": "IN", "lat": 28.5562, "lon": 77.1000},
    "BOM": {"city": "Mumbai", "country": "India", "country_code": "IN", "lat": 19.0896, "lon": 72.8656},
    "BLR": {"city": "Bangalore", "country": "India", "country_code": "IN", "lat": 13.1986, "lon": 77.7066},
    "MAA": {"city": "Chennai", "country": "India", "country_code": "IN", "lat": 12.9941, "lon": 80.1709},
    "KHI": {"city": "Karachi", "country": "Pakistan", "country_code": "PK", "lat": 24.9065, "lon": 67.1608},
    "DAC": {"city": "Dhaka", "country": "Bangladesh", "country_code": "BD", "lat": 23.8433, "lon": 90.3978},
    "SYD": {"city": "Sydney", "country": "Australia", "country_code": "AU", "lat": -33.9399, "lon": 151.1753},
    "MEL": {"city": "Melbourne", "country": "Australia", "country_code": "AU", "lat": -37.6690, "lon": 144.8410},
    "BNE": {"city": "Brisbane", "country": "Australia", "country_code": "AU", "lat": -27.3842, "lon": 153.1175},
    "PER": {"city": "Perth", "country": "Australia", "country_code": "AU", "lat": -31.9385, "lon": 115.9672},
    "AKL": {"city": "Auckland", "country": "New Zealand", "country_code": "NZ", "lat": -37.0082, "lon": 174.7850},
}

# Real IATA codes deliberately NOT in the table above, because they collide
# with common networking hostname tokens and would produce too many false
# positives: GIG (Rio de Janeiro -- collides with "gigabitethernet"/"gig0"
# interface names), AUS (Austin -- collides with "aus" as a country-code
# abbreviation for Australia in many ISPs' naming schemes).

_LABEL_RE = re.compile(r"^([a-z]{3})\d{0,3}$", re.IGNORECASE)


def find_airport_code_in_hostname(hostname: Optional[str]) -> Optional[dict]:
    """Scan a reverse-DNS hostname for a dot/hyphen-delimited label that is
    exactly a known 3-letter airport code, optionally followed by 1-3
    digits (e.g. "lax1", "lax20"). Returns the matched city's info (plus
    the matched `code`) or None. Only whole-label matches count -- we never
    search for a code as a substring of a longer label, to keep the
    false-positive rate down.
    """
    if not hostname:
        return None
    for label in re.split(r"[.\-_]", hostname):
        m = _LABEL_RE.match(label)
        if not m:
            continue
        code = m.group(1).upper()
        hit = AIRPORT_CODES.get(code)
        if hit:
            return {**hit, "code": code}
    return None

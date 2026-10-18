import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "SpotterFuelPlanner/1.0 (fuel route planning assessment)"
}


def geocode_location(address: str) -> dict:
    """Convert a US address or place name into coordinates."""
    if not address or not address.strip():
        raise ValueError("Address cannot be empty.")

    response = requests.get(
        NOMINATIM_URL,
        params={
            "q": address.strip(),
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "us",
        },
        headers=HEADERS,
        timeout=10,
    )
    response.raise_for_status()
    results = response.json()

    if not results:
        raise ValueError(f"Location not found: {address}")

    result = results[0]
    return {
        "latitude": float(result["lat"]),
        "longitude": float(result["lon"]),
        "display_name": result["display_name"],
    }


def geocode_station(station_name: str, address: str, city: str, state: str) -> dict | None:
    """Geocode a fuel station using its name and full address details."""
    parts = [
        part.strip()
        for part in (station_name, address, city, state)
        if part and part.strip()
    ]
    if not parts:
        return None

    parts.append("USA")
    query = ", ".join(parts)

    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "us",
            },
            headers=HEADERS,
            timeout=10,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            return None

        result = results[0]
        return {
            "latitude": float(result["lat"]),
            "longitude": float(result["lon"]),
            "display_name": result.get("display_name", ""),
        }
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
        return None
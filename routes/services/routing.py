import requests

OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


def get_driving_route(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> dict:
    """Calculate a driving route using OSRM.

    Coordinates are supplied as latitude and longitude. Returns the route
    distance in miles, duration in seconds, and GeoJSON geometry.
    """
    coordinates = (
        f"{start_longitude},{start_latitude};"
        f"{end_longitude},{end_latitude}"
    )

    try:
        response = requests.get(
            f"{OSRM_URL}/{coordinates}",
            params={
                "overview": "full",
                "geometries": "geojson",
                "steps": "false",
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as e:
        raise ValueError("Unable to retrieve a driving route from OSRM.") from e

    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError("No driving route could be found.")

    route = data["routes"][0]
    return {
        "distance_miles": round(route["distance"] / 1609.344, 2),
        "duration_seconds": round(route["duration"]),
        "geometry": route["geometry"],
    }

import math
import re

import requests
from django.core.cache import cache

from fuel_stations.models import FuelStation

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "SpotterFuelPlanner/1.0 (development assessment)"
VEHICLE_RANGE_MILES = 500.0
FUEL_EFFICIENCY_MPG = 10.0
METERS_PER_MILE = 1609.344


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _distance_meters(a, b):
    lat1, lon1 = math.radians(a[1]), math.radians(a[0])
    lat2, lon2 = math.radians(b[1]), math.radians(b[0])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(min(1.0, h)))


def _route_positions(coordinates):
    cumulative = [0.0]
    for previous, current in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + _distance_meters(previous, current) / METERS_PER_MILE)
    return cumulative


def _fetch_osm_fuel_stations(coordinates):
    step = max(1, len(coordinates) // 80)
    sampled = coordinates[::step]
    if sampled[-1] != coordinates[-1]:
        sampled.append(coordinates[-1])
    south = min(point[1] for point in sampled) - 0.08
    north = max(point[1] for point in sampled) + 0.08
    west = min(point[0] for point in sampled) - 0.08
    east = max(point[0] for point in sampled) + 0.08
    cache_key = f"spotter:osm-fuel:{south:.3f}:{west:.3f}:{north:.3f}:{east:.3f}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    query = f'''[out:json][timeout:25];
    (node["amenity"="fuel"]({south},{west},{north},{east});
     way["amenity"="fuel"]({south},{west},{north},{east});
     relation["amenity"="fuel"]({south},{west},{north},{east}););
    out center tags;'''
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=35,
    )
    response.raise_for_status()
    stations = []
    for item in response.json().get("elements", []):
        tags = item.get("tags", {})
        center = item.get("center", {})
        lat = item.get("lat", center.get("lat"))
        lon = item.get("lon", center.get("lon"))
        if lat is None or lon is None:
            continue
        stations.append({
            "osm_id": f"{item.get('type')}:{item.get('id')}",
            "name": tags.get("name", ""),
            "brand": tags.get("brand", ""),
            "city": tags.get("addr:city", ""),
            "state": tags.get("addr:state", ""),
            "latitude": float(lat),
            "longitude": float(lon),
        })
    cache.set(cache_key, stations, timeout=6 * 60 * 60)
    return stations


def recommend_fuel_stops(route_geometry, route_distance_miles):
    coordinates = route_geometry.get("coordinates", [])
    if len(coordinates) < 2:
        raise ValueError("The route geometry is missing or invalid.")
    if route_distance_miles <= 0:
        raise ValueError("Route distance must be positive.")
    base = {
        "vehicle_range_miles": VEHICLE_RANGE_MILES,
        "fuel_efficiency_mpg": FUEL_EFFICIENCY_MPG,
        "fuel_needed_gallons": round(route_distance_miles / FUEL_EFFICIENCY_MPG, 2),
        "recommended_stops": [],
    }
    if route_distance_miles <= VEHICLE_RANGE_MILES:
        return {**base, "price_match_count": 0, "message": "No refuelling stop is required if the vehicle starts with a full tank."}
    route_miles = _route_positions(coordinates)
    osm_stations = _fetch_osm_fuel_stations(coordinates)
    if not osm_stations:
        return {**base, "price_match_count": 0, "message": "No OpenStreetMap fuel stations were found near this route."}
    rows = list(FuelStation.objects.all().only("station_id", "name", "city", "state", "address", "retail_price"))
    by_name_city_state = {}
    by_name_state = {}
    for row in rows:
        name = _normalize(row.name)
        if not name:
            continue
        by_name_city_state.setdefault((name, _normalize(row.city), _normalize(row.state)), []).append(row)
        by_name_state.setdefault((name, _normalize(row.state)), []).append(row)
    candidates = []
    for osm in osm_stations:
        point = (osm["longitude"], osm["latitude"])
        nearest_index = min(range(len(coordinates)), key=lambda index: _distance_meters(point, coordinates[index]))
        off_route_miles = _distance_meters(point, coordinates[nearest_index]) / METERS_PER_MILE
        if off_route_miles > 2.0:
            continue
        along_miles = route_miles[nearest_index]
        if along_miles <= 0 or along_miles >= route_distance_miles:
            continue
        matched = {}
        for name_value in (osm["name"], osm["brand"]):
            normalized_name = _normalize(name_value)
            if not normalized_name:
                continue
            for row in by_name_city_state.get((normalized_name, _normalize(osm["city"]), _normalize(osm["state"])), []):
                matched[row.station_id] = row
            if not osm["city"]:
                for row in by_name_state.get((normalized_name, _normalize(osm["state"])), []):
                    matched[row.station_id] = row
        if len(matched) != 1:
            continue
        row = next(iter(matched.values()))
        candidates.append({
            "station_id": row.station_id,
            "name": row.name,
            "address": row.address,
            "city": row.city,
            "state": row.state,
            "retail_price_per_gallon": float(row.retail_price),
            "latitude": osm["latitude"],
            "longitude": osm["longitude"],
            "distance_from_start_miles": round(along_miles, 2),
            "off_route_miles": round(off_route_miles, 2),
            "osm_id": osm["osm_id"],
        })
    candidates.sort(key=lambda item: item["distance_from_start_miles"])
    if not candidates:
        return {**base, "price_match_count": 0, "message": "No nearby station could be matched unambiguously to a CSV price; no prices were guessed."}
    stops = []
    current_mile = 0.0
    while route_distance_miles - current_mile > VEHICLE_RANGE_MILES:
        reachable = [station for station in candidates if current_mile < station["distance_from_start_miles"] <= current_mile + VEHICLE_RANGE_MILES]
        if not reachable:
            return {**base, "recommended_stops": stops, "price_match_count": len(candidates), "message": "No verified priced station was found within the remaining range; route feasibility is not guaranteed."}
        stop = min(reachable, key=lambda item: (item["retail_price_per_gallon"], item["off_route_miles"]))
        miles_since_last = stop["distance_from_start_miles"] - current_mile
        gallons_used = miles_since_last / FUEL_EFFICIENCY_MPG
        stops.append({**stop, "gallons_used_since_previous_stop": round(gallons_used, 2), "estimated_cost_since_previous_stop": round(gallons_used * stop["retail_price_per_gallon"], 2)})
        current_mile = stop["distance_from_start_miles"]
    return {**base, "recommended_stops": stops, "price_match_count": len(candidates), "message": "Stops use actual OpenStreetMap coordinates and uniquely matched CSV prices."}

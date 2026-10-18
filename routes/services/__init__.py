import math
import re
import time

import requests
from django.core.cache import cache
from fuel_stations.models import FuelStation

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
    if not coordinates:
        return []

    route_miles = _route_positions(coordinates)
    total_miles = route_miles[-1]
    sample_count = min(60, len(coordinates))
    if sample_count == 1 or total_miles <= 0:
        sampled = [coordinates[0]]
    else:
        targets = [i * total_miles / (sample_count - 1) for i in range(sample_count)]
        indices = []
        for target in targets:
            # Binary search cumulative route miles instead of scanning all
            # geometry vertices for every sample target.
            import bisect
            index = bisect.bisect_left(route_miles, target)
            if index >= len(route_miles):
                index = len(route_miles) - 1
            if index > 0 and abs(route_miles[index - 1] - target) <= abs(route_miles[index] - target):
                index -= 1
            if not indices or indices[-1] != index:
                indices.append(index)
        sampled = [coordinates[index] for index in indices]

    import hashlib
    route_signature = ":".join(f"{point[0]:.2f},{point[1]:.2f}" for point in sampled)
    key = "spotter_osm_fuel_v2_" + hashlib.sha256(route_signature.encode("utf-8")).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached

    # Overpass searches for actual OpenStreetMap fuel POIs around route points.
    clauses = "\n".join(
        f'nwr(around:15000,{latitude},{longitude})["amenity"="fuel"];'
        for longitude, latitude, *_ in sampled
    )
    query = f"[out:json][timeout:45];({clauses});out center tags;"
    endpoints = (
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    )
    all_stations = {}
    lookup_succeeded = False
    for endpoint in endpoints:
        for attempt in range(2):
            try:
                response = requests.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=(10, 60),
                )
                response.raise_for_status()
                payload = response.json()
                for element in payload.get("elements", []):
                    tags = element.get("tags", {})
                    if element.get("type") == "node":
                        latitude, longitude = element.get("lat"), element.get("lon")
                    else:
                        center = element.get("center", {})
                        latitude, longitude = center.get("lat"), center.get("lon")
                    if latitude is None or longitude is None:
                        continue
                    osm_id = f"{element.get('type', 'unknown')}:{element.get('id', '')}"
                    all_stations[osm_id] = {
                        "osm_id": osm_id,
                        "name": tags.get("name", ""),
                        "brand": tags.get("brand", ""),
                        "city": tags.get("addr:city", tags.get("addr:town", tags.get("addr:hamlet", ""))),
                        "state": tags.get("addr:state", ""),
                        "street": tags.get("addr:street", ""),
                        "housenumber": tags.get("addr:housenumber", ""),
                        "postcode": tags.get("addr:postcode", ""),
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    }
                lookup_succeeded = True
                print(f"Fuel planner: Overpass lookup succeeded using {endpoint}.")
                break
            except (requests.RequestException, ValueError) as exc:
                print(f"Fuel station lookup failed at {endpoint} (attempt {attempt + 1}/2): {type(exc).__name__}: {exc}")
                if attempt == 0:
                    time.sleep(2)
        if lookup_succeeded:
            break

    stations = list(all_stations.values())
    print(f"Fuel planner: Overpass lookup produced {len(stations)} station locations across {len(sampled)} sampled route points.")
    if stations:
        cache.set(key, stations, timeout=6 * 60 * 60)
    elif not lookup_succeeded:
        print("Fuel planner: all Overpass endpoints failed; empty result was not cached.")
    elif lookup_succeeded:
        # Overpass can occasionally return an empty/partial result for a large
        # multi-clause query. Retry with fewer route samples before returning no stations.
        print("Fuel planner: Overpass returned zero stations; retrying a smaller route query.")
        if len(sampled) > 12:
            reduced_coordinates = [sampled[round(i * (len(sampled) - 1) / 11)] for i in range(12)]
            reduced_miles = _route_positions(reduced_coordinates)
            # Retry once using 12 evenly distributed points, with a distinct cache key.
            reduced_signature = ":".join(f"{point[0]:.2f},{point[1]:.2f}" for point in reduced_coordinates)
            reduced_key = "spotter_osm_fuel_retry_v1_" + hashlib.sha256(reduced_signature.encode("utf-8")).hexdigest()
            reduced_cached = cache.get(reduced_key)
            if reduced_cached is not None:
                return reduced_cached
            reduced_clauses = "\n".join(
                f'nwr(around:25000,{latitude},{longitude})["amenity"="fuel"];'
                for longitude, latitude, *_ in reduced_coordinates
            )
            reduced_query = f"[out:json][timeout:45];({reduced_clauses});out center tags;"
            for endpoint in endpoints:
                try:
                    response = requests.post(
                        endpoint,
                        data={"data": reduced_query},
                        headers={"User-Agent": USER_AGENT},
                        timeout=(10, 60),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    for element in payload.get("elements", []):
                        tags = element.get("tags", {})
                        if element.get("type") == "node":
                            latitude, longitude = element.get("lat"), element.get("lon")
                        else:
                            center = element.get("center", {})
                            latitude, longitude = center.get("lat"), center.get("lon")
                        if latitude is None or longitude is None:
                            continue
                        osm_id = f"{element.get('type', 'unknown')}:{element.get('id', '')}"
                        all_stations[osm_id] = {
                            "osm_id": osm_id,
                            "name": tags.get("name", ""),
                            "brand": tags.get("brand", ""),
                            "city": tags.get("addr:city", tags.get("addr:town", tags.get("addr:hamlet", ""))),
                            "state": tags.get("addr:state", ""),
                            "street": tags.get("addr:street", ""),
                            "housenumber": tags.get("addr:housenumber", ""),
                            "postcode": tags.get("addr:postcode", ""),
                            "latitude": float(latitude),
                            "longitude": float(longitude),
                        }
                    if all_stations:
                        break
                except (requests.RequestException, ValueError) as exc:
                    print(f"Fuel station retry failed at {endpoint}: {type(exc).__name__}: {exc}")
            stations = list(all_stations.values())
            if stations:
                cache.set(reduced_key, stations, timeout=6 * 60 * 60)
    return stations


def _reverse_geocode_city_state(latitude, longitude):
    key = f"spotter_reverse_city_state_{latitude:.3f}_{longitude:.3f}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        response = requests.get(
            "https://photon.komoot.io/reverse",
            params={"lat": latitude, "lon": longitude},
            headers={"User-Agent": USER_AGENT},
            timeout=(5, 12),
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        properties = features[0].get("properties", {}) if features else {}
        city = properties.get("city") or properties.get("town") or properties.get("village") or properties.get("locality") or ""
        state = properties.get("state") or ""
        result = (_normalize(city), _normalize(state))
        cache.set(key, result, timeout=30 * 24 * 60 * 60)
        return result
    except (requests.RequestException, ValueError):
        return ("", "")

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
    required_stops = max(0, math.ceil(route_distance_miles / VEHICLE_RANGE_MILES) - 1)


    print(f"Fuel planner: route has {len(coordinates)} geometry points; looking up nearby stations.")
    print(f"Fuel planner: CSV currently contains {FuelStation.objects.count()} fuel-price rows.")
    osm_stations = _fetch_osm_fuel_stations(coordinates)
    if not osm_stations:
        return {
            **base,
            "price_match_count": 0,
            "estimated_total_fuel_cost": None,
            "required_refuelling_stops_by_range": required_stops,
            "route_reachable_by_range": False,
            "route_reachable_with_verified_stops": False,
            "message": "Fuel-station location service returned no stations; no station prices or stops were guessed.",
        }

    price_rows = list(FuelStation.objects.all().only(
        "name", "city", "state", "address", "retail_price", "station_id"
    ))
    rows_by_identity = {}
    rows_by_city_state = {}
    rows_by_brand_city_state = {}
    known_brands = ("circle k", "murphy usa", "pilot", "flying j", "love s", "loves", "shell", "chevron", "valero", "exxon", "mobil", "bp", "marathon", "speedway", "casey s", "caseys", "quiktrip", "qt", "walmart", "murphy express", "cefco", "ta", "petro", "kwik trip", "kwik star")
    for row in price_rows:
        name = _normalize(row.name)
        city = _normalize(row.city)
        state = _normalize(row.state)
        if name and city and state:
            rows_by_identity.setdefault((name, city, state), []).append(row)
        if city and state:
            rows_by_city_state.setdefault((city, state), []).append(row)
        tokens = set(name.split())
        brand = next((known for known in known_brands if set(known.split()).issubset(tokens)), "")
        if brand and city and state:
            rows_by_brand_city_state.setdefault((brand, city, state), []).append(row)

    route_miles_at_vertex = _route_positions(coordinates)
    route_points = list(zip(coordinates, route_miles_at_vertex))
    candidates = []
    state_abbreviations = {
        "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
        "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
        "district of columbia": "dc", "florida": "fl", "georgia": "ga", "hawaii": "hi",
        "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
        "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
        "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
        "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
        "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
        "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
        "oregon": "or", "pennsylvania": "pa", "rhode island": "ri", "south carolina": "sc",
        "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut",
        "vermont": "vt", "virginia": "va", "washington": "wa", "west virginia": "wv",
        "wisconsin": "wi", "wyoming": "wy",
    }
    # Cache route vertices in a spatial grid so each station only compares
    # against nearby route points, rather than all 9,588+ vertices.
    route_grid = {}
    grid_size = 0.03  # roughly a few kilometres in latitude/longitude
    for coordinate, mile in route_points:
        cell = (int(coordinate[0] / grid_size), int(coordinate[1] / grid_size))
        route_grid.setdefault(cell, []).append((coordinate, mile))

    for station in osm_stations:
        station_name = _normalize(station.get("name"))
        station_brand = _normalize(station.get("brand"))
        station_city = _normalize(station.get("city"))
        station_state = _normalize(station.get("state"))
        # Avoid a reverse-geocoding network call for every station. Only use
        # locality already supplied by OpenStreetMap; unmatched locality stays
        # unmatched rather than being guessed.
        station_state = state_abbreviations.get(station_state, station_state)
        # If exact brand/locality matching is unavailable, the route-local
        # fallback is handled below only after station coordinates are checked.
        matching_rows = []
        price_match_method = ""

        # NOTE: Exact normalized name plus city/state is a candidate match only, not proof of physical identity,
        # because the CSV does not contain coordinates or a shared OSM station ID.
        # Match a unique exact station name first. If OSM omits a station number
        # (for example, "CEFCO" vs "CEFCO 2004"), allow a conservative name
        # containment match only when it identifies one CSV row in that city/state.
        if station_name and station_city and station_state:
            exact = rows_by_identity.get((station_name, station_city, station_state), [])
            if len(exact) == 1:
                matching_rows = exact
                price_match_method = "exact_station_name_city_state"
            else:
                station_tokens = set(station_name.split())
                possible_matches = []
                for (csv_name, csv_city, csv_state), rows in rows_by_identity.items():
                    if csv_city != station_city or csv_state not in (station_state, state_abbreviations.get(station_state, station_state)):
                        continue
                    csv_tokens = set(csv_name.split())
                    if station_tokens and (station_tokens.issubset(csv_tokens) or csv_tokens.issubset(station_tokens)):
                        possible_matches.extend(rows)
                # Never choose arbitrarily if the shorter map name matches multiple CSV stations.
                unique_possible = {row.station_id: row for row in possible_matches}
                if len(unique_possible) == 1:
                    matching_rows = [next(iter(unique_possible.values()))]
                    price_match_method = "unique_station_name_city_state"

        # Match normalized street address only when both datasets contain it.
        osm_address = _normalize(" ".join(
            part for part in (station.get("housenumber", ""), station.get("street", "")) if part
        ))
        if len(matching_rows) != 1 and osm_address and station_city and station_state:
            address_matches = []
            for row in price_rows:
                if _normalize(row.city) != station_city:
                    continue
                row_state = _normalize(row.state)
                row_state = state_abbreviations.get(row_state, row_state)
                if row_state != station_state:
                    continue
                csv_address = _normalize(row.address)
                # Require a full normalized address match; substring matches
                # can incorrectly identify a different station on the same road.
                if csv_address and csv_address == osm_address:
                    address_matches.append(row)
            if len(address_matches) == 1:
                matching_rows = address_matches
                price_match_method = "exact_address_city_state"

        # If exact matching failed, retain a brand/locality price only as an
        # explicitly approximate estimate. Never describe it as exact.
        if len(matching_rows) != 1:
            matching_rows = []
            combined_tokens = set((station_name + " " + station_brand).split())
            brand = next((known for known in known_brands if set(known.split()).issubset(combined_tokens)), "")
            if brand and station_city and station_state:
                brand_rows = rows_by_brand_city_state.get((brand, station_city, station_state), [])
                if brand_rows:
                    # Use the median listed price when several records exist.
                    sorted_rows = sorted(brand_rows, key=lambda row: float(row.retail_price))
                    matching_rows = [sorted_rows[len(sorted_rows) // 2]]
                    price_match_method = "brand_city_state_estimate"

        if len(matching_rows) != 1:
            continue

        station_point = [station["longitude"], station["latitude"]]
        cell_x = int(station_point[0] / grid_size)
        cell_y = int(station_point[1] / grid_size)
        nearby_route_points = [
            point
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for point in route_grid.get((cell_x + dx, cell_y + dy), [])
        ]
        if not nearby_route_points:
            continue
        nearest = min(nearby_route_points, key=lambda item: _distance_meters(station_point, item[0]))
        offset_miles = _distance_meters(station_point, nearest[0]) / METERS_PER_MILE
        if offset_miles > 3.0:
            continue
        row = matching_rows[0]
        candidates.append({
            "osm_id": station["osm_id"],
            "name": station.get("name") or station.get("brand") or row.name,
            "city": row.city,
            "state": row.state,
            "latitude": station["latitude"],
            "longitude": station["longitude"],
            "distance_from_start_miles": round(nearest[1], 2),
            "distance_from_route_miles": round(offset_miles, 2),
            "price_per_gallon": float(row.retail_price),
            "price_source_station_id": row.station_id,
            "price_match_method": price_match_method,
        })

    # Remove duplicate OSM records for the same brand/city/state and nearby route mile.
    deduplicated = {}
    for item in candidates:
        key = (
            _normalize(item["name"]),
            _normalize(item["city"]),
            _normalize(item["state"]),
            round(item["distance_from_start_miles"] / 2.0),
            item["price_source_station_id"],
        )
        previous = deduplicated.get(key)
        if previous is None or item["distance_from_route_miles"] < previous["distance_from_route_miles"]:
            deduplicated[key] = item
    candidates = list(deduplicated.values())

    candidates.sort(key=lambda item: item["distance_from_start_miles"])
    print("Fuel planner: matched candidate distances (miles from start):", [
        {"name": item["name"], "city": item["city"], "state": item["state"],
         "mile": item["distance_from_start_miles"],
         "offset": item["distance_from_route_miles"],
         "price": item["price_per_gallon"],
         "match_method": item["price_match_method"],
         "source_station_id": item["price_source_station_id"]}
        for item in candidates
    ])

    unique_candidates = []
    seen = set()
    for item in candidates:
        identity = item["osm_id"]
        if identity not in seen:
            seen.add(identity)
            unique_candidates.append(item)

    # Find the lowest estimated-cost sequence of stops that keeps every leg
    # within the vehicle's 500-mile range. The vehicle starts with a full tank.
    unique_candidates = sorted(
        (item for item in unique_candidates
         if 0 < item["distance_from_start_miles"] < route_distance_miles),
        key=lambda item: item["distance_from_start_miles"],
    )
    stops = []
    can_reach_finish = route_distance_miles <= VEHICLE_RANGE_MILES

    if route_distance_miles > VEHICLE_RANGE_MILES:
        count = len(unique_candidates)
        best_cost = [float("inf")] * count
        previous_index = [None] * count

        for i, station in enumerate(unique_candidates):
            mile = station["distance_from_start_miles"]
            price = station["price_per_gallon"]

            # The first station can be reached directly from the start.
            if mile <= VEHICLE_RANGE_MILES:
                # Its price is only a proxy for the initial leg's unknown price.
                best_cost[i] = mile / FUEL_EFFICIENCY_MPG * price

            # Or reach this station from a previous station within range.
            for j in range(i):
                previous = unique_candidates[j]
                gap = mile - previous["distance_from_start_miles"]
                if gap > VEHICLE_RANGE_MILES:
                    continue
                if best_cost[j] == float("inf"):
                    continue
                # Fuel for this leg is priced at the departure station.
                proposal = (
                    best_cost[j]
                    + gap / FUEL_EFFICIENCY_MPG * previous["price_per_gallon"]
                )
                if proposal < best_cost[i]:
                    best_cost[i] = proposal
                    previous_index[i] = j

        best_total = float("inf")
        best_finish_index = None
        for i, station in enumerate(unique_candidates):
            remaining = route_distance_miles - station["distance_from_start_miles"]
            if remaining > VEHICLE_RANGE_MILES or best_cost[i] == float("inf"):
                continue
            # The last stop's price estimates the fuel consumed after it.
            total = (
                best_cost[i]
                + remaining / FUEL_EFFICIENCY_MPG * station["price_per_gallon"]
            )
            if total < best_total:
                best_total = total
                best_finish_index = i

        if best_finish_index is not None:
            chosen_indices = []
            index = best_finish_index
            while index is not None:
                chosen_indices.append(index)
                index = previous_index[index]
            chosen_indices.reverse()
            stops = [unique_candidates[i] for i in chosen_indices]
            can_reach_finish = True
    # With current data, no physical station identity can be verified.
    has_verified_prices = False
    estimated_cost = None
    cost_price_source = None
    if can_reach_finish and candidates:
        if stops:
            # Estimate total trip fuel cost using one route-local price per leg.
            # The initial leg has no known purchase station, so the first stop's
            # price is a disclosed proxy; subsequent legs use the departure stop.
            leg_miles = [stops[0]["distance_from_start_miles"]] + [
                next_stop["distance_from_start_miles"] - stop["distance_from_start_miles"]
                for stop, next_stop in zip(stops, stops[1:])
            ] + [route_distance_miles - stops[-1]["distance_from_start_miles"]]
            leg_prices = [stops[0]["price_per_gallon"]] + [
                stop["price_per_gallon"] for stop in stops
            ]
            estimated_cost = sum(
                miles / FUEL_EFFICIENCY_MPG * price
                for miles, price in zip(leg_miles, leg_prices)
            )
            cost_price_source = "estimated route-local prices; initial leg uses first stop price as proxy"
        else:
            route_prices = sorted(item["price_per_gallon"] for item in candidates)
            middle = len(route_prices) // 2
            route_median_price = (
                route_prices[middle]
                if len(route_prices) % 2
                else (route_prices[middle - 1] + route_prices[middle]) / 2
            )
            estimated_cost = route_distance_miles / FUEL_EFFICIENCY_MPG * route_median_price
            cost_price_source = "median of route-local matched station price estimates"

    if not can_reach_finish:
        message = "A complete refuelling plan could not be confirmed: no suitable price-matched station was found within range for every leg. Any returned stops are partial candidates only."
    elif not candidates:
        message = "No route-local fuel stations could be matched reliably to prices in the supplied CSV; total fuel cost is unavailable rather than guessed."
    elif not stops:
        message = "No refuelling stop is required if the vehicle starts with a full tank. Trip fuel cost is estimated from route-local matched station prices; it is not a verified purchase receipt."
    elif not has_verified_prices:
        message = (
            "A range-feasible stop plan was found, but physical station-price identity could not be verified from the supplied CSV and map data. Total consumed-fuel cost is an estimate."
        )
    else:
        message = (
            "A range-feasible stop plan was found, but physical station-price identity could not be verified from the supplied CSV and map data. Total consumed-fuel cost is an estimate."
        )

    return {
        **base,
        "recommended_stops": stops,
        "price_match_count": len(candidates),
        "estimated_total_fuel_cost": round(estimated_cost, 2) if estimated_cost is not None else None,
        "cost_price_source": cost_price_source,
        "required_refuelling_stops_by_range": required_stops,
        "route_reachable_by_range": (
            route_distance_miles <= VEHICLE_RANGE_MILES
            or (
                can_reach_finish
                and bool(stops)
                and stops[0]["distance_from_start_miles"] <= VEHICLE_RANGE_MILES
                and all(
                    next_stop["distance_from_start_miles"] - stop["distance_from_start_miles"] <= VEHICLE_RANGE_MILES
                    for stop, next_stop in zip(stops, stops[1:])
                )
                and route_distance_miles - stops[-1]["distance_from_start_miles"] <= VEHICLE_RANGE_MILES
            )
        ),
        # With current data, verified price matches are not possible
        "route_reachable_with_verified_stops": False,
        "message": message,
    }
import hashlib

import requests
from django.core.cache import cache
from django.shortcuts import render
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services import recommend_fuel_stops


class RouteAPIView(APIView):
    GEOCODING_URL = "https://nominatim.openstreetmap.org/search"
    ROUTING_URL = "https://router.project-osrm.org/route/v1/driving"
    REQUEST_HEADERS = {
        "User-Agent": "SpotterFuelPlanner/1.0 (development assessment)"
    }
    REQUEST_TIMEOUT = 15

    def _geocode(self, location):
        location_key = hashlib.sha256(
            location.strip().casefold().encode("utf-8")
        ).hexdigest()
        cache_key = f"spotter_geocode_v1_{location_key}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        response = requests.get(
            self.GEOCODING_URL,
            params={
                "q": location,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "us",
            },
            headers=self.REQUEST_HEADERS,
            timeout=self.REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        results = response.json()
        if not results:
            cache.set(cache_key, None, timeout=300)
            return None

        result = {
            "latitude": float(results[0]["lat"]),
            "longitude": float(results[0]["lon"]),
            "display_name": results[0].get("display_name", location),
        }
        cache.set(cache_key, result, timeout=60 * 60 * 24)
        return result

    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        start = serializer.validated_data["start"].strip()
        finish = serializer.validated_data["finish"].strip()

        try:
            start_point = self._geocode(start)
            finish_point = self._geocode(finish)
        except requests.RequestException:
            return Response(
                {"error": "The geocoding service is temporarily unavailable. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if not start_point:
            return Response(
                {"error": "Could not find the start location. Include city, state, and country."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not finish_point:
            return Response(
                {"error": "Could not find the finish location. Include city, state, and country."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        coordinates = (
            f"{start_point['longitude']},{start_point['latitude']};"
            f"{finish_point['longitude']},{finish_point['latitude']}"
        )
        try:
            response = requests.get(
                f"{self.ROUTING_URL}/{coordinates}",
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
                timeout=20,
            )
            response.raise_for_status()
            route_data = response.json()
        except requests.RequestException:
            return Response(
                {"error": "The routing service is temporarily unavailable. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if route_data.get("code") != "Ok" or not route_data.get("routes"):
            return Response(
                {"error": "No driving route was found between those locations."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route = route_data["routes"][0]
        distance_miles = route["distance"] / 1609.344
        try:
            fuel_planning = recommend_fuel_stops(route["geometry"], distance_miles)
        except (requests.RequestException, ValueError):
            return Response(
                {"error": "Fuel-station lookup failed. Please retry shortly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {
                "start": {"query": start, **start_point},
                "finish": {"query": finish, **finish_point},
                "distance_miles": round(distance_miles, 2),
                "duration_minutes": round(route["duration"] / 60, 1),
                "geometry": route["geometry"],
                "fuel_planning": fuel_planning,
            },
            status=status.HTTP_200_OK,
        )


def map_page(request):
    return render(request, "routes/map.html")
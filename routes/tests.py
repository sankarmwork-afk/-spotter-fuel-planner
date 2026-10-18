from django.test import TestCase
from routes.services import recommend_fuel_stops


class FuelPlanningTests(TestCase):
    def test_short_route_needs_no_refuelling_stop(self):
        result = recommend_fuel_stops(
            {"type": "LineString", "coordinates": [[-96.8, 32.8], [-97.7, 30.3]]},
            195.0,
        )

        self.assertEqual(result["recommended_stops"], [])
        self.assertEqual(result["required_refuelling_stops_by_range"], 0)
        self.assertTrue(result["route_reachable_by_range"])
        self.assertEqual(result["estimated_total_fuel_cost"], 0.0)

    def test_long_route_reports_required_stops_by_range(self):
        result = recommend_fuel_stops(
            {"type": "LineString", "coordinates": [[-96.8, 32.8], [-104.99, 39.74]]},
            795.23,
        )

        self.assertEqual(result["required_refuelling_stops_by_range"], 1)
        self.assertEqual(result["vehicle_range_miles"], 500.0)
        self.assertEqual(result["fuel_efficiency_mpg"], 10.0)
        self.assertAlmostEqual(result["fuel_needed_gallons"], 79.523, places=2)

from django.db import models


class FuelStation(models.Model):
    station_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=10)
    rack_id = models.CharField(max_length=100, blank=True)
    retail_price = models.DecimalField(max_digits=8, decimal_places=4)
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} — {self.city}, {self.state} ({self.station_id})"
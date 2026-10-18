from django.contrib import admin
from .models import FuelStation

# Register your models here.
@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    list_display = (
        "station_id",
        "name",
        "address",
        "city",
        "state",
        "rack_id",
        "retail_price",
        "latitude",
        "longitude",
        "created_at",
    )
    search_fields = ("station_id", "name", "city", "state")
    list_filter = ("state",)

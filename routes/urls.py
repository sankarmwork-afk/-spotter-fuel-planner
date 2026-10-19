from django.urls import path
from .views import RouteAPIView, map_page

urlpatterns = [
    path("route/", RouteAPIView.as_view(), name="route_api"),
    path("map/", map_page, name="map_page"),
]
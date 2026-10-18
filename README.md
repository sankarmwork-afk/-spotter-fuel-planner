# Spotter Fuel Planner

A Django REST API that calculates driving routes and recommends fuel stops based on vehicle range, fuel efficiency, and available fuel-price data.

## Features

- Accepts start and finish locations in the USA.
- Geocodes locations using OpenStreetMap Nominatim.
- Calculates driving routes using OSRM.
- Returns route distance, estimated duration, and route geometry.
- Imports fuel prices from the provided CSV dataset.
- Recommends fuel stops using a 500-mile vehicle range and 10 MPG fuel efficiency.
- Estimates fuel costs when suitable fuel-price data is available.
- Caches results to reduce repeated external API calls.

## Technology Stack

- Python
- Django
- Django REST Framework
- SQLite
- OpenStreetMap Nominatim
- OSRM Routing API

## Installation

Clone the repository:

    git clone https://github.com/sankarmwork-afk/-spotter-fuel-planner.git
    cd -spotter-fuel-planner

Create a virtual environment:

    python3 -m venv .venv
    source .venv/bin/activate

Install dependencies:

    pip install -r requirements.txt

Apply migrations:

    python manage.py migrate

Import fuel-station data:

    python manage.py import_fuel_stations data/fuel-prices-for-be-assessment.csv

Start the server:

    python manage.py runserver

## API Usage

**Endpoint:** `POST /api/route/`

Local URL: `http://127.0.0.1:8000/api/route/`

Request body:

    {
      "start": "Dallas, Texas, USA",
      "finish": "Atlanta, Georgia, USA"
    }

Set the request header to `Content-Type: application/json`.

The response includes start and finish coordinates, driving distance, estimated duration, route geometry, recommended fuel stops, and fuel-planning results.

## Fuel Planning Assumptions

- Vehicle range: 500 miles.
- Fuel efficiency: 10 miles per gallon.
- Fuel prices are sourced from the supplied assessment CSV.
- Station matching and price availability depend on the available data.
- External mapping and station services may be temporarily unavailable.

Estimated costs and recommendations depend on data availability. A station's price should not be considered independently verified unless the API explicitly confirms the match.

## Assessment

Developed as part of a backend engineering coding assessment.
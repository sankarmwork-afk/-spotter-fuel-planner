import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel_stations.models import FuelStation


class Command(BaseCommand):
    help = "Import fuel stations and prices from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            type=str,
            help="Path to the fuel-price CSV file.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["csv_file"]).expanduser()
        if csv_path.is_file() and csv_path.stat().st_size == 0:
            raise CommandError(
                f"The CSV file exists but is empty: {csv_path}. "
                "Copy the original fuel-prices-for-be-assessment.csv attachment "
                "into the project's data/ directory, then run this command again."
            )
        if not csv_path.is_file():
            raise CommandError(f"CSV file not found: {csv_path}")

        required_columns = {
            "OPIS Truckstop ID",
            "Truckstop Name",
            "Address",
            "City",
            "State",
            "Rack ID",
            "Retail Price",
        }

        stations_by_id = {}
        skipped = 0

        try:
            with csv_path.open(
                mode="r",
                encoding="utf-8-sig",
                newline="",
            ) as file:
                sample = file.read(8192)
                if not sample.strip():
                    raise CommandError(f"CSV file has no content: {csv_path}")
                file.seek(0)

                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel

                reader = csv.DictReader(file, dialect=dialect)
                if not reader.fieldnames:
                    raise CommandError("Could not read a header row from the CSV file.")

                # Remove accidental spaces from header names.
                reader.fieldnames = [name.strip() for name in reader.fieldnames]
                missing_columns = required_columns - set(reader.fieldnames)
                if missing_columns:
                    raise CommandError(
                        "CSV headers do not match the expected format. "
                        f"Missing columns: {sorted(missing_columns)}. "
                        f"Detected headers: {reader.fieldnames}"
                    )

                for row in reader:
                    try:
                        station_id = (row.get("OPIS Truckstop ID") or "").strip()
                        name = (row.get("Truckstop Name") or "").strip()
                        address = (row.get("Address") or "").strip()
                        city = (row.get("City") or "").strip()
                        state = (row.get("State") or "").strip()
                        rack_id = (row.get("Rack ID") or "").strip()
                        price_text = (row.get("Retail Price") or "").strip()

                        if not station_id or not name or not state or not price_text:
                            skipped += 1
                            continue

                        price = Decimal(price_text)
                        if not price.is_finite() or price < 0:
                            skipped += 1
                            continue

                        stations_by_id[station_id] = FuelStation(
                            station_id=station_id,
                            name=name,
                            address=address,
                            city=city,
                            state=state,
                            rack_id=rack_id,
                            latitude=None,
                            longitude=None,
                            retail_price=price.quantize(Decimal("0.0001")),
                        )
                    except (InvalidOperation, AttributeError, ValueError):
                        skipped += 1

        except OSError as exc:
            raise CommandError(f"Could not read CSV file: {exc}") from exc

        stations = list(stations_by_id.values())
        batch_size = 500
        with transaction.atomic():
            for start in range(0, len(stations), batch_size):
                FuelStation.objects.bulk_create(
                    stations[start : start + batch_size],
                    batch_size=batch_size,
                    update_conflicts=True,
                    update_fields=[
                        "name",
                        "address",
                        "city",
                        "state",
                        "rack_id",
                        "retail_price",
                        "latitude",
                        "longitude",
                    ],
                    unique_fields=["station_id"],
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Processed: {len(stations)}, Skipped: {skipped}."
            )
        )
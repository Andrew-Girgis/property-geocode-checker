#!/usr/bin/env python3
"""Property Geocode Checker.

Reads a CSV of properties, geocodes each address using Google Maps Geocoding API,
compares returned coordinates to provided latitude/longitude, and outputs:
- A summary report (printed to stdout and optionally written to a file)
- A CSV of mismatched rows in the same column format as the input

This script intentionally avoids hard-coding business rules; key behaviors are configurable
via CLI flags.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


GOOGLE_GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"


CACHE_VERSION = 1


@dataclass(frozen=True)
class GeocodeResult:
    status: str
    formatted_address: Optional[str]
    latitude: Optional[float]
    longitude: Optional[float]
    result_count: int
    partial_match: bool
    location_type: Optional[str]
    error_message: Optional[str]
    raw: Dict[str, Any]


def _normalize_address(address: str) -> str:
    return " ".join(address.strip().split())


def _load_cache(cache_file: str) -> Dict[str, Any]:
    if not cache_file:
        return {"version": CACHE_VERSION, "entries": {}}
    if not os.path.exists(cache_file):
        return {"version": CACHE_VERSION, "entries": {}}

    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": CACHE_VERSION, "entries": {}}
        if data.get("version") != CACHE_VERSION:
            # Ignore incompatible cache formats.
            return {"version": CACHE_VERSION, "entries": {}}
        if not isinstance(data.get("entries"), dict):
            return {"version": CACHE_VERSION, "entries": {}}
        return data
    except Exception:
        return {"version": CACHE_VERSION, "entries": {}}


def _save_cache(cache_file: str, cache: Dict[str, Any]) -> None:
    if not cache_file:
        return
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        # Cache write failure should never break the main run.
        return


def _geocode_result_to_cache_entry(result: GeocodeResult) -> Dict[str, Any]:
    return {
        "status": result.status,
        "formatted_address": result.formatted_address,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "result_count": result.result_count,
        "partial_match": result.partial_match,
        "location_type": result.location_type,
        "error_message": result.error_message,
    }


def _cache_entry_to_geocode_result(entry: Dict[str, Any]) -> Optional[GeocodeResult]:
    if not isinstance(entry, dict):
        return None
    try:
        return GeocodeResult(
            status=str(entry.get("status", "")),
            formatted_address=entry.get("formatted_address"),
            latitude=entry.get("latitude"),
            longitude=entry.get("longitude"),
            result_count=int(entry.get("result_count", 0)),
            partial_match=bool(entry.get("partial_match", False)),
            location_type=entry.get("location_type"),
            error_message=entry.get("error_message"),
            raw={},
        )
    except Exception:
        return None


def _load_env_file_if_present(env_file: str) -> None:
    """Best-effort .env loader.

    - Only sets env vars that are not already set.
    - Supports simple KEY=VALUE lines (optionally quoted). Ignores comments and blank lines.
    """

    if not env_file:
        return

    if not os.path.exists(env_file):
        return

    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # If the .env can't be read, we just fall back to environment.
        return


def _parse_float(value: str) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_valid_lat_lng(lat: Optional[float], lng: Optional[float]) -> bool:
    if lat is None or lng is None:
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in meters."""

    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def geocode_address(address: str, api_key: str, timeout_seconds: float = 20.0) -> GeocodeResult:
    params = {
        "address": address,
        "key": api_key,
    }
    url = GOOGLE_GEOCODE_ENDPOINT + "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url, headers={"User-Agent": "property-geocode-checker/1.0"})

    # On some macOS/Python setups, the default trust store can be missing which causes
    # CERTIFICATE_VERIFY_FAILED. If certifi is installed, prefer its CA bundle.
    context = ssl.create_default_context()
    try:
        import certifi  # type: ignore

        context.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass

    with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as resp:
        payload = resp.read().decode("utf-8")

    raw: Dict[str, Any] = json.loads(payload)
    status = str(raw.get("status", ""))
    error_message = raw.get("error_message") if isinstance(raw.get("error_message"), str) else None

    results = raw.get("results")
    if not isinstance(results, list):
        results = []
    result_count = len(results)

    if status != "OK":
        return GeocodeResult(
            status=status,
            formatted_address=None,
            latitude=None,
            longitude=None,
            result_count=result_count,
            partial_match=False,
            location_type=None,
            error_message=error_message,
            raw=raw,
        )

    if result_count == 0:
        return GeocodeResult(
            status="NO_RESULTS",
            formatted_address=None,
            latitude=None,
            longitude=None,
            result_count=0,
            partial_match=False,
            location_type=None,
            error_message=error_message,
            raw=raw,
        )

    first = results[0] if isinstance(results[0], dict) else {}
    formatted_address = first.get("formatted_address") if isinstance(first, dict) else None
    partial_match = bool(first.get("partial_match")) if isinstance(first, dict) else False

    location_type: Optional[str] = None
    lat = None
    lng = None

    geometry = first.get("geometry") if isinstance(first, dict) else None
    if isinstance(geometry, dict):
        lt = geometry.get("location_type")
        location_type = str(lt) if lt is not None else None
        location = geometry.get("location")
        if isinstance(location, dict):
            lat = _parse_float(location.get("lat"))
            lng = _parse_float(location.get("lng"))

    return GeocodeResult(
        status=status,
        formatted_address=formatted_address,
        latitude=lat,
        longitude=lng,
        result_count=result_count,
        partial_match=partial_match,
        location_type=location_type,
        error_message=error_message,
        raw=raw,
    )


def _ambiguous_reason(result: GeocodeResult) -> Optional[str]:
    if result.status != "OK":
        return None

    if result.result_count != 1:
        return f"ambiguous: result_count={result.result_count}"

    if result.partial_match:
        return "ambiguous: partial_match=true"

    if (result.location_type or "").upper() != "ROOFTOP":
        return f"ambiguous: location_type={result.location_type or 'UNKNOWN'}"

    return None


def _required_column(fieldnames: List[str], requested: str, label: str) -> str:
    if requested in fieldnames:
        return requested

    # Case-insensitive match convenience.
    lowered = {name.lower(): name for name in fieldnames}
    if requested.lower() in lowered:
        return lowered[requested.lower()]

    available = ", ".join(fieldnames)
    raise SystemExit(
        f"Missing required column for {label}: '{requested}'. Available columns: {available}"
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate property latitude/longitude by re-geocoding addresses using Google Maps.",
    )

    parser.add_argument("--input", required=True, help="Path to input CSV (e.g. properties.csv).")
    parser.add_argument(
        "--mismatches-output",
        default="mismatches.csv",
        help="Path to write mismatched rows CSV (default: mismatches.csv).",
    )
    parser.add_argument(
        "--summary-output",
        default=None,
        help="Optional path to write summary as JSON (if omitted, summary is only printed).",
    )

    parser.add_argument(
        "--id-column",
        default="id",
        help="Column name for property id (default: id).",
    )
    parser.add_argument(
        "--address-column",
        default="address",
        help="Column name for address (default: address).",
    )
    parser.add_argument(
        "--lat-column",
        default="latitude",
        help="Column name for latitude (default: latitude).",
    )
    parser.add_argument(
        "--lng-column",
        default="longitude",
        help="Column name for longitude (default: longitude).",
    )

    parser.add_argument(
        "--tolerance-meters",
        type=float,
        required=True,
        help="Distance tolerance in meters for match vs mismatch (required).",
    )

    # Behavior notes:
    # - Missing/invalid latitude/longitude are counted as mismatches (and written to mismatches output).
    # - Rows that cannot be geocoded by Google (missing address / non-OK status / ambiguous results) are skipped,
    #   with clear stderr errors describing why.

    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to .env file to load if GOOGLE_MAPS_API_KEY is not set (default: .env).",
    )

    parser.add_argument(
        "--cache-file",
        default=None,
        help="Optional path to a JSON cache file to reuse geocode results across runs.",
    )

    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=0,
        help="Optional delay between geocode requests in milliseconds (default: 0).",
    )

    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional max number of rows to process (useful for quick tests).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call Google; only validate CSV parsing and required columns.",
    )

    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if args.tolerance_meters < 0:
        print("--tolerance-meters must be >= 0", file=sys.stderr)
        return 2

    if not args.dry_run:
        _load_env_file_if_present(args.env_file)

    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not args.dry_run and not api_key:
        print(
            "Missing GOOGLE_MAPS_API_KEY. Set it in your environment or a .env file.\n"
            "Example:\n"
            "  export GOOGLE_MAPS_API_KEY=\"...\"\n",
            file=sys.stderr,
        )
        return 2

    total_rows = 0
    checked_rows = 0
    matched = 0
    mismatched = 0
    invalid_coord_mismatches = 0
    skipped_missing_address = 0
    skipped_ambiguous = 0
    skipped_geocode_failure = 0

    cache = _load_cache(args.cache_file) if (not args.dry_run and args.cache_file) else {"version": CACHE_VERSION, "entries": {}}
    cache_entries: Dict[str, Any] = cache.get("entries", {}) if isinstance(cache.get("entries"), dict) else {}
    cache_hits = 0
    cache_misses = 0

    def geocode_with_cache(address: str) -> GeocodeResult:
        nonlocal cache_hits, cache_misses
        key = _normalize_address(address)
        if args.cache_file and key in cache_entries:
            cached = _cache_entry_to_geocode_result(cache_entries[key])
            if cached is not None:
                cache_hits += 1
                return cached
        cache_misses += 1
        result = geocode_address(address=address, api_key=api_key)
        if args.cache_file:
            cache_entries[key] = _geocode_result_to_cache_entry(result)
        return result

    mismatches: List[Dict[str, str]] = []

    with open(args.input, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print("Input CSV has no header row.", file=sys.stderr)
            return 2

        input_fieldnames = list(reader.fieldnames)
        output_fieldnames = list(input_fieldnames)
        for extra_col in ["google_latitude", "google_longitude", "distance_meters"]:
            if extra_col not in output_fieldnames:
                output_fieldnames.append(extra_col)

        id_col = _required_column(input_fieldnames, args.id_column, "id")
        address_col = _required_column(input_fieldnames, args.address_column, "address")
        lat_col = _required_column(input_fieldnames, args.lat_column, "latitude")
        lng_col = _required_column(input_fieldnames, args.lng_column, "longitude")

        if args.dry_run:
            print("Dry run OK: required columns found:")
            print(f"- id: {id_col}")
            print(f"- address: {address_col}")
            print(f"- latitude: {lat_col}")
            print(f"- longitude: {lng_col}")
            return 0

        for row in reader:
            if args.max_rows is not None and total_rows >= args.max_rows:
                break
            total_rows += 1

            property_id = (row.get(id_col) or "").strip()
            address = (row.get(address_col) or "").strip()
            lat = _parse_float(row.get(lat_col))
            lng = _parse_float(row.get(lng_col))

            row_tag = f"id={property_id or '?'}"

            if not address:
                skipped_missing_address += 1
                print(f"Skip {row_tag}: missing address (cannot geocode)", file=sys.stderr)
                continue

            if not _is_valid_lat_lng(lat, lng):
                # Missing/invalid coords are a mismatch. If we can still geocode the address,
                # include Google's coordinates in the mismatch output.
                invalid_coord_mismatches += 1
                mismatched += 1
                output_row = dict(row)

                google_lat: Optional[float] = None
                google_lng: Optional[float] = None
                try:
                    result = geocode_with_cache(address)
                    if result.status == "OK" and _is_valid_lat_lng(result.latitude, result.longitude):
                        reason = _ambiguous_reason(result)
                        if reason is None:
                            google_lat = result.latitude
                            google_lng = result.longitude
                except Exception:
                    # Keep mismatch; just omit Google coords.
                    pass

                output_row["google_latitude"] = "" if google_lat is None else str(google_lat)
                output_row["google_longitude"] = "" if google_lng is None else str(google_lng)
                output_row["distance_meters"] = ""
                mismatches.append(output_row)

                print(
                    f"Mismatch {row_tag}: invalid/missing coordinates (lat={row.get(lat_col)!r}, lng={row.get(lng_col)!r})",
                    file=sys.stderr,
                )
                continue

            # Call Google geocode.
            try:
                result = geocode_with_cache(address)
            except Exception as e:
                skipped_geocode_failure += 1
                print(f"Skip {row_tag}: geocode request error: {e}", file=sys.stderr)
                continue

            if result.status != "OK" or not _is_valid_lat_lng(result.latitude, result.longitude):
                skipped_geocode_failure += 1
                extra = f" error_message={result.error_message!r}" if result.error_message else ""
                print(
                    f"Skip {row_tag}: geocode failed (status={result.status}){extra}",
                    file=sys.stderr,
                )
                continue

            reason = _ambiguous_reason(result)
            if reason:
                skipped_ambiguous += 1
                print(
                    f"Skip {row_tag}: {reason} (formatted_address={result.formatted_address!r})",
                    file=sys.stderr,
                )
                continue

            distance_m = _haversine_meters(lat, lng, result.latitude, result.longitude)
            checked_rows += 1

            if distance_m <= args.tolerance_meters:
                matched += 1
            else:
                mismatched += 1
                output_row = dict(row)
                output_row["google_latitude"] = "" if result.latitude is None else str(result.latitude)
                output_row["google_longitude"] = "" if result.longitude is None else str(result.longitude)
                output_row["distance_meters"] = f"{distance_m:.3f}"
                mismatches.append(output_row)

            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000.0)

    # Write mismatches output (same columns as input).
    with open(args.mismatches_output, "w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=output_fieldnames)
        writer.writeheader()
        for row in mismatches:
            writer.writerow(row)

    distance_mismatches = mismatched - invalid_coord_mismatches
    if distance_mismatches < 0:
        # Defensive: should never happen, but avoid confusing negative numbers.
        distance_mismatches = 0

    accounted_rows = checked_rows + invalid_coord_mismatches + skipped_missing_address + skipped_ambiguous + skipped_geocode_failure

    summary = {
        "input": args.input,
        "mismatches_output": args.mismatches_output,
        "tolerance_meters": args.tolerance_meters,
        "total_rows": total_rows,
        "checked_rows": checked_rows,
        "matched": matched,
        "mismatched": mismatched,
        "invalid_coord_mismatches": invalid_coord_mismatches,
        "distance_mismatches": distance_mismatches,
        "skipped_missing_address": skipped_missing_address,
        "skipped_ambiguous": skipped_ambiguous,
        "skipped_geocode_failure": skipped_geocode_failure,
        "cache_file": args.cache_file,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "accounted_rows": accounted_rows,
    }

    print(json.dumps(summary, indent=2))

    if args.summary_output:
        with open(args.summary_output, "w", encoding="utf-8") as sf:
            json.dump(summary, sf, indent=2)

    # Persist cache at end of run.
    if not args.dry_run and args.cache_file:
        cache["entries"] = cache_entries
        _save_cache(args.cache_file, cache)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

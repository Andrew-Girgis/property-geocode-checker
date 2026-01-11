# Property Geocode Checker

Validate existing latitude/longitude values for a list of properties by re-geocoding each address with the Google Maps Geocoding API and comparing results.

## What it does

- Reads a list of properties (each with an `id`, `address`, `latitude`, `longitude`).
- Geocodes each `address` via Google Maps.
- Compares the geocoded coordinates against the provided coordinates.
- Produces:
    - A short summary (how many match vs. don’t match)
    - A list of the properties that do not match, in the same column format as the input

## Input format

Your input file should contain (at minimum) these columns:

- `id` (unique identifier)
- `address` (**full address string**)
- `latitude` (decimal degrees)
- `longitude` (decimal degrees)

### Address requirements (important)

For the most accurate geocoding results, the `address` value should include:

- **Street number** (e.g., `1600`)
- **Street name** (e.g., `Amphitheatre Parkway`)
- **City**
- **Province/State**
- **Postal/ZIP code** (recommended)

Addresses missing key parts (like street number, city, or province/state) are more likely to geocode to the wrong place or return inconsistent results.

Example (CSV):

```csv
id,address,latitude,longitude
1,"1600 Amphitheatre Parkway, Mountain View, CA 94043, USA",37.4220,-122.0841
2,"11 Wall St, New York, NY 10005, USA",40.7074,-74.0113
```
## Output

The script produces two things:

1. A summary report (e.g. total rows checked, count matched, count mismatched)
2. A “mismatches” file containing only the rows where the provided coordinates do not match the geocoded result.

The mismatches output keeps the same core columns as the input (`id`, `address`, `latitude`, `longitude`) and adds:

- `google_latitude`
- `google_longitude`

## Google Maps API setup

You’ll need a Google Maps API key with access to the **Geocoding API**.

Set your API key as an environment variable (recommended) so you don’t commit secrets:

```bash
export GOOGLE_MAPS_API_KEY="your_api_key_here"
```

If you’re using a local `.env` file, keep it out of git (this repo’s `.gitignore` already ignores `.env`).

## How to run

From the repo directory, make the script executable (one-time) and run it:

```bash
chmod +x geocode_check.py
./geocode_check.py --input properties.csv --tolerance-meters <METERS>
```

If you prefer, you can also run it with Python:

```bash
python geocode_check.py --input properties.csv --tolerance-meters <METERS>
```

### Matching tolerance

Geocoded coordinates may not exactly match your stored coordinates (due to rounding, different data sources, or changes in geocoding over time). This tool treats a property as a **match** if the distance between the two coordinate pairs is within `--tolerance-meters`.

### Mismatches output

Write mismatched rows to a CSV file:

```bash
./geocode_check.py --input properties.csv --mismatches-output mismatches.csv --tolerance-meters <METERS>
```

### Handling invalid rows

Rows are handled like this:

- Missing/invalid `latitude` or `longitude`: **counted as a mismatch** (written to `mismatches.csv`) with a clear stderr message.
- Rows that cannot be geocoded by Google (missing address, geocode failure, or ambiguous result): **skipped** with a clear stderr message explaining why.

## Troubleshooting

### macOS: `CERTIFICATE_VERIFY_FAILED`

If you see errors like `CERTIFICATE_VERIFY_FAILED` when calling Google, your Python environment may be missing trusted root certificates.

- Recommended fix: install `certifi` in your venv:

```bash
/Users/agirgis/Downloads/property-geocode-checker/venv/bin/python -m pip install --upgrade certifi
```

- If it still fails, try setting this for the current shell session:

```bash
export SSL_CERT_FILE="$(/Users/agirgis/Downloads/property-geocode-checker/venv/bin/python -c 'import certifi; print(certifi.where())')"
```
import ee

try:
    ee.Initialize()
except Exception:
    # If standard init fails, try using the project from env if available, or just authenticate
    # Assuming the user has set up auth in the environment or via gcloud
    ee.Authenticate()
    ee.Initialize()

# Budapest coordinates
point = ee.Geometry.Point([19.0402, 47.4979])

collection_id = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
collection = ee.ImageCollection(collection_id)


def check_year(year):
    print(f"Checking year {year}...")
    # The pipeline uses mosaic(), so we should too.
    # filterBounds is important to select relevant images before mosaicking.
    dataset = collection.filterDate(f"{year}-01-01", f"{year}-12-31").filterBounds(point)

    if dataset.size().getInfo() == 0:
        print(f"  No images found in collection for {year} covering the point.")
        return False

    img = dataset.mosaic()

    # Check if the point is masked
    # We select the first band 'A00'
    try:
        value = (
            img.select("A00")
            .reduceRegion(reducer=ee.Reducer.first(), geometry=point, scale=10)
            .getInfo()
        )

        if value and value.get("A00") is not None:
            print(f"  Data AVAILABLE for {year} at Budapest. Value: {value}")
            return True
        else:
            print(f"  Data MASKED/MISSING for {year} at Budapest (after mosaic). Value: {value}")
            return False
    except Exception as e:
        print(f"  Error checking {year}: {e}")
        return False


print("Checking data availability for Budapest (19.0402, 47.4979)...")
available_2023 = check_year(2023)
available_2024 = check_year(2024)

# Check a point that seems to have data in the user's plot (West Hungary)
point_west = ee.Geometry.Point([17.5, 47.0])
print("\nChecking data availability for West Hungary (17.5, 47.0)...")
# We need to update the global 'point' variable or pass it to the function.
# Let's refactor slightly or just change the global variable for this test.
point = point_west
available_west_2024 = check_year(2024)

if available_2024:
    print("\nCONCLUSION: 2024 data is available at Budapest.")
elif available_west_2024:
    print(
        "\nCONCLUSION: 2024 data is MISSING at Budapest, but AVAILABLE in West Hungary. The gap is real."
    )
else:
    print("\nCONCLUSION: 2024 data seems MISSING everywhere (or script is broken).")

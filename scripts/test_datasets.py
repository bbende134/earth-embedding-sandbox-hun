# %%
import os

import ee
import geemap
from dotenv import load_dotenv

load_dotenv()
# ### Earth Engine Initialization ###
credentials = ee.ServiceAccountCredentials(
    os.environ["service_account_email"], os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
)

ee.Initialize(
    credentials=credentials,
    project=os.environ.get("GCP_PROJECT_ID", "your-gcp-project"),
    url=os.environ.get("HV_URL", "https://earthengine-highvolume.googleapis.com"),
)

# %%
# Load collection
dataset = ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")

# Point of interest
point = ee.Geometry.Point(-121.8036, 39.0372)

# %%
# Check available dates in the collection
print("Checking available images in the dataset...")
all_images = dataset.filterBounds(point).sort("system:time_start")
count = all_images.size().getInfo()
print(f"Total images available at point: {count}")

if count > 0:
    image_list = all_images.toList(min(10, count))
    for i in range(image_list.size().getInfo()):
        img = ee.Image(image_list.get(i))
        date = ee.Date(img.get("system:time_start")).format("YYYY-MM-dd").getInfo()
        print(f"Image {i + 1}: {date}")
else:
    print("No images found at this location.")

# %%
# Check for 2023 images
images_2023 = dataset.filterDate("2023-01-01", "2024-01-01").filterBounds(point)
count_2023 = images_2023.size().getInfo()
print(f"\nImages available for 2023: {count_2023}")

# Check for 2024 images
images_2024 = dataset.filterDate("2024-01-01", "2025-01-01").filterBounds(point)
count_2024 = images_2024.size().getInfo()
print(f"Images available for 2024: {count_2024}")

if count_2023 > 0 and count_2024 > 0:
    image1 = images_2023.first()
    image2 = images_2024.first()

    print("\nBoth years have data. Creating visualization...")

    # Create a geemap Map
    Map = geemap.Map()

    # Visualize three axes of the embedding space as an RGB
    vis_params = {"min": -0.3, "max": 0.3, "bands": ["A01", "A16", "A09"]}

    # Calculate dot product as a measure of similarity between embedding vectors
    dot_prod = image1.multiply(image2).reduce(ee.Reducer.sum())

    # Add layers to the map
    Map.addLayer(image1, vis_params, "2023 embeddings")
    Map.addLayer(image2, vis_params, "2024 embeddings")
    Map.addLayer(
        dot_prod, {"min": 0, "max": 1, "palette": ["white", "black"]}, "Similarity between years"
    )
    Map.centerObject(point, 12)

    print("Visualization created. Displaying map...")
    Map.save("map_output.html")
    print("Map saved to 'map_output.html'")
else:
    print(f"\nCannot proceed: 2023 has {count_2023} images, 2024 has {count_2024} images.")

# %%

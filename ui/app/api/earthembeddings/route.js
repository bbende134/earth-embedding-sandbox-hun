// https://github.com/ramiqcom/ee-webmap/blob/master/src/app/api/ee/route.js

import ee from "@google/earthengine";



/**
 * Function to authenticate and initialize earth engine using google service account private key
 * This function is made so that authentication doesnt have to use callback but with promise (better to read)
 * @param {JSON} key JSON string of the private key
 * @returns {Promise<void>} did not return anything
 */
async function authenticate(key) {
  return new Promise((resolve, reject) => {
    ee.data.authenticateViaPrivateKey(
      JSON.parse(key),
      () =>
        ee.initialize(
          null,
          null,
          () => resolve(),
          (error) => reject(new Error(error))
        ),
      (error) => reject(new Error(error))
    );
  });
}

/**
 * Function to get the image tile url
 * This function is also for no callback
 * @param {ee.Image} image
 * @param {{ min: [number, number, number], max: [number, number, number], bands: [string, string, string]}}
 * @returns {Promise<{urlFormat: string}>} Will return the object with key urlFormat for viewing in web map
 */
function getMapId(image, vis) {
  return new Promise((resolve, reject) => {
    image.getMapId(vis, (obj, error) =>
      error ? reject(new Error(error)) : resolve(obj)
    );
  });
}

/**
 * Function to get an actual value of an ee object
 * @param {any} obj
 * @returns {any}
 */
function evaluate(obj) {
  return new Promise((resolve, reject) =>
    obj.evaluate((result, error) =>
      error ? reject(new Error(error)) : resolve(result)
    )
  );
}

/**
 * Function that process earth engine script
 * Earth Engine script can only be processed on the server. So you cannot run it on the browser
 * @returns {Response} Returning response which body contain the url of the earth engine layer
 */
export async function GET(req) {
  try {
    const key = process.env.EE_SERVICE_ACCOUNT;
    await authenticate(key);

    const url = new URL(req.url);
    const bandParam = url.searchParams.get("band") || "A20"; // fallback if not provided

    const minParam = url.searchParams.get("min");
    const maxParam = url.searchParams.get("max");

    const bands = bandParam.split(",").map((b) => b.trim());
    const mins = minParam?.split(",").map((v) => parseFloat(v.trim()));
    const maxs = maxParam?.split(",").map((v) => parseFloat(v.trim()));

    // --- Validations ---
    if (![1, 3].includes(bands.length)) {
      return Response.json(
        { message: "Only 1 or 3 bands may be requested." },
        { status: 400 }
      );
    }

    if ((mins && mins.length !== bands.length) || (maxs && maxs.length !== bands.length)) {
      return Response.json(
        {
          message: `min and max arrays must match the number of bands (${bands.length})`,
        },
        { status: 400 }
      );
    }

    const minValues = mins ?? Array(bands.length).fill(-1);
    const maxValues = maxs ?? Array(bands.length).fill(1);

    // Image collection of sentinel-2
    const col = ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL");

    // Geojson geometry of the are we want
    const geojson = {
      coordinates: [
        [
          [103.57567457694688, -1.5538708282870601],
          [103.57567457694688, -1.6154123989164617],
          [103.63624333965345, -1.6154123989164617],
          [103.63624333965345, -1.5538708282870601],
          [103.57567457694688, -1.5538708282870601],
        ],
      ],
      type: "Polygon",
    };

    // Turn the geojson geometry to ee.Geometry for filtering earth engine collection
    const geometry = ee.Geometry(geojson);

    // Range of date for filter
    const start = "2023-12-30";
    const end = "2024-01-02";

    // Filter by date and bounds
    const filtered = col.filterDate(start, end).select(bands);

    // Create a median composite of the image
    const mosaic = filtered.mosaic();

    // Image visualization parameter
    // Using NIR-SWIR1-SWIR2 composite
    const vis = {
      min: minValues,
      max: maxValues,
      bands: bands,
    };

    // Get url format of the image
    const { urlFormat } = await getMapId(mosaic, vis);

    // Also get the image geometry
    //const imageGeom = filtered.geometry();
    //const imageGeometryGeojson = await evaluate(imageGeom);

    // Return the result to the client/browser
    return Response.json({ urlFormat, geojson: geojson });
  } catch (error) {
    return Response.json({ message: error.message }, { status: 500 });
  }
}
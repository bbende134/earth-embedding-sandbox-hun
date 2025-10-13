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

async function loadGeoJSONFromGCS(url) {
  const res = await fetch(url, { method: "GET" });
  if (!res.ok) throw new Error(`Failed to fetch ${url}: ${res.status}`);
  return (await res.json()) ;
}

/**
 * Function that process earth engine script
 * Earth Engine script can only be processed on the server. So you cannot run it on the browser
 * @returns {Response} Returning response which body contain the url of the earth engine layer
 */
export async function GET(req) {
  try {
    // Read service account key from file
    const fs = require('fs');
    const path = require('path');
    const keyPath = '/home/barczabende/dev/earth-embedding-sandbox-hun/gen-lang-client-0291927848-14f8e1a428bd.json';
    console.log('Key path:', keyPath);
    const key = fs.readFileSync(keyPath, 'utf8');
    console.log('Key loaded successfully, length:', key.length);

    await authenticate(key);
    console.log('Earth Engine authentication successful');

    const url = new URL(req.url);
    const bandParam = url.searchParams.get("band") || "A20"; // fallback if not provided
    const areaParam = url.searchParams.get("area") || "budapest"; // default to budapest
    const startParam = url.searchParams.get("start") || "2023-12-30"; // default start
    const endParam = url.searchParams.get("end") || "2024-01-02"; // default end

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

    // Read geojson from file based on area
    const geojsonPath = `/home/barczabende/dev/earth-embedding-sandbox-hun/${areaParam}.geojson`;
    console.log('GeoJSON path:', geojsonPath);
    const gj = JSON.parse(fs.readFileSync(geojsonPath, 'utf8'));
    console.log('GeoJSON loaded successfully');

    // Image collection of sentinel-2
    const col = ee.ImageCollection("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL");

    // Geojson geometry of the are we want
    // const geojson = {
    //   coordinates: [
    //     [
    //       [103.57567457694688, -1.5538708282870601],
    //       [103.57567457694688, -1.6154123989164617],
    //       [103.63624333965345, -1.6154123989164617],
    //       [103.63624333965345, -1.5538708282870601],
    //       [103.57567457694688, -1.5538708282870601],
    //     ],
    //   ],
    //   type: "Polygon",
    // };
    console.log(gj)

    // Turn the geojson geometry to ee.Geometry for filtering earth engine collection
    const geometry = ee.Geometry(gj.geometry);

    // Range of date for filter
    const start = startParam;
    const end = endParam;

    // Filter by date and bounds
    const filtered = col.filterDate(start, end).filterBounds(geometry).select(bands)//.filter(ee.Filter.eq("UTM_ZONE","30N"));

    // Create a median composite of the image
    const mosaic = filtered.mosaic().clip(geometry);

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
    return Response.json({ urlFormat });
  } catch (error) {
    console.error('Earth Engine API error:', error);
    console.error('Error stack:', error.stack);
    return Response.json({ message: error.message }, { status: 500 });
  }
}
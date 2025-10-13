"use client";

import { bbox } from "@turf/turf";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";
import MapboxDraw from "@mapbox/mapbox-gl-draw";
import { useEffect, useRef, useState } from "react";
import { send } from "process";

const BAND_OPTIONS = Array.from({ length: 64 }, (_, i) => `A${i.toString().padStart(2, "0")}`);
// bands: ["A00", "A01", ..., "A63"];
const mapIdDiv = "map";
const eeLayerId = "ee-layer";

const RESULT_SOURCE_ID = "server-result";
const RESULT_FILL_ID = "server-result-fill";
const RESULT_LINE_ID = "server-result-line";

// Define available areas
const AREAS = [
  { name: "Budapest", file: "budapest", center: [19.0402, 47.4979], zoom: 10 },
  { name: "Hungary", file: "hungary", center: [19.0402, 47.4979], zoom: 7 },
  { name: "Northwest Hungary 0", file: "processed/northwest_hungary_0", center: [16.7, 47.7], zoom: 12 },
];



function PillSwitch({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <div
      onClick={onChange}
      style={{
        width: "70px",
        height: "20px",
        backgroundColor: checked ? "#4caf50" : "#888",
        borderRadius: "999px",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        alignContent: "space-between",
        justifyContent: "space-between",
        padding: "4px",
        transition: "background-color 0.3s",
      }}
    >
      {checked ? "RGB" : "" }
      <div
        style={{
          height: "26px",
          width: "26px",
          borderRadius: "50%",
          backgroundColor: "#fff",
          transform: checked ? "translateX(0px)" : "translateX(-30)",
          transition: "transform 0.3s",
        }}
      />
      { checked ? "" : "Mono" }
    </div>
  );
}

export default function EarthEmbeddings() {
  
  const mapStyle = {
    height: "100%",
    width: "100%",
  };

  const mapRef = useRef<mapboxgl.Map | null>(null);
  const drawRef = useRef<MapboxDraw | null>(null);
  const labelLayerIdsRef = useRef<string[]>([]);
  const [isRGB, setIsRGB] = useState(true);
  const [bands, setBands] = useState(["A00","A01","A02"]); // Grayscale or [R, G, B]
  const [minValues, setMinValues] = useState(["-1","-1","-1"]);
  const [maxValues, setMaxValues] = useState(["1","1","1"]);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [k, setK] = useState<number>(5); // allow user to increase up to 200
  const kRef = useRef(k);


  // UI state for layer visibility + labels toggle
  const [eeVisible, setEeVisible] = useState(true);
  const [labelsVisible, setLabelsVisible] = useState(false);
  const [visualizationMode, setVisualizationMode] = useState<'points' | 'polygons'>('points');
  const [lastResult, setLastResult] = useState<GeoJSON.FeatureCollection>({ type: 'FeatureCollection', features: [] });

  // New: selected area
  const [selectedArea, setSelectedArea] = useState(AREAS[0]); // default to first

  // lightweight draw state
  // New: track if any polygon exists
  const [hasPolygon, setHasPolygon] = useState(false);

  const updateBand = (index: number, value: string) => {
    const updated = [...bands];
    updated[index] = value;
    setBands(updated);
  };

  const updateMin = (index: number, value: string) => {
    const updated = [...minValues];
    updated[index] = value;
    setMinValues(updated);
  };

  const updateMax = (index: number, value: string) => {
    const updated = [...maxValues];
    updated[index] = value;
    setMaxValues(updated);
  };

  const numberInputStyle: React.CSSProperties = {
    marginLeft: 8,
    width: 50,
    color: "#fff",           // make text white
    background: "#222",      // dark bg for contrast
    border: "1px solid #444",
    borderRadius: 6,
    padding: "4px 6px",
  };

  const fetchAndRenderLayer = async () => {
    const bandQuery = bands.join(",");
    const minQuery = minValues.join(",");
    const maxQuery = maxValues.join(",");

    const res = await fetch(
      `/api/earthembeddings?band=${bandQuery}&min=${minQuery}&max=${maxQuery}&area=${selectedArea.file}`
    );

    const { urlFormat, message } = await res.json();

    if (!res.ok) {
      console.error("API error:", message);
      return;
    }

    const map = mapRef.current;
    if (!map) return;

    if (map.getLayer(eeLayerId)) {
      map.removeLayer(eeLayerId);
      map.removeSource(eeLayerId);
    }

    map.addSource(eeLayerId, {
      type: "raster",
      tiles: [urlFormat],
      tileSize: 256,
    });

    map.addLayer({
      id: eeLayerId,
      type: "raster",
      source: eeLayerId,
      layout: { visibility: eeVisible ? "visible" : "none" }, // respect toggle
      minzoom: 0,
      maxzoom: 20,
    },"z-index-1" );
  };

  function upsertResultFeatureCollection(fc: GeoJSON.FeatureCollection) {
    const map = mapRef.current;
    if (!map) return;

    // Add or update the source
    if (map.getSource(RESULT_SOURCE_ID)) {
      (map.getSource(RESULT_SOURCE_ID) as mapboxgl.GeoJSONSource).setData(fc);
    } else {
      map.addSource(RESULT_SOURCE_ID, { type: "geojson", data: fc});
    }

    // Ensure circle layer exists
    if (!map.getLayer(RESULT_FILL_ID)) {
      map.addLayer({
        id: RESULT_FILL_ID,
        type: "circle",
        source: RESULT_SOURCE_ID,
        paint: { 
          "circle-color": "#FF5722",
          "circle-radius": 8,
          "circle-opacity": 0.8
        },
      });
    }
  }

  // Put this inside your component:
  const setBasemapLabels = (visible: boolean) => {
    const map = mapRef.current;
    if (!map) return;

    // Flip all your basemap switches together
    map.setConfig?.("basemap",{
        showPlaceLabels: visible,
        showPointOfInterestLabels: visible,
        showLandmarkIcons: visible,
        showLandmarkIconLabels: visible,
        showRoadLabels: visible,
        showTransitLabels: visible,
        showRoadsAndTransit: visible,
      },
    );
  };

  // init draw
  const draw = new MapboxDraw({
    displayControlsDefault: false,
    controls: {
      // we’ll trigger polygon mode via a custom button, but you can enable the UI here if you like:
      polygon: true, trash: true
    },
    defaultMode: "simple_select",
  });

  // keep drawer in sync with k
  useEffect(() => { kRef.current = k; }, [k]);

  // Map setup
  useEffect(() => {
    mapboxgl.accessToken = process.env.NEXT_PUBLIC_MAPBOX_ACCESS_TOKEN!;
    // Disable Mapbox telemetry
    (mapboxgl.config as any).ENABLE_TELEMETRY = false;
    if (typeof window !== 'undefined') {
      localStorage.setItem('mapbox:events:enabled', 'false');
    }
    const map = new mapboxgl.Map({
      container: mapIdDiv,
      zoom: selectedArea.zoom,
      center: selectedArea.center as [number, number],
      style: "mapbox://styles/mapbox/standard-satellite",
    });
    mapRef.current = map;

    map.on("load", () => {
      map.addSource('empty', {
        type: 'geojson',
        data: { type: 'FeatureCollection', features: [] }
      });

      const zIndex2 = map.addLayer({
        id: 'z-index-2',
        type: 'symbol',
        source: 'empty'
      });

      const zIndex1 = map.addLayer({
        id: 'z-index-1',
        type: 'symbol',
        source: 'empty'
      }, 'z-index-2'); // place this layer below zIndex2

      fetchAndRenderLayer();
      setBasemapLabels(labelsVisible);

      map.addSource(RESULT_SOURCE_ID, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ 
        id: RESULT_FILL_ID, 
        type: "circle", 
        source: RESULT_SOURCE_ID, 
        paint: {
            'circle-color': '#FF5722',
            'circle-radius': 8,
            'circle-opacity': 0.8
        },
        layout: { visibility: visualizationMode === 'points' ? 'visible' : 'none' }
      });
      map.addLayer({
        id: RESULT_LINE_ID,
        type: "fill",
        source: RESULT_SOURCE_ID,
        paint: {
          'fill-color': '#FF5722',
          'fill-opacity': 0.6
        },
        layout: { visibility: visualizationMode === 'polygons' ? 'visible' : 'none' }
      });

      drawRef.current = draw;

      map.addControl(draw, "top-left");

      // Create a container for our custom control
      const sendBtnContainer = document.createElement("div");
      sendBtnContainer.className = "mapboxgl-ctrl mapboxgl-ctrl-group";
      sendBtnContainer.style.marginTop = "4px"; // little gap below draw controls

      // Create the actual button
      const sendBtn = document.createElement("button");
      sendBtn.type = "button";
      sendBtn.title = "Send Polygon";
      sendBtn.innerHTML = "🔎 Search"; // you could use text, an SVG icon, or emoji
      sendBtn.style.width = "80px"
      sendBtn.style.color = "#000000";
      sendBtn.onclick = sendPolygon; // call your function directly

      sendBtnContainer.appendChild(sendBtn);

      // Add to the same corner as the draw controls
      map.addControl({
        onAdd: () => sendBtnContainer,
        onRemove: () => { sendBtnContainer.parentNode?.removeChild(sendBtnContainer); }
      }, "top-left");

      const layers = map.getStyle().layers || [];
      console.log("All layer IDs:", layers.map(l => l.id));
    });

    // keep hasPolygon in sync
    const updateHasPoly = () => {
      const fc = draw.getAll();
      const has = fc.features.some((f) => f.geometry?.type === "Polygon" || f.geometry?.type === "MultiPolygon");
      setHasPolygon(has);
    };
    map.on("draw.create", updateHasPoly);
    map.on("draw.update", updateHasPoly);
    map.on("draw.delete", updateHasPoly);

    return () => {
      map.off("draw.create", updateHasPoly);
      map.off("draw.update", updateHasPoly);
      map.off("draw.delete", updateHasPoly);
      map.removeControl(draw);
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-render EE layer when params change and style is ready
  useEffect(() => {
    if (mapRef.current?.isStyleLoaded()) {
      fetchAndRenderLayer();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bands, minValues, maxValues, selectedArea]);

  // Update map center and zoom when area changes
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.setCenter(selectedArea.center as [number, number]);
    map.setZoom(selectedArea.zoom);
  }, [selectedArea]);

  // Toggle EE layer visibility
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!map.getLayer(eeLayerId)) return;
    map.setLayoutProperty(eeLayerId, "visibility", eeVisible ? "visible" : "none");
  }, [eeVisible]);

  // Toggle labels visibility
  useEffect(() => {
    setBasemapLabels(labelsVisible);
  }, [labelsVisible]);

  // Toggle result visualization mode
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    if (map.getLayer(RESULT_FILL_ID)) {
      map.setLayoutProperty(RESULT_FILL_ID, "visibility", visualizationMode === 'points' ? 'visible' : 'none');
    }
    if (map.getLayer(RESULT_LINE_ID)) {
      map.setLayoutProperty(RESULT_LINE_ID, "visibility", visualizationMode === 'polygons' ? 'visible' : 'none');
    }

    // Re-apply visualization to existing results
    if (lastResult && lastResult.features.length > 0) {
      const visualizedFC = applyVisualizationMode(lastResult);
      (map.getSource(RESULT_SOURCE_ID) as mapboxgl.GeoJSONSource)?.setData(visualizedFC);
    }
  }, [visualizationMode, lastResult]);

  // Mode toggle (RGB vs grayscale)
  const handleModeToggle = () => {
    setIsRGB((prev) => {
      const next = !prev;
      if (next) {
        setBands(["A00", "A01", "A02"]);
        setMinValues(["-1", "-1", "-1"]);
        setMaxValues(["1", "1", "1"]);
      } else {
        setBands(["A00"]);
        setMinValues(["-1"]);
        setMaxValues(["1"]);
      }
      return next;
    });
  };

  // Function to apply visualization mode to feature collection
  const applyVisualizationMode = (fc: GeoJSON.FeatureCollection): GeoJSON.FeatureCollection => {
    if (visualizationMode === 'polygons') {
      return {
        ...fc,
        features: fc.features.map(f => {
          const [lon, lat] = (f.geometry as GeoJSON.Point).coordinates;
          const size = 0.005; // larger squares
          return {
            ...f,
            geometry: {
              type: 'Polygon',
              coordinates: [[
                [lon - size/2, lat - size/2],
                [lon + size/2, lat - size/2],
                [lon + size/2, lat + size/2],
                [lon - size/2, lat + size/2],
                [lon - size/2, lat - size/2]
              ]]
            }
          };
        })
      };
    }
    return fc; // return as points
  };

  // Draw actions
  const startPolygonMode = () => drawRef.current?.changeMode("draw_polygon");
  const clearDrawing = () => {
    drawRef.current?.deleteAll();
    setHasPolygon(false);
  };

  const sendPolygon = async () => {
    const draw = drawRef.current;
    const map = mapRef.current;
    const currentK = kRef.current;
    if (!map) return;
    if (!draw) return;
    const fc = draw.getAll();
    if (!fc.features.length) {
      alert("Draw a polygon first.");
      return;
    }
    if (fc.features.length > 1) {
      alert("Send only a _single_ polygon.");
      return;
    }
    const poly = [...fc.features].reverse().find(
    (f) => f.geometry?.type === "Polygon"
  );

  if (!poly) {
    alert("Draw a polygon first.");
    return;
  }

  // If it's a MultiPolygon, send just the first polygon as a single Polygon
  const featureToSend: GeoJSON.Feature<GeoJSON.Polygon> = poly as GeoJSON.Feature<GeoJSON.Polygon>;

  try {
    console.log("send k", currentK);
        const resp = await fetch(`http://${window.location.hostname}:8000/neighbours`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Send a SINGLE polygon feature
      body: JSON.stringify({
        "geojson": featureToSend.geometry,
        "k": currentK,
      }),
    });

    if (!resp.ok) throw new Error(await resp.text());

    // Expect a FeatureCollection back
    const result = (await resp.json())
    const resultFC = result.neighbours as GeoJSON.FeatureCollection;

    // Store the original result (points)
    setLastResult(resultFC);

    // Apply visualization mode
    const visualizedFC = applyVisualizationMode(resultFC);

    (map.getSource(RESULT_SOURCE_ID) as mapboxgl.GeoJSONSource)?.setData(visualizedFC);

    // Replace whatever is currently on the map with the new FeatureCollection
    //upsertResultFeatureCollection(resultFC);

    // Zoom to result
    const b = bbox(resultFC) as [number, number, number, number];
    console.log("Bounding box:", b);
    map.fitBounds(b, { padding: 24 });

  } catch (e) {
    console.error(e);
    let errorMessage = "Failed to send polygon. See console for details.";
    if (e instanceof Error) {
      try {
        const errorData = JSON.parse(e.message);
        if (errorData.detail) {
          errorMessage = `Error: ${errorData.detail}`;
        }
      } catch {
        // If not JSON, use the message as is
        errorMessage = e.message;
      }
    }
    alert(errorMessage);
  }
};


  return (
    <div style={{ display: "flex", height: "100vh", width: "100vw" }}>
      {/* Drawer */}
      <div
        style={{
          width: drawerOpen ? 400 : 40,
          transition: "width 0.3s",
          background: "#000",
          color: "#fff",
          borderRight: "1px solid #333",
          padding: drawerOpen ? "1rem" : "0.5rem",
          overflow: "auto",
          fontSize: 13,               // smaller overall text in drawer
          lineHeight: 1.3,
        }}
      >
        <button onClick={() => setDrawerOpen(!drawerOpen)} title="Toggle panel">
          {drawerOpen ? "←" : "→"}
        </button>

        {drawerOpen && (
          <div>
            <h1 style={{ marginTop: 8, fontSize: 16 }}>Earth Embeddings Sandbox</h1>
            <p style={{ marginTop: 4, fontSize: 12, color: "cyan" }}> <a href="https://github.com/Lkruitwagen/earth-embedding-sandbox">GitHub Repository</a></p>

            <hr style={{ margin: "8px 0" }} />

            <p style={{ fontSize: 11, marginBottom: 8, textAlign: "justify" }}> 
              With this demo you can inspect the new <a style={{ color: "cyan" }} href="https://deepmind.google/discover/blog/alphaearth-foundations-helps-map-our-planet-in-unprecedented-detail/">AlphaEarth Foundations</a> embeddings from Google Deepmind and query them using a drawn polygon. 
              These embeddings are a general-purpose vector representation of every 10mx10m are on Earth&apos;s land surface area, trained on a large corpus of satellite data and text.
              One of the most promising applications of general-purpose embeddings is to enable similarity search and change detection with no required training or finetuning.
              Draw a polygon on the map to select an area of interest, then click the &quot;🔎 Search&quot; button to find similar areas in the dataset!
            </p>

            <p style={{ fontSize: 11, marginBottom: 8, textAlign: "justify" }}>
              AlphaEarth Foundations is licenced under CC-BY-4.0; it is a dataset produced by Google and Google Deepmind. It is available from <a style={{ color: "cyan" }} href="https://earthengine.google.com/alphaearth/">Google Earth Engine</a>.
            </p>

            

            <hr style={{ margin: "8px 0" }} />
            <h2 style={{ margin: 0, marginBottom: 8, fontSize: 14 }}>Area Selection</h2>
            <div style={{ marginTop: 8 }}>
              <label style={{ display: "block", marginBottom: 4 }}>Select Area</label>
              <select
                value={selectedArea.file}
                onChange={(e) => {
                  const area = AREAS.find(a => a.file === e.target.value);
                  if (area) setSelectedArea(area);
                }}
                style={{
                  background: "#111",
                  color: "#fff",
                  border: "1px solid #444",
                  borderRadius: 6,
                  padding: "4px 8px",
                  fontSize: 12,
                  width: "100%",
                }}
              >
                {AREAS.map((area) => (
                  <option key={area.file} value={area.file}>{area.name}</option>
                ))}
              </select>
            </div>

            <hr style={{ margin: "8px 0" }} />
            <h2 style={{ margin: 0, marginBottom: 8, fontSize: 14 }}>Visualization Controls</h2>

            {/* 1) EE layer toggle */}
            <div style={{ marginTop: 16 }}>
              <h3 style={{ margin: 0, marginBottom: 8 }}>Layers</h3>
              <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input type="checkbox" checked={eeVisible} onChange={(e) => setEeVisible(e.target.checked)} />
                Show Earth Engine layer
              </label>
              {/* 2) labels toggle */}
              <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                <input
                  type="checkbox"
                  checked={labelsVisible}
                  onChange={(e) => setLabelsVisible(e.target.checked)}
                />
                Show place labels
              </label>
              {/* 3) visualization mode toggle */}
              <div style={{ marginTop: 8 }}>
                <label style={{ display: "block", marginBottom: 4 }}>Result Visualization</label>
                <select
                  value={visualizationMode}
                  onChange={(e) => setVisualizationMode(e.target.value as 'points' | 'polygons')}
                  style={{
                    background: "#111",
                    color: "#fff",
                    border: "1px solid #444",
                    borderRadius: 6,
                    padding: "4px 8px",
                    fontSize: 12,
                    width: "100%",
                  }}
                >
                  <option value="points">Points (Circles)</option>
                  <option value="polygons">Polygons (Squares)</option>
                </select>
              </div>
            </div>

            {(() => {
            const channels = isRGB ? ["Red", "Green", "Blue"] : ["Gray"];
            const gridCols = `72px repeat(${channels.length}, 1fr)`;
            const headerCell: React.CSSProperties = { textAlign: "center", fontWeight: "bold" };

            return (
              <div style={{ marginTop: 12, fontSize: 12 }}>
                {/* Header row */}
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 4, marginBottom: 6 }}>
                  <div><PillSwitch checked={isRGB} onChange={handleModeToggle} /></div> {/* empty top-left cell */}
                  {channels.map((label) => (
                    <div key={label} style={headerCell}>{label}</div>
                  ))}
                </div>

                {/* Band selector row */}
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 4, marginBottom: 4 }}>
                  <div style={{ fontWeight: "bold" }}>Band</div>
                  {bands.map((band, i) => (
                    <select
                      key={`band-${i}`}
                      value={band}
                      onChange={(e) => updateBand(i, e.target.value)}
                      style={{
                        background: "#111",
                        color: "#fff",
                        border: "1px solid #444",
                        borderRadius: 6,
                        padding: "2px 4px",
                        fontSize: 12,
                        width: "100%",
                      }}
                    >
                      {BAND_OPTIONS.map((b) => (
                        <option key={b} value={b}>{b}</option>
                      ))}
                    </select>
                  ))}
                </div>

                {/* Min row */}
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 4, marginBottom: 4 }}>
                  <div style={{ fontWeight: "bold" }}>Min</div>
                  {minValues.map((minVal, i) => (
                    <input
                      key={`min-${i}`}
                      type="number"
                      step={0.1}
                      value={minVal}
                      onChange={(e) => updateMin(i, e.target.value)}
                      style={{ ...numberInputStyle, fontSize: 12, padding: "2px 4px", width: "100%" }}
                    />
                  ))}
                </div>

                {/* Max row */}
                <div style={{ display: "grid", gridTemplateColumns: gridCols, gap: 4 }}>
                  <div style={{ fontWeight: "bold" }}>Max</div>
                  {maxValues.map((maxVal, i) => (
                    <input
                      key={`max-${i}`}
                      type="number"
                      step={0.1}
                      value={maxVal}
                      onChange={(e) => updateMax(i, e.target.value)}
                      style={{ ...numberInputStyle, fontSize: 12, padding: "2px 4px", width: "100%" }}
                    />
                  ))}
                </div>
              </div>
                  );
                })()}

            {/* k control */}
            <hr style={{ margin: "16px 0" }} />
            <h2 style={{ margin: 0, marginBottom: 8, fontSize: 14 }}>Similarity Search Controls</h2>
            <p style={{ fontSize: 11, marginBottom: 8, textAlign: "justify" }}>
              Similarity search uses the embeddings of the drawn polygon to find similar areas in the dataset.
              Adjust the number of neighbours (k) to control how many similar areas are returned.
              These are the closest &apos;neighbours&apos; in the embedding space - areas that have the most similar vector representations.
              Search is sensitive to the drawn polygon&apos;s size - you should get results that are similar in scale to the drawn area.
            </p>
            <div style={{ marginTop: 16 }}>
              <h3 style={{ margin: 0, marginBottom: 8, fontSize: 14 }}>Neighbours (k)</h3>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="range"
                  min={0}
                  max={200}
                  step={1}
                  value={k}
                  onChange={(e) => setK(parseInt(e.target.value, 10))}
                  style={{ width: 160 }}
                />
                <input
                  type="number"
                  min={0}
                  max={200}
                  step={1}
                  value={k}
                  onChange={(e) => {
                    const val = Math.max(0, Math.min(200, Number(e.target.value)));
                    console.log("set k", val);
                    setK(val);
                  }}
                  style={numberInputStyle}
                />
              </div>
            </div>
            <hr style={{ margin: "16px 0" }} />
      <p style={{ fontSize: 10, marginBottom: 8, textAlign: "justify" }}>
              Some technical details: the embeddings have been extracted from Google Earth Engine using Apache Beam, GCP&apos;s Dataflow, <a style={{ color: "cyan" }} href="https://xarray-beam.readthedocs.io/en/latest/index.html">xarray-beam</a>, and <a style={{ color: "cyan" }} href="https://github.com/google/Xee/tree/main/xee">xee</a>.
              The embeddings have been mean-pooled through a pyramid of spatial reductions and stored in a <a style={{ color: "cyan" }} href="https://milvus.io/">Milvus</a> database.
              A <a style={{ color: "cyan" }} href="https://fastapi.tiangolo.com/">FastAPI</a> application handles the similarity search.
              This UI is built with React and <a style={{ color: "cyan" }} href="https://nextjs.org/">Next.js</a>, and is served using <a style={{ color: "cyan" }} href="https://vercel.com/">Vercel</a>.
              Deployment is handled using Github Actions and <a style={{ color: "cyan" }} href="https://developer.hashicorp.com/terraform">Terraform</a>.
            </p>

            
          </div>
        )}
      </div>
      

      <div id={mapIdDiv} style={mapStyle} />
    </div>
  );
}
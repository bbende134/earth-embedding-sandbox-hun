"use client";

import { bbox } from "@turf/turf";
import mapboxgl from "mapbox-gl";
import "mapbox-gl/dist/mapbox-gl.css";
import { useEffect, useRef, useState } from "react";

const BAND_OPTIONS = Array.from({ length: 64 }, (_, i) => `A${i.toString().padStart(2, "0")}`);
// bands: ["A00", "A01", ..., "A63"];
const mapIdDiv = "map";
const eeLayerId = "ee-layer";

function PillSwitch({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <div
      onClick={onChange}
      style={{
        width: "60px",
        height: "30px",
        backgroundColor: checked ? "#4caf50" : "#888",
        borderRadius: "999px",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        padding: "2px",
        transition: "background-color 0.3s",
      }}
    >
      <div
        style={{
          height: "26px",
          width: "26px",
          borderRadius: "50%",
          backgroundColor: "#fff",
          transform: checked ? "translateX(30px)" : "translateX(0)",
          transition: "transform 0.3s",
        }}
      />
    </div>
  );
}

export default function EarthEmbeddings() {
  
  const mapStyle = {
    height: "100%",
    width: "100%",
  };

  const mapRef = useRef<mapboxgl.Map | null>(null);
  const [isRGB, setIsRGB] = useState(false);
  const [bands, setBands] = useState(["A00"]); // Grayscale or [R, G, B]
  const [minValues, setMinValues] = useState(["-0.7"]);
  const [maxValues, setMaxValues] = useState(["0.7"]);
  const [drawerOpen, setDrawerOpen] = useState(true);

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

  const fetchAndRenderLayer = async () => {
    const bandQuery = bands.join(",");
    const minQuery = minValues.join(",");
    const maxQuery = maxValues.join(",");

    const res = await fetch(
      `/api/earthembeddings?band=${bandQuery}&min=${minQuery}&max=${maxQuery}`
    );

    const { urlFormat, geojson, message } = await res.json();

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
      minzoom: 0,
      maxzoom: 20,
    });

    const bounds = bbox(geojson);
    map.fitBounds(bounds, { padding: 20 });
  };

  useEffect(() => {
    mapboxgl.accessToken = process.env.NEXT_PUBLIC_MAPBOX_ACCESS_TOKEN!;
    const map = new mapboxgl.Map({
      container: mapIdDiv,
      zoom: 4,
      center: [117, 0],
      style: "mapbox://styles/mapbox/standard-satellite",
    });
    mapRef.current = map;

    map.on("load", fetchAndRenderLayer);
    return () => map.remove();
  }, []);

  useEffect(() => {
    if (mapRef.current?.isStyleLoaded()) {
      fetchAndRenderLayer();
    }
  }, [bands, minValues, maxValues]);

  // Toggle between RGB and grayscale modes
  const handleModeToggle = () => {
    setIsRGB(!isRGB);
    if (!isRGB) {
      // switching to RGB
      setBands(["A00", "A01", "A02"]);
      setMinValues(["-0.7", "-0.7", "-0.7"]);
      setMaxValues(["0.7", "0.7", "0.7"]);
    } else {
      // switching to grayscale
      setBands(["A00"]);
      setMinValues(["-0.7"]);
      setMaxValues(["0.7"]);
    }
  };

  return (
  <div style={{ display: "flex", height: "100vh", width: "100vw" }}>
      {/* Drawer */}
      <div
        style={{
          width: drawerOpen ? "320px" : "40px",
          transition: "width 0.3s",
          backgroundColor: "#000000",
          borderRight: "1px solid #ccc",
          padding: drawerOpen ? "1rem" : "0.5rem",
          overflow: "hidden",
        }}
      >
        <button onClick={() => setDrawerOpen(!drawerOpen)}>
          {drawerOpen ? "←" : "→"}
        </button>
        {drawerOpen && (
          <div>
            <h1>Earth Embeddings</h1>
            <p>Toggle between grayscale and RGB band visualization:</p>

            <label>
              <div style={{ marginTop: "1rem" }}>
                <p style={{ marginBottom: "0.5rem" }}>RGB Mode</p>
                <PillSwitch checked={isRGB} onChange={handleModeToggle} />
              </div>
            </label>

            <div style={{ marginTop: "1rem" }}>
              {bands.map((band, i) => (
                <div key={i} style={{ marginBottom: "1rem" }}>
                  <label>
                    {isRGB ? ["R", "G", "B"][i] : "Band"}:
                    <select
                      value={band}
                      onChange={(e) => updateBand(i, e.target.value)}
                      style={{ marginLeft: "0.5rem" }}
                    >
                      {BAND_OPTIONS.map((b) => (
                        <option key={b} value={b}>
                          {b}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div style={{ marginTop: "0.25rem" }}>
                    <label>
                      Min:
                      <input
                        type="number"
                        step={0.1}
                        value={minValues[i]}
                        onChange={(e) => updateMin(i, e.target.value)}
                        style={{ marginLeft: "0.5rem", width: "80px" }}
                      />
                    </label>
                    <label style={{ marginLeft: "1rem" }}>
                      Max:
                      <input
                        type="number"
                        step={0.1}
                        value={maxValues[i]}
                        onChange={(e) => updateMax(i, e.target.value)}
                        style={{ marginLeft: "0.5rem", width: "80px" }}
                      />
                    </label>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div id={mapIdDiv} style={mapStyle}></div>
    </div>
  );
}
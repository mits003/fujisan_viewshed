"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

type Mountain = {
  id: string;
  name: string;
  fuji_alias: string;
  elevation: number;
  coordinates: [number, number]; // [lon, lat]
};

const VIEWSHED_SOURCE = "viewshed";
const VIEWSHED_FILL_LAYER = "viewshed-fill";
const VIEWSHED_OUTLINE_LAYER = "viewshed-outline";

export default function Map() {
  const mapContainer = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<maplibregl.Marker[]>([]);
  const [mountains, setMountains] = useState<Mountain[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Load mountain metadata
  useEffect(() => {
    fetch("/data/mountains.geojson")
      .then((res) => res.json())
      .then((geojson) => {
        const items: Mountain[] = geojson.features.map(
          (f: {
            properties: Record<string, unknown>;
            geometry: { coordinates: [number, number] };
          }) => ({
            id: f.properties.id as string,
            name: f.properties.name as string,
            fuji_alias: f.properties.fuji_alias as string,
            elevation: f.properties.elevation as number,
            coordinates: f.geometry.coordinates,
          })
        );
        setMountains(items);
      });
  }, []);

  // Initialize map
  useEffect(() => {
    if (!mapContainer.current) return;

    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);

    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "&copy; OpenStreetMap contributors",
          },
        },
        layers: [
          {
            id: "osm",
            type: "raster",
            source: "osm",
          },
        ],
      },
      center: [138.0, 37.0],
      zoom: 6,
    });

    map.addControl(new maplibregl.NavigationControl(), "top-right");

    map.on("load", () => {
      map.addSource(VIEWSHED_SOURCE, {
        type: "vector",
        url: `pmtiles://${window.location.origin}/data/fuji_viewshed.pmtiles`,
      });

      map.addLayer({
        id: VIEWSHED_FILL_LAYER,
        type: "fill",
        source: VIEWSHED_SOURCE,
        "source-layer": "viewshed",
        paint: {
          "fill-color": "#ff4444",
          "fill-opacity": 0.3,
        },
      });

      map.addLayer({
        id: VIEWSHED_OUTLINE_LAYER,
        type: "line",
        source: VIEWSHED_SOURCE,
        "source-layer": "viewshed",
        paint: {
          "line-color": "#ff4444",
          "line-width": 0.5,
          "line-opacity": 0.5,
        },
      });
    });

    mapRef.current = map;

    return () => {
      map.remove();
      maplibregl.removeProtocol("pmtiles");
    };
  }, []);

  // Add summit markers when mountains data and map are both ready
  useEffect(() => {
    const map = mapRef.current;
    if (!map || mountains.length === 0) return;

    // Clear existing markers
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    mountains.forEach((mt) => {
      const el = document.createElement("div");
      el.style.width = "14px";
      el.style.height = "14px";
      el.style.backgroundColor = "#d97706";
      el.style.border = "2px solid #ffffff";
      el.style.borderRadius = "50%";
      el.style.boxShadow = "0 1px 3px rgba(0,0,0,0.4)";
      el.style.cursor = "pointer";
      el.title = `${mt.fuji_alias} (${mt.name} ${mt.elevation}m)`;

      const marker = new maplibregl.Marker({ element: el })
        .setLngLat(mt.coordinates)
        .setPopup(
          new maplibregl.Popup({ offset: 12 }).setHTML(
            `<strong>${mt.fuji_alias}</strong><br/>${mt.name} ${mt.elevation}m`
          )
        )
        .addTo(map);

      markersRef.current.push(marker);
    });
  }, [mountains]);

  // Filter by selected mountain
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;

    const filter: maplibregl.FilterSpecification | null = selectedId
      ? ["==", ["get", "mountain_id"], selectedId]
      : null;

    map.setFilter(VIEWSHED_FILL_LAYER, filter);
    map.setFilter(VIEWSHED_OUTLINE_LAYER, filter);

    // Highlight selected marker, fly to it
    mountains.forEach((mt, i) => {
      const marker = markersRef.current[i];
      if (!marker) return;
      const el = marker.getElement();
      const isSelected = mt.id === selectedId;
      el.style.width = isSelected ? "18px" : "14px";
      el.style.height = isSelected ? "18px" : "14px";
      el.style.backgroundColor = isSelected ? "#ef4444" : "#d97706";
    });

    if (selectedId) {
      const mountain = mountains.find((m) => m.id === selectedId);
      if (mountain) {
        map.flyTo({ center: mountain.coordinates, zoom: 10 });
      }
    }
  }, [selectedId, mountains]);

  return (
    <div className="relative h-full w-full">
      <div className="absolute inset-0">
        <div ref={mapContainer} style={{ width: "100%", height: "100%" }} />
      </div>

      {/* Mountain selector panel */}
      <div className="absolute top-3 left-3 bg-white/90 backdrop-blur rounded-lg shadow-lg p-3 max-w-xs z-10">
        <h2 className="text-sm font-bold mb-2 text-gray-800">
          ご当地富士可視域マップ
        </h2>
        <select
          className="w-full text-sm border border-gray-300 rounded px-2 py-1.5 text-gray-700"
          value={selectedId ?? ""}
          onChange={(e) => setSelectedId(e.target.value || null)}
        >
          <option value="">すべて表示</option>
          {mountains.map((m) => (
            <option key={m.id} value={m.id}>
              {m.fuji_alias} ({m.name} {m.elevation}m)
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

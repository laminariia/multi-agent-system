import { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, Polygon, Popup, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Lead } from "~/lib/types";

/* ---------- H3 hex approximation ---------- */

/**
 * Generates hexagon polygon coordinates centered at (lat, lng).
 * Uses a radius in degrees that approximates H3 resolution 7 (~5km).
 */
function hexCorners(
  lat: number,
  lng: number,
  radiusDeg = 0.025
): [number, number][] {
  const corners: [number, number][] = [];
  for (let i = 0; i < 6; i++) {
    const angleDeg = 60 * i - 30;
    const angleRad = (Math.PI / 180) * angleDeg;
    // Correct for latitude distortion on longitude
    const lngCorrection = 1 / Math.cos((lat * Math.PI) / 180);
    corners.push([
      lat + radiusDeg * Math.sin(angleRad),
      lng + radiusDeg * Math.cos(angleRad) * lngCorrection,
    ]);
  }
  return corners;
}

/**
 * Groups leads into hex cells by rounding coordinates to a grid.
 * This is a client-side approximation of H3 hexagonal indexing.
 */
function groupIntoHexCells(
  leads: Lead[],
  gridSize = 0.04
): Map<string, { lat: number; lng: number; leads: Lead[] }> {
  const cells = new Map<string, { lat: number; lng: number; leads: Lead[] }>();

  for (const lead of leads) {
    if (lead.latitude == null || lead.longitude == null) continue;
    if (lead.latitude === 0 && lead.longitude === 0) continue;

    // Snap to grid
    const gridLat = Math.round(lead.latitude / gridSize) * gridSize;
    const gridLng = Math.round(lead.longitude / gridSize) * gridSize;
    const key = `${gridLat.toFixed(4)},${gridLng.toFixed(4)}`;

    const existing = cells.get(key);
    if (existing) {
      existing.leads.push(lead);
    } else {
      cells.set(key, { lat: gridLat, lng: gridLng, leads: [lead] });
    }
  }

  return cells;
}

/* ---------- temperature color mapping ---------- */

const TEMP_COLORS: Record<string, string> = {
  hot: "#ef4444",      // red
  warm: "#eab308",     // yellow
  cold: "#3b82f6",     // blue
};

const TEMP_FILL: Record<string, string> = {
  hot: "rgba(239, 68, 68, 0.4)",
  warm: "rgba(234, 179, 8, 0.3)",
  cold: "rgba(59, 130, 246, 0.25)",
};

/**
 * Determine the dominant temperature for a group of leads.
 * Priority: hot > warm > cold > unknown.
 */
function dominantTemperature(leads: Lead[]): string {
  let hot = 0;
  let warm = 0;
  let cold = 0;

  for (const lead of leads) {
    const temp =
      (lead as unknown as Record<string, unknown>).temperature as string | null;
    const score = lead.lead_score ?? 0;

    if (temp === "hot" || score >= 80) hot++;
    else if (temp === "warm" || score >= 50) warm++;
    else cold++;
  }

  if (hot >= warm && hot >= cold) return "hot";
  if (warm >= cold) return "warm";
  return "cold";
}

function densityOpacity(count: number): number {
  if (count >= 10) return 0.7;
  if (count >= 5) return 0.5;
  if (count >= 2) return 0.35;
  return 0.2;
}

/* ---------- fit bounds helper ---------- */

function FitBoundsHex({
  cells,
}: {
  cells: Map<string, { lat: number; lng: number; leads: Lead[] }>;
}) {
  const map = useMap();

  useEffect(() => {
    if (cells.size === 0) return;

    const coords = Array.from(cells.values()).map((c) => [c.lat, c.lng] as [number, number]);

    if (coords.length === 1) {
      map.setView(coords[0], 13);
      return;
    }

    const bounds = L.latLngBounds(coords);
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 14 });
  }, [map, cells]);

  return null;
}

/* ---------- component ---------- */

interface GeoHexMapProps {
  leads: Lead[];
  onHexClick?: (leads: Lead[]) => void;
  selectedCategory?: string;
  className?: string;
}

export default function GeoHexMap({
  leads,
  onHexClick,
  selectedCategory,
  className,
}: GeoHexMapProps) {
  const mapRef = useRef<L.Map | null>(null);

  const filteredLeads = useMemo(() => {
    if (!selectedCategory || selectedCategory === "all") return leads;
    return leads.filter(
      (l) =>
        l.category?.toLowerCase() === selectedCategory.toLowerCase()
    );
  }, [leads, selectedCategory]);

  const cells = useMemo(
    () => groupIntoHexCells(filteredLeads),
    [filteredLeads]
  );

  if (cells.size === 0) {
    return (
      <div
        className={`flex items-center justify-center min-h-[300px] rounded-lg border border-dashed border-border/50 bg-muted/20 ${className ?? ""}`}
      >
        <div className="text-center">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-8 w-8 mx-auto text-muted-foreground mb-2"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
            <path d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          <p className="text-sm text-muted-foreground">
            No leads with coordinates
          </p>
        </div>
      </div>
    );
  }

  const firstCell = Array.from(cells.values())[0];
  const center: [number, number] = [firstCell.lat, firstCell.lng];

  return (
    <div
      className={`rounded-lg overflow-hidden border border-border/50 ${className ?? ""}`}
    >
      <MapContainer
        center={center}
        zoom={12}
        className="h-full w-full min-h-[300px]"
        ref={mapRef}
        scrollWheelZoom={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBoundsHex cells={cells} />
        {Array.from(cells.entries()).map(([key, cell]) => {
          const temp = dominantTemperature(cell.leads);
          const color = TEMP_COLORS[temp] ?? "#6b7280";
          const opacity = densityOpacity(cell.leads.length);
          const corners = hexCorners(cell.lat, cell.lng);

          return (
            <Polygon
              key={key}
              positions={corners}
              pathOptions={{
                color,
                fillColor: color,
                fillOpacity: opacity,
                weight: 1.5,
                opacity: 0.8,
              }}
              eventHandlers={{
                click: () => onHexClick?.(cell.leads),
              }}
            >
              <Popup>
                <div className="min-w-[160px]">
                  <p className="font-semibold text-sm mb-1">
                    {cell.leads.length} lead{cell.leads.length !== 1 ? "s" : ""}
                  </p>
                  <p className="text-xs text-gray-500 capitalize mb-2">
                    Temperature: {temp}
                  </p>
                  <div className="space-y-1 max-h-[120px] overflow-y-auto">
                    {cell.leads.slice(0, 5).map((lead) => (
                      <div key={lead.id} className="text-xs">
                        <span className="font-medium">{lead.name}</span>
                        {lead.category && (
                          <span className="text-gray-400 ml-1">
                            ({lead.category})
                          </span>
                        )}
                      </div>
                    ))}
                    {cell.leads.length > 5 && (
                      <p className="text-xs text-gray-400 italic">
                        +{cell.leads.length - 5} more
                      </p>
                    )}
                  </div>
                </div>
              </Popup>
            </Polygon>
          );
        })}
      </MapContainer>
    </div>
  );
}

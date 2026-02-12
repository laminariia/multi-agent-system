import { useEffect, useMemo, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Lead } from "~/lib/types";

// Fix default marker icons (Leaflet's webpack issue)
// We use inline SVG data URIs to avoid asset bundling problems
const markerSvg = (color: string) =>
  `data:image/svg+xml;base64,${btoa(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 36" width="24" height="36"><path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 24 12 24s12-15 12-24C24 5.4 18.6 0 12 0z" fill="${color}" stroke="#fff" stroke-width="1.5"/><circle cx="12" cy="12" r="5" fill="#fff"/></svg>`)}`;

const STATUS_COLORS: Record<string, string> = {
  enriched: "#22c55e",
  new: "#3b82f6",
  no_contact: "#ef4444",
  failed: "#ef4444",
  contacted: "#f97316",
};

function createIcon(status: string) {
  const color = STATUS_COLORS[status] ?? "#6b7280";
  return L.icon({
    iconUrl: markerSvg(color),
    iconSize: [24, 36],
    iconAnchor: [12, 36],
    popupAnchor: [0, -36],
  });
}

interface LeadWithCoords {
  id: string;
  name: string;
  category: string | null;
  city: string | null;
  phone: string | null;
  email: string | null;
  status: string;
  lat: number;
  lng: number;
}

function FitBounds({ leads }: { leads: LeadWithCoords[] }) {
  const map = useMap();

  useEffect(() => {
    if (leads.length === 0) return;

    if (leads.length === 1) {
      map.setView([leads[0].lat, leads[0].lng], 15);
      return;
    }

    const bounds = L.latLngBounds(leads.map((l) => [l.lat, l.lng]));
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
  }, [map, leads]);

  return null;
}

interface LeadMapProps {
  leads: Lead[];
  onMarkerClick?: (id: string) => void;
  className?: string;
}

export default function LeadMap({ leads, onMarkerClick, className }: LeadMapProps) {
  const mapRef = useRef<L.Map | null>(null);

  const leadsWithCoords = useMemo(() => {
    const result: LeadWithCoords[] = [];
    for (const lead of leads) {
      const lat = lead.latitude;
      const lng = lead.longitude;
      if (lat != null && lng != null && lat !== 0 && lng !== 0) {
        result.push({
          id: lead.id,
          name: lead.name,
          category: lead.category,
          city: lead.city,
          phone: lead.phone,
          email: lead.email,
          status: lead.status,
          lat,
          lng,
        });
      }
    }
    return result;
  }, [leads]);

  if (leadsWithCoords.length === 0) {
    return (
      <div className={`flex items-center justify-center min-h-[300px] rounded-lg border border-dashed border-border/50 bg-muted/20 ${className ?? ""}`}>
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
          <p className="text-sm text-muted-foreground">No leads with coordinates</p>
        </div>
      </div>
    );
  }

  const center: [number, number] = [leadsWithCoords[0].lat, leadsWithCoords[0].lng];

  return (
    <div className={`rounded-lg overflow-hidden border border-border/50 ${className ?? ""}`}>
      <MapContainer
        center={center}
        zoom={13}
        className="h-full w-full min-h-[300px]"
        ref={mapRef}
        scrollWheelZoom={true}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds leads={leadsWithCoords} />
        {leadsWithCoords.map((lead) => (
          <Marker
            key={lead.id}
            position={[lead.lat, lead.lng]}
            icon={createIcon(lead.status)}
            eventHandlers={{
              click: () => onMarkerClick?.(lead.id),
            }}
          >
            <Popup>
              <div className="min-w-[180px]">
                <p className="font-semibold text-sm mb-1">{lead.name}</p>
                {lead.category && (
                  <p className="text-xs text-gray-500">{lead.category}</p>
                )}
                {lead.city && (
                  <p className="text-xs text-gray-500">{lead.city}</p>
                )}
                {lead.email && (
                  <p className="text-xs mt-1">
                    <a href={`mailto:${lead.email}`} className="text-blue-600 hover:underline">
                      {lead.email}
                    </a>
                  </p>
                )}
                {lead.phone && (
                  <p className="text-xs text-gray-600">{lead.phone}</p>
                )}
                <a
                  href={`/leads/${lead.id}`}
                  className="text-xs text-blue-600 hover:underline mt-2 inline-block"
                >
                  View details
                </a>
              </div>
            </Popup>
          </Marker>
        ))}
      </MapContainer>
    </div>
  );
}

export type HopKind = "private" | "cgnat" | "public" | "unknown";

export interface HopData {
  hop: number;
  ip: string | null;
  rtts: (number | null)[];
  avg_rtt: number | null;
  timeout: boolean;
}

export interface GeoData {
  hop: number;
  ip: string;
  kind: HopKind;
  country: string | null;
  country_code: string | null;
  city: string | null;
  lat: number | null;
  lon: number | null;
  isp: string | null;
  org: string | null;
  asn: string | null;
  as_name: string | null;
  reverse: string | null;
  source: string;
  /** True when lat/lon were assumed from the trace origin (e.g. hop 1's own
   * router), not measured -- kind still reflects the IP's real class. */
  inferred: boolean;
}

export interface OriginData {
  ip: string;
  kind: HopKind;
  country: string | null;
  country_code: string | null;
  city: string | null;
  lat: number | null;
  lon: number | null;
  isp: string | null;
  org: string | null;
  asn: string | null;
  as_name: string | null;
  reverse: string | null;
  source: string;
}

export interface HopRecord extends HopData {
  geo?: GeoData;
}

export interface DoneInfo {
  hop_count: number;
}

export interface ErrorInfo {
  message: string;
}

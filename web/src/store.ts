import { IcechunkStore } from "@carbonplan/icechunk-js";
import * as zarr from "zarrita";
import { VARIABLE_SOURCE } from "./config";

export interface StoreData {
  latitude: Float64Array;
  longitude: Float64Array;
  qcPass: Int8Array | null;
  // Pick-independent QC AND (masks surface-side metrics). Null on stores that
  // predate the split-QC schema (2026-07); fall back to qcPass.
  qcSurfacePass: Int8Array | null;
  // Bed-pick availability flags (null on older stores). Censored traces =
  // qcSurfacePass & bedPickAttempted & !bedPickAvailable.
  bedPickAvailable: Int8Array | null;
  bedPickAttempted: Int8Array | null;
  frameId: string[] | null;
  // Collection name (e.g. "2018_Greenland_P3") per trace, parallel to frameId.
  // Null when the store predates frame_collections backfill.
  frameCollection: string[] | null;
  variables: Record<string, Float64Array>;
  numTraces: number;
}

// QC mask for surface-side variables: pick-independent where available,
// falling back to the full mask on older stores.
export function surfaceQcMask(data: StoreData): Int8Array | null {
  return data.qcSurfacePass ?? data.qcPass;
}

// Store carries the flags needed to identify censored (no-bed-detected) traces.
export function hasCensoredInfo(data: StoreData): boolean {
  return !!(data.qcSurfacePass && data.bedPickAvailable && data.bedPickAttempted);
}

// Bed picking was attempted here, the trace passes pick-independent QC, but no
// bed was found — usually because bed SNR was too low.
export function isCensored(data: StoreData, i: number): boolean {
  return (
    !!data.qcSurfacePass?.[i] &&
    !!data.bedPickAttempted?.[i] &&
    data.bedPickAvailable?.[i] === 0
  );
}

export async function openStore(storeUrl: string, snapshotId?: string): Promise<IcechunkStore> {
  const opts = snapshotId ? { snapshot: snapshotId } : { branch: "main" };
  return IcechunkStore.open(storeUrl, opts);
}

async function loadArray(
  store: IcechunkStore,
  name: string
): Promise<zarr.Chunk<zarr.DataType>> {
  const root = zarr.root(store);
  const arr = await zarr.open(root.resolve(`/${name}`), { kind: "array" });
  return zarr.get(arr);
}

function toFloat64Array(chunk: zarr.Chunk<zarr.DataType>): Float64Array {
  const data = chunk.data;
  if (data instanceof Float64Array) return data;
  if (data instanceof Float32Array) return new Float64Array(data);
  if (ArrayBuffer.isView(data))
    return new Float64Array(data.buffer, data.byteOffset, data.byteLength / 8);
  return new Float64Array(data as unknown as ArrayLike<number>);
}

function toInt8Array(chunk: zarr.Chunk<zarr.DataType>): Int8Array {
  const data = chunk.data;
  if (data instanceof Int8Array) return data;
  if (ArrayBuffer.isView(data))
    return new Int8Array(data.buffer, data.byteOffset, data.byteLength);
  return new Int8Array(data as unknown as ArrayLike<number>);
}

// Load frame IDs (and per-trace collection if available) via frame_index
// (uint16 per trace) plus the frame_names / frame_collections group
// attributes. The native frame_id array uses zarr-python v3's numpy.str_
// dtype which zarrita cannot parse.
async function loadFrameInfo(
  store: IcechunkStore
): Promise<{ frameId: string[] | null; frameCollection: string[] | null }> {
  const rootGrp = await zarr.open(zarr.root(store), { kind: "group" });
  const attrs = rootGrp.attrs as Record<string, unknown>;
  const frameNames = attrs["frame_names"] as string[] | undefined;
  const frameCollections = attrs["frame_collections"] as string[] | undefined;
  if (!frameNames?.length) return { frameId: null, frameCollection: null };

  const idxArr = await zarr.open(
    zarr.root(store).resolve("/frame_index"),
    { kind: "array" }
  );
  const chunk = await zarr.get(idxArr);
  const indices = chunk.data as Uint16Array;

  const frameId = Array.from(indices, (i) => frameNames[i] ?? "unknown");
  const frameCollection =
    frameCollections && frameCollections.length === frameNames.length
      ? Array.from(indices, (i) => frameCollections[i] ?? "")
      : null;
  return { frameId, frameCollection };
}

export async function loadEssentials(store: IcechunkStore): Promise<StoreData> {
  const [latChunk, lonChunk] = await Promise.all([
    loadArray(store, "latitude"),
    loadArray(store, "longitude"),
  ]);

  const latitude = toFloat64Array(latChunk);
  const longitude = toFloat64Array(lonChunk);

  // Optional flag arrays — absent on stores predating the relevant schema.
  const loadFlag = async (name: string): Promise<Int8Array | null> => {
    try {
      return toInt8Array(await loadArray(store, name));
    } catch {
      return null;
    }
  };
  const [qcPass, qcSurfacePass, bedPickAvailable, bedPickAttempted] =
    await Promise.all([
      loadFlag("qc_pass"),
      loadFlag("qc_surface_pass"),
      loadFlag("bed_pick_available"),
      loadFlag("bed_pick_attempted"),
    ]);

  let frameId: string[] | null = null;
  let frameCollection: string[] | null = null;
  try {
    ({ frameId, frameCollection } = await loadFrameInfo(store));
  } catch (err) {
    console.warn("frame info not loaded:", err);
  }

  return {
    latitude,
    longitude,
    qcPass,
    qcSurfacePass,
    bedPickAvailable,
    bedPickAttempted,
    frameId,
    frameCollection,
    variables: {},
    numTraces: latitude.length,
  };
}

export async function loadVariables(
  store: IcechunkStore,
  data: StoreData,
  names: string[]
): Promise<void> {
  const missing = names.filter((n) => !(n in data.variables));
  if (missing.length === 0) return;

  const results = await Promise.all(
    missing.map(async (name) => {
      const source = VARIABLE_SOURCE[name] ?? name;
      try {
        const chunk = await loadArray(store, source);
        return [name, toFloat64Array(chunk)] as const;
      } catch {
        return null;
      }
    })
  );

  for (const entry of results) {
    if (entry) data.variables[entry[0]] = entry[1];
  }
}

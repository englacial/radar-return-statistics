export type Hemisphere = "antarctic" | "arctic";

export interface StoreConfig {
  label: string;
  url: string;
  hemisphere: Hemisphere;
}

export const STORES: StoreConfig[] = [
  {
    label: "Amundsen Sea Embayment",
    url: "https://opr-radar-metrics.s3.us-west-2.amazonaws.com/icechunk/ase/",
    hemisphere: "antarctic",
  },
  {
    label: "UTIG",
    url: "https://opr-radar-metrics.s3.us-west-2.amazonaws.com/icechunk/utig/",
    hemisphere: "antarctic",
  },
  {
    label: "Greenland",
    url: "https://opr-radar-metrics.s3.us-west-2.amazonaws.com/icechunk/greenland/",
    hemisphere: "arctic",
  },
];

// Display variable name -> zarr array name in the store. Omitted keys default
// to a 1:1 mapping. Add an entry here when the display name differs from the
// stored array (e.g. RSSNR is stored as `required_surface_snr_dB`).
export const VARIABLE_SOURCE: Record<string, string> = {
  rssnr: "required_surface_snr_dB",
};

// Raw zarr arrays each display variable depends on. Defaults to the source
// array (or the variable name itself).
export const VARIABLE_DEPS: Record<string, string[]> = {};

export const VARIABLES: Record<
  string,
  { label: string; cmap: string; unit: string }
> = {
  rssnr: {
    label: "Required Surface SNR",
    cmap: "viridis",
    unit: "dB",
  },
  surface_elevation: {
    label: "Surface Elevation",
    cmap: "terrain",
    unit: "m WGS84",
  },
  bed_elevation: {
    label: "Bed Elevation",
    cmap: "terrain",
    unit: "m WGS84",
  },
  surface_power_dB: {
    label: "Surface Power",
    cmap: "viridis",
    unit: "dB",
  },
  bed_power_dB: {
    label: "Bed Power",
    cmap: "viridis",
    unit: "dB",
  },
  surface_twtt: {
    label: "Surface TWTT",
    cmap: "viridis",
    unit: "s",
  },
  bed_twtt: {
    label: "Bed TWTT",
    cmap: "viridis",
    unit: "s",
  },
};

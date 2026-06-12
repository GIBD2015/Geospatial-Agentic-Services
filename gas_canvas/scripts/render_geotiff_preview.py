import base64
import io
import json
import math
import sys

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds


MAX_PREVIEW_DIMENSION = 1600


def finite_or_none(value):
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def stretch_band(band, valid_mask):
    output = np.zeros(band.shape, dtype=np.uint8)
    values = band[valid_mask]
    values = values[np.isfinite(values)]
    if values.size == 0:
        return output

    low, high = np.nanpercentile(values, [2, 98])
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low = float(np.nanmin(values))
        high = float(np.nanmax(values))

    if high <= low:
        output[valid_mask] = 128
        return output

    stretched = (band.astype("float32") - low) / (high - low)
    stretched = np.clip(stretched * 255, 0, 255)
    output[valid_mask] = stretched[valid_mask].astype(np.uint8)
    return output


def render_preview(path):
    with rasterio.open(path) as src:
        scale = min(1.0, MAX_PREVIEW_DIMENSION / max(src.width, src.height))
        out_width = max(1, int(src.width * scale))
        out_height = max(1, int(src.height * scale))
        indexes = list(range(1, min(src.count, 3) + 1))

        data = src.read(
            indexes,
            out_shape=(len(indexes), out_height, out_width),
            masked=True,
            resampling=Resampling.bilinear,
        )

        if data.ndim == 2:
            data = data[np.newaxis, :, :]

        data_float = data.astype("float32")
        valid_mask = ~np.ma.getmaskarray(data_float).all(axis=0)
        filled = np.asarray(data_float.filled(np.nan), dtype="float32")
        if src.nodata is not None and math.isfinite(float(src.nodata)):
            valid_mask &= np.all(filled != float(src.nodata), axis=0)
        if filled.shape[0] >= 3:
            rgb = np.dstack([stretch_band(filled[i], valid_mask) for i in range(3)])
        else:
            gray = stretch_band(filled[0], valid_mask)
            rgb = np.dstack([gray, gray, gray])

        alpha = np.where(valid_mask, 220, 0).astype(np.uint8)
        rgba = np.dstack([rgb, alpha])

        image = Image.fromarray(rgba, mode="RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        image_base64 = base64.b64encode(buffer.getvalue()).decode("ascii")

        bounds = src.bounds
        crs = src.crs
        if crs:
            west, south, east, north = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
        else:
            west, south, east, north = bounds.left, bounds.bottom, bounds.right, bounds.top

        valid_values = filled[:, valid_mask]
        stats = {
            "min": finite_or_none(np.nanmin(valid_values)) if valid_values.size else None,
            "max": finite_or_none(np.nanmax(valid_values)) if valid_values.size else None,
        }

        return {
            "image_data_url": f"data:image/png;base64,{image_base64}",
            "bounds": {
                "west": finite_or_none(west),
                "south": finite_or_none(south),
                "east": finite_or_none(east),
                "north": finite_or_none(north),
            },
            "width": src.width,
            "height": src.height,
            "preview_width": out_width,
            "preview_height": out_height,
            "crs": str(crs) if crs else None,
            "band_count": src.count,
            "stats": stats,
        }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: render_geotiff_preview.py <geotiff_path>")

    print(json.dumps(render_preview(sys.argv[1])))


if __name__ == "__main__":
    main()

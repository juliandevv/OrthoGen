r"""downsample_ms_ortho.py — low-pass the MS bands of a combined ortho to their native GSD.

The combined RGB+MS ortho is written at the RGB grid (~1.3 cm/px), so the four MS bands
are 2x OVERSAMPLED vs their true optical resolution (~2.6 cm/px). This tool restores each
MS band to native resolution: average-downsample to native GSD, then resample back up to
the ortho grid so it stays pixel-aligned with the RGB bands. RGB + alpha bands pass through
untouched. Output is a single multi-band raster on the same grid/CRS.

Only GDAL is used (Translate/BuildVRT), which streams block-by-block, so memory stays low
regardless of ortho size, and no whole-raster array is ever held. Output is a TILED,
DEFLATE-compressed BigTIFF.

RUN IN QGIS (Python Console):
    Edit the CONFIG block below, then: exec(open(r"...\tools\downsample_ms_ortho.py").read())

RUN FROM A SHELL (QGIS/OSGeo4W python, or the ODM venv python):
    python tools\downsample_ms_ortho.py INPUT.tif OUTPUT.tif [--ms 1,2,3,4] [--native-cm 2.6]
"""
import os
import sys
import tempfile
from osgeo import gdal

# NB: no gdal.UseExceptions() and no array reads — GDAL's gdal_array clashes with the ODM
# venv's numpy build. Failures are caught via None return values instead.

# ----------------------------------------------------------------- CONFIG (edit for QGIS)
CONFIG = {
    "input":       r"C:\Users\devrj\OrthoGen_work\combined_prewarp_60\odm_orthophoto\odm_orthophoto.tif",
    "output":      r"C:\Users\devrj\OrthoGen_work\combined_prewarp_60\odm_orthophoto\odm_orthophoto_msnative.tif",
    "ms_bands":    [1, 2, 3, 4],   # 1-based band indices to downsample (Red, Green, NIR, RedEdge)
    "native_cm":   2.6,            # MS native GSD; factor = round(native_cm / ortho_cm)
    "factor":      None,           # override the auto factor (int) if you prefer; else None
    "down_resample": "average",    # antialiased downsample to native
    "up_resample":   "bilinear",   # smooth resample back to the ortho grid
}
# ----------------------------------------------------------------------------------------

CREATE_OPTS = ["TILED=YES", "BLOCKXSIZE=512", "BLOCKYSIZE=512",
               "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=YES", "NUM_THREADS=ALL_CPUS"]


def run(input_path, output_path, ms_bands, native_cm=2.6, factor=None,
        down_resample="average", up_resample="bilinear"):
    src = gdal.Open(input_path)
    if src is None:
        raise IOError(f"cannot open {input_path}")
    W, H, nb = src.RasterXSize, src.RasterYSize, src.RasterCount
    gt = src.GetGeoTransform()
    ortho_cm = abs(gt[1]) * 100.0
    if factor is None:
        factor = max(1, round(native_cm / ortho_cm)) if ortho_cm > 0 else 1
    ms_bands = [b for b in ms_bands if 1 <= b <= nb]
    print(f"input {W}x{H} px, {nb} bands, ortho GSD {ortho_cm:.2f} cm/px")
    print(f"downsample factor {factor}x (-> ~{ortho_cm*factor:.2f} cm/px) on bands {ms_bands}; "
          f"others pass through")
    if factor <= 1:
        print("factor <= 1: nothing to do (ortho already at/above native res).")
        return

    tmpdir = tempfile.mkdtemp(prefix="msnative_")
    dw, dh = max(1, W // factor), max(1, H // factor)
    srcs = []
    try:
        for b in range(1, nb + 1):
            if b in ms_bands:
                native = os.path.join(tmpdir, f"b{b}_native.tif")
                restored = os.path.join(tmpdir, f"b{b}_restored.tif")
                # 1) average-downsample this band to native GSD
                gdal.Translate(native, src, bandList=[b], width=dw, height=dh,
                               resampleAlg=down_resample,
                               creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=YES"])
                # 2) resample back up to the ortho grid (same extent -> same geotransform)
                gdal.Translate(restored, native, width=W, height=H, resampleAlg=up_resample,
                               creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=YES"])
                srcs.append(restored)
                print(f"  band {b}: MS -> native {dw}x{dh} -> restored {W}x{H}")
            else:
                # passthrough as a lightweight VRT (no data copied)
                vrt = os.path.join(tmpdir, f"b{b}_pass.vrt")
                gdal.Translate(vrt, src, bandList=[b], format="VRT")
                srcs.append(vrt)
                print(f"  band {b}: passthrough")

        # stack the single-band sources back into band order, then write the final tiled GTiff
        stack_vrt = os.path.join(tmpdir, "stack.vrt")
        if gdal.BuildVRT(stack_vrt, srcs, options=gdal.BuildVRTOptions(separate=True)) is None:
            raise RuntimeError("BuildVRT (band stack) failed")
        print("writing output (tiled, streamed)...")
        if gdal.Translate(output_path, stack_vrt, creationOptions=CREATE_OPTS) is None:
            raise RuntimeError("final Translate failed")

        # restore per-band descriptions, nodata, and the alpha color interpretation
        out = gdal.Open(output_path, gdal.GA_Update)
        for b in range(1, nb + 1):
            sb, ob = src.GetRasterBand(b), out.GetRasterBand(b)
            if sb.GetDescription():
                ob.SetDescription(sb.GetDescription())
            ob.SetColorInterpretation(sb.GetColorInterpretation())
            nd = sb.GetNoDataValue()
            if nd is not None:
                ob.SetNoDataValue(nd)
        out.FlushCache()
        out = None
        print(f"done -> {output_path}")
    finally:
        src = None
        for f in os.listdir(tmpdir):
            try:
                os.remove(os.path.join(tmpdir, f))
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def _parse_cli(argv):
    cfg = dict(CONFIG)
    if len(argv) >= 2:
        cfg["input"], cfg["output"] = argv[0], argv[1]
    rest = argv[2:]
    i = 0
    while i < len(rest):
        if rest[i] == "--ms":
            cfg["ms_bands"] = [int(x) for x in rest[i + 1].split(",")]; i += 2
        elif rest[i] == "--native-cm":
            cfg["native_cm"] = float(rest[i + 1]); i += 2
        elif rest[i] == "--factor":
            cfg["factor"] = int(rest[i + 1]); i += 2
        else:
            i += 1
    return cfg


if __name__ == "__main__":
    cfg = _parse_cli(sys.argv[1:])
    run(cfg["input"], cfg["output"], cfg["ms_bands"], native_cm=cfg["native_cm"],
        factor=cfg["factor"], down_resample=cfg["down_resample"], up_resample=cfg["up_resample"])
else:
    # exec()'d in the QGIS Python Console -> run with the CONFIG block above
    run(CONFIG["input"], CONFIG["output"], CONFIG["ms_bands"], native_cm=CONFIG["native_cm"],
        factor=CONFIG["factor"], down_resample=CONFIG["down_resample"], up_resample=CONFIG["up_resample"])

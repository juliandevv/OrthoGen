"""Batch ECC band pre-warp (Architecture B, step 2).

Place each DJI M3M multispectral band into the RGB image frame per capture, so the
four MS bands become a co-registered RGB+MS stack that ODM can orthorectify with
``--skip-band-alignment`` (no engine band alignment — we own the placement).

Method (per capture):
  1. Read + undistort the 20 MP RGB ONCE, reuse across all four MS bands.
  2. Per band: warm-start from the factory ``DewarpHMatrix`` and refine with
     homography ECC (``rig.refine_alignment``) — ~1 px residual, vs ODM's ~40 cm.
  3. Warp the RAW MS (16-bit DN preserved) into the RGB (undistorted) pixel frame and
     write a single-band uint16 image at RGB resolution.

The output frame is the *undistorted* RGB grid (RGB and MS share one pixel grid ->
perfectly co-registered). The choice of what geometry to hand ODM for the skip run
(undistorted primary + zero-distortion camera, vs. re-distorted to the raw RGB grid)
is resolved when wiring ``combined-sfm --skip-band-alignment``.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

import numpy as np
import cv2

from . import rig, config
from .metadata import _extract_xmp


def _redistort_map(refined_H, rgb_intr, ms_intr, rw, rh, step: int = 8):
    """Composite remap from the RAW (distorted) RGB output grid back to RAW MS pixels:
    raw-RGB -> undistort(RGB) -> refined_H^-1 -> undist-MS -> distort(MS) -> raw-MS.
    Computed on a coarse grid (the field is smooth) and upsampled to full RGB res.
    ``refined_H`` maps undist-MS -> undist-RGB.
    """
    xs = np.arange(0, rw, step, dtype=np.float64)
    ys = np.arange(0, rh, step, dtype=np.float64)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], 1).reshape(-1, 1, 2)
    # raw-RGB -> undistorted-RGB
    U = cv2.undistortPoints(pts, rgb_intr.K, rgb_intr.dist, P=rgb_intr.K).reshape(-1, 2)
    # undist-RGB -> undist-MS  (inverse of the refined MS->RGB homography)
    Hinv = np.linalg.inv(refined_H)
    Uh = np.concatenate([U, np.ones((len(U), 1))], 1)
    Vh = (Hinv @ Uh.T).T
    V = Vh[:, :2] / Vh[:, 2:3]
    # undist-MS -> normalized -> distort(MS) -> raw-MS pixels
    xn = (V[:, 0] - ms_intr.K[0, 2]) / ms_intr.K[0, 0]
    yn = (V[:, 1] - ms_intr.K[1, 2]) / ms_intr.K[1, 1]
    objp = np.stack([xn, yn, np.ones_like(xn)], 1).reshape(-1, 1, 3)
    img, _ = cv2.projectPoints(objp, np.zeros(3), np.zeros(3), ms_intr.K, ms_intr.dist)
    img = img.reshape(gx.shape[0], gx.shape[1], 2).astype(np.float32)
    mapx = cv2.resize(img[:, :, 0], (rw, rh), interpolation=cv2.INTER_LINEAR)
    mapy = cv2.resize(img[:, :, 1], (rw, rh), interpolation=cv2.INTER_LINEAR)
    return mapx, mapy


def warp_bands(capture, work_px: int = 900, frame: str = "raw"):
    """Yield ``(band, warped_uint16, qa)`` for each MS band of a capture, placed into
    the RGB frame. The 20 MP RGB is read + undistorted ONCE (for ECC) and reused.

    ``frame="raw"`` (default) writes into the RAW/distorted RGB grid so ODM's own
    undistort is correct for the carrier (matches how it treats the RGB) — required for
    the --skip-band-alignment ortho. ``frame="undist"`` writes the undistorted frame
    (a directly co-registered RGB+MS stack; used for standalone QA/benchmarks).
    """
    rgb = capture.bands.get("RGB")
    if rgb is None:
        return
    rgb_gray = rig._read_gray(rgb.path)
    rh, rw = rgb_gray.shape
    rgb_intr = rig.parse_dewarp(rgb, (rw, rh))
    rgbu = rig.undistort(rgb_gray, rgb_intr)
    for b in config.MS_BANDS:
        ms = capture.bands.get(b)
        if ms is None:
            continue
        refined_H, qa = rig.refine_alignment(rgb, ms, work_px=work_px, rgbu=rgbu)
        ms_raw = cv2.imread(ms.path, cv2.IMREAD_UNCHANGED).astype(np.float32)
        ms_intr = rig.parse_dewarp(ms, (ms_raw.shape[1], ms_raw.shape[0]))
        if frame == "raw":
            mapx, mapy = _redistort_map(refined_H, rgb_intr, ms_intr, rw, rh)
            warped = cv2.remap(ms_raw, mapx, mapy, cv2.INTER_LINEAR, borderValue=0)
        else:
            ms_u = rig.undistort(ms_raw, ms_intr)
            warped = cv2.warpPerspective(ms_u, refined_H, (rw, rh), flags=cv2.INTER_LINEAR)
        yield b, np.clip(warped, 0, 65535).astype(np.uint16), qa


def prewarp_capture(capture, out_dir: Optional[Path], work_px: int = 900,
                    write: bool = True) -> dict:
    """Pre-warp all MS bands of one capture into the RGB frame. Returns timings + QA.

    ``out_dir=None`` or ``write=False`` runs the full compute but skips file writes
    (isolates compute cost from disk I/O for benchmarking).
    """
    rgb = capture.bands.get("RGB")
    if rgb is None:
        return {"uuid": capture.uuid, "error": "no RGB band"}

    t0 = time.time()
    bands = []
    for b, warped, qa in warp_bands(capture, work_px=work_px):
        tb = time.time()
        out_path = None
        if write and out_dir is not None:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / capture.bands[b].filename
            cv2.imwrite(str(out_path), warped)
        bands.append({
            "band": b,
            "sec": round(time.time() - tb, 3),
            "refined": qa.get("refined"),
            "ecc_cc": qa.get("ecc_correlation"),
            "coverage": round(float((warped > 0).mean()), 3),
            "out": str(out_path) if out_path else None,
        })
    rh, rw = None, None
    return {
        "uuid": capture.uuid,
        "rgb_file": rgb.filename,
        "bands": bands,
        "total_sec": round(time.time() - t0, 3),
    }


def band_xmp(rgb_path: str, band: str) -> str:
    """The RGB image's XMP packet (shared CaptureUUID + camera) with Camera:BandName
    set to ``band`` — so a warped MS carrier groups with its RGB and reads as that band.
    """
    xmp = _extract_xmp(Path(rgb_path))
    if "<Camera:BandName>" in xmp:
        return re.sub(r"<Camera:BandName>[^<]*</Camera:BandName>",
                      f"<Camera:BandName>{band}</Camera:BandName>", xmp)
    ci = xmp.rfind("</rdf:Description>")
    if ci < 0:
        raise RuntimeError(f"no </rdf:Description> in RGB XMP of {rgb_path}")
    return xmp[:ci] + f"\n   <Camera:BandName>{band}</Camera:BandName>\n  " + xmp[ci:]


def write_carrier_tif(array_u16: np.ndarray, out_path: Path, rgb_path: str, band: str):
    """Write a warped MS band as a DEFLATE-compressed 16-bit GeoTIFF carrying the RGB's
    XMP (with this band's name) so ODM groups it with the RGB for --skip-band-alignment.
    """
    import rasterio
    from osgeo import gdal  # XMP metadata only (SetMetadata does not need gdal_array)
    out_path = Path(out_path)
    h, w = array_u16.shape
    with rasterio.open(out_path, "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="uint16", compress="deflate", predictor=2, tiled=True) as dst:
        dst.write(array_u16, 1)
    ds = gdal.Open(str(out_path), gdal.GA_Update)
    ds.SetMetadata([band_xmp(rgb_path, band)], "xml:XMP")
    ds.FlushCache()
    ds = None


def _prewarp_one(args) -> int:
    """Worker: stage one capture — disguise the RGB (Pan) + write the four MS carriers.
    Module-level (picklable) so it runs under a multiprocessing Pool on Windows spawn.
    """
    from . import disguise
    cap, images_dir, work_px = args
    images_dir = Path(images_dir)
    rgb = cap.bands.get("RGB")
    if rgb is None:
        return 0
    n = 0
    dst = images_dir / Path(rgb.path).name
    if not dst.exists():
        shutil.copy2(rgb.path, dst)               # copy: we edit its XMP
        disguise.inject_bandname(str(dst))        # RGB -> Pan primary
    n += 1
    for band, warped, _qa in warp_bands(cap, work_px=work_px):
        out_path = images_dir / cap.bands[band].filename
        if not out_path.exists():
            write_carrier_tif(warped, out_path, rgb.path, band)
        n += 1
    return n


def prewarp_stage(captures, images_dir, work_px: int = 900, workers: Optional[int] = None) -> int:
    """Stage disguised-RGB + prewarped-MS carriers for all captures, in parallel.

    ``workers`` defaults to min(8, cpu_count); pass 1 to force serial. Parallelism is
    per capture; the per-refine cost is dominated by image I/O + the 20 MP RGB undistort.
    """
    images_dir = Path(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)
    args = [(c, str(images_dir), work_px) for c in captures]
    if workers is None:
        workers = min(8, os.cpu_count() or 1)
    if workers <= 1 or len(args) <= 1:
        return sum(_prewarp_one(a) for a in args)
    with Pool(min(workers, len(args))) as pool:
        return sum(pool.map(_prewarp_one, args))

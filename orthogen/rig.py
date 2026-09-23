"""Stage 1 — MS band placement into the RGB image frame (DJI M3M).

The RGB and MS lenses are co-located, so the RGB<->MS relationship is a fixed 2D
homography, published by DJI as ``drone-dji:DewarpHMatrix`` in each MS file (it maps
MS pixels into the RGB frame). Per-camera lens distortion is ``drone-dji:DewarpData``
(``DewarpFlag=0`` => images are NOT pre-undistorted, so we apply it).

This module: parse those, undistort a band, and warp an MS band into the RGB frame.
``validate_capture`` measures the residual RGB<->MS misalignment with ECC to confirm
the factory homography (and the correct distortion-application order).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
import cv2

from .metadata import BandImage


@dataclass
class Intrinsics:
    K: np.ndarray            # 3x3 camera matrix (px)
    dist: np.ndarray         # (k1,k2,p1,p2,k3)
    size: tuple              # (w, h)


def parse_dewarp(band: BandImage, size: tuple) -> Intrinsics:
    """DewarpData = 'date;fx,fy,cx,cy,k1,k2,p1,p2,k3'. cx,cy are offsets from the
    image centre, so principal point = (w/2 + cx, h/2 + cy)."""
    if not band.dewarp_data:
        raise ValueError(f"{band.filename}: no DewarpData")
    payload = band.dewarp_data.split(";", 1)[-1]
    v = [float(x) for x in payload.replace(";", ",").split(",") if x.strip()]
    fx, fy, cx, cy, k1, k2, p1, p2, k3 = v[:9]
    w, h = size
    K = np.array([[fx, 0, w / 2 + cx],
                  [0, fy, h / 2 + cy],
                  [0,  0, 1]], dtype=np.float64)
    dist = np.array([k1, k2, p1, p2, k3], dtype=np.float64)
    return Intrinsics(K=K, dist=dist, size=size)


def ms_to_rgb_H(ms_band: BandImage) -> np.ndarray:
    """DewarpHMatrix (9 floats, row-major): maps MS pixel coords -> RGB frame."""
    h = ms_band.dewarp_hmatrix
    if len(h) != 9:
        raise ValueError(f"{ms_band.filename}: DewarpHMatrix has {len(h)} values")
    return np.array(h, dtype=np.float64).reshape(3, 3)


def _read_gray(path: str) -> np.ndarray:
    """Read RGB (jpg) or single-band MS (tif) as float32 grayscale, 0..1-ish scaled."""
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise IOError(f"cannot read {path}")
    if img.ndim == 3:
        img = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
    img = img.astype(np.float32)
    lo, hi = np.percentile(img, 1), np.percentile(img, 99)
    return np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)


def undistort(img: np.ndarray, intr: Intrinsics) -> np.ndarray:
    return cv2.undistort(img, intr.K, intr.dist)


def warp_ms_to_rgb(ms_img: np.ndarray, H: np.ndarray, rgb_size: tuple) -> np.ndarray:
    """Warp an MS image into the RGB pixel frame (rgb_size = (w, h))."""
    return cv2.warpPerspective(ms_img, H, rgb_size, flags=cv2.INTER_LINEAR)


def _grad(a: np.ndarray) -> np.ndarray:
    """Gradient magnitude — a band-invariant structure image for cross-band matching."""
    gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def refine_alignment(rgb: BandImage, ms: BandImage,
                     motion: int = cv2.MOTION_HOMOGRAPHY,
                     work_px: int = 1600,
                     rgbu: Optional[np.ndarray] = None) -> tuple:
    """Refine the factory DewarpHMatrix with ECC, mirroring ODM's band alignment
    (gradient-of-gaussian images, cross-band robust). The factory H handles the 1.7x
    RGB/MS scale + gross offset (putting us in ECC's basin); ECC removes the residual.

    ``rgbu`` may be a pre-undistorted RGB grayscale image (from ``undistort``); when
    given, the 20 MP RGB read+undistort is skipped (batch pre-warp undistorts the RGB
    once per capture and reuses it across all four MS bands).

    Returns (refined_H, qa) where refined_H maps undistorted-MS pixels -> RGB frame.
    """
    ms_img = _read_gray(ms.path)
    H0 = ms_to_rgb_H(ms)
    msu = undistort(ms_img, parse_dewarp(ms, (ms_img.shape[1], ms_img.shape[0])))
    if rgbu is None:
        rgb_img = _read_gray(rgb.path)
        rh, rw = rgb_img.shape
        rgbu = undistort(rgb_img, parse_dewarp(rgb, (rw, rh)))
    else:
        rh, rw = rgbu.shape
    ms0 = warp_ms_to_rgb(msu, H0, (rw, rh))
    mask = ms0 > 0

    ys, xs = np.where(mask)
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    A = rgbu[y0:y1, x0:x1]
    B = ms0[y0:y1, x0:x1]
    m = mask[y0:y1, x0:x1].astype(np.uint8)
    s = work_px / max(A.shape)
    As = cv2.resize(A, None, fx=s, fy=s)
    Bs = cv2.resize(B, None, fx=s, fy=s)
    ms_s = (cv2.resize(m, None, fx=s, fy=s) > 0).astype(np.uint8)
    ga = _grad(cv2.GaussianBlur(As, (0, 0), 1.5))
    gb = _grad(cv2.GaussianBlur(Bs, (0, 0), 1.5))

    W = np.eye(3, dtype=np.float32) if motion == cv2.MOTION_HOMOGRAPHY else np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 1000, 1e-7)
    try:
        # template=MS (moving), input=RGB (reference) => W maps MS-frame -> RGB-frame
        cc, W = cv2.findTransformECC(gb, ga, W, motion, crit, inputMask=ms_s, gaussFiltSize=9)
    except cv2.error as e:
        return H0, {"refined": False, "reason": str(e)[:80]}
    if W.shape == (2, 3):
        W = np.vstack([W, [0, 0, 1]]).astype(np.float32)
    # lift the downsampled-crop warp back to full RGB pixel coords: W_full = S^-1 W S
    S = np.array([[s, 0, -s * x0], [0, s, -s * y0], [0, 0, 1]], np.float64)
    Sinv = np.array([[1 / s, 0, x0], [0, 1 / s, y0], [0, 0, 1]], np.float64)
    W_full = Sinv @ W @ S
    refined_H = W_full @ H0
    return refined_H, {"refined": True, "ecc_correlation": round(float(cc), 4)}


def validate_capture(rgb: BandImage, ms: BandImage,
                     undistort_first: bool = True, tile: int = 384,
                     H_override: Optional[np.ndarray] = None) -> dict:
    """Warp MS into the RGB frame and measure the residual RGB<->MS misalignment.

    Uses tiled sub-pixel phase correlation on gradient images (robust to the
    radiometric difference between RGB and a narrow MS band). Pass ``H_override``
    (e.g. a refined homography) to score it against the factory DewarpHMatrix.
    Returns the footprint bbox (to sanity-check the warp geometry) and the residual.
    """
    rgb_img = _read_gray(rgb.path)
    ms_img = _read_gray(ms.path)
    rh, rw = rgb_img.shape
    H = H_override if H_override is not None else ms_to_rgb_H(ms)

    if undistort_first:
        ms_img = undistort(ms_img, parse_dewarp(ms, (ms_img.shape[1], ms_img.shape[0])))
        rgb_ref = undistort(rgb_img, parse_dewarp(rgb, (rw, rh)))
    else:
        rgb_ref = rgb_img
    ms_in_rgb = warp_ms_to_rgb(ms_img, H, (rw, rh))
    mask = ms_in_rgb > 0

    ys, xs = np.where(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    A, B = _grad(rgb_ref), _grad(ms_in_rgb)

    mags = []
    def prep(t):
        t = t - t.mean()
        return t * cv2.createHanningWindow((t.shape[1], t.shape[0]), cv2.CV_32F)
    for y in range(bbox[1], bbox[3] - tile, tile):
        for x in range(bbox[0], bbox[2] - tile, tile):
            if not mask[y:y+tile, x:x+tile].all():
                continue
            a, b = A[y:y+tile, x:x+tile], B[y:y+tile, x:x+tile]
            if a.std() < 1e-4 or b.std() < 1e-4:
                continue
            (dx, dy), resp = cv2.phaseCorrelate(prep(a), prep(b))
            if resp > 0.03:
                mags.append((dx*dx + dy*dy) ** 0.5)
    mags = np.array(mags) if mags else np.array([np.nan])
    return {
        "capture": ms.filename,
        "undistort_first": undistort_first,
        "footprint_bbox_rgb_px": bbox,
        "tiles": int(np.sum(~np.isnan(mags))),
        "residual_median_px": round(float(np.nanmedian(mags)), 2),
        "residual_p90_px": round(float(np.nanpercentile(mags, 90)), 2),
    }

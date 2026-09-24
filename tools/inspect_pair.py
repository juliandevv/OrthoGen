r"""inspect_pair.py — visual inspection of RGB<->MS cross-band feature matching.

Pick a capture and MS band, then see how each detector finds features, how the
matcher + RANSAC homography perform, and how enhancements (CLAHE / gradient) change
things. Renders an annotated montage PNG and prints a stats table.

RGB is reduced to a single spectral channel before matching (M3M spectral overlap):
  Green<->RGB G, Red<->RGB R, RedEdge/NIR<->RGB R (blue is never used — no MS overlap).

Run via tools\inspect.bat (it uses the bundled ODM venv Python). --capture is the filename
SEQUENCE number (the _0188_ in DJI_..._0188_D.JPG), not a positional index; use --index for
position, --list to see all. NOTE: orthogen.bat is the OrthoGen CLI, not a script runner —
use tools\inspect.bat for this tool.

Examples (PowerShell, from the project root):
  .\tools\inspect.bat --list
  .\tools\inspect.bat --capture 0188 --band NIR --detector all
  .\tools\inspect.bat --band RedEdge --enhance all --detector SIFT
  .\tools\inspect.bat --index 30 --band Green --rgb-channel G

(or run inspect_pair.py directly with the venv Python:
   & "C:\WebODM\resources\app\apps\ODX\venv\Scripts\python.exe" tools\inspect_pair.py ...)
"""
import argparse
import os
import sys
import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orthogen.metadata import Dataset          # noqa: E402
from orthogen import rig                        # noqa: E402

BAND_TO_RGBCH = {"Green": "G", "Red": "R", "RedEdge": "R", "NIR": "R"}
ALL_DETECTORS = ["SIFT", "AKAZE", "ORB", "BRISK", "KAZE"]
BINARY = {"AKAZE", "ORB", "BRISK"}


# ----------------------------------------------------------------------------- image ops
def norm8(a):
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    return np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)


def enhance(u8, mode):
    if mode == "none":
        return u8
    if mode == "clahe":
        return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(u8)
    if mode == "grad":
        a = u8.astype(np.float32)
        gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
        return norm8(cv2.magnitude(gx, gy))
    raise ValueError(mode)


def make_detector(name, nfeatures):
    if name == "SIFT":
        return cv2.SIFT_create(nfeatures=nfeatures)
    if name == "AKAZE":
        return cv2.AKAZE_create()
    if name == "ORB":
        return cv2.ORB_create(nfeatures=nfeatures)
    if name == "BRISK":
        return cv2.BRISK_create()
    if name == "KAZE":
        return cv2.KAZE_create()
    raise ValueError(name)


def read_rgb_plane(path, channel):
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise IOError(f"cannot read {path}")
    b, g, r = [img[:, :, i].astype(np.float32) for i in range(3)]
    if channel == "R":
        a = r
    elif channel == "G":
        a = g
    elif channel == "B":
        a = b
    elif channel == "RG":
        a = (r + g) / 2
    else:  # gray (mixes in blue)
        a = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY).astype(np.float32)
    lo, hi = np.percentile(a, 1), np.percentile(a, 99)
    return np.clip((a - lo) / max(hi - lo, 1e-6), 0, 1)


# ----------------------------------------------------------------------------- matching
def _cap_kp(kp, desc, maxkp=20000):
    """Keep the strongest `maxkp` keypoints (BFMatcher caps train descriptors, and
    detectors like AKAZE/BRISK can emit >100k on canopy texture)."""
    if desc is None or len(kp) <= maxkp:
        return kp, desc
    order = np.argsort([-k.response for k in kp])[:maxkp]
    return [kp[i] for i in order], desc[order]


def match_fit(rgb_in_ms8, ms8, detector, binary, H0, ratio=0.8):
    """Detect + match + fit MS_undist->RGB homography (direct, no inverse ambiguity).
    Returns dict with kp counts, good/inliers, H, and drawable kp/matches."""
    ka, da = detector.detectAndCompute(rgb_in_ms8, None)
    kb, db = detector.detectAndCompute(ms8, None)
    ka, da = _cap_kp(ka, da)
    kb, db = _cap_kp(kb, db)
    out = {"kp_a": len(ka), "kp_b": len(kb), "good": 0, "inliers": 0,
           "H": None, "ka": ka, "kb": kb, "matches": [], "inlier_mask": None}
    if da is None or db is None or len(ka) < 4 or len(kb) < 4:
        return out
    bf = cv2.BFMatcher(cv2.NORM_HAMMING if binary else cv2.NORM_L2)
    knn = bf.knnMatch(da, db, k=2)
    good = [m for m, n in (p for p in knn if len(p) == 2) if m.distance < ratio * n.distance]
    out["good"] = len(good)
    out["matches"] = good
    if len(good) < 8:
        return out
    q = np.float32([ka[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    t = np.float32([kb[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    q_rgb = cv2.perspectiveTransform(q, H0)
    H, mask = cv2.findHomography(t, q_rgb, cv2.USAC_MAGSAC, 3.0)
    out["H"] = H
    out["inlier_mask"] = mask
    out["inliers"] = int(mask.sum()) if mask is not None else 0
    return out


def residual(rgbu_grad, msu, H, rgb_size, tile=384):
    rw, rh = rgb_size
    if H is None:
        return {"med": float("nan"), "p90": float("nan"), "tiles": 0}
    ms_in_rgb = cv2.warpPerspective(msu, H, (rw, rh), flags=cv2.INTER_LINEAR)
    mask = ms_in_rgb > 0
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return {"med": float("nan"), "p90": float("nan"), "tiles": 0}
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    B = rig._grad(ms_in_rgb)
    mags = []
    def prep(tt):
        tt = tt - tt.mean()
        return tt * cv2.createHanningWindow((tt.shape[1], tt.shape[0]), cv2.CV_32F)
    for y in range(y0, y1 - tile, tile):
        for x in range(x0, x1 - tile, tile):
            if not mask[y:y+tile, x:x+tile].all():
                continue
            a, b = rgbu_grad[y:y+tile, x:x+tile], B[y:y+tile, x:x+tile]
            if a.std() < 1e-4 or b.std() < 1e-4:
                continue
            (dx, dy), resp = cv2.phaseCorrelate(prep(a), prep(b))
            if resp > 0.03:
                mags.append((dx*dx + dy*dy) ** 0.5)
    mags = np.array(mags) if mags else np.array([np.nan])
    return {"med": round(float(np.nanmedian(mags)), 2),
            "p90": round(float(np.nanpercentile(mags, 90)), 2),
            "tiles": int(np.sum(~np.isnan(mags)))}


# ----------------------------------------------------------------------------- rendering
def _label(img, lines, org=(10, 24)):
    x, y = org
    for i, ln in enumerate(lines):
        cv2.putText(img, ln, (x, y + i*22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, ln, (x, y + i*22), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 255, 0), 1, cv2.LINE_AA)
    return img


def kp_panel(u8, kps, title, maxkp=1500):
    bgr = cv2.cvtColor(u8, cv2.COLOR_GRAY2BGR)
    ks = kps[:maxkp]
    bgr = cv2.drawKeypoints(bgr, ks, None, color=(0, 255, 0),
                            flags=cv2.DRAW_MATCHES_FLAGS_DRAW_RICH_KEYPOINTS)
    _label(bgr, [title, f"{len(kps)} kp"])
    return bgr


def match_panel(a8, b8, res, maxdraw=80):
    ka, kb, good, mask = res["ka"], res["kb"], res["matches"], res["inlier_mask"]
    if not good:
        canvas = np.hstack([cv2.cvtColor(a8, cv2.COLOR_GRAY2BGR),
                            cv2.cvtColor(b8, cv2.COLOR_GRAY2BGR)])
        _label(canvas, ["matches: none"])
        return canvas
    inl = mask.ravel().astype(bool) if mask is not None else np.ones(len(good), bool)
    idx = np.where(inl)[0]
    sel = idx[np.linspace(0, len(idx)-1, min(maxdraw, len(idx))).astype(int)] if len(idx) else []
    draw = [good[i] for i in sel]
    mm = [1] * len(draw)
    canvas = cv2.drawMatches(cv2.cvtColor(a8, cv2.COLOR_GRAY2BGR), ka,
                             cv2.cvtColor(b8, cv2.COLOR_GRAY2BGR), kb,
                             draw, None, matchColor=(0, 255, 0),
                             singlePointColor=(0, 0, 255),
                             flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS)
    _label(canvas, [f"inliers {res['inliers']}/{res['good']} drawn {len(draw)}"])
    return canvas


def overlay_panel(rgbu, msu, H, rgb_size, size=700):
    rw, rh = rgb_size
    if H is None:
        return np.zeros((size, size, 3), np.uint8)
    ms_in_rgb = cv2.warpPerspective(msu, H, (rw, rh), flags=cv2.INTER_LINEAR)
    cx, cy = rw//2 - size//2, rh//2 - size//2
    g = norm8(rgbu)[cy:cy+size, cx:cx+size]
    m = norm8(ms_in_rgb)[cy:cy+size, cx:cx+size]
    over = np.zeros((size, size, 3), np.uint8)
    over[:, :, 0] = m; over[:, :, 1] = g; over[:, :, 2] = m
    _label(over, ["overlay center 700px", "RGB=green MS=magenta", "aligned=grey"])
    return over


def fit_to(img, w, h):
    ih, iw = img.shape[:2]
    s = min(w/iw, h/ih)
    r = cv2.resize(img, (int(iw*s), int(ih*s)))
    canvas = np.zeros((h, w, 3), np.uint8)
    yo, xo = (h-r.shape[0])//2, (w-r.shape[1])//2
    canvas[yo:yo+r.shape[0], xo:xo+r.shape[1]] = r if r.ndim == 3 else cv2.cvtColor(r, cv2.COLOR_GRAY2BGR)
    return canvas


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default="subset_test")
    ap.add_argument("--capture", default=None,
                    help="filename sequence number, e.g. 0188 (the _0188_ in DJI_..._0188_D.JPG), "
                         "or a filename substring. Default = middle capture.")
    ap.add_argument("--index", type=int, default=None,
                    help="alternative to --capture: 0-based position in the sorted complete-capture list")
    ap.add_argument("--list", action="store_true", help="list available captures (index, seq#, filename) and exit")
    ap.add_argument("--band", default="NIR", choices=["Green", "Red", "RedEdge", "NIR"])
    ap.add_argument("--rgb-channel", default="auto", choices=["auto", "gray", "R", "G", "B", "RG"])
    ap.add_argument("--detector", default="SIFT", help="one of SIFT/AKAZE/ORB/BRISK/KAZE or 'all'")
    ap.add_argument("--enhance", default="clahe", help="none/clahe/grad or 'all'")
    ap.add_argument("--nfeatures", type=int, default=4000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from pathlib import Path
    import re
    ds = Dataset.from_folder(Path(args.input) if os.path.isabs(args.input) else Path(root) / args.input)
    caps = [c for c in ds.captures if not c.missing_bands]

    def seq_of(c):  # the _0188_ token in DJI_<time>_0188_D.JPG
        m = re.search(r"_(\d{4})_D", c.rgb.filename)
        return m.group(1) if m else "?"

    if args.list:
        print(f"{len(caps)} complete captures in {args.input}:")
        for i, c in enumerate(caps):
            print(f"  index {i:3d}   seq {seq_of(c)}   {c.rgb.filename}")
        return

    if args.index is not None:
        cap = caps[args.index]
    elif args.capture is None:
        cap = caps[len(caps) // 2]
    else:
        val = args.capture
        hits = [c for c in caps if val.isdigit() and seq_of(c) == val.zfill(4)] \
            or [c for c in caps if val in c.rgb.filename]
        if not hits:
            avail = ", ".join(seq_of(c) for c in caps)
            raise SystemExit(f"no capture matches '{val}'. Use --list, or pick a seq#: {avail}")
        cap = hits[0]
    ch = BAND_TO_RGBCH[args.band] if args.rgb_channel == "auto" else args.rgb_channel
    print(f"capture seq {seq_of(cap)}  ({cap.rgb.filename})  band {args.band}  RGB channel '{ch}'")

    # load + undistort
    rgb = cap.rgb
    rgb_plane = read_rgb_plane(rgb.path, ch)
    rh, rw = rgb_plane.shape
    rgb_intr = rig.parse_dewarp(rgb, (rw, rh))
    rgbu = rig.undistort(rgb_plane, rgb_intr)
    rgbu_grad = rig._grad(rgbu)
    ms = cap.bands[args.band]
    ms_gray = rig._read_gray(ms.path)
    mh, mw = ms_gray.shape
    msu = rig.undistort(ms_gray, rig.parse_dewarp(ms, (mw, mh)))
    H0 = rig.ms_to_rgb_H(ms)
    rgb_in_ms = cv2.warpPerspective(rgbu, H0, (mw, mh),
                                    flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR)

    dets = ALL_DETECTORS if args.detector == "all" else [args.detector]
    enhs = ["none", "clahe", "grad"] if args.enhance == "all" else [args.enhance]

    rows = []
    stats = []
    print(f"\n{'detector':8s} {'enhance':7s} {'kpRGB':>6s} {'kpMS':>6s} {'good':>6s} {'inl':>5s} "
          f"{'med px':>7s} {'p90':>6s}")
    for dn in dets:
        det = make_detector(dn, args.nfeatures)
        binary = dn in BINARY
        for en in enhs:
            a8 = enhance(norm8(rgb_in_ms), en)
            b8 = enhance(norm8(msu), en)
            res = match_fit(a8, b8, det, binary, H0)
            r = residual(rgbu_grad, msu, res["H"], (rw, rh))
            print(f"{dn:8s} {en:7s} {res['kp_a']:6d} {res['kp_b']:6d} {res['good']:6d} "
                  f"{res['inliers']:5d} {str(r['med']):>7s} {str(r['p90']):>6s}")
            stats.append((dn, en, res, r))
            # build a row: kpA | kpB | matches | overlay
            pa = kp_panel(a8, res["ka"], f"RGB[{ch}] {dn}/{en}")
            pb = kp_panel(b8, res["kb"], f"{args.band} {dn}/{en}")
            pm = match_panel(a8, b8, res)
            po = overlay_panel(rgbu, msu, res["H"], (rw, rh))
            cell_h = 360
            row = np.hstack([fit_to(pa, 360, cell_h), fit_to(pb, 360, cell_h),
                             fit_to(pm, 640, cell_h), fit_to(po, 360, cell_h)])
            lbl = f"{dn}/{en}: kp {res['kp_a']}/{res['kp_b']} inl {res['inliers']}/{res['good']} med {r['med']}px"
            bar = np.zeros((30, row.shape[1], 3), np.uint8)
            _label(bar, [lbl], org=(10, 21))
            rows.append(np.vstack([bar, row]))

    montage = np.vstack(rows)
    out = args.out or os.path.join(root, "tools",
          f"inspect_{cap.rgb.filename.split('_')[-2]}_{args.band}_{args.detector}_{args.enhance}.png")
    cv2.imwrite(out, montage)
    print(f"\nwrote {out}  ({montage.shape[1]}x{montage.shape[0]})")

    # pick + print winner
    valid = [t for t in stats if not np.isnan(t[3]["med"])]
    if valid:
        best = min(valid, key=lambda t: t[3]["med"])
        print(f"best: {best[0]}/{best[1]}  med {best[3]['med']}px  inliers {best[2]['inliers']}")


if __name__ == "__main__":
    main()

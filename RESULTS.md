# OrthoGen — run results (for cross-run comparison)

Test set: `subset_test` = 60 captures (90×90 m block), all 5 bands, all RTK-fixed.
Engine: bundled ODX (native WebODM, Dockerless), CPU-only (no NVIDIA GPU), 16 cores.
Runs 1–3 on **ODX 3.7.4**; run 4 on **ODX 3.8.3** (WebODM Desktop upgraded in place).
Full per-run detail is in each run's `orthogen_run.json` + `odm_report/stats.json`
under `%USERPROFILE%\OrthoGen_work\<run>`.

| # | Run | Bands used for geometry | Key settings | Time | Imgs recon. | Feat/img | Sparse pts | Reproj. err (px) | Georef residual (avg / y-std) |
|---|-----|------------------------|--------------|------|-------------|----------|-----------|------------------|-------------------------------|
| 1 | `baseline` | ODM auto (MS band, 1296 px) | radiometric=camera+sun, ortho/dem-res=2.6, pc=high, feat=high, gps-acc=0.1 | 39.2 min | 59/60 | 1963 | 4,822 | 0.351 | 0.81 m avg / 1.77 m std |
| 2 | `rgb_sfm_medium` | RGB only (1320 px) | feat=medium, pc=low, fast-orthophoto, ortho-res=1.3, gps-acc=0.1 | 5.7 min | **60/60** | 1444 | **14,157** | **0.109** | **0.012 m avg / 0.01 m std** |
| 3 | `ms_sfm_nir_high` | 4 MS bands, **NIR** primary (1296 px) | feat=high, primary-band=NIR, pc=low, fast-orthophoto, ortho-res=1.3, gps-acc=0.1 | 58.5 min | 60/60 | 3749 | 10,876 | 0.375 | 0.67 m avg / 0.49 m y-std (z-std 1.46 m) — **DEGENERATE** |
| 4 | `ms_sfm_nir_383_gsd` | 4 MS bands, **NIR** primary (1296 px), **ODX 3.8.3** | feat=high, primary-band=NIR, pc=low, fast-orthophoto, ortho/dem-res=2.6, gps-acc=0.1 | 27.8 min | **60/60** | 3749 | **35,458** | **0.146** | **0.42 m avg / 0.40 m y-std (z-std 0.05 m)** ✓ sound |
| 5 | `combined_prelim` | RGB-primary disguise, **ODM band-align ON**, ODX 3.8.3 (20-cap prelim) | primary=Pan(RGB), feat=medium, pc=low, fast-orthophoto, ortho-res=1.3, gps-acc=0.1 | 48.6 min¹ | **20/20** | 1306 | 4,376 | 0.096 (norm) | **0.023 m avg** (CE90 1.5 cm, LE90 3.4 cm) |

¹ SfM itself was ~22 s; the 47 min was almost entirely ODM's cross-modal **ECC band alignment** — the step whose output we then measured (and rejected) below.

> **⚠ Correction (post-hoc diagnosis).** Runs #1 and #3 (the MS-driven runs) did
> **not** produce valid 3D reconstructions. Their sparse point clouds are degenerate —
> Z scattered from ~−1.2e9 to +7.7e5 m (RGB's sit in a clean ~2 m slab at field
> elevation). ODM's absurd MS "GSD" of ~62 cm/px (true optical GSD is ~2.4 cm/px) is a
> *symptom*: GSD = camera-height-above-points ÷ focal, and the points are junk. The
> cameras still landed at correct RTK positions **only because `gps-accuracy=0.1` pins
> them regardless of geometry** — so the georef residuals in the table for #1/#3 measure
> the **GPS prior, not reconstruction quality**. Root cause: MS bands downsampled to
> 1296 px (`high`) or less don't yield enough reliable matches on low-texture ag imagery.
> Prior successful M3M-MS runs were on **ultra** (2592 px). Judge MS runs by point-cloud
> Z sanity + inlier reprojection, NOT georef residual. Only run #2 (RGB) is sound **on ODX 3.7.4**.
>
> **Resolved in run #4 (ODX 3.8.3).** The MS failure was an **engine limitation, not the
> sensor**. Feature *detection* was identical across engines (3749/img); 3.8.3's
> `bundle_outlier_filtering_type: AUTO` + `triangulation_type: ROBUST` rejected the bad MS
> matches that 3.7.4 passed through to poison triangulation. On 3.8.3 the same NIR-MS run
> produces a **sound reconstruction**: points in a 1.4 m slab at field elevation, implied
> GSD 2.45 cm/px (matches optical), z-std 1.46 m → **0.05 m**, 3.3× the points, real 298 MB
> ortho. The earlier "partly inherent to the sensor" framing was too pessimistic — the
> newer engine's outlier filtering was the missing piece. (`--gsd` is still not a flag in
> 3.8.3; run 4 "specified GSD" via `--orthophoto-resolution/--dem-resolution 2.6`.)
>
> **Band co-registration (run #4 ortho, phase-correlation on 86 textured tiles vs NIR
> primary):** all bands sub-pixel — RedEdge median **0.05 px** (0.14 cm), Green 0.16 px
> (0.41 cm), Red 0.30 px (0.78 cm; slightly higher likely radiometric, not geometric).
> ODM's per-capture ECC warp fully corrects even RedEdge's 27 px physical lens offset.
> **Verdict: native MS ortho is analysis-ready for NDVI/NDRE — Architecture B (RGB-pose
> propagation) is NOT needed for band co-registration.** Caveats for a production run:
> (1) run #4 used `radiometric-calibration=none` (raw DN, not reflectance) — set
> `camera+sun` for real index work; (2) absolute georef is ~0.4 m (vs RGB's ~0.01 m) —
> fine for single-date zone mapping; revisit if multi-temporal stacking needs tighter.
>
> **RGB↔MS cross-sensor alignment (why one combined stack needs Architecture B).**
> (1) The RGB ortho (run #2) and MS ortho (run #4) are independent solves + separate DSMs;
> measured offset between them = **median 20 cm, 90th pct 38 cm, max 1.1 m**, systematic
> shift only ~3 cm → spatially-varying/relief-driven, NOT a global shift a 2D registration
> could remove. (2) Ingestion test on **ODX 3.8.3 with `--primary-band RGB` forced**: engine
> STILL trims RGB (`Skipping RGB band (60 images)`) and reconstructs from a single MS band
> (fell through to Red, 2592×1944 camera) — verified via `image_list.txt`/`camera_models.json`,
> not the misleading "will use ... from rgb band" log line. So ODM cannot co-process RGB+MS
> in one run even on 3.8.3. **Conclusion: an aligned RGB+MS stack requires custom shared-
> extrinsics + common-DSM orthorectification (Architecture B); it cannot be coerced from ODM.**

## Architecture B — RGB↔MS band placement (run 5 + custom pre-warp)

The combined run (#5) and a series of measurements settle **who should do the MS→RGB
band placement: ODM, or our per-capture code.** Verdict: **our code, decisively.**

**Combined RGB-primary geometry is sound (the disguise works).** Renaming RGB's
`Camera:BandName` to `Pan` (`disguise.py`) skips ODM's M3M RGB trim, so all 5 bands
ingest and RGB drives SfM. Run #5 reproduces the RGB-only reconstruction exactly:
1 connected component, 20/20 images, georef avg **2.3 cm** (CE90 1.5 cm ≈ RTK precision),
reproj 0.096 (norm), ortho GSD 1.30 cm. Carrying the 4 MS bands costs nothing
geometrically. **ODM's SfM/pose/ortho on the RGB is exactly what we want to keep.**

> **ODM's own band alignment is structurally unfit for the M3M (~40 cm, uncorrectable).**
> Phase-correlation of run #5's ortho, ~200 textured tiles/band vs the RGB reference:
>
> | Band | Median | p90 | Max |
> |---|---|---|---|
> | Red | 30.7 px / **39.9 cm** | 62 px / 80 cm | 120 px |
> | Green | 31.7 px / **41.2 cm** | 72 px / 94 cm | 162 px |
> | NIR | 31.3 px / **40.8 cm** | 60 px / 78 cm | 87 px |
> | RedEdge | 31.2 px / **40.6 cm** | 62 px / 80 cm | 121 px |
>
> Fitting a similarity (translation+scale+rotation) to each shift field: **global
> translation only ~2 cm**, but the **residual after removing translation+scale+rotation
> is still ~40 cm** — i.e. **no single global 2D transform can fix it.** ODM computes one
> global homography per band from a few ECC samples (RedEdge got only ~2 good matches of
> 16), then stamps it across every frame; the true RGB↔MS relation is **per-capture**
> (each frame has its own dewarp + ~27 ms-motion parallax). Visually: every plant splits
> into offset ghosts and the MS footprint lands on a different shape than the RGB.
> **This is why ODM cannot make the combined stack — not tuning, a model limitation.**

**Custom per-capture pre-warp (`rig.py` + `prewarp.py`) — ~1–2 px, 20–40× better.**
Warm-start each band from the factory `DewarpHMatrix`, refine with homography ECC.
Head-to-head on captures × 4 bands (residual in RGB px ≈ 1.3 cm each):

| Method | DOF | Residual (median) | Time/refine | Note |
|---|---|---|---|---|
| dewarp only (factory H) | 0 | **55 px** (~72 cm) | 0 | basin start, not accurate alone |
| FFT / Fourier-Mellin | 4 | **90 px** ✗ | 1.8 s | *worse than nothing* — see below |
| FFT translation (phase-corr) | 2 | 13.6 px | 1.8 s | |
| euclidean ECC | 3 | 9.1 px | 2.2 s | |
| **homography ECC** | 8 | **1.25 px (~1.6 cm)** | 2.7 s | the winner |

- **The dewarp is a *basin start*, not a shortcut** — alone it's ~55 px. And its residual
  is **not** pure translation: only the 8-DOF homography reaches ~1 px (translation
  plateaus at ~14 px, euclidean ~9 px). The ~27 ms inter-band motion shows up as real
  scale/rotation/relief-parallax, not just an along-track slide.
- **Motion model barely affects compute** (1.8 → 2.7 s) — per-refine cost is dominated by
  image I/O + the 20 MP RGB undistort, not ECC iterations. So keep the accurate 8-DOF model
  and cut compute elsewhere (undistort RGB **once/capture**, parallelize).
- **FFT/Fourier-Mellin fails here** (90 px, NCC ≈ 0.04, garbage 257 px solutions): a regime
  mismatch — it needs large global rotation/scale on same-modality, well-textured images;
  we have a tiny residual, cross-band, low-texture canopy. Gradient-domain local ECC thrives
  exactly where the whole-image FFT magnitude approach collapses.

**Pre-warp product + speed (`prewarp.py`, measured on written TIFs).** Per-band residual
on the on-disk output: Green 1.06 px, Red 1.03 px, RedEdge 1.43 px, NIR 1.92 px (p90 ≤ 3.3),
coverage ~0.70 (MS FOV narrower than RGB). Speed (5280×3956 uint16 output):

| Mode | 60-cap subset | 391-cap flight (est.) |
|---|---|---|
| serial | 11.5 min | ~75 min |
| **parallel, 8 workers** | **4.5 min** | **~29 min** |

Caveats for the full flight: parallel scaling is only ~2.5× (memory/disk-write bound);
output is bulky (~23 MB/band DEFLATE-compressed carrier, ~36 GB flight — upsampling 5 MP
MS onto the 20 MP grid).

**End-to-end skip run wired + validated (`combined-sfm --prewarp`).** Disguise RGB→`Pan`,
ECC-prewarp each MS band into the **raw (distorted) RGB frame**, write 16-bit DEFLATE
carriers inheriting the RGB's `CaptureUUID` + a per-band `Camera:BandName`; ODM groups all
five, keeps Pan primary, logs **"Skipping band alignment"**, orthorectifies onto the RGB
DSM. Head-to-head on the same 20 captures (`combined_prewarp_20` vs `combined_prelim`,
phase-correlation, ~590 tiles/band):

| Metric | ODM alignment (run 5) | **`--prewarp`** |
|---|---|---|
| Band residual, median | ~40 cm | **6.1–6.5 cm** |
| Band residual, p90 | ~80–94 cm | **7.8–9.5 cm** |
| Geometry (georef avg / CE90) | 0.023 m / 1.5 cm | 0.024 m / 1.4 cm (same) |
| ODM runtime | 48.6 min | **11.6 min** |

**~6× tighter and ~4× faster** (skips ODM's 37-min ECC grind), and far more *consistent*
(p90 ~9 cm vs ~85 cm — no wild tiles). Two things mattered: (1) the **raw-frame** carriers —
the undistorted frame lands at ~13 cm because ODM undistorts again with the Pan camera
(RGB k1=−0.083) → double-undistort; re-distorting to the raw RGB grid drops it to ~6 cm.
(2) The remaining gap from the ~1–2 px per-capture figure is **ortho resampling + mosaic
blend** across overlapping captures — a ceiling of letting ODM mosaic our carriers, not a
bug. To approach per-capture accuracy would need our own per-capture orthorectification.

**Full 60-cap run (`combined_prewarp_60`, ODX 3.8.3).** Band residual **5.6–5.9 cm median /
~8 cm p90** (~1080 tiles/band) — holds at full scale, ~7× better than ODM. Recon 60/60, 1
component, reproj 0.109. Compute: **parallel prewarp staging ~10.3 min (8 workers) + ODM
25.0 min = ~35 min total**; carriers 5.8 GB (240 TIFs, DEFLATE).

Two follow-ups surfaced: (a) **staging under-scales** (10.3 min ≈ serial) — thread
oversubscription (cv2/OpenBLAS internal threads × 8 procs on 16 cores); pin workers to 1
thread (`cv2.setNumThreads(1)`, `OMP_NUM_THREADS=1`) before the 391-cap flight. (b) **georef
relaxed to ~0.11 m** (the `gps-accuracy=0.1` prior) vs RGB-only's 0.012 m at the same
reproj — RTK is ~1–2 cm, so drop `gps-accuracy` to ~0.02 to recover cm absolute (doesn't
affect the relative band co-registration). **Next: radiometric calibration (reflectance).**

## Notes

**Run 1 — baseline (stock ODM, all 5 bands).** Reference/control. Reconstruction
succeeds with excellent sub-pixel reprojection error (0.351 px), but only ~1963
features/image and 4822 sparse points because geometry is driven by a **5 MP MS
band downsized to 1296 px**. The georeferencing residual std (~1.8 m in Y) is far
larger than the RTK precision (~1–2 cm) — i.e. the reconstruction *geometry*, not
the GPS, is the limiting factor. This is exactly the weakness Architecture B
targets: driving SfM from the 20 MP RGB should sharply increase features/points
and tighten geometry, which should also improve band co-registration.

**Run 2 — RGB-only SfM (Architecture B, step 1).** Drives geometry from the 20 MP
RGB at `feature-quality medium` (1320 px ≈ the baseline's 1296 px). Result validates
the whole premise:
- **Georef residual collapsed from ~1.8 m std to ~0.01 m** — now consistent with the
  RTK precision. The reconstruction is finally as accurate as the GPS.
- **60/60 images reconstructed** (vs 59/60), **~3× the sparse tie points** (14,157 vs
  4,822), and **~3× lower reprojection error** (0.109 vs 0.351 px).
- Finished in 5.7 min (post-SfM stages turned down: pc=low + fast-orthophoto).

Surprise worth noting: RGB detected *fewer* raw features/image (1444 vs 1963) — the
field is fairly low-texture at 1320 px. So the win was **not** raw feature count; it
was **match quality + higher overlap**: RGB keypoints match far more reliably across
views, yielding many more valid tracks and a geometrically consistent, RTK-tight
reconstruction. Feature *density* was a red herring; feature *matchability* + overlap
were the real levers.

Next: since medium already nails cm-level georef, `feature-quality high` (2640 px) is
optional headroom (denser tracks), not a necessity. The open work is band-pose
propagation + DSM-based orthorectification (Architecture B, steps 2–3).

**Ingestion check (before run 3).** Inspected run 1's `opensfm/image_list.txt` +
`cameras.json` + console log. Confirmed: ODM **trims the RGB band** when it's mixed
with single-band MS (`[WARNING] Skipping RGB band (60 images)`), so SfM ran on 60
`_MS_G.TIF` at the 2592×1944 MS camera — the 20 MP RGB never entered geometry.
`primary_band: auto` selected **Green**; Red/NIR/RedEdge were 2D-warped onto Green.
Implication: ODM's native `--primary-band=RGB` path is **closed** on this dataset —
RGB geometry must come from a RGB-only run (run 2) + our own pose propagation.

**Run 3 — MS-only, NIR primary (band-choice + input-content control).** Four MS bands,
no RGB (avoids the trim), `feature-quality high` = 1296 px ≈ run 2's 1320 px, post-SfM
matched to run 2. Only variables vs baseline: primary band (Green→NIR) + turned-down
post-SfM. Two findings:
- **Band choice matters.** NIR primary ~doubled features/img (3749 vs 1963), got 60/60
  images (vs 59/60), 2.3× the points (10,876 vs 4,822), and tightened georef y-std 3.6×
  (1.77 → 0.49 m). NIR's canopy/soil contrast is far richer than Green on this vegetated
  field — Green was a poor auto-pick.
- **But the sensor is the ceiling.** Even the best MS band sits at ~0.5 m y-std (z-std
  1.46 m) — still ~50× looser than RGB's 0.01 m. At equal ~1300 px feature resolution,
  20 MP RGB beats 5 MP MS on *content*, and no band/quality knob closes the gap.
- Cost: NIR's feature richness made matching expensive — 58.5 min despite low pc + fast
  ortho (matching is ~O(n²) in features).

**Verdict for Test C.** Both native shortcuts are ruled out (RGB trimmed; MS self-SfM
caps at ~0.5 m). RGB-driven geometry (run 2) is the only cm-level source, so band
registration must be **RGB-pose propagation + DSM orthorectification (our code)**, not
an ODM MS re-run.

Compare subsequent runs against these rows (same subset).

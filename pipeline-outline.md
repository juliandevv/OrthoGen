# OrthoGen — RGB-driven multispectral orthomosaic pipeline (DJI Mavic 3M)

**Goal:** derive cm-accurate camera geometry from the M3M's 20 MP RGB via ODM SfM, then
orthorectify the four 5 MP multispectral bands onto that geometry so RGB + MS land on one
co-registered grid — analysis-ready for vegetation indices (clover-N → variable-rate N).

The reflectance product stays 5 MP-limited; RGB buys geometric fidelity and RGB↔MS alignment,
not spectral resolution.

## Current state

- **Engine:** ODM **3.8.3** ("ODX"), bundled in native WebODM Desktop, driven by direct CLI
  (`run.py`), no Docker/login. Run mechanics + gotchas: see `[[orthogen-runtime]]` memory.
- **RGB-only SfM** reconstructs to RTK precision (georef ~1 cm). **MS-only SfM** also reconstructs
  soundly on 3.8.3 (was degenerate on 3.7.4), with **sub-pixel MS band co-registration** (0.05–0.30 px).
  Per-run metrics + comparisons: `RESULTS.md`.
- **ODM cannot co-process RGB + MS in one run** — it trims RGB even when forced as primary. So an
  aligned RGB+MS stack must be built by us (below). Independent RGB and MS orthos are ~20 cm (up to 1 m)
  misaligned, relief-driven — not a global shift.

## Architecture — RGB-anchored orthorectification

ODM does one-band SfM + a 2D homography warp of the other bands into the primary frame (not per-band
3D poses). We mirror that, anchored on RGB:

1. **RGB SfM** (ODM, `rgb-sfm`) → cm-accurate poses + DSM.  *(done)*
2. **Place each MS band into the RGB image frame** via DJI's fixed `DewarpHMatrix` (MS→RGB 2D homography),
   after undistorting with per-camera `DewarpData`. RGB and MS lenses are co-located, so a 2D homography
   suffices — no 3D extrinsic needed (and DJI publishes none). See `[[m3m-rgb-ms-transform]]`.
3. **Orthorectify** RGB + placed MS onto the RGB DSM → one co-registered multi-band COG stack.
4. Radiometric calibration (`camera+sun`) for true reflectance; cross-capture blend.

## Code map

- `orthogen/metadata.py` — XMP parse; `BandImage`/`Capture`/`Dataset`, grouped by `CaptureUUID`.
- `orthogen/config.py` — engine paths, band/rig maps, `WORK_DIR` (off OneDrive).
- `orthogen/odm_runner.py` — stage images + build/run ODM command with replicated env.
- `orthogen/cli.py` — `doctor`, `ingest`, `baseline`, `rgb-sfm`, `ms-sfm` (`--dry-run`, band/quality overrides).
- `orthogen/rig.py` — **stage 2 (band placement):** parse `DewarpData`/`DewarpHMatrix`, undistort, warp
  MS→RGB frame, `validate_capture` (ECC/phase-corr QA of the alignment).

## Stage status

- **Stage 1 — RGB & MS SfM via ODM:** done (see `RESULTS.md`).
- **Stage 2 — MS→RGB placement (`rig.py`):** done. Undistort + `DewarpHMatrix` warp (handles the 1.7×
  RGB/MS scale + offset) then `refine_alignment` (ECC homography on gradient images, factory H as init)
  brings RGB↔MS from ~50 px to **~1–2 px** on all four bands. `validate_capture` is the QA.
  *Per-capture, not constant:* checked over 10 captures — own-H residual ~1–2 px, but the homography
  varies ~6 px across captures (a single median H spikes to ~18 px on some). Cause: RGB and MS expose
  ~27 ms apart → platform motion adds a per-capture offset (~6 px at RGB GSD). So Stage 3 refines
  **per capture** (median H as fallback for low-texture/failed ECC); 240 solves, parallelize across cores.
- **Stage 3 — orthorectify onto RGB DSM + blend + COG:** not started.

# OrthoGen

RGB-driven multispectral orthomosaic harness for the DJI Mavic 3M. Drives the
**Dockerless ODM 3.8.3 engine bundled with native WebODM Desktop** — no Docker,
no server, no login. See [`pipeline-outline.md`](pipeline-outline.md) for the
architecture and current stage status.

## Runtime

The harness runs on the ODM venv Python bundled with WebODM
(`C:\WebODM\resources\app\apps\ODX\venv`), which already ships numpy, rasterio,
GDAL, opencv, pyproj, scipy **and** the `opendm` package (for radiometry reuse).
No installs required.

Use the launcher (picks that interpreter automatically):

```bat
orthogen.bat <command> [args]
```

## Commands

| Command | What it does |
|---|---|
| `doctor` | Check the bundled engine paths are present. |
| `ingest --input subset_test` | Parse XMP, group the 5 files/capture by `CaptureUUID`, write `captures.csv` + `captures.json` with poses, intrinsics, radiometry, `CalibratedHMatrix`, RTK σ, plus a completeness/RTK validation report. |
| `baseline --input subset_test [--dry-run]` | Stock ODM run on all 5 bands (control reference). |
| `rgb-sfm --input subset_test [--dry-run]` | ODM run on RGB only → cm-accurate poses + DSM. |
| `ms-sfm --input subset_test [--primary-band NIR] [--dry-run]` | ODM run on the 4 MS bands only, chosen primary band. |
| `combined-sfm --input subset_test [--limit N] [--skip-band-alignment] [--dry-run]` | RGB+MS in one ODM pass: RGB is disguised as the primary band so all 5 bands survive and ODM aligns MS to RGB + orthorectifies onto the RGB DSM. |

`--dry-run` prints the exact ODM command without launching it.

## Data

- `DJI_202609091043_001_Corteva/` — full flight (391 captures, 20 GB).
- `subset_test/` — 60-capture / 90×90 m test block (300 files, 3 GB).
- `work/` — ODM projects + harness outputs (created on first run).

## Status

- **Ingest, ODM runner, RGB/MS SfM runs: done** — RGB reconstructs to ~1 cm; MS sound with sub-pixel
  band co-registration (see `RESULTS.md`).
- **Combined RGB-primary path (`disguise.py` + `combined-sfm`): working.** ODM hard-drops the M3M RGB
  band; renaming RGB's `Camera:BandName` to a neutral token (in-place XMP edit, all DJI tags + pixels
  preserved) skips that trim, so RGB survives and drives SfM. Verified in a live run: all 5 bands
  ingest, RGB is the SfM primary, MS pair to RGB by `CaptureUUID`.
- **MS→RGB band placement (`rig.py`): built** — undistort + `DewarpHMatrix` warp + per-capture ECC
  refinement gives ~1–2 px RGB↔MS residual (see `RESULTS.md`).

### Next step — ODM's band alignment vs. our custom placement

`combined-sfm` lets ODM do the MS→RGB warp itself: it computes **one global homography per band**
(best of ~30 sampled captures) via feature-matching→ECC, then `resize_match` upscales each MS to the
RGB grid before warping. Our `rig.py` instead solves **per capture** (factory `DewarpHMatrix` at full
resolution + ECC). The two approaches are what we now compare:

1. **Baseline** — run `combined-sfm` (band alignment ON) and phase-correlate the aligned MS against RGB
   for the per-band residual = ODM's alignment quality.
2. **Custom** — run `combined-sfm --skip-band-alignment` feeding MS pre-warped by `rig.py`, and measure
   the same residual.

Whichever wins drives Architecture B; ODM still handles SfM, pose duplication, and orthorectification
onto the RGB DSM either way.

- **Orthorectify → blend → COG stack:** delegated to ODM within the `combined-sfm` pass.

## Module layout

```
orthogen/
  config.py      engine + project paths, verified M3M rig facts
  metadata.py    XMP parsing, Capture/Dataset grouping, CSV/JSON export
  odm_runner.py  stage images + build/run the bundled ODM CLI
  cli.py         command-line interface
  rig.py         MS->RGB band placement (undistort + DewarpHMatrix + per-capture ECC) + QA
  disguise.py    in-place XMP band-name edit so ODM keeps RGB as a primary band
orthogen_cli.py  bootstrap launcher (works around the venv's pinned sys.path)
orthogen.bat     Windows launcher using the bundled venv Python
```

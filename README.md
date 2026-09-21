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

`--dry-run` prints the exact ODM command without launching it.

## Data

- `DJI_202609091043_001_Corteva/` — full flight (391 captures, 20 GB).
- `subset_test/` — 60-capture / 90×90 m test block (300 files, 3 GB).
- `work/` — ODM projects + harness outputs (created on first run).

## Status

- **Ingest, ODM runner, RGB/MS SfM runs: done** — RGB reconstructs to ~1 cm; MS sound with sub-pixel
  band co-registration (see `RESULTS.md`).
- **MS→RGB band placement (`rig.py`): in progress** — undistort + `DewarpHMatrix` warp working;
  per-capture ECC refinement next.
- **Orthorectify onto RGB DSM + blend + COG stack: not started.**

## Module layout

```
orthogen/
  config.py      engine + project paths, verified M3M rig facts
  metadata.py    XMP parsing, Capture/Dataset grouping, CSV/JSON export
  odm_runner.py  stage images + build/run the bundled ODM CLI
  cli.py         command-line interface
  rig.py         MS->RGB band placement (undistort + DewarpHMatrix) + alignment QA
orthogen_cli.py  bootstrap launcher (works around the venv's pinned sys.path)
orthogen.bat     Windows launcher using the bundled venv Python
```

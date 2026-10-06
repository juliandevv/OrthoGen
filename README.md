# OrthoGen

RGB-driven multispectral orthomosaic harness for the DJI Mavic 3M. Drives the
**ODM engine directly via its `run.py`** — no WebODM server, no login. On Linux it
runs inside the `webodm/nodeodx:gpu` container (ODM 3.8.4); on Windows on the
native WebODM Desktop install (ODM 3.8.3). See [`pipeline-outline.md`](pipeline-outline.md) for the
architecture and current stage status.

## Runtime

The harness runs on the ODM engine's own Python, which already ships numpy, rasterio,
GDAL, opencv, pyproj, scipy **and** the `opendm` package (for radiometry reuse).
No installs required.

**Linux** — runs inside the `webodm/nodeodx:gpu` image (ODM at `/code`). Needs Docker
(user in the `docker` group) and the NVIDIA container toolkit for `--gpus all`.

```bash
./orthogen.sh <command> [args]          # OrthoGen CLI
tools/inspect.sh [args]                 # cross-band inspection tool
tools/container.sh <script.py> [args]   # any project script in the container
```

The project is mounted at `/project` and runs as your uid (outputs stay yours). Env:
`ORTHOGEN_IMAGE` (default `webodm/nodeodx:gpu`), `ORTHOGEN_GPUS` (default `all`; empty
disables GPU). `ORTHOGEN_WORK` must stay inside the project, since only it is mounted.

**Windows** — runs on the WebODM Desktop venv
(`C:\WebODM\resources\app\apps\ODX\venv`):

```bat
orthogen.bat <command> [args]
```

## Commands

| Command | What it does |
|---|---|
| `doctor` | Check the ODM engine paths + work dir are present. |
| `ingest --input subset_test` | Parse XMP, group the 5 files/capture by `CaptureUUID`, write `captures.csv` + `captures.json` with poses, intrinsics, radiometry, `CalibratedHMatrix`, RTK σ, plus a completeness/RTK validation report. |
| `baseline --input subset_test [--dry-run]` | Stock ODM run on all 5 bands (control reference). |
| `rgb-sfm --input subset_test [--dry-run]` | ODM run on RGB only → cm-accurate poses + DSM. |
| `ms-sfm --input subset_test [--primary-band NIR] [--dry-run]` | ODM run on the 4 MS bands only, chosen primary band. |
| `combined-sfm --input subset_test [--limit N] [--prewarp] [--skip-band-alignment] [--dry-run]` | RGB+MS in one ODM pass: RGB is disguised as the primary band so all 5 bands survive. Default lets ODM align MS→RGB (~40 cm, poor). **`--prewarp`** instead ECC-places each MS band into the RGB frame ourselves (`rig.py`+`prewarp.py`, ~3 cm) and feeds them to ODM with band alignment skipped. Placement is per-band ECC by default (`--align feature` for the SIFT stack anchor). **`--full`** drops fast-orthophoto (dense cloud → 2.5D mesh → ortho) and adds a DSM, pc-quality high, the GPU matcher and gps-accuracy 0.02. |

`--dry-run` prints the exact ODM command without launching it. Each run writes
`orthogen_run.json` (settings, command, staging time, ODM per-stage times) into its project dir.
For memory/GPU usage, name the container and sample it:

```bash
tools/sample_resources.sh og_run work/run/resources.csv &
ORTHOGEN_CONTAINER_NAME=og_run ./orthogen.sh combined-sfm --prewarp --name run
```

## Data

- `DJI_202609091043_001_Corteva/` — full flight (391 captures, 20 GB).
- `subset_test/` — 60-capture / 90×90 m test block (300 files, 3 GB).
- `work/` — ODM projects + large intermediaries (gitignored; Linux default `WORK_DIR`,
  override with `ORTHOGEN_WORK`; on Windows it defaults to `~/OrthoGen_work`, off OneDrive).

## Status

- **Ingest, ODM runner, RGB/MS SfM runs: done** — RGB reconstructs to ~1 cm; MS sound with sub-pixel
  band co-registration (see `RESULTS.md`).
- **Combined RGB-primary path (`disguise.py` + `combined-sfm`): working.** ODM hard-drops the M3M RGB
  band; renaming RGB's `Camera:BandName` to a neutral token (in-place XMP edit, all DJI tags + pixels
  preserved) skips that trim, so RGB survives and drives SfM. Verified in a live run: all 5 bands
  ingest, RGB is the SfM primary, MS pair to RGB by `CaptureUUID`.
- **MS→RGB band placement (`rig.py` + `prewarp.py`): done** — factory `DewarpHMatrix` warm-start +
  per-capture homography ECC gives ~1–2 px RGB↔MS residual; batch pre-warp (RGB undistorted once/capture,
  parallel) ≈ 4.5 min/60-cap subset.
- **Band alignment resolved: custom pre-warp wins.** ODM's own MS→RGB alignment (one global homography
  per band) measures **~40 cm** on the ortho and is uncorrectable by any global transform. Our
  `combined-sfm --prewarp` (ECC-placed carriers into the raw RGB frame, ODM band alignment skipped)
  lands at **~3 cm — 12–16× better**, verified end-to-end (see `RESULTS.md`).

### Next steps

- Full 20/60-cap `combined-sfm --prewarp` run for robust ortho-residual numbers.
- Radiometric calibration (reflectance) for real NDVI/NDRE index work.
- **Orthorectify → blend → COG stack:** delegated to ODM within the `combined-sfm --prewarp` pass.

## Module layout

```
orthogen/
  config.py      engine + project paths, verified M3M rig facts
  metadata.py    XMP parsing, Capture/Dataset grouping, CSV/JSON export
  odm_runner.py  stage images + build/run the bundled ODM CLI
  cli.py         command-line interface
  rig.py         MS->RGB band placement (undistort + DewarpHMatrix + per-capture ECC) + QA
  prewarp.py     batch ECC pre-warp: MS carriers in the RGB frame for --skip-band-alignment
  disguise.py    in-place XMP band-name edit so ODM keeps RGB as a primary band
orthogen_cli.py  bootstrap launcher (works around the venv's pinned sys.path)
orthogen.sh      Linux launcher (runs the CLI in the webodm/nodeodx container)
orthogen.bat     Windows launcher using the bundled venv Python
tools/container.sh  Linux: run any project script in the container
```

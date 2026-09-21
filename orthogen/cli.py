"""OrthoGen command-line interface.

Run with the bundled ODM venv Python (has the full geo stack + opendm)::

    "C:\\WebODM\\resources\\app\\apps\\ODX\\venv\\Scripts\\python.exe" -m orthogen ingest --input subset_test

Commands:
    ingest    Parse XMP, group captures by UUID, write capture table + validation.
    baseline  Stock ODM run on all 5 bands (control reference).  [--dry-run to preview]
    rgb-sfm   ODM run on RGB only -> poses + dense DSM.           [--dry-run to preview]
    doctor    Check the bundled engine paths are present.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import platform
import socket
import sys
from pathlib import Path

from . import config, disguise, __version__
from .metadata import Dataset
from . import odm_runner

# --- Documented run settings (edit here; recorded into each run manifest) ---
# Baseline = stock ODM on all 5 bands, the control every later run compares to.
# Resolutions match the M3M multispectral native GSD (~2.6 cm/px at 50 m AGL);
# gps-accuracy reflects RTK-fixed positioning. Keep these stable across runs.
BASELINE_SETTINGS = {
    "radiometric-calibration": "camera+sun",  # camera cal + sunshine sensor (DLS)
    "orthophoto-resolution": 2.6,             # cm/px (MS native)
    "dem-resolution": 2.6,                    # cm/px
    "dsm": True,
    "pc-quality": "high",
    "feature-quality": "high",
    "gps-accuracy": 0.1,                       # m (RTK-fixed)
    "auto-boundary": True,                     # crop ortho to data hull
    "cog": True,                               # cloud-optimized GeoTIFF outputs
}

# RGB-only geometry (Architecture B, step 1): poses from 20 MP RGB.
# Track-reconstruction focus: feature-quality=medium (1320 px, ~= the MS
# baseline's 1296 px but from a sharper 20 MP source), and everything AFTER
# SfM turned down for speed (fast-orthophoto + pc-quality low), since the SfM
# metrics we compare come out before densify. gps-accuracy matches the baseline
# for a fair georef-residual comparison.
RGB_SFM_SETTINGS = {
    "feature-quality": "medium",              # 1320 px feature extraction
    "pc-quality": "low",                       # minimal densify (not the focus)
    "fast-orthophoto": True,                    # skip full 3D mesh
    "orthophoto-resolution": 1.3,             # cm/px (RGB native)
    "gps-accuracy": 0.1,                        # m (RTK), same as baseline
    "auto-boundary": True,
    "cog": True,
}

# MS-only geometry control (Architecture B diagnostic): drive SfM from the four
# multispectral bands (no RGB -> avoids ODM's "redundant bands" RGB trim), at the
# SAME effective feature resolution as the RGB run so the comparison isolates
# *image content* (5 MP MS vs 20 MP RGB), not the knob. feature-quality=high on the
# 5 MP MS = 1296 px, ~= the RGB run's medium (1320 px). Post-SfM turned down to match
# the RGB run (tracks come out before densify). primary-band selects which band drives
# SfM; run #1 auto-picked Green, so this defaults to NIR (highest canopy/soil contrast).
MS_SFM_SETTINGS = {
    "feature-quality": "high",                # 1296 px (~= RGB medium's 1320 px)
    "primary-band": "NIR",                     # run #1 auto=Green; NIR for veg contrast
    "pc-quality": "low",                       # match RGB run (not the focus)
    "fast-orthophoto": True,                    # match RGB run
    "orthophoto-resolution": 1.3,             # match RGB run (parity)
    "gps-accuracy": 0.1,                        # m (RTK), same as baseline
    "auto-boundary": True,
    "cog": True,
}

# Combined RGB+MS in one ODM pass via the RGB-as-primary disguise (Architecture B).
# RGB is renamed to a neutral band ("Pan") so ODM keeps it and drives SfM from the
# 20 MP RGB; the four MS bands are then aligned to it and orthorectified onto the RGB
# DSM in the same run. Geometry knobs mirror the RGB-only SfM run. Band alignment is
# LEFT ON here (ODM's global per-band homography) as the baseline; a later variant
# skips it to inject rig.py's per-capture placement instead.
COMBINED_SFM_SETTINGS = {
    "primary-band": disguise.NEUTRAL_RGB_BAND,  # the disguised RGB band
    "feature-quality": "medium",              # 1320 px (RGB geometry)
    "pc-quality": "low",                       # minimal densify (not the focus)
    "fast-orthophoto": True,                    # skip full 3D mesh
    "orthophoto-resolution": 1.3,             # cm/px (RGB native)
    "gps-accuracy": 0.1,                        # m (RTK)
    "auto-boundary": True,
    "cog": True,
}

# Key ODM artifacts to record for verification.
_OUTPUT_ARTIFACTS = [
    "odm_orthophoto/odm_orthophoto.tif",
    "odm_dem/dsm.tif",
    "odm_report/report.pdf",
    "odm_report/stats.json",
    "opensfm/reconstruction.json",
    "cameras.json",
    "odm_georeferencing/odm_georeferenced_model.laz",
    "odm_georeferencing/proj.txt",
]


def _resolve(input_arg: str) -> Path:
    p = Path(input_arg)
    if not p.is_absolute():
        # allow bare names relative to the project root (e.g. "subset_test")
        cand = config.PROJECT_ROOT / input_arg
        p = cand if cand.exists() else p
    return p


def cmd_doctor(_args):
    problems = config.validate_engine()
    print("ODX_HOME:", config.ODX_HOME)
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  -", p)
        return 1
    print("Bundled ODM engine: OK")
    return 0


def cmd_ingest(args):
    folder = _resolve(args.input)
    print(f"Ingesting {folder} ...")
    ds = Dataset.from_folder(folder)
    report = ds.validate()
    print(json.dumps(report, indent=2))

    out_dir = _resolve(args.out) if args.out else folder
    out_dir.mkdir(parents=True, exist_ok=True)
    ds.to_csv(out_dir / "captures.csv")
    ds.to_json(out_dir / "captures.json")
    print(f"Wrote {out_dir/'captures.csv'} and captures.json")
    # exit non-zero if any capture is incomplete
    return 0 if not report["incomplete_captures"] else 2


def _band_images(ds: Dataset, bands):
    for c in ds.captures:
        for b in bands:
            img = c.bands.get(b)
            if img:
                yield Path(img.path)


def _summarize_outputs(project_dir: Path) -> dict:
    out = {}
    for rel in _OUTPUT_ARTIFACTS:
        p = project_dir / rel
        out[rel] = {
            "exists": p.exists(),
            "size_bytes": p.stat().st_size if p.exists() else 0,
        }
    return out


def _run_project(name: str, folder: Path, bands: list, settings: dict,
                 dry_run: bool, stager=None) -> int:
    """Shared run path: stage images, write a manifest, run ODM (logged), summarize.

    ``stager(ds, project_dir) -> int`` overrides the default hard-link staging
    (used by the combined run, which copies + disguises RGB and hard-links MS).
    """
    ds = Dataset.from_folder(folder)
    complete = [c for c in ds.captures if not c.missing_bands]
    project_parent = config.WORK_DIR
    project_dir = project_parent / name
    project_dir.mkdir(parents=True, exist_ok=True)

    staged = 0
    if not dry_run:
        if stager is not None:
            staged = stager(ds, project_dir)
        else:
            staged = odm_runner.stage_images(_band_images(ds, bands), project_dir)
        print(f"Staged {staged} images ({'+'.join(bands)}) into {project_dir/'images'}")

    started = _dt.datetime.now().astimezone().isoformat()
    manifest = {
        "orthogen_version": __version__,
        "run_name": name,
        "started": started,
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "odm_engine": {"path": str(config.ODX_HOME), "version": "3.8.3"},
        "input_folder": str(folder),
        "bands": bands,
        "captures_total": len(ds.captures),
        "captures_complete": len(complete),
        "images_staged": staged,
        "settings": settings,
        "command": odm_runner.build_command(name, project_parent, settings),
    }
    manifest_path = project_dir / "orthogen_run.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Run manifest -> {manifest_path}")
    if dry_run:
        odm_runner.build_command(name, project_parent, settings)
        print("[dry-run] not launching ODM")
        return 0

    log_path = project_dir / "orthogen_odm_console.log"
    code, elapsed = odm_runner.run(name, project_parent, settings,
                                   log_path=log_path)

    manifest["finished"] = _dt.datetime.now().astimezone().isoformat()
    manifest["exit_code"] = code
    manifest["elapsed_seconds"] = round(elapsed, 1)
    manifest["console_log"] = str(log_path)
    manifest["outputs"] = _summarize_outputs(project_dir)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"\n{'='*60}\nODM exit={code}  elapsed={elapsed/60:.1f} min")
    print(f"Project: {project_dir}")
    for rel, info in manifest["outputs"].items():
        mark = "OK " if info["exists"] else "-- "
        mb = info["size_bytes"] / 1e6
        print(f"  [{mark}] {rel}  ({mb:.1f} MB)" if info["exists"] else f"  [{mark}] {rel}")
    print(f"Manifest + log written to {project_dir}")
    return code


def cmd_baseline(args):
    folder = _resolve(args.input)
    settings = dict(BASELINE_SETTINGS)
    if args.pc_quality:
        settings["pc-quality"] = args.pc_quality
    if getattr(args, "primary_band", None):
        settings["primary-band"] = args.primary_band
    return _run_project(args.name, folder, config.ALL_BANDS, settings, args.dry_run)


def cmd_rgb_sfm(args):
    folder = _resolve(args.input)
    settings = dict(RGB_SFM_SETTINGS)
    if args.pc_quality:
        settings["pc-quality"] = args.pc_quality
    if args.feature_quality:
        settings["feature-quality"] = args.feature_quality
    return _run_project(args.name, folder, ["RGB"], settings, args.dry_run)


def cmd_ms_sfm(args):
    folder = _resolve(args.input)
    settings = dict(MS_SFM_SETTINGS)
    if args.feature_quality:
        settings["feature-quality"] = args.feature_quality
    if args.primary_band:
        settings["primary-band"] = args.primary_band
    if args.pc_quality:
        settings["pc-quality"] = args.pc_quality
    if args.orthophoto_resolution:
        # ODM has no --gsd flag; orthophoto/dem-resolution (cm/px) IS the GSD control.
        settings["orthophoto-resolution"] = args.orthophoto_resolution
        settings["dem-resolution"] = args.orthophoto_resolution
    return _run_project(args.name, folder, config.MS_BANDS, settings, args.dry_run)


def cmd_combined_sfm(args):
    """RGB+MS in one ODM pass: disguise RGB as primary band, all 5 bands survive,
    ODM aligns MS to RGB and orthorectifies onto the RGB DSM."""
    folder = _resolve(args.input)
    settings = dict(COMBINED_SFM_SETTINGS)
    if args.feature_quality:
        settings["feature-quality"] = args.feature_quality
    if args.pc_quality:
        settings["pc-quality"] = args.pc_quality
    if args.skip_band_alignment:
        settings["skip-band-alignment"] = True

    limit = args.limit

    def stager(ds, project_dir):
        complete = [c for c in ds.captures if not c.missing_bands]
        if limit:
            complete = complete[:limit]
        rgb = [c.bands["RGB"].path for c in complete if c.bands.get("RGB")]
        ms = [c.bands[b].path for c in complete for b in config.MS_BANDS
              if c.bands.get(b)]
        print(f"Staging {len(complete)} captures: {len(rgb)} RGB (disguised) "
              f"+ {len(ms)} MS")
        return odm_runner.stage_combined(rgb, ms, project_dir)

    return _run_project(args.name, folder, config.ALL_BANDS, settings,
                        args.dry_run, stager=stager)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orthogen", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check bundled engine paths")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("ingest", help="parse XMP + group captures")
    i.add_argument("--input", default="subset_test", help="image folder (or name under project root)")
    i.add_argument("--out", default=None, help="output dir for captures.csv/json (default: input folder)")
    i.set_defaults(func=cmd_ingest)

    b = sub.add_parser("baseline", help="stock ODM run, all 5 bands (control)")
    b.add_argument("--input", default="subset_test")
    b.add_argument("--name", default="baseline")
    b.add_argument("--pc-quality", default=None,
                   help="override documented pc-quality (ultra/high/medium/low/lowest)")
    b.add_argument("--primary-band", default=None,
                   help="force SfM primary band (e.g. RGB) instead of auto")
    b.add_argument("--dry-run", action="store_true")
    b.set_defaults(func=cmd_baseline)

    r = sub.add_parser("rgb-sfm", help="ODM run on RGB only -> poses (track reconstruction)")
    r.add_argument("--input", default="subset_test")
    r.add_argument("--name", default="rgb_sfm_medium")
    r.add_argument("--feature-quality", default=None,
                   help="override documented feature-quality (ultra/high/medium/low/lowest)")
    r.add_argument("--pc-quality", default=None,
                   help="override documented pc-quality")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_rgb_sfm)

    m = sub.add_parser("ms-sfm", help="ODM run on the 4 MS bands only, chosen primary band")
    m.add_argument("--input", default="subset_test")
    m.add_argument("--name", default="ms_sfm_nir_high")
    m.add_argument("--primary-band", default=None,
                   help="band that drives SfM (Green/Red/RedEdge/NIR); default NIR")
    m.add_argument("--feature-quality", default=None,
                   help="override documented feature-quality (ultra/high/medium/low/lowest)")
    m.add_argument("--pc-quality", default=None,
                   help="override documented pc-quality")
    m.add_argument("--orthophoto-resolution", type=float, default=None,
                   help="explicit output GSD in cm/px (sets ortho + dem resolution; ODM's real 'GSD' control)")
    m.add_argument("--dry-run", action="store_true")
    m.set_defaults(func=cmd_ms_sfm)

    c = sub.add_parser("combined-sfm",
                       help="RGB+MS one pass: disguise RGB as primary, ODM aligns MS to it")
    c.add_argument("--input", default="subset_test")
    c.add_argument("--name", default="combined_rgb_primary")
    c.add_argument("--limit", type=int, default=None,
                   help="use only the first N complete captures (quick preliminary runs)")
    c.add_argument("--feature-quality", default=None)
    c.add_argument("--pc-quality", default=None)
    c.add_argument("--skip-band-alignment", action="store_true",
                   help="skip ODM's band alignment (requires MS pre-warped into the RGB frame)")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_combined_sfm)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

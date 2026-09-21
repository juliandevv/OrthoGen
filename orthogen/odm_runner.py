"""Thin wrapper around the bundled ODM 3.7.4 engine CLI.

Runs the Dockerless native engine directly via ``winrun.bat`` (which sets up
GDAL/PROJ/OpenSfM env then calls ``run.py``). ODM's project convention is::

    <project_parent>/<name>/images/*.{jpg,tif}   -> inputs
    <project_parent>/<name>/                      -> outputs (odm_orthophoto, dsm, opensfm, ...)

Images are hard-linked into ``images/`` when possible (same NTFS volume) to
avoid duplicating gigabytes; falls back to copy across volumes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

from . import config


def stage_images(image_paths: Iterable[Path], project_dir: Path) -> int:
    """Populate <project_dir>/images with hard links (or copies) to inputs."""
    images_dir = project_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in image_paths:
        src = Path(src)
        dst = images_dir / src.name
        if dst.exists():
            n += 1
            continue
        try:
            os.link(src, dst)          # hard link (same volume)
        except OSError:
            shutil.copy2(src, dst)     # cross-volume fallback
        n += 1
    return n


def stage_combined(rgb_paths: Iterable[Path], ms_paths: Iterable[Path],
                   project_dir: Path) -> int:
    """Stage a disguised RGB+MS project.

    RGB files are COPIED (not hard-linked) and their XMP band name is rewritten to
    a neutral token so ODM keeps RGB and can use it as the SfM primary band
    (see ``disguise.inject_bandname``). MS files are hard-linked unchanged.
    """
    from . import disguise
    images_dir = project_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in rgb_paths:
        src = Path(src)
        dst = images_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)              # copy: we edit its XMP
            disguise.inject_bandname(str(dst))
        n += 1
    for src in ms_paths:
        src = Path(src)
        dst = images_dir / src.name
        if not dst.exists():
            try:
                os.link(src, dst)               # hard link (unchanged)
            except OSError:
                shutil.copy2(src, dst)
        n += 1
    return n


def odm_env() -> dict:
    """Replicate win32env.bat: GDAL/PROJ/PDAL env for the bundled engine.

    We invoke run.py directly rather than winrun.bat because the vendor batch
    resolves ``%~dp0`` / ``call win32env.bat`` incorrectly when launched as a
    subprocess. run.py is all winrun.bat ultimately runs.
    """
    odx = config.ODX_HOME
    venv = odx / "venv"
    venv_scripts = venv / "Scripts"
    gdalbase = venv / "Lib" / "site-packages" / "osgeo"
    sbbin = odx / "SuperBuild" / "install" / "bin"
    osfm = sbbin / "opensfm" / "bin"
    env = dict(os.environ)
    env["GDAL_DATA"] = str(gdalbase / "data" / "gdal")
    env["GDAL_DRIVER_PATH"] = str(gdalbase / "gdalplugins")
    env["PROJ_LIB"] = str(venv / "Lib" / "site-packages" / "rasterio" / "proj_data")
    env["PDAL_DRIVER_PATH"] = str(sbbin)
    env["PYTHONPYCACHEPREFIX"] = os.path.join(
        os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "ODM", "pycache")
    # venv\Scripts MUST come first: ODM spawns nested tools (opensfm.bat, etc.)
    # that call bare `python`; without this they hit the system Python and the
    # 3.12-built native extensions (pybundle) fail to load.
    env["PATH"] = os.pathsep.join(
        [str(venv_scripts), str(gdalbase), str(sbbin), str(osfm), env.get("PATH", "")])
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)   # run.py's own dir (on the venv ._pth) provides opendm
    env["ODM_NONINTERACTIVE"] = "1"
    return env


def build_command(name: str, project_parent: Path, options: Optional[dict] = None,
                  rerun_from: Optional[str] = None) -> list[str]:
    """Construct the ODM run argv (venv python + run.py, invoked directly).

    ``options`` maps ODM flags (without leading --) to values; boolean True
    renders a bare flag. Example: {"orthophoto-resolution": 2, "dsm": True}.
    """
    argv = [str(config.ODX_VENV_PYTHON), "-X", "utf8", str(config.ODX_RUN_PY),
            name, "--project-path", str(project_parent)]
    if rerun_from:
        argv += ["--rerun-from", rerun_from]
    for key, val in (options or {}).items():
        flag = "--" + key.lstrip("-")
        if val is True:
            argv.append(flag)
        elif val is False or val is None:
            continue
        else:
            argv += [flag, str(val)]
    return argv


def run(name: str, project_parent: Path, options: Optional[dict] = None,
        rerun_from: Optional[str] = None, dry_run: bool = False,
        log_path: Optional[Path] = None) -> tuple[int, float]:
    """Execute an ODM run; returns (exit_code, elapsed_seconds).

    ``dry_run`` prints the command and returns (0, 0.0) without launching ODM.
    If ``log_path`` is given, the ODM console output is streamed to that file
    (line-buffered) as well as to stdout.
    """
    project_parent = Path(project_parent)
    project_parent.mkdir(parents=True, exist_ok=True)
    argv = build_command(name, project_parent, options, rerun_from)
    print("[odm]", " ".join(f'"{a}"' if " " in a else a for a in argv))
    if dry_run:
        return 0, 0.0
    env = odm_env()
    t0 = time.time()
    log_fh = open(log_path, "w", encoding="utf-8", buffering=1) if log_path else None
    try:
        proc = subprocess.Popen(
            argv, cwd=str(config.ODX_HOME), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_fh:
                log_fh.write(line)
        proc.wait()
    finally:
        if log_fh:
            log_fh.close()
    return proc.returncode, time.time() - t0

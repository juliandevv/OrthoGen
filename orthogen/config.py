"""Environment + path configuration for OrthoGen.

OrthoGen drives the ODM engine directly via its ``run.py`` (no WebODM server, no
login), running on the engine's own Python, which already ships the full geo stack
(numpy, rasterio, GDAL, opencv, pyproj, scipy) plus the `opendm` package.

- Linux: the harness runs INSIDE the ``webodm/nodeodx`` container (``orthogen.sh``),
  where ODM lives at ``/code`` and the image's env already wires GDAL/PROJ/OpenSfM.
- Windows: the native WebODM Desktop install's bundled ODX venv (``orthogen.bat``).
"""
from __future__ import annotations

import os
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# --- ODM engine -----------------------------------------------------------
if IS_WINDOWS:
    WEBODM_HOME = Path(os.environ.get("WEBODM_HOME", r"C:\WebODM"))
    ODX_HOME = Path(os.environ.get("ODX_HOME", WEBODM_HOME / "resources" / "app" / "apps" / "ODX"))
    ODX_PYTHON = ODX_HOME / "venv" / "Scripts" / "python.exe"
    ODX_WINRUN = ODX_HOME / "winrun.bat"        # sets GDAL/PROJ/OpenSfM env, then run.py
else:
    ODX_HOME = Path(os.environ.get("ODX_HOME", "/code"))   # webodm/nodeodx image layout
    ODX_PYTHON = Path("/usr/bin/python3")
ODX_RUN_PY = ODX_HOME / "run.py"

# --- Project layout -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FULL_DATASET = PROJECT_ROOT / "DJI_202609091043_001_Corteva"
SUBSET_DIR = PROJECT_ROOT / "subset_test"
# ODM projects + large intermediaries. On Linux this is <project>/work (gitignored;
# same filesystem as the datasets, so staged images can be hard-linked, and inside
# the container's single /project mount). On Windows the project sat in OneDrive,
# whose sync client holds handles on fresh files and breaks ODM's cleanup
# (WinError 5), so work lived off-tree in the home dir. Override with ORTHOGEN_WORK.
_DEFAULT_WORK = Path.home() / "OrthoGen_work" if IS_WINDOWS else PROJECT_ROOT / "work"
WORK_DIR = Path(os.environ.get("ORTHOGEN_WORK", _DEFAULT_WORK))

# --- DJI M3M rig facts (verified from this dataset's XMP) -----------------
# RigCameraIndex -> logical band. RGB is index 5; MS bands 1..4.
RIG_INDEX_TO_BAND = {1: "Green", 2: "Red", 3: "RedEdge", 4: "NIR", 5: "RGB"}
BAND_FILE_SUFFIX = {
    "RGB": "_D.JPG",
    "Green": "_MS_G.TIF",
    "Red": "_MS_R.TIF",
    "RedEdge": "_MS_RE.TIF",
    "NIR": "_MS_NIR.TIF",
}
MS_BANDS = ["Green", "Red", "RedEdge", "NIR"]
ALL_BANDS = ["RGB"] + MS_BANDS
EXPECTED_FILES_PER_CAPTURE = 5


def engine_version() -> str:
    """ODM version string from the engine's VERSION file ("unknown" if absent)."""
    try:
        return (ODX_HOME / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def validate_engine() -> list[str]:
    """Return a list of problems with the ODM engine paths (empty = OK)."""
    checks = [("ODM python", ODX_PYTHON), ("ODM run.py", ODX_RUN_PY)]
    if IS_WINDOWS:
        checks.append(("winrun.bat", ODX_WINRUN))
    problems = [f"missing {label}: {p}" for label, p in checks if not p.exists()]
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        if not os.access(WORK_DIR, os.W_OK):
            problems.append(f"work dir not writable: {WORK_DIR}")
    except OSError as e:
        problems.append(f"cannot create work dir {WORK_DIR}: {e}")
    return problems

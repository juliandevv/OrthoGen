"""Environment + path configuration for OrthoGen.

OrthoGen drives the ODM engine bundled inside the native WebODM Desktop install
(no Docker, no login). The same install's Python venv (3.12) already ships the
full geo stack (numpy, rasterio, GDAL, opencv, pyproj, scipy) plus the `opendm`
package, so that interpreter is used to run this harness.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Bundled WebODM / ODM engine (native, Dockerless) ---------------------
WEBODM_HOME = Path(os.environ.get("WEBODM_HOME", r"C:\WebODM"))
ODX_HOME = Path(os.environ.get("ODX_HOME", WEBODM_HOME / "resources" / "app" / "apps" / "ODX"))

ODX_VENV_PYTHON = ODX_HOME / "venv" / "Scripts" / "python.exe"
ODX_RUN_PY = ODX_HOME / "run.py"
ODX_WINRUN = ODX_HOME / "winrun.bat"        # sets GDAL/PROJ/OpenSfM env, then run.py
ODX_WIN32ENV = ODX_HOME / "win32env.bat"

# --- Project layout -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FULL_DATASET = PROJECT_ROOT / "DJI_202609091043_001_Corteva"
SUBSET_DIR = PROJECT_ROOT / "subset_test"
# ODM projects must NOT live inside OneDrive: the sync client holds handles on
# freshly written files and ODM's cleanup (shutil.rmtree of temp dirs) then
# fails with WinError 5. Keep work off the synced tree (same C: volume, so the
# hard-linked input images still work). Override with ORTHOGEN_WORK.
WORK_DIR = Path(os.environ.get("ORTHOGEN_WORK", Path.home() / "OrthoGen_work"))

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


def validate_engine() -> list[str]:
    """Return a list of problems with the bundled-engine paths (empty = OK)."""
    problems = []
    for label, p in [
        ("ODX venv python", ODX_VENV_PYTHON),
        ("ODM run.py", ODX_RUN_PY),
        ("winrun.bat", ODX_WINRUN),
    ]:
        if not p.exists():
            problems.append(f"missing {label}: {p}")
    return problems

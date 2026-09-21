"""Phase 1 — ingest & group DJI Mavic 3M captures from XMP metadata.

Each M3M capture writes 5 files that share a ``drone-dji:CaptureUUID``:
one 20 MP RGB JPG and four 5 MP multispectral TIFs (G/R/RE/NIR). DJI embeds
everything the pipeline needs as a plaintext XMP packet in every file, so this
module parses that packet directly (no exiftool dependency) and assembles
validated :class:`Capture` groups.

Fields captured mirror what was verified in the sample dataset and what ODM's
``photo.py`` consumes downstream (radiometry, rig index, RTK sigmas), plus the
DJI-only fields ODM ignores but OrthoGen uses (``CalibratedHMatrix``,
``RelativeOpticalCenter``, ``DewarpData``).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from . import config

_XMP_RE = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.DOTALL)
# Read enough of each file to contain the XMP packet (APP1 lives near the head).
_XMP_SCAN_BYTES = 300_000


def _extract_xmp(path: Path) -> str:
    with open(path, "rb") as fh:
        data = fh.read(_XMP_SCAN_BYTES)
    m = _XMP_RE.search(data)
    if not m:
        raise ValueError(f"no XMP packet found in {path}")
    return m.group(0).decode("utf-8", "replace")


def _attr(xmp: str, ns: str, key: str) -> Optional[str]:
    """Read either an attribute (ns:key="v") or an element (<ns:key>v</ns:key>)."""
    m = re.search(rf'{ns}:{key}="([^"]*)"', xmp)
    if m:
        return m.group(1)
    m = re.search(rf"<{ns}:{key}>([^<]*)</{ns}:{key}>", xmp)
    return m.group(1) if m else None


def _f(v: Optional[str]) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except ValueError:
        return None


def _floats(csv_str: Optional[str]) -> list[float]:
    if not csv_str:
        return []
    return [float(x) for x in re.split(r"[,\s]+", csv_str.strip()) if x]


def _seq(xmp: str, ns: str, key: str) -> list[float]:
    """Read an rdf:Seq element (<ns:key><rdf:Seq><rdf:li>..</rdf:li>..)."""
    m = re.search(rf"<{ns}:{key}>(.*?)</{ns}:{key}>", xmp, re.DOTALL)
    if not m:
        return []
    return [float(x) for x in re.findall(r"<rdf:li>([^<]+)</rdf:li>", m.group(1))]


@dataclass
class BandImage:
    path: str
    filename: str
    band: str                      # logical band name (RGB/Green/Red/RedEdge/NIR)
    rig_index: Optional[int]
    image_source: Optional[str]
    # Geometry / intrinsics
    focal_px: Optional[float] = None          # drone-dji:CalibratedFocalLength (px)
    optical_center: Optional[tuple] = None    # (cx, cy) px
    dewarp_data: Optional[str] = None         # DJI intrinsics+distortion string
    relative_optical_center: Optional[tuple] = None  # per-sensor px offset (ODM ignores)
    calibrated_hmatrix: list = field(default_factory=list)  # DJI band->MS-reference H (ODM ignores)
    dewarp_hmatrix: list = field(default_factory=list)       # DJI MS-pixel -> RGB-frame H (9 floats)
    # Pose
    lat: Optional[float] = None
    lon: Optional[float] = None
    abs_alt: Optional[float] = None
    rel_alt: Optional[float] = None
    gimbal: Optional[tuple] = None            # (roll, pitch, yaw) deg
    flight: Optional[tuple] = None            # (roll, pitch, yaw) deg
    rtk_flag: Optional[str] = None
    rtk_std: Optional[tuple] = None           # (lon, lat, hgt) m
    # Radiometry (MS only)
    black_level: Optional[float] = None
    sensor_gain: Optional[float] = None
    sensor_gain_adj: Optional[float] = None
    exposure_time: Optional[float] = None
    irradiance: Optional[float] = None
    sun_sensor: Optional[float] = None
    vignetting_poly: list = field(default_factory=list)
    central_wavelength: Optional[float] = None
    utc: Optional[str] = None

    @classmethod
    def from_file(cls, path: Path) -> "BandImage":
        xmp = _extract_xmp(path)
        rig_index = _attr(xmp, "Camera", "RigCameraIndex")
        rig_index = int(rig_index) if rig_index and rig_index.isdigit() else None
        band = config.RIG_INDEX_TO_BAND.get(rig_index) or (
            _attr(xmp, "Camera", "BandName") or "Unknown"
        )

        cx = _f(_attr(xmp, "drone-dji", "CalibratedOpticalCenterX"))
        cy = _f(_attr(xmp, "drone-dji", "CalibratedOpticalCenterY"))
        rocx = _f(_attr(xmp, "drone-dji", "RelativeOpticalCenterX"))
        rocy = _f(_attr(xmp, "drone-dji", "RelativeOpticalCenterY"))

        return cls(
            path=str(path),
            filename=path.name,
            band=band,
            rig_index=rig_index,
            image_source=_attr(xmp, "drone-dji", "ImageSource"),
            focal_px=_f(_attr(xmp, "drone-dji", "CalibratedFocalLength")),
            optical_center=(cx, cy) if cx is not None else None,
            dewarp_data=_attr(xmp, "drone-dji", "DewarpData"),
            relative_optical_center=(rocx, rocy) if rocx is not None else None,
            calibrated_hmatrix=_floats(_attr(xmp, "drone-dji", "CalibratedHMatrix")),
            dewarp_hmatrix=_floats(_attr(xmp, "drone-dji", "DewarpHMatrix")),
            lat=_f(_attr(xmp, "drone-dji", "GpsLatitude")),
            lon=_f(_attr(xmp, "drone-dji", "GpsLongitude")),
            abs_alt=_f(_attr(xmp, "drone-dji", "AbsoluteAltitude")),
            rel_alt=_f(_attr(xmp, "drone-dji", "RelativeAltitude")),
            gimbal=(
                _f(_attr(xmp, "drone-dji", "GimbalRollDegree")),
                _f(_attr(xmp, "drone-dji", "GimbalPitchDegree")),
                _f(_attr(xmp, "drone-dji", "GimbalYawDegree")),
            ),
            flight=(
                _f(_attr(xmp, "drone-dji", "FlightRollDegree")),
                _f(_attr(xmp, "drone-dji", "FlightPitchDegree")),
                _f(_attr(xmp, "drone-dji", "FlightYawDegree")),
            ),
            rtk_flag=_attr(xmp, "drone-dji", "RtkFlag"),
            rtk_std=(
                _f(_attr(xmp, "drone-dji", "RtkStdLon")),
                _f(_attr(xmp, "drone-dji", "RtkStdLat")),
                _f(_attr(xmp, "drone-dji", "RtkStdHgt")),
            ),
            black_level=_f(_attr(xmp, "drone-dji", "BlackLevel"))
            or _f(_attr(xmp, "Camera", "BlackCurrent")),
            sensor_gain=_f(_attr(xmp, "drone-dji", "SensorGain")),
            sensor_gain_adj=_f(_attr(xmp, "drone-dji", "SensorGainAdjustment")),
            exposure_time=_f(_attr(xmp, "drone-dji", "ExposureTime")),
            irradiance=_f(_attr(xmp, "Camera", "Irradiance"))
            or _f(_attr(xmp, "drone-dji", "Irradiance")),
            sun_sensor=_f(_attr(xmp, "Camera", "SunSensor")),
            vignetting_poly=_seq(xmp, "Camera", "VignettingPolynomial")
            or _floats(_attr(xmp, "drone-dji", "VignettingData")),
            central_wavelength=_f(_attr(xmp, "Camera", "CentralWavelength")),
            utc=_attr(xmp, "drone-dji", "UTCAtExposure"),
        )


@dataclass
class Capture:
    uuid: str
    bands: dict                     # band name -> BandImage
    utc: Optional[str] = None

    @property
    def rgb(self) -> Optional[BandImage]:
        return self.bands.get("RGB")

    @property
    def missing_bands(self) -> list[str]:
        return [b for b in config.ALL_BANDS if b not in self.bands]

    @property
    def lat(self):
        r = self.rgb
        return r.lat if r else None

    @property
    def lon(self):
        r = self.rgb
        return r.lon if r else None


class Dataset:
    """A folder of M3M captures grouped by CaptureUUID."""

    def __init__(self, captures: list[Capture]):
        self.captures = captures

    @classmethod
    def from_folder(cls, folder: Path) -> "Dataset":
        folder = Path(folder)
        groups: dict[str, dict] = {}
        utc: dict[str, str] = {}
        img_files = sorted(
            p for p in folder.iterdir()
            if p.suffix.upper() in (".JPG", ".TIF", ".TIFF") and p.is_file()
        )
        for p in img_files:
            bi = BandImage.from_file(p)
            xmp = _extract_xmp(p)
            uuid = _attr(xmp, "drone-dji", "CaptureUUID")
            if not uuid:
                raise ValueError(f"{p.name}: no CaptureUUID")
            groups.setdefault(uuid, {})[bi.band] = bi
            if bi.band == "RGB":
                utc[uuid] = bi.utc
        captures = [
            Capture(uuid=u, bands=b, utc=utc.get(u)) for u, b in groups.items()
        ]
        captures.sort(key=lambda c: (c.rgb.filename if c.rgb else c.uuid))
        return cls(captures)

    # --- reporting / export ---
    def validate(self) -> dict:
        full = [c for c in self.captures if not c.missing_bands]
        incomplete = {c.uuid: c.missing_bands for c in self.captures if c.missing_bands}
        rtk_flags: dict[str, int] = {}
        for c in self.captures:
            if c.rgb:
                rtk_flags[c.rgb.rtk_flag] = rtk_flags.get(c.rgb.rtk_flag, 0) + 1
        return {
            "captures": len(self.captures),
            "complete_captures": len(full),
            "incomplete_captures": incomplete,
            "rtk_flag_counts": rtk_flags,
        }

    def to_json(self, path: Path):
        payload = [
            {"uuid": c.uuid, "utc": c.utc,
             "bands": {b: asdict(img) for b, img in c.bands.items()}}
            for c in self.captures
        ]
        Path(path).write_text(json.dumps(payload, indent=2))

    def to_csv(self, path: Path):
        """One row per capture (RGB-anchored) for quick inspection / GIS."""
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["uuid", "rgb_file", "lat", "lon", "abs_alt", "rel_alt",
                        "gimbal_yaw", "rtk_flag", "missing_bands", "utc"])
            for c in self.captures:
                r = c.rgb
                w.writerow([
                    c.uuid, r.filename if r else "",
                    r.lat if r else "", r.lon if r else "",
                    r.abs_alt if r else "", r.rel_alt if r else "",
                    r.gimbal[2] if r and r.gimbal else "",
                    r.rtk_flag if r else "",
                    "|".join(c.missing_bands), c.utc or "",
                ])

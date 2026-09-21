"""Disguise the M3M RGB band so ODM keeps it and can use it as the SfM primary.

ODM hard-drops the Mavic 3M RGB channel whenever the multispectral Red+Green bands
are present ("Mavic 3M's RGB camera lens are too different..."; opendm/types.py
``filter_photos``). That trim only fires when a band is named rgb/redgreenblue/red/
green/blue. Renaming RGB's ``Camera:BandName`` to a neutral token (default "Pan")
skips the whole trim block, so all five bands survive and RGB can be the primary.

``inject_bandname`` edits the JPEG's XMP packet in place: it inserts
``<Camera:BandName>{band}</Camera:BandName>`` before ``</rdf:Description>`` and
removes an equal number of trailing xpacket-padding bytes, so the APP1 segment
length is unchanged. Every existing DJI tag (CaptureUUID, RtkFlag, gimbal) and the
image scan data are preserved byte-for-byte; the element lands inside the
``<x:xmpmeta>..</x:xmpmeta>`` region ODM parses (opendm/photo.py ``get_xmp``).

The Pix4D "Camera" namespace is already declared in M3M RGB XMP, so only the
element is added. Primary-band selection then uses ``--primary-band <band>``.
"""
from __future__ import annotations

NEUTRAL_RGB_BAND = "Pan"  # not in {rgb, redgreenblue, red, green, blue} -> no trim


def inject_bandname(path: str, band: str = NEUTRAL_RGB_BAND) -> str:
    """Add a ``Camera:BandName`` to an M3M RGB JPEG's XMP, in place, without
    rewriting the packet (preserves all other tags + pixels). Returns a status
    string ("injected" / "already-present"). Raises on unexpected XMP layout."""
    with open(path, "rb") as f:
        data = f.read()
    if b"<Camera:BandName>" in data:
        return "already-present"

    ci = data.find(b"</rdf:Description>")
    ei = data.find(b"<?xpacket end")
    if ci < 0 or ei < 0 or not (ci < ei):
        raise RuntimeError("unexpected XMP layout in %s" % path)

    insert = ("\n   <Camera:BandName>%s</Camera:BandName>" % band).encode("utf8")
    n = len(insert)

    # Trailing whitespace run immediately before <?xpacket end (safe to shrink).
    j = ei
    while j > 0 and data[j - 1] in (0x20, 0x09, 0x0a, 0x0d):
        j -= 1
    if ei - j < n:
        raise RuntimeError("not enough XMP padding to compensate (%d < %d)" % (ei - j, n))

    new = data[:ci] + insert + data[ci:ei - n] + data[ei:]
    if len(new) != len(data):
        raise RuntimeError("length changed (%d -> %d)" % (len(data), len(new)))
    with open(path, "wb") as f:
        f.write(new)
    return "injected"

"""N3AnimControl — animation metadata (named animation → frame ranges).

format: each entry is a Chaos Expansion encrypted blob.
Encryption: DES-like Feistel network only (Layer 1), no XOR stream (Layer 2).
Uses the same CHAOS_KEY / S-boxes as TBL files.

Decrypted entry layout:
  [uint16]  nL (version/padding marker, varies: 2,3,5,9)
  [float]   unknown0 (constant per file, purpose unclear)
  [float]   frmStart
  [float]   frmEnd
  [float]   frmPerSec (game FPS for frame conversion, varies per clip)
  [float]   frmPlugTraceStart
  [float]   frmPlugTraceEnd
  [float]   frmSound0
  [float]   frmSound1
  [float]   frmBlend
  [float]   timeBlend
  [int32]   blendFlags
  [float]   frmStrike0
  [float]   frmStrike1
  [int32]   nameLen
  [char[]]  name
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter
from ..tbl.reader import CHAOS_HEADER, CHAOS_KEY, _decode_layer1


@dataclass
class AnimData:
    """Single named animation definition."""
    name: str = ""
    frame_start: float = 0.0
    frame_end: float = 0.0
    fps: float = 30.0
    plug_trace_start: float = 0.0
    plug_trace_end: float = 0.0
    sound0_frame: float = 0.0
    sound1_frame: float = 0.0
    blend_time: float = 0.25
    blend_flags: int = 0
    strike0_frame: float = 0.0
    strike1_frame: float = 0.0

    @property
    def duration(self) -> float:
        return (self.frame_end - self.frame_start) / self.fps if self.fps > 0 else 0

    @classmethod
    def from_encrypted_blob(cls, blob: bytes) -> "AnimData":
        """Decrypt and parse a Chaos Expansion encrypted animation entry."""
        buf = bytearray(blob)
        real_len = struct.unpack_from(">I", buf, 16)[0]
        decrypted = bytes(_decode_layer1(CHAOS_KEY, buf, len(buf))[:real_len])

        # uint16 nL, then 12 32-bit words matching the runtime __AnimData:
        #   unk, start, end, fps, traceStart, traceEnd, sound0, sound1,
        #   blendTime, blendFlags(int), strike0, strike1
        o = 2
        _unk0, fs, fe, fps, pts, pte, s0, s1, bt = struct.unpack_from("<9f", decrypted, o)
        o += 36
        bf = struct.unpack_from("<i", decrypted, o)[0]
        o += 4
        st0, st1 = struct.unpack_from("<ff", decrypted, o)
        o += 8

        # Read name
        name = ""
        if o + 4 <= len(decrypted):
            name_len = struct.unpack_from("<i", decrypted, o)[0]
            o += 4
            if 0 < name_len < 200 and o + name_len <= len(decrypted):
                name = decrypted[o : o + name_len].decode("ascii", errors="replace")

        return cls(
            name=name,
            frame_start=fs,
            frame_end=fe,
            fps=fps,
            plug_trace_start=pts,
            plug_trace_end=pte,
            sound0_frame=s0,
            sound1_frame=s1,
            blend_time=bt,
            blend_flags=bf,
            strike0_frame=st0,
            strike1_frame=st1,
        )

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "AnimData":
        """Read an animation entry (auto-detects encrypted vs plain)."""
        blob_size = reader.read_int32()
        blob_start = reader.tell()

        # Check if this blob is Chaos encrypted
        if blob_size >= 20:
            header_peek = reader.peek_bytes(16)
            if header_peek == CHAOS_HEADER:
                blob_data = reader.read_bytes(blob_size)
                return cls.from_encrypted_blob(blob_data)

        # Plain (non-encrypted) fallback — original format
        fs = reader.read_float()
        fe = reader.read_float()
        fps = reader.read_float()
        pts = reader.read_float()
        pte = reader.read_float()
        s0 = reader.read_float()
        s1 = reader.read_float()
        bt = reader.read_float()
        bf = reader.read_int32()
        st0 = reader.read_float()
        st1 = reader.read_float()
        name = reader.read_n3_name()

        # Skip any remaining bytes in the blob
        consumed = reader.tell() - blob_start
        if consumed < blob_size:
            reader.skip(blob_size - consumed)

        return cls(
            name=name,
            frame_start=fs,
            frame_end=fe,
            fps=fps,
            plug_trace_start=pts,
            plug_trace_end=pte,
            sound0_frame=s0,
            sound1_frame=s1,
            blend_time=bt,
            blend_flags=bf,
            strike0_frame=st0,
            strike1_frame=st1,
        )

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_int32(0)  # legacy
        w.write_float(self.frame_start)
        w.write_float(self.frame_end)
        w.write_float(self.fps)
        w.write_float(self.plug_trace_start)
        w.write_float(self.plug_trace_end)
        w.write_float(self.sound0_frame)
        w.write_float(self.sound1_frame)
        w.write_float(self.blend_time)
        w.write_int32(self.blend_flags)
        w.write_float(self.strike0_frame)
        w.write_float(self.strike1_frame)
        w.write_n3_name(self.name)
        return w.getvalue()


@dataclass
class N3AnimControl:
    """Animation control — maps named animations to frame ranges."""
    animations: list[AnimData] = field(default_factory=list)

    def find(self, name: str) -> AnimData | None:
        for a in self.animations:
            if a.name.lower() == name.lower():
                return a
        return None

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3AnimControl":
        count = reader.read_int32()
        return cls(animations=[AnimData.from_reader(reader) for _ in range(count)])

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_int32(len(self.animations))
        for anim in self.animations:
            w.write_bytes(anim.to_bytes())
        return w.getvalue()


def read_anim(path: str | Path) -> N3AnimControl:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3AnimControl.from_reader(BinaryReader(data))


def write_anim(anim: N3AnimControl, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(anim.to_bytes())

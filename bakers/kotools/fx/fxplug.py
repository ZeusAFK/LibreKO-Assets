"""N3FXPlug — Visual effects plugin."""

from dataclasses import dataclass, field
from pathlib import Path

from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter


@dataclass
class N3FXPlugPart:
    """Single FX plug part.

    Layout, with the byte offset of each field:
    Fields: name, bundle path, ref index (+100), offset pos (+104), offset dir (+116),
    then TWO trailing floats — ``scale`` (+128) and ``tail`` (+132).

    ``scale`` is not cosmetic: retail assigns it straight into the spawned bundle at +328
    (), and the bundle multiplies every part's drawn SIZE by it — mesh scale in
    and billboard quad corner offsets. It ranges down to 0.07 in the
    shipped data (191 of 634 parts are not 1.0).

    These two floats used to be missing here. Because parts are read sequentially, that left every
    part after the first in a multi-part plug misaligned by 8 bytes — and 132 of the 267 shipped
    plugs have more than one part, so most of them decoded to garbage and were dropped entirely.
    """
    name: str = ""
    bundle_path: str = ""
    ref_index: int = 0
    offset_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    offset_dir: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: float = 1.0
    tail: float = 0.0

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3FXPlugPart":
        name = reader.read_n3_name()
        bundle_path = reader.read_n3_path()
        ref_index = reader.read_int32()
        offset_pos = reader.read_float3()
        offset_dir = reader.read_float3()
        scale = reader.read_float()
        tail = reader.read_float()
        return cls(name=name, bundle_path=bundle_path, ref_index=ref_index,
                   offset_pos=offset_pos, offset_dir=offset_dir, scale=scale, tail=tail)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_n3_path(self.bundle_path)
        w.write_int32(self.ref_index)
        w.write_float3(*self.offset_pos)
        w.write_float3(*self.offset_dir)
        w.write_float(self.scale)
        w.write_float(self.tail)
        return w.getvalue()


@dataclass
class N3FXPlug:
    """Visual effects plugin with multiple parts."""
    name: str = ""
    parts: list[N3FXPlugPart] = field(default_factory=list)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "N3FXPlug":
        name = reader.read_n3_name()
        count = reader.read_int32()
        parts = [N3FXPlugPart.from_reader(reader) for _ in range(count)]
        return cls(name=name, parts=parts)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_n3_name(self.name)
        w.write_int32(len(self.parts))
        for p in self.parts:
            w.write_bytes(p.to_bytes())
        return w.getvalue()


def read_fxplug(path: str | Path) -> N3FXPlug:
    with open(str(path), "rb") as f:
        data = f.read()
    return N3FXPlug.from_reader(BinaryReader(data))


def write_fxplug(fxplug: N3FXPlug, path: str | Path):
    with open(str(path), "wb") as f:
        f.write(fxplug.to_bytes())

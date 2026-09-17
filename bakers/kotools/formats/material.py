"""D3DMATERIAL9 + KO extension (__Material struct, 92 bytes)."""

import math
from enum import IntFlag
from dataclasses import dataclass, field
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter


class RenderFlag(IntFlag):
    """Render flag bitmask (uint32) — controls material rendering behavior."""
    ALPHA_BLENDING = 0x001   # Enable alpha blending
    NO_FOG         = 0x002   # Disable fog
    DOUBLE_SIDED   = 0x004   # Two-sided rendering (no backface cull)
    BILLBOARD_Y    = 0x008   # Y-axis billboard
    POINT_SAMPLING = 0x010   # Point texture sampling (no bilinear)
    WINDY          = 0x020   # Wind swaying effect
    NO_LIGHT       = 0x040   # Fully self-lit (emission-only)
    DIFFUSE_ALPHA  = 0x080   # Use diffuse alpha channel
    NO_Z_WRITE     = 0x100   # Disable depth write
    UV_CLAMP       = 0x200   # Clamp UV coordinates
    NO_Z_BUFFER    = 0x400   # Disable depth test


@dataclass
class ColorValue:
    """D3DCOLORVALUE (16 bytes)."""
    r: float = 0.0
    g: float = 0.0
    b: float = 0.0
    a: float = 1.0

    @classmethod
    def read(cls, reader: BinaryReader) -> "ColorValue":
        r, g, b, a = reader.read_float4()
        return cls(r, g, b, a)

    def write(self, writer: BinaryWriter):
        writer.write_float4(self.r, self.g, self.b, self.a)


@dataclass
class Material:
    """__Material struct (92 bytes): D3DMATERIAL9 (68 bytes) + KO extension (24 bytes).

    render_flags is a RenderFlag bitmask (NOT a bool — this is a common bug source).
    """
    diffuse: ColorValue = field(default_factory=ColorValue)
    ambient: ColorValue = field(default_factory=ColorValue)
    specular: ColorValue = field(default_factory=ColorValue)
    emissive: ColorValue = field(default_factory=ColorValue)
    power: float = 0.0
    # KO extension
    color_op: int = 0
    color_arg1: int = 0
    color_arg2: int = 0
    render_flags: int = 0
    src_blend: int = 0
    dest_blend: int = 0

    @classmethod
    def read(cls, reader: BinaryReader) -> "Material":
        return cls(
            diffuse=ColorValue.read(reader),
            ambient=ColorValue.read(reader),
            specular=ColorValue.read(reader),
            emissive=ColorValue.read(reader),
            power=reader.read_float(),
            color_op=reader.read_dword(),
            color_arg1=reader.read_dword(),
            color_arg2=reader.read_dword(),
            render_flags=reader.read_dword(),
            src_blend=reader.read_dword(),
            dest_blend=reader.read_dword(),
        )

    def write(self, writer: BinaryWriter):
        self.diffuse.write(writer)
        self.ambient.write(writer)
        self.specular.write(writer)
        self.emissive.write(writer)
        writer.write_float(self.power)
        writer.write_dword(self.color_op)
        writer.write_dword(self.color_arg1)
        writer.write_dword(self.color_arg2)
        writer.write_dword(self.render_flags)
        writer.write_dword(self.src_blend)
        writer.write_dword(self.dest_blend)

    # ── Flag accessors ──────────────────────────────────────────────────

    def has_flag(self, flag: RenderFlag) -> bool:
        return bool(self.render_flags & flag)

    @property
    def alpha_blend(self) -> bool:
        return self.has_flag(RenderFlag.ALPHA_BLENDING)

    @property
    def double_side(self) -> bool:
        return self.has_flag(RenderFlag.DOUBLE_SIDED)

    @property
    def no_light(self) -> bool:
        return self.has_flag(RenderFlag.NO_LIGHT)

    @property
    def diffuse_alpha(self) -> bool:
        return self.has_flag(RenderFlag.DIFFUSE_ALPHA)

    @property
    def uv_clamp(self) -> bool:
        return self.has_flag(RenderFlag.UV_CLAMP)

    @property
    def is_transparent(self) -> bool:
        """True if either ALPHA_BLENDING or DIFFUSE_ALPHA is set."""
        return self.has_flag(RenderFlag.ALPHA_BLENDING | RenderFlag.DIFFUSE_ALPHA)

    # ── PBR conversion helpers ──────────────────────────────────────────

    @property
    def roughness(self) -> float:
        """Convert D3D specular power to PBR roughness (0..1)."""
        if self.power <= 0:
            return 1.0
        return math.sqrt(2.0 / (self.power + 2.0))

    @staticmethod
    def roughness_to_power(roughness: float) -> float:
        """Convert PBR roughness back to D3D specular power."""
        r = max(roughness, 0.01)
        return (2.0 / (r * r)) - 2.0

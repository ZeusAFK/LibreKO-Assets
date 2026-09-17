"""Direct3D pixel format enum used by Knight Online NTF textures."""

from enum import IntEnum


class D3DFormat(IntEnum):
    """D3DFORMAT values found in KO texture files."""
    DXT1 = 0x31545844
    DXT2 = 0x32545844
    DXT3 = 0x33545844
    DXT4 = 0x34545844
    DXT5 = 0x35545844
    A8R8G8B8 = 21
    X8R8G8B8 = 22
    A1R5G5B5 = 25
    A4R4G4B4 = 26

    @classmethod
    def from_value(cls, val: int) -> "D3DFormat":
        for member in cls:
            if member.value == val:
                return member
        raise ValueError(f"Unknown D3DFORMAT: {val} (0x{val:08x})")

    @property
    def is_compressed(self) -> bool:
        return self in (D3DFormat.DXT1, D3DFormat.DXT2, D3DFormat.DXT3,
                        D3DFormat.DXT4, D3DFormat.DXT5)

    @property
    def block_size(self) -> int:
        if self in (D3DFormat.DXT1, D3DFormat.DXT2):
            return 8
        elif self in (D3DFormat.DXT3, D3DFormat.DXT4, D3DFormat.DXT5):
            return 16
        raise ValueError(f"{self.name} is not block-compressed")

    @property
    def bits_per_pixel(self) -> int:
        if self in (D3DFormat.A8R8G8B8, D3DFormat.X8R8G8B8):
            return 32
        elif self in (D3DFormat.A1R5G5B5, D3DFormat.A4R4G4B4):
            return 16
        raise ValueError(f"{self.name} is block-compressed, use block_size instead")

    def compute_data_size(self, width: int, height: int) -> int:
        if self.is_compressed:
            bx = max(1, (width + 3) // 4)
            by = max(1, (height + 3) // 4)
            return bx * by * self.block_size
        return width * height * (self.bits_per_pixel // 8)

    def compute_mipchain_size(self, width: int, height: int) -> int:
        total = 0
        w, h = width, height
        while True:
            total += self.compute_data_size(w, h)
            if w == 1 and h == 1:
                break
            w = max(1, w // 2)
            h = max(1, h // 2)
        return total

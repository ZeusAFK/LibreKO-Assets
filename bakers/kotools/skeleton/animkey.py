"""N3AnimKey — keyframe data for position, rotation, and scale.

Binary layout:
  [int32]  count (0 = no keys)
  if count > 0:
    [int32]  type (0 = KEY_VECTOR3, 1 = KEY_QUATERNION)
    [float]  samplingRate (typically 30.0 FPS)
    if type == KEY_VECTOR3:
      [float3[count]]   — vec3 keyframes (12 bytes each)
    if type == KEY_QUATERNION:
      [float4[count]]   — quaternion keyframes (16 bytes each: x, y, z, w)
"""

from dataclasses import dataclass, field
from enum import IntEnum
from ..io.binary_reader import BinaryReader
from ..io.binary_writer import BinaryWriter


class KeyType(IntEnum):
    VECTOR3 = 0
    QUATERNION = 1


@dataclass
class AnimKey:
    """Animation keyframe channel."""
    key_type: KeyType = KeyType.VECTOR3
    sampling_rate: float = 30.0
    data: list = field(default_factory=list)  # list of (x,y,z) or (x,y,z,w) tuples

    @property
    def count(self) -> int:
        return len(self.data)

    @classmethod
    def from_reader(cls, reader: BinaryReader) -> "AnimKey":
        count = reader.read_int32()
        if count <= 0:
            return cls()
        type_raw = reader.read_int32()
        try:
            key_type = KeyType(type_raw)
        except ValueError:
            key_type = KeyType.VECTOR3  # fallback

        sampling_rate = reader.read_float()
        data = []
        if key_type == KeyType.QUATERNION:
            data = [reader.read_float4() for _ in range(count)]
        else:
            data = [reader.read_float3() for _ in range(count)]
        return cls(key_type=key_type, sampling_rate=sampling_rate, data=data)

    def to_bytes(self) -> bytes:
        w = BinaryWriter()
        w.write_int32(self.count)
        if self.count > 0:
            w.write_int32(self.key_type.value)
            w.write_float(self.sampling_rate)
            for kf in self.data:
                if self.key_type == KeyType.VECTOR3:
                    w.write_float3(*kf)
                else:
                    w.write_float4(*kf)
        return w.getvalue()

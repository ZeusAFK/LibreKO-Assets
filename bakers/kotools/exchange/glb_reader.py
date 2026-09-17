"""Read geometry + skin data directly from a GLB file.

Used by the export pipeline to recover bone weights that trimesh doesn't surface.
When a GLB was produced by this library's own GLB exporter (add_skinned_mesh) and edited
in Blender, JOINTS_0 / WEIGHTS_0 round-trip correctly and hold KO-space bone
indices. Reading them here lets us skip the lossy nearest-neighbor bone transfer.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from pygltflib import GLTF2


# glTF component type constants (mirrors pygltflib)
_FLOAT = 5126
_UNSIGNED_BYTE = 5121
_UNSIGNED_SHORT = 5123
_UNSIGNED_INT = 5125

_COMPONENT_SIZE = {
    _FLOAT: 4,
    _UNSIGNED_BYTE: 1,
    _UNSIGNED_SHORT: 2,
    _UNSIGNED_INT: 4,
}

_TYPE_COUNT = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
}

_NP_DTYPE = {
    _FLOAT: np.float32,
    _UNSIGNED_BYTE: np.uint8,
    _UNSIGNED_SHORT: np.uint16,
    _UNSIGNED_INT: np.uint32,
}


@dataclass
class GLBPrimitive:
    """Raw per-vertex + index arrays for a single glTF primitive.

    All arrays are indexed by vertex. Missing attributes are None.
    joints/weights hold up to 4 influences per vertex as (N, 4) arrays.
    """
    positions: np.ndarray                         # (N, 3) float32
    normals: Optional[np.ndarray]                 # (N, 3) float32 or None
    uvs: Optional[np.ndarray]                     # (N, 2) float32 or None
    indices: np.ndarray                           # (M,) uint32
    joints: Optional[np.ndarray] = None           # (N, 4) uint16 or None
    weights: Optional[np.ndarray] = None          # (N, 4) float32 or None


def _get_buffer_bytes(gltf: GLTF2, glb_path: Path) -> bytes:
    """Return the concatenated binary buffer backing the glTF file.

    For a .glb the data is embedded; for a .gltf with external .bin the file
    is sideloaded. pygltflib handles both via binary_blob() after load().
    """
    data = gltf.binary_blob()
    if data is None:
        # Fallback: read external buffer file if any
        if gltf.buffers and gltf.buffers[0].uri:
            buf_path = Path(glb_path).parent / gltf.buffers[0].uri
            data = buf_path.read_bytes()
    if data is None:
        raise ValueError(f"No binary buffer found in GLB: {glb_path}")
    return data


def _read_accessor(gltf: GLTF2, blob: bytes, accessor_idx: int) -> np.ndarray:
    """Decode one accessor into a numpy array shaped (count, components)."""
    acc = gltf.accessors[accessor_idx]
    bv = gltf.bufferViews[acc.bufferView]

    comp_size = _COMPONENT_SIZE[acc.componentType]
    comp_count = _TYPE_COUNT[acc.type]
    stride = bv.byteStride or (comp_size * comp_count)
    dtype = _NP_DTYPE[acc.componentType]

    offset = (bv.byteOffset or 0) + (acc.byteOffset or 0)

    if stride == comp_size * comp_count:
        # Tightly packed — fast path
        length = acc.count * comp_count
        raw = np.frombuffer(blob, dtype=dtype, count=length, offset=offset)
        arr = raw.reshape(acc.count, comp_count)
    else:
        # Interleaved — slow path, read each element
        arr = np.empty((acc.count, comp_count), dtype=dtype)
        for i in range(acc.count):
            elem_off = offset + i * stride
            arr[i] = np.frombuffer(blob, dtype=dtype, count=comp_count, offset=elem_off)

    # Collapse SCALAR to 1D
    if acc.type == "SCALAR":
        arr = arr.reshape(-1)
    return arr


def read_glb_primitive(glb_path: str | Path) -> GLBPrimitive:
    """Load the first mesh primitive of a GLB as raw numpy arrays.

    Extracts POSITION, NORMAL, TEXCOORD_0, JOINTS_0, WEIGHTS_0, and the index
    buffer. JOINTS_0 / WEIGHTS_0 are returned as None if absent.
    """
    glb_path = Path(glb_path)
    gltf = GLTF2().load(str(glb_path))
    blob = _get_buffer_bytes(gltf, glb_path)

    if not gltf.meshes or not gltf.meshes[0].primitives:
        raise ValueError(f"GLB has no mesh primitives: {glb_path}")

    prim = gltf.meshes[0].primitives[0]
    attrs = prim.attributes

    if attrs.POSITION is None:
        raise ValueError(f"GLB primitive has no POSITION accessor: {glb_path}")

    positions = _read_accessor(gltf, blob, attrs.POSITION).astype(np.float32)

    normals = None
    if attrs.NORMAL is not None:
        normals = _read_accessor(gltf, blob, attrs.NORMAL).astype(np.float32)

    uvs = None
    if attrs.TEXCOORD_0 is not None:
        uvs = _read_accessor(gltf, blob, attrs.TEXCOORD_0).astype(np.float32)

    joints = None
    if attrs.JOINTS_0 is not None:
        joints = _read_accessor(gltf, blob, attrs.JOINTS_0).astype(np.uint16)

    weights = None
    if attrs.WEIGHTS_0 is not None:
        weights = _read_accessor(gltf, blob, attrs.WEIGHTS_0).astype(np.float32)

    if prim.indices is None:
        # Non-indexed — synthesize [0, 1, 2, ...]
        indices = np.arange(len(positions), dtype=np.uint32)
    else:
        indices = _read_accessor(gltf, blob, prim.indices).astype(np.uint32)

    return GLBPrimitive(
        positions=positions, normals=normals, uvs=uvs, indices=indices,
        joints=joints, weights=weights,
    )


def has_skin_data(glb_path: str | Path) -> bool:
    """Quick probe: does the GLB contain JOINTS_0 and WEIGHTS_0 on any primitive?"""
    try:
        gltf = GLTF2().load(str(glb_path))
        for mesh in (gltf.meshes or []):
            for prim in (mesh.primitives or []):
                attrs = prim.attributes
                if attrs.JOINTS_0 is not None and attrs.WEIGHTS_0 is not None:
                    return True
    except Exception:
        return False
    return False

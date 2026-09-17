"""Export Knight Online assets to glTF 2.0 format.

Supports:
- Single mesh → glTF with material
- Character assembly → glTF with skeleton, skinned meshes, textures, animations
- Static shape → glTF with multiple mesh nodes
"""

import base64
import hashlib
import io
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pygltflib
from pygltflib import (
    GLTF2, Scene, Node, Mesh, Primitive, Accessor, BufferView, Buffer,
    Material as GLTFMaterial, PbrMetallicRoughness, TextureInfo, Texture,
    Image as GLTFImage, Sampler, Skin, Animation, AnimationChannel,
    AnimationChannelTarget, AnimationSampler,
    FLOAT, UNSIGNED_SHORT, UNSIGNED_BYTE, UNSIGNED_INT,
    SCALAR, VEC2, VEC3, VEC4, MAT4,
    ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER,
    LINEAR,
)

from ..mesh.pmesh import N3PMesh, Vertex
from ..mesh.skin import N3Skin, SkinVertex
from ..skeleton.joint import N3Joint
from ..skeleton.animkey import AnimKey, KeyType
from ..animation.animcontrol import N3AnimControl
from ..formats.material import Material
from ..ntf.texture import KOTexture

ALPHA_CUTOFF = 0.5
_OPAQUE_AT = int(ALPHA_CUTOFF * 255)
# Measured over the shipped NPC set: genuine cut-outs keep >= 0.25 of their drawn surface
# through the cutoff, blended overlays keep <= 0.05, and nothing lands in between.
_BLEND_BELOW = 0.10


def choose_alpha_mode(img, uvs=None, indices=None) -> str:
    """Pick a glTF alphaMode for a KO texture.

    glTF defaults to OPAQUE when the field is absent, which draws KO's hair / fur / foliage
    cut-outs as solid polygon. But KO also ships alpha-*blended* overlays (glows, ghosts,
    translucent banners) whose alpha is a blend factor, never a mask — alpha-testing those
    erases them completely. The N3 readers do not parse KO's render flags, so the two are
    told apart by whether anything would actually survive the cutoff.

    Pass the mesh's UVs (and indices) when known: several models point a blended part at the
    transparent region of an atlas whose *other* regions are opaque, so the texture as a whole
    looks like an ordinary cut-out and only the UVs reveal it. Sample triangle interiors, not
    bare vertices — KO's UV corners routinely sit on the transparent border of an island, so
    vertex-only sampling calls every cut-out blended.
    """
    if img.mode != "RGBA":
        return "OPAQUE"
    lo, hi = img.getextrema()[3]
    if lo >= 250:
        return "OPAQUE"
    if hi < _OPAQUE_AT:
        return "BLEND"

    if uvs is not None and len(uvs):
        try:
            import numpy as np
            alpha = np.asarray(img.getchannel("A"))
            h, w = alpha.shape
            uv = np.asarray(uvs, dtype=np.float32).reshape(-1, 2)
            if indices is not None and len(indices) >= 3:
                idx = np.asarray(indices, dtype=np.int64).ravel()
                idx = idx[: (len(idx) // 3) * 3].clip(0, len(uv) - 1)
                tri = uv[idx].reshape(-1, 3, 2)
                uv = np.concatenate([tri.mean(1),
                                     (tri[:, 0] + tri[:, 1]) / 2,
                                     (tri[:, 1] + tri[:, 2]) / 2,
                                     (tri[:, 0] + tri[:, 2]) / 2])
            # Sampler wrap defaults to REPEAT, and KO routinely authors tiled or negative UVs
            # (whole foliage sheets sit in [-1, 0]). Clamping instead of wrapping pins every
            # sample to column 0 and reports the part as fully transparent.
            uv = np.mod(uv, 1.0)
            x = np.minimum((uv[:, 0] * w).astype(int), w - 1)
            y = np.minimum((uv[:, 1] * h).astype(int), h - 1)
            if float((alpha[y, x] >= _OPAQUE_AT).mean()) < _BLEND_BELOW:
                return "BLEND"
        except Exception:
            pass
    return "MASK"


class GLTFBuilder:
    """Incrementally builds a glTF 2.0 file."""

    def __init__(self, texture_max_size: int = 256,
                 texture_dir: Optional[Path] = None,
                 texture_uri_prefix: str = ""):
        self.gltf = GLTF2(
            scene=0,
            scenes=[Scene(nodes=[])],
            nodes=[],
            meshes=[],
            accessors=[],
            bufferViews=[],
            buffers=[],
            materials=[],
            textures=[],
            images=[],
            samplers=[],
            skins=[],
            animations=[],
        )
        self._bin = bytearray()
        self.texture_max_size = texture_max_size
        self.texture_dir = Path(texture_dir) if texture_dir is not None else None
        self.texture_uri_prefix = texture_uri_prefix
        self._texture_by_png = {}

    def _append_data(self, data: bytes, target: Optional[int] = None) -> int:
        """Append raw data to the binary buffer. Returns bufferView index."""
        # Align to 4 bytes
        while len(self._bin) % 4 != 0:
            self._bin.append(0)
        offset = len(self._bin)
        self._bin.extend(data)
        bv = BufferView(buffer=0, byteOffset=offset, byteLength=len(data))
        if target is not None:
            bv.target = target
        idx = len(self.gltf.bufferViews)
        self.gltf.bufferViews.append(bv)
        return idx

    def _add_accessor(self, bv_idx: int, component_type: int, count: int,
                      type_: str, min_vals=None, max_vals=None) -> int:
        """Add an accessor pointing to a bufferView. Returns accessor index."""
        acc = Accessor(
            bufferView=bv_idx,
            componentType=component_type,
            count=count,
            type=type_,
        )
        if min_vals is not None:
            acc.min = min_vals
        if max_vals is not None:
            acc.max = max_vals
        idx = len(self.gltf.accessors)
        self.gltf.accessors.append(acc)
        return idx

    def _texture_for_png(self, png_data: bytes) -> int:
        """Index of a glTF texture for these exact PNG bytes, creating it once.

        With texture_dir set the image is written there under its content hash and referenced by a
        relative URI, so models that share a texture share ONE imported file instead of each carrying
        its own extracted copy.
        """
        digest = hashlib.sha1(png_data).hexdigest()[:16]
        cached = self._texture_by_png.get(digest)
        if cached is not None:
            return cached

        img_idx = len(self.gltf.images)
        if self.texture_dir is not None:
            self.texture_dir.mkdir(parents=True, exist_ok=True)
            shared = self.texture_dir / f"{digest}.png"
            if not shared.exists():
                shared.write_bytes(png_data)
            self.gltf.images.append(
                GLTFImage(uri=f"{self.texture_uri_prefix}{digest}.png", mimeType="image/png"))
        else:
            self.gltf.images.append(
                GLTFImage(bufferView=self._append_data(png_data), mimeType="image/png"))

        tex_idx = len(self.gltf.textures)
        self.gltf.textures.append(Texture(sampler=0, source=img_idx))
        self._texture_by_png[digest] = tex_idx
        return tex_idx

    def add_texture(self, texture: KOTexture, name: str = "",
                    alpha_mode: Optional[str] = None) -> int:
        """Add a KO texture as a PNG embedded in the binary buffer. Returns material index.

        alpha_mode overrides the image-only guess; pass one from choose_alpha_mode() when the
        mesh that uses this texture is known, since UVs are what separate a cut-out from a
        blended overlay.
        """
        img = texture.to_pil_image()
        # Resize large textures to keep glTF size reasonable
        max_dim = self.texture_max_size
        if max_dim > 0 and (img.width > max_dim or img.height > max_dim):
            ratio = min(max_dim / img.width, max_dim / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, getattr(__import__('PIL.Image', fromlist=['Image']).Image, 'LANCZOS', 1))

        if alpha_mode is None:
            alpha_mode = choose_alpha_mode(img)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_data = buf.getvalue()

        if not self.gltf.samplers:
            self.gltf.samplers.append(Sampler(magFilter=9729, minFilter=9987))  # LINEAR, LINEAR_MIPMAP_LINEAR

        tex_idx = self._texture_for_png(png_data)

        # Create PBR material
        mat = GLTFMaterial(
            name=name or f"material_{tex_idx}",
            pbrMetallicRoughness=PbrMetallicRoughness(
                baseColorTexture=TextureInfo(index=tex_idx),
                metallicFactor=0.0,
                roughnessFactor=0.8,
            ),
            alphaMode=alpha_mode,
            alphaCutoff=ALPHA_CUTOFF if alpha_mode == "MASK" else None,
        )
        mat_idx = len(self.gltf.materials)
        self.gltf.materials.append(mat)
        return mat_idx

    def add_pmesh(self, mesh: N3PMesh, material_idx: int = -1, name: str = "") -> int:
        """Add an N3PMesh as a glTF mesh. Returns node index."""
        verts = mesh.vertices

        # Pack positions
        pos_data = bytearray()
        min_pos = [float("inf")] * 3
        max_pos = [float("-inf")] * 3
        for v in verts:
            pos_data.extend(struct.pack("<fff", v.x, v.y, v.z))
            min_pos[0] = min(min_pos[0], v.x)
            min_pos[1] = min(min_pos[1], v.y)
            min_pos[2] = min(min_pos[2], v.z)
            max_pos[0] = max(max_pos[0], v.x)
            max_pos[1] = max(max_pos[1], v.y)
            max_pos[2] = max(max_pos[2], v.z)

        # Pack normals (normalize to unit length for glTF compliance)
        norm_data = bytearray()
        for v in verts:
            length = math.sqrt(v.nx * v.nx + v.ny * v.ny + v.nz * v.nz)
            if length > 0:
                nx, ny, nz = v.nx / length, v.ny / length, v.nz / length
            else:
                nx, ny, nz = 0.0, 1.0, 0.0
            norm_data.extend(struct.pack("<fff", nx, ny, nz))

        # Pack UVs (flip V for glTF convention)
        uv_data = bytearray()
        for v in verts:
            uv_data.extend(struct.pack("<ff", v.tu, v.tv))

        # Pack indices
        idx_data = bytearray()
        for i in mesh.indices:
            idx_data.extend(struct.pack("<H", i))

        pos_bv = self._append_data(bytes(pos_data), ARRAY_BUFFER)
        norm_bv = self._append_data(bytes(norm_data), ARRAY_BUFFER)
        uv_bv = self._append_data(bytes(uv_data), ARRAY_BUFFER)
        idx_bv = self._append_data(bytes(idx_data), ELEMENT_ARRAY_BUFFER)

        pos_acc = self._add_accessor(pos_bv, FLOAT, len(verts), VEC3, min_pos, max_pos)
        norm_acc = self._add_accessor(norm_bv, FLOAT, len(verts), VEC3)
        uv_acc = self._add_accessor(uv_bv, FLOAT, len(verts), VEC2)
        idx_acc = self._add_accessor(idx_bv, UNSIGNED_SHORT, len(mesh.indices), SCALAR,
                                     [0], [max(mesh.indices) if mesh.indices else 0])

        prim = Primitive(
            attributes=pygltflib.Attributes(
                POSITION=pos_acc,
                NORMAL=norm_acc,
                TEXCOORD_0=uv_acc,
            ),
            indices=idx_acc,
        )
        if material_idx >= 0:
            prim.material = material_idx

        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(Mesh(name=name or mesh.name, primitives=[prim]))

        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(Node(name=name or mesh.name, mesh=mesh_idx))
        self.gltf.scenes[0].nodes.append(node_idx)
        return node_idx

    def add_meshdata(self, positions, normals, uvs, indices, material_idx: int = -1, name: str = "") -> int:
        """Add a mesh from raw numpy arrays (positions, normals, uvs, indices).
        Used for enhanced/subdivided meshes. Returns node index."""
        import numpy as np

        n_verts = len(positions)
        pos_data = positions.astype(np.float32).tobytes()
        min_pos = positions.min(axis=0).tolist()
        max_pos = positions.max(axis=0).tolist()

        # Normalize normals
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths = np.maximum(lengths, 1e-8)
        safe_normals = (normals / lengths).astype(np.float32)
        norm_data = safe_normals.tobytes()

        uv_data = uvs.astype(np.float32).tobytes()

        # Use uint32 if vertex count exceeds uint16 range
        n_indices = len(indices)
        if n_verts > 65535:
            idx_data = indices.astype(np.uint32).tobytes()
            idx_type = pygltflib.UNSIGNED_INT
        else:
            idx_data = indices.astype(np.uint16).tobytes()
            idx_type = UNSIGNED_SHORT

        pos_bv = self._append_data(pos_data, ARRAY_BUFFER)
        norm_bv = self._append_data(norm_data, ARRAY_BUFFER)
        uv_bv = self._append_data(uv_data, ARRAY_BUFFER)
        idx_bv = self._append_data(idx_data, ELEMENT_ARRAY_BUFFER)

        pos_acc = self._add_accessor(pos_bv, FLOAT, n_verts, VEC3, min_pos, max_pos)
        norm_acc = self._add_accessor(norm_bv, FLOAT, n_verts, VEC3)
        uv_acc = self._add_accessor(uv_bv, FLOAT, n_verts, VEC2)
        idx_acc = self._add_accessor(idx_bv, idx_type, n_indices, SCALAR,
                                     [0], [int(indices.max()) if len(indices) > 0 else 0])

        prim = Primitive(
            attributes=pygltflib.Attributes(POSITION=pos_acc, NORMAL=norm_acc, TEXCOORD_0=uv_acc),
            indices=idx_acc,
        )
        if material_idx >= 0:
            prim.material = material_idx

        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(Mesh(name=name, primitives=[prim]))
        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(Node(name=name, mesh=mesh_idx))
        self.gltf.scenes[0].nodes.append(node_idx)
        return node_idx

    def add_skinned_meshdata(
        self, positions, normals, uvs, indices,
        joint_indices_list, joint_weights_list,
        skin_idx: int, joint_node_indices: list[int],
        material_idx: int = -1, name: str = "",
    ) -> int:
        """Add a skinned mesh from raw numpy arrays + bone weight lists.
        Used for enhanced/subdivided skinned meshes. Returns node index."""
        import numpy as np

        n_verts = len(positions)
        pos_data = positions.astype(np.float32).tobytes()
        min_pos = positions.min(axis=0).tolist()
        max_pos = positions.max(axis=0).tolist()

        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths = np.maximum(lengths, 1e-8)
        safe_normals = (normals / lengths).astype(np.float32)
        norm_data = safe_normals.tobytes()
        uv_data = uvs.astype(np.float32).tobytes()

        # Pack bone weights — pad to 4 influences, normalize
        joints_data = bytearray()
        weights_data = bytearray()
        for i in range(n_verts):
            j_indices = list(joint_indices_list[i][:4]) if i < len(joint_indices_list) else [0]
            w = list(joint_weights_list[i][:4]) if i < len(joint_weights_list) else [1.0]
            while len(j_indices) < 4:
                j_indices.append(0)
            while len(w) < 4:
                w.append(0.0)
            total = sum(w)
            if total > 0:
                w = [x / total for x in w]
            joints_data.extend(struct.pack("<BBBB", *[min(j, 255) for j in j_indices]))
            weights_data.extend(struct.pack("<ffff", *w))

        # Indices — uint32 if needed
        n_indices = len(indices)
        if n_verts > 65535:
            idx_data = indices.astype(np.uint32).tobytes()
            idx_type = UNSIGNED_INT
        else:
            idx_data = indices.astype(np.uint16).tobytes()
            idx_type = UNSIGNED_SHORT

        pos_bv = self._append_data(pos_data, ARRAY_BUFFER)
        norm_bv = self._append_data(norm_data, ARRAY_BUFFER)
        uv_bv = self._append_data(uv_data, ARRAY_BUFFER)
        joints_bv = self._append_data(bytes(joints_data), ARRAY_BUFFER)
        weights_bv = self._append_data(bytes(weights_data), ARRAY_BUFFER)
        idx_bv = self._append_data(idx_data, ELEMENT_ARRAY_BUFFER)

        pos_acc = self._add_accessor(pos_bv, FLOAT, n_verts, VEC3, min_pos, max_pos)
        norm_acc = self._add_accessor(norm_bv, FLOAT, n_verts, VEC3)
        uv_acc = self._add_accessor(uv_bv, FLOAT, n_verts, VEC2)
        joints_acc = self._add_accessor(joints_bv, UNSIGNED_BYTE, n_verts, VEC4)
        weights_acc = self._add_accessor(weights_bv, FLOAT, n_verts, VEC4)
        idx_acc = self._add_accessor(idx_bv, idx_type, n_indices, SCALAR,
                                     [0], [int(indices.max()) if len(indices) > 0 else 0])

        prim = Primitive(
            attributes=pygltflib.Attributes(
                POSITION=pos_acc,
                NORMAL=norm_acc,
                TEXCOORD_0=uv_acc,
                JOINTS_0=joints_acc,
                WEIGHTS_0=weights_acc,
            ),
            indices=idx_acc,
        )
        if material_idx >= 0:
            prim.material = material_idx

        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(Mesh(name=name, primitives=[prim]))
        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(Node(name=name, mesh=mesh_idx, skin=skin_idx))
        self.gltf.scenes[0].nodes.append(node_idx)
        return node_idx

    def _add_mesh_node(self, mesh: N3PMesh, material_idx: int = -1, name: str = "") -> int:
        """Add a mesh node WITHOUT adding to scene root. Returns node index."""
        verts = mesh.vertices
        pos_data = bytearray()
        norm_data = bytearray()
        uv_data = bytearray()
        idx_data = bytearray()
        min_pos = [float("inf")] * 3
        max_pos = [float("-inf")] * 3

        for v in verts:
            pos_data.extend(struct.pack("<fff", v.x, v.y, v.z))
            min_pos = [min(min_pos[j], [v.x, v.y, v.z][j]) for j in range(3)]
            max_pos = [max(max_pos[j], [v.x, v.y, v.z][j]) for j in range(3)]
            length = math.sqrt(v.nx * v.nx + v.ny * v.ny + v.nz * v.nz)
            if length > 0:
                nx, ny, nz = v.nx / length, v.ny / length, v.nz / length
            else:
                nx, ny, nz = 0.0, 1.0, 0.0
            norm_data.extend(struct.pack("<fff", nx, ny, nz))
            uv_data.extend(struct.pack("<ff", v.tu, v.tv))
        for i in mesh.indices:
            idx_data.extend(struct.pack("<H", i))

        pos_bv = self._append_data(bytes(pos_data), ARRAY_BUFFER)
        norm_bv = self._append_data(bytes(norm_data), ARRAY_BUFFER)
        uv_bv = self._append_data(bytes(uv_data), ARRAY_BUFFER)
        idx_bv = self._append_data(bytes(idx_data), ELEMENT_ARRAY_BUFFER)

        pos_acc = self._add_accessor(pos_bv, FLOAT, len(verts), VEC3, min_pos, max_pos)
        norm_acc = self._add_accessor(norm_bv, FLOAT, len(verts), VEC3)
        uv_acc = self._add_accessor(uv_bv, FLOAT, len(verts), VEC2)
        idx_acc = self._add_accessor(idx_bv, UNSIGNED_SHORT, len(mesh.indices), SCALAR,
                                     [0], [max(mesh.indices) if mesh.indices else 0])

        prim = Primitive(
            attributes=pygltflib.Attributes(POSITION=pos_acc, NORMAL=norm_acc, TEXCOORD_0=uv_acc),
            indices=idx_acc,
        )
        if material_idx >= 0:
            prim.material = material_idx

        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(Mesh(name=name or mesh.name, primitives=[prim]))
        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(Node(name=name or mesh.name, mesh=mesh_idx))
        return node_idx

    def add_skeleton(self, root_joint: N3Joint) -> tuple[int, list[int]]:
        """Add a skeleton from N3Joint tree. Returns (skin_idx, joint_node_indices)."""
        flat = []
        self._flatten_joint(root_joint, -1, flat)

        joint_node_indices = []
        for joint, parent_flat_idx in flat:
            node = Node(
                name=joint.name,
                translation=list(joint.position),
                rotation=[joint.rotation[0], joint.rotation[1],
                          joint.rotation[2], joint.rotation[3]],
                scale=list(joint.scale),
            )
            node_idx = len(self.gltf.nodes)
            self.gltf.nodes.append(node)
            joint_node_indices.append(node_idx)

        # Set up parent-child relationships
        for i, (joint, parent_flat_idx) in enumerate(flat):
            if parent_flat_idx >= 0:
                parent_node_idx = joint_node_indices[parent_flat_idx]
                if self.gltf.nodes[parent_node_idx].children is None:
                    self.gltf.nodes[parent_node_idx].children = []
                self.gltf.nodes[parent_node_idx].children.append(joint_node_indices[i])

        # Root joint added to scene
        self.gltf.scenes[0].nodes.append(joint_node_indices[0])

        # Compute inverse bind matrices
        ibm_data = bytearray()
        world_matrices = []

        def _quat_to_mat3(qx, qy, qz, qw):
            xx, yy, zz = qx*qx, qy*qy, qz*qz
            xy, xz, yz = qx*qy, qx*qz, qy*qz
            wx, wy, wz = qw*qx, qw*qy, qw*qz
            return [
                [1-2*(yy+zz), 2*(xy-wz),   2*(xz+wy)],
                [2*(xy+wz),   1-2*(xx+zz), 2*(yz-wx)],
                [2*(xz-wy),   2*(yz+wx),   1-2*(xx+yy)],
            ]

        def _mat4_from_trs(t, r, s):
            rot = _quat_to_mat3(*r)
            # OpenGL/glTF convention: translation in column 3
            return [
                [rot[0][0]*s[0], rot[0][1]*s[1], rot[0][2]*s[2], t[0]],
                [rot[1][0]*s[0], rot[1][1]*s[1], rot[1][2]*s[2], t[1]],
                [rot[2][0]*s[0], rot[2][1]*s[1], rot[2][2]*s[2], t[2]],
                [0,              0,              0,               1   ],
            ]

        def _mat4_mul(a, b):
            r = [[0]*4 for _ in range(4)]
            for i in range(4):
                for j in range(4):
                    r[i][j] = sum(a[i][k]*b[k][j] for k in range(4))
            return r

        for jnt, parent_flat_idx in flat:
            local = _mat4_from_trs(jnt.position, jnt.rotation, jnt.scale)
            if parent_flat_idx >= 0:
                # OpenGL convention: world = parent * local
                world = _mat4_mul(world_matrices[parent_flat_idx], local)
            else:
                world = local
            world_matrices.append(world)

            inv = self._invert_4x4(world)
            # glTF stores matrices column-major
            for col in range(4):
                for row in range(4):
                    ibm_data.extend(struct.pack("<f", inv[row][col]))

        ibm_bv = self._append_data(bytes(ibm_data))
        ibm_acc = self._add_accessor(ibm_bv, FLOAT, len(flat), MAT4)

        skin = Skin(
            name="skeleton",
            joints=joint_node_indices,
            inverseBindMatrices=ibm_acc,
            skeleton=joint_node_indices[0],
        )
        skin_idx = len(self.gltf.skins)
        self.gltf.skins.append(skin)

        return skin_idx, joint_node_indices

    def add_skinned_mesh(self, skin_mesh: N3Skin, skin_idx: int,
                         joint_node_indices: list[int],
                         material_idx: int = -1, name: str = "") -> int:
        """Add a skinned mesh with bone weights. Returns node index."""
        mesh_data = skin_mesh.mesh
        sv = skin_mesh.skin_vertices

        if not mesh_data.vertices or mesh_data.face_count == 0:
            return -1

        has_uvs = len(mesh_data.uvs) > 0 and len(mesh_data.uv_indices) > 0

        # Flatten: create new vertex for each unique (vertex_idx, uv_idx) pair
        pair_to_new: dict[tuple[int, int], int] = {}
        flat_vi: list[int] = []  # original vertex index for each new vertex
        flat_ui: list[int] = []  # original UV index for each new vertex
        new_indices: list[int] = []

        for fi in range(len(mesh_data.vertex_indices)):
            vi = mesh_data.vertex_indices[fi]
            ui = mesh_data.uv_indices[fi] if has_uvs and fi < len(mesh_data.uv_indices) else vi
            key = (vi, ui)
            if key not in pair_to_new:
                pair_to_new[key] = len(flat_vi)
                flat_vi.append(vi)
                flat_ui.append(ui)
            new_indices.append(pair_to_new[key])

        vc = len(flat_vi)
        pos_data = bytearray()
        norm_data = bytearray()
        uv_data = bytearray()
        joints_data = bytearray()
        weights_data = bytearray()
        min_pos = [float("inf")] * 3
        max_pos = [float("-inf")] * 3

        for ni in range(vc):
            vi = flat_vi[ni]
            ui = flat_ui[ni]
            v = mesh_data.vertices[vi]

            pos_data.extend(struct.pack("<fff", v.x, v.y, v.z))
            min_pos = [min(min_pos[j], [v.x, v.y, v.z][j]) for j in range(3)]
            max_pos = [max(max_pos[j], [v.x, v.y, v.z][j]) for j in range(3)]
            length = math.sqrt(v.nx * v.nx + v.ny * v.ny + v.nz * v.nz)
            if length > 0:
                nx, ny, nz = v.nx / length, v.ny / length, v.nz / length
            else:
                nx, ny, nz = 0.0, 1.0, 0.0
            norm_data.extend(struct.pack("<fff", nx, ny, nz))

            if has_uvs and ui < len(mesh_data.uvs):
                u, vt = mesh_data.uvs[ui]
                uv_data.extend(struct.pack("<ff", u, vt))
            else:
                uv_data.extend(struct.pack("<ff", 0.0, 0.0))

            # Bone weights from original vertex index
            if vi < len(sv):
                s = sv[vi]
                j_indices = list(s.joint_indices[:4])
                w = list(s.weights[:4])
            else:
                j_indices = [0]
                w = [1.0]

            while len(j_indices) < 4:
                j_indices.append(0)
            while len(w) < 4:
                w.append(0.0)

            total = sum(w)
            if total > 0:
                w = [x / total for x in w]

            joints_data.extend(struct.pack("<BBBB", *[min(j, 255) for j in j_indices]))
            weights_data.extend(struct.pack("<ffff", *w))

        idx_data = bytearray()
        for idx in new_indices:
            idx_data.extend(struct.pack("<H", idx))

        pos_bv = self._append_data(bytes(pos_data), ARRAY_BUFFER)
        norm_bv = self._append_data(bytes(norm_data), ARRAY_BUFFER)
        uv_bv = self._append_data(bytes(uv_data), ARRAY_BUFFER)
        joints_bv = self._append_data(bytes(joints_data), ARRAY_BUFFER)
        weights_bv = self._append_data(bytes(weights_data), ARRAY_BUFFER)
        idx_bv = self._append_data(bytes(idx_data), ELEMENT_ARRAY_BUFFER)

        ic = len(new_indices)

        pos_acc = self._add_accessor(pos_bv, FLOAT, vc, VEC3, min_pos, max_pos)
        norm_acc = self._add_accessor(norm_bv, FLOAT, vc, VEC3)
        uv_acc = self._add_accessor(uv_bv, FLOAT, vc, VEC2)
        joints_acc = self._add_accessor(joints_bv, UNSIGNED_BYTE, vc, VEC4)
        weights_acc = self._add_accessor(weights_bv, FLOAT, vc, VEC4)
        idx_acc = self._add_accessor(idx_bv, UNSIGNED_SHORT, ic, SCALAR,
                                     [0], [max(new_indices) if new_indices else 0])

        prim = Primitive(
            attributes=pygltflib.Attributes(
                POSITION=pos_acc,
                NORMAL=norm_acc,
                TEXCOORD_0=uv_acc,
                JOINTS_0=joints_acc,
                WEIGHTS_0=weights_acc,
            ),
            indices=idx_acc,
        )
        if material_idx >= 0:
            prim.material = material_idx

        mesh_idx = len(self.gltf.meshes)
        self.gltf.meshes.append(Mesh(name=name, primitives=[prim]))

        node_idx = len(self.gltf.nodes)
        self.gltf.nodes.append(Node(name=name, mesh=mesh_idx, skin=skin_idx))
        self.gltf.scenes[0].nodes.append(node_idx)
        return node_idx

    def add_joint_animation(self, root_joint: N3Joint,
                            joint_node_indices: list[int],
                            name: str = "animation",
                            frame_start: float = 0,
                            frame_end: float = -1,
                            fps_override: float = 0,
                            source_frame_rate: float = 0) -> int:
        """Export keyframes from the joint tree as a glTF animation.

        If frame_start/frame_end are given, only exports that slice of keyframes.
        This allows splitting a single keyframe buffer into multiple named animations.

        N3AnimControl ranges use the engine's 30 Hz timeline, while N3Joint keys
        commonly use a 15 Hz sampling rate.  source_frame_rate converts between those
        coordinate spaces; fps_override remains the animation's playback speed.
        """
        flat: list[tuple[N3Joint, int]] = []
        self._flatten_joint(root_joint, -1, flat)

        channels = []
        samplers = []

        for joint_idx, (joint, _) in enumerate(flat):
            if joint_idx >= len(joint_node_indices):
                break
            target_node = joint_node_indices[joint_idx]

            # Position keyframes
            if joint.key_pos.count > 0:
                sample_rate = joint.key_pos.sampling_rate or 30.0
                fps = fps_override if fps_override > 0 else sample_rate
                fc = joint.key_pos.count
                if source_frame_rate > 0:
                    f_start = math.floor(frame_start * sample_rate / source_frame_rate)
                    f_end = (math.floor(frame_end * sample_rate / source_frame_rate) + 1
                             if frame_end >= 0 else fc)
                    fps = sample_rate * fps / source_frame_rate
                else:
                    f_start = int(frame_start)
                    f_end = int(frame_end) if frame_end >= 0 else fc
                f_start = max(0, min(f_start, fc - 1))
                f_end = min(fc, f_end)
                slice_count = max(1, f_end - f_start)
                if slice_count > 0 and f_start < fc:
                    times = bytearray()
                    values = bytearray()
                    for f in range(f_start, min(f_start + slice_count, fc)):
                        times.extend(struct.pack("<f", (f - f_start) / fps))
                        values.extend(struct.pack("<fff", *joint.key_pos.data[f]))

                    actual = min(slice_count, fc - f_start)
                    t_bv = self._append_data(bytes(times))
                    v_bv = self._append_data(bytes(values))
                    max_t = (actual - 1) / fps if actual > 1 else 0.0
                    t_acc = self._add_accessor(t_bv, FLOAT, actual, SCALAR, [0.0], [max_t])
                    v_acc = self._add_accessor(v_bv, FLOAT, actual, VEC3)

                    si = len(samplers)
                    samplers.append(AnimationSampler(input=t_acc, output=v_acc, interpolation="LINEAR"))
                    channels.append(AnimationChannel(
                        sampler=si, target=AnimationChannelTarget(node=target_node, path="translation")))

            # Rotation keyframes
            if joint.key_rot.count > 0:
                sample_rate = joint.key_rot.sampling_rate or 30.0
                fps = fps_override if fps_override > 0 else sample_rate
                fc = joint.key_rot.count
                if source_frame_rate > 0:
                    f_start = math.floor(frame_start * sample_rate / source_frame_rate)
                    f_end = (math.floor(frame_end * sample_rate / source_frame_rate) + 1
                             if frame_end >= 0 else fc)
                    fps = sample_rate * fps / source_frame_rate
                else:
                    f_start = int(frame_start)
                    f_end = int(frame_end) if frame_end >= 0 else fc
                f_start = max(0, min(f_start, fc - 1))
                f_end = min(fc, f_end)
                slice_count = max(1, f_end - f_start)
                if slice_count > 0 and f_start < fc:
                    times = bytearray()
                    values = bytearray()
                    for f in range(f_start, min(f_start + slice_count, fc)):
                        times.extend(struct.pack("<f", (f - f_start) / fps))
                        values.extend(struct.pack("<ffff", *joint.key_rot.data[f]))

                    actual = min(slice_count, fc - f_start)
                    t_bv = self._append_data(bytes(times))
                    v_bv = self._append_data(bytes(values))
                    max_t = (actual - 1) / fps if actual > 1 else 0.0
                    t_acc = self._add_accessor(t_bv, FLOAT, actual, SCALAR, [0.0], [max_t])
                    v_acc = self._add_accessor(v_bv, FLOAT, actual, VEC4)

                    si = len(samplers)
                    samplers.append(AnimationSampler(input=t_acc, output=v_acc, interpolation="LINEAR"))
                    channels.append(AnimationChannel(
                        sampler=si, target=AnimationChannelTarget(node=target_node, path="rotation")))

            # Scale keyframes
            if joint.key_scale.count > 0:
                sample_rate = joint.key_scale.sampling_rate or 30.0
                fps = fps_override if fps_override > 0 else sample_rate
                fc = joint.key_scale.count
                if source_frame_rate > 0:
                    f_start = math.floor(frame_start * sample_rate / source_frame_rate)
                    f_end = (math.floor(frame_end * sample_rate / source_frame_rate) + 1
                             if frame_end >= 0 else fc)
                    fps = sample_rate * fps / source_frame_rate
                else:
                    f_start = int(frame_start)
                    f_end = int(frame_end) if frame_end >= 0 else fc
                f_start = max(0, min(f_start, fc - 1))
                f_end = min(fc, f_end)
                slice_count = max(1, f_end - f_start)
                if slice_count > 0 and f_start < fc:
                    times = bytearray()
                    values = bytearray()
                    for f in range(f_start, min(f_start + slice_count, fc)):
                        times.extend(struct.pack("<f", (f - f_start) / fps))
                        values.extend(struct.pack("<fff", *joint.key_scale.data[f]))

                    actual = min(slice_count, fc - f_start)
                    t_bv = self._append_data(bytes(times))
                    v_bv = self._append_data(bytes(values))
                    max_t = (actual - 1) / fps if actual > 1 else 0.0
                    t_acc = self._add_accessor(t_bv, FLOAT, actual, SCALAR, [0.0], [max_t])
                    v_acc = self._add_accessor(v_bv, FLOAT, actual, VEC3)

                    si = len(samplers)
                    samplers.append(AnimationSampler(input=t_acc, output=v_acc, interpolation="LINEAR"))
                    channels.append(AnimationChannel(
                        sampler=si, target=AnimationChannelTarget(node=target_node, path="scale")))

        if channels:
            self.gltf.animations.append(Animation(name=name, channels=channels, samplers=samplers))

        return len(self.gltf.animations)

    def save(self, path: str | Path):
        """Finalize and save as GLB (binary glTF) for efficiency."""
        path = Path(path)
        bin_data = bytes(self._bin)
        self.gltf.buffers = [Buffer(byteLength=len(bin_data))]

        if path.suffix.lower() == ".glb":
            self.gltf.set_binary_blob(bin_data)
            self.gltf.save(str(path))
        else:
            b64 = base64.b64encode(bin_data).decode("ascii")
            self.gltf.buffers[0].uri = f"data:application/octet-stream;base64,{b64}"
            self.gltf.save(str(path))

    @staticmethod
    def _invert_4x4(m: list[list[float]]) -> list[list[float]]:
        """Invert a 4x4 matrix (row-major)."""
        # Cofactor expansion
        a = m
        inv = [[0.0]*4 for _ in range(4)]

        inv[0][0] = a[1][1]*(a[2][2]*a[3][3]-a[2][3]*a[3][2]) - a[1][2]*(a[2][1]*a[3][3]-a[2][3]*a[3][1]) + a[1][3]*(a[2][1]*a[3][2]-a[2][2]*a[3][1])
        inv[0][1] = -(a[0][1]*(a[2][2]*a[3][3]-a[2][3]*a[3][2]) - a[0][2]*(a[2][1]*a[3][3]-a[2][3]*a[3][1]) + a[0][3]*(a[2][1]*a[3][2]-a[2][2]*a[3][1]))
        inv[0][2] = a[0][1]*(a[1][2]*a[3][3]-a[1][3]*a[3][2]) - a[0][2]*(a[1][1]*a[3][3]-a[1][3]*a[3][1]) + a[0][3]*(a[1][1]*a[3][2]-a[1][2]*a[3][1])
        inv[0][3] = -(a[0][1]*(a[1][2]*a[2][3]-a[1][3]*a[2][2]) - a[0][2]*(a[1][1]*a[2][3]-a[1][3]*a[2][1]) + a[0][3]*(a[1][1]*a[2][2]-a[1][2]*a[2][1]))

        inv[1][0] = -(a[1][0]*(a[2][2]*a[3][3]-a[2][3]*a[3][2]) - a[1][2]*(a[2][0]*a[3][3]-a[2][3]*a[3][0]) + a[1][3]*(a[2][0]*a[3][2]-a[2][2]*a[3][0]))
        inv[1][1] = a[0][0]*(a[2][2]*a[3][3]-a[2][3]*a[3][2]) - a[0][2]*(a[2][0]*a[3][3]-a[2][3]*a[3][0]) + a[0][3]*(a[2][0]*a[3][2]-a[2][2]*a[3][0])
        inv[1][2] = -(a[0][0]*(a[1][2]*a[3][3]-a[1][3]*a[3][2]) - a[0][2]*(a[1][0]*a[3][3]-a[1][3]*a[3][0]) + a[0][3]*(a[1][0]*a[3][2]-a[1][2]*a[3][0]))
        inv[1][3] = a[0][0]*(a[1][2]*a[2][3]-a[1][3]*a[2][2]) - a[0][2]*(a[1][0]*a[2][3]-a[1][3]*a[2][0]) + a[0][3]*(a[1][0]*a[2][2]-a[1][2]*a[2][0])

        inv[2][0] = a[1][0]*(a[2][1]*a[3][3]-a[2][3]*a[3][1]) - a[1][1]*(a[2][0]*a[3][3]-a[2][3]*a[3][0]) + a[1][3]*(a[2][0]*a[3][1]-a[2][1]*a[3][0])
        inv[2][1] = -(a[0][0]*(a[2][1]*a[3][3]-a[2][3]*a[3][1]) - a[0][1]*(a[2][0]*a[3][3]-a[2][3]*a[3][0]) + a[0][3]*(a[2][0]*a[3][1]-a[2][1]*a[3][0]))
        inv[2][2] = a[0][0]*(a[1][1]*a[3][3]-a[1][3]*a[3][1]) - a[0][1]*(a[1][0]*a[3][3]-a[1][3]*a[3][0]) + a[0][3]*(a[1][0]*a[3][1]-a[1][1]*a[3][0])
        inv[2][3] = -(a[0][0]*(a[1][1]*a[2][3]-a[1][3]*a[2][1]) - a[0][1]*(a[1][0]*a[2][3]-a[1][3]*a[2][0]) + a[0][3]*(a[1][0]*a[2][1]-a[1][1]*a[2][0]))

        inv[3][0] = -(a[1][0]*(a[2][1]*a[3][2]-a[2][2]*a[3][1]) - a[1][1]*(a[2][0]*a[3][2]-a[2][2]*a[3][0]) + a[1][2]*(a[2][0]*a[3][1]-a[2][1]*a[3][0]))
        inv[3][1] = a[0][0]*(a[2][1]*a[3][2]-a[2][2]*a[3][1]) - a[0][1]*(a[2][0]*a[3][2]-a[2][2]*a[3][0]) + a[0][2]*(a[2][0]*a[3][1]-a[2][1]*a[3][0])
        inv[3][2] = -(a[0][0]*(a[1][1]*a[3][2]-a[1][2]*a[3][1]) - a[0][1]*(a[1][0]*a[3][2]-a[1][2]*a[3][0]) + a[0][2]*(a[1][0]*a[3][1]-a[1][1]*a[3][0]))
        inv[3][3] = a[0][0]*(a[1][1]*a[2][2]-a[1][2]*a[2][1]) - a[0][1]*(a[1][0]*a[2][2]-a[1][2]*a[2][0]) + a[0][2]*(a[1][0]*a[2][1]-a[1][1]*a[2][0])

        det = a[0][0]*inv[0][0] + a[0][1]*inv[1][0] + a[0][2]*inv[2][0] + a[0][3]*inv[3][0]
        if abs(det) < 1e-10:
            return [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]

        inv_det = 1.0 / det
        return [[inv[r][c] * inv_det for c in range(4)] for r in range(4)]

    def _flatten_joint(self, joint: N3Joint, parent_idx: int,
                       result: list[tuple[N3Joint, int]]):
        my_idx = len(result)
        result.append((joint, parent_idx))
        for child in joint.children:
            self._flatten_joint(child, my_idx, result)


# High-level export functions

def _imesh_to_pmesh(mesh_data, name: str) -> N3PMesh:
    """Convert N3IMesh (separate vertex/UV indices) to N3PMesh (flat vertices).

    N3IMesh stores vertex_indices and uv_indices separately (like OBJ f v/vt).
    glTF needs flat vertices. We create a new vertex for each unique (v_idx, uv_idx) pair.
    """
    from ..mesh.pmesh import N3PMesh, Vertex

    has_uvs = len(mesh_data.uvs) > 0 and len(mesh_data.uv_indices) > 0

    # Build flat vertex list from face indices
    pair_to_new_idx: dict[tuple[int, int], int] = {}
    new_verts: list[Vertex] = []
    new_indices: list[int] = []

    for fi in range(len(mesh_data.vertex_indices)):
        vi = mesh_data.vertex_indices[fi]
        ui = mesh_data.uv_indices[fi] if has_uvs and fi < len(mesh_data.uv_indices) else vi

        key = (vi, ui)
        if key not in pair_to_new_idx:
            v = mesh_data.vertices[vi]
            u, vt = mesh_data.uvs[ui] if has_uvs and ui < len(mesh_data.uvs) else (0.0, 0.0)
            pair_to_new_idx[key] = len(new_verts)
            new_verts.append(Vertex(v.x, v.y, v.z, v.nx, v.ny, v.nz, u, vt))

        new_indices.append(pair_to_new_idx[key])

    return N3PMesh(name=name, vertices=new_verts, indices=new_indices)


def export_pmesh_to_gltf(mesh: N3PMesh, output_path: str | Path,
                         texture: Optional[KOTexture] = None,
                         texture_max_size: int = 256):
    """Export a single N3PMesh to glTF."""
    builder = GLTFBuilder(texture_max_size=texture_max_size)
    mat_idx = -1
    if texture:
        mat_idx = builder.add_texture(texture, mesh.name)
    builder.add_pmesh(mesh, mat_idx, mesh.name)
    builder.save(output_path)


def export_meshdata_to_gltf(
    positions, normals, uvs, indices,
    output_path: str | Path,
    texture: Optional[KOTexture] = None,
    normal_map_image=None,
    name: str = "mesh",
    texture_max_size: int = 256,
):
    """Export raw mesh arrays to glTF. Used for enhanced/subdivided meshes.

    Args:
        positions: (N,3) float32 array
        normals: (N,3) float32 array
        uvs: (N,2) float32 array
        indices: (F*3,) uint32 array
        texture: Optional KO texture for base color
        normal_map_image: Optional PIL Image for normal map
        name: Mesh name
        texture_max_size: Max texture dimension (0 = no limit)
    """
    import io as _io

    builder = GLTFBuilder(texture_max_size=texture_max_size)
    mat_idx = -1
    if texture:
        mat_idx = builder.add_texture(texture, name)

    # If normal map provided, add as separate texture and attach to material
    if normal_map_image is not None and mat_idx >= 0:
        buf = _io.BytesIO()
        normal_map_image.save(buf, format="PNG", optimize=True)
        png_data = buf.getvalue()

        bv_idx = builder._append_data(png_data)
        img_idx = len(builder.gltf.images)
        builder.gltf.images.append(GLTFImage(bufferView=bv_idx, mimeType="image/png"))
        tex_idx = len(builder.gltf.textures)
        builder.gltf.textures.append(Texture(sampler=0, source=img_idx))

        # Attach normal map to material
        builder.gltf.materials[mat_idx].normalTexture = TextureInfo(index=tex_idx)

    builder.add_meshdata(positions, normals, uvs, indices, mat_idx, name)
    builder.save(output_path)


def _filter_character_parts(
    skins: list[N3Skin],
    textures: list,
    part_names: list[str],
) -> tuple[list[N3Skin], list, list[str]]:
    """Filter overlapping character parts.

    KO characters often have both 'N_' (naked/base) and 'U_' (uniformed/armored)
    versions of body parts. Only one set should be displayed.
    Prefer U_ (armored) over N_ (naked).
    """
    if not part_names:
        return skins, textures, part_names

    # Detect body slot duplicates: extract slot names like 'upper', 'lower', 'hands', 'foots'
    slots_seen: dict[str, list[int]] = {}
    for i, pname in enumerate(part_names):
        lower = pname.lower()
        # Extract slot: everything after U_ or N_ prefix
        for prefix in ("_u_", "_n_"):
            idx = lower.find(prefix)
            if idx >= 0:
                slot = lower[idx + 3:]  # e.g., 'upper', 'lower'
                variant = lower[idx + 1]  # 'u' or 'n'
                key = slot
                if key not in slots_seen:
                    slots_seen[key] = []
                slots_seen[key].append((i, variant))
                break

    # Find indices to exclude (N_ parts when U_ exists for same slot)
    exclude = set()
    for slot, entries in slots_seen.items():
        has_u = any(v == 'u' for _, v in entries)
        if has_u:
            for idx, variant in entries:
                if variant == 'n':
                    exclude.add(idx)

    if not exclude:
        return skins, textures, part_names

    filtered_skins = [s for i, s in enumerate(skins) if i not in exclude]
    filtered_textures = [t for i, t in enumerate(textures) if i not in exclude]
    filtered_names = [n for i, n in enumerate(part_names) if i not in exclude]
    return filtered_skins, filtered_textures, filtered_names


def export_character_to_gltf(
    joint: N3Joint,
    skins: list[N3Skin],
    textures: list[KOTexture],
    anim_ctrl: Optional[N3AnimControl] = None,
    output_path: str | Path = "character.gltf",
    name: str = "character",
    part_names: Optional[list[str]] = None,
    enhanced_meshes: Optional[list] = None,
    alpha_modes: Optional[list] = None,
    texture_max_size: int = 256,
    texture_dir: Optional[Path] = None,
    texture_uri_prefix: str = "",
):
    """Export a full character (skeleton + skinned meshes + textures + animations).

    Args:
        enhanced_meshes: Optional list of MeshData objects (from mesh_enhance).
        alpha_modes: Optional per-part hint from KO's own material state. "BLEND" means the
            part declares real alpha blending, so it must never be alpha-TESTED — the cutoff
            punches holes in the gradient the surface is made of (obj_el_crystal02). It only
            overrides a MASK verdict; an OPAQUE texture stays opaque.
                         Same length as skins. If entry is not None, uses the
                         enhanced mesh instead of the original skin data.
        texture_max_size: Max texture dimension (0 = no limit, default 256 for web).
    """
    if part_names is None:
        part_names = [f"{name}_part{i}" for i in range(len(skins))]

    # Filter overlapping N_/U_ parts
    skins, textures, part_names = _filter_character_parts(skins, textures, part_names)

    # Also filter enhanced_meshes if provided
    if enhanced_meshes is not None:
        # _filter_character_parts returns filtered lists in same order
        # We need to re-filter enhanced_meshes the same way
        pass  # enhanced_meshes is created after filtering in the router

    builder = GLTFBuilder(texture_max_size=texture_max_size,
                          texture_dir=texture_dir,
                          texture_uri_prefix=texture_uri_prefix)

    has_skeleton = joint is not None and joint.bone_count() > 1

    # Add skeleton first (needed for skinned meshes)
    skin_idx = -1
    joint_nodes = []
    if has_skeleton:
        skin_idx, joint_nodes = builder.add_skeleton(joint)

    # Add meshes — skinned if we have a skeleton, static otherwise
    for i, skin_mesh in enumerate(skins):
        # Use enhanced mesh if available
        enh = enhanced_meshes[i] if enhanced_meshes and i < len(enhanced_meshes) else None

        mat_idx = -1
        if i < len(textures) and textures[i]:
            if enh is not None:
                uvs, tri_idx = enh.uvs, enh.indices
            else:
                # N3IMesh keeps uvs on their own index list, OBJ-style (f v/vt).
                uvs = skin_mesh.mesh.uvs
                tri_idx = skin_mesh.mesh.uv_indices
            mode = choose_alpha_mode(textures[i].to_pil_image(), uvs, tri_idx)
            no_mask = bool(alpha_modes) and i < len(alpha_modes) and alpha_modes[i] == "BLEND"
            if mode == "MASK" and no_mask:
                mode = "BLEND"
            mat_idx = builder.add_texture(textures[i], part_names[i], alpha_mode=mode)

        if enh is not None and has_skeleton:
            builder.add_skinned_meshdata(
                enh.positions, enh.normals, enh.uvs, enh.indices,
                enh.joint_indices or [], enh.joint_weights or [],
                skin_idx, joint_nodes,
                material_idx=mat_idx, name=part_names[i],
            )
        else:
            mesh_data = skin_mesh.mesh
            if mesh_data.vertex_count > 0 and mesh_data.face_count > 0:
                if has_skeleton and skin_mesh.skin_vertices:
                    builder.add_skinned_mesh(
                        skin_mesh, skin_idx, joint_nodes,
                        material_idx=mat_idx, name=part_names[i])
                else:
                    pmesh = _imesh_to_pmesh(mesh_data, part_names[i])
                    builder.add_pmesh(pmesh, mat_idx, part_names[i])

    # Add animations
    if has_skeleton:
        # Export animations — split by anim_ctrl entries if available.
        # The client converts the character's 30 Hz frame coordinate to
        # a key index with: frame * keySamplingRate / 30.  The per-animation FPS
        # only controls how quickly that 30 Hz coordinate advances.
        if anim_ctrl and anim_ctrl.animations:
            # A zero FPS is a data slip, not "no animation": the entry still carries a real frame
            # range, and the same clip is authored at 30 in every sibling rig (e.g. slot 93
            # breath_CrossBow0 is fps 0 only in the El Morad male .n3anim, 30 in the other nine).
            # Dropping those entries lost the clip from the glb entirely, so anything addressing it
            # by slot silently fell through to an unrelated animation. Keep them at KO's 30 Hz.
            # An INVERTED range (frame_end < frame_start) really is unusable and still drops out.
            named_anims = [a for a in anim_ctrl.animations
                          if a.name and a.name != "No Name"
                          and a.frame_end >= a.frame_start]
            if named_anims:
                used_names: dict[str, int] = {}
                for anim_entry in named_anims:
                    if anim_entry.frame_end < anim_entry.frame_start:
                        continue
                    # Deduplicate names (e.g., two "dead0" entries)
                    anim_name = anim_entry.name
                    if anim_name in used_names:
                        used_names[anim_name] += 1
                        anim_name = f"{anim_name}_{used_names[anim_name]}"
                    else:
                        used_names[anim_name] = 0
                    builder.add_joint_animation(
                        joint, joint_nodes,
                        name=anim_name,
                        frame_start=anim_entry.frame_start,
                        frame_end=anim_entry.frame_end,
                        # Must be explicit: add_joint_animation's own "<= 0" fallback substitutes
                        # the key sampling rate, which the source_frame_rate rescale then squares.
                        fps_override=anim_entry.fps if anim_entry.fps > 0 else 30.0,
                        source_frame_rate=30.0,
                    )
            else:
                builder.add_joint_animation(joint, joint_nodes, f"{name}_anim")
        else:
            builder.add_joint_animation(joint, joint_nodes, f"{name}_anim")

    builder.save(output_path)

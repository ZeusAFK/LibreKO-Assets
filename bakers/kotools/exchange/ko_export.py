"""Export enhanced assets back to Knight Online native formats.

Converts this library's enhanced PNG textures and regenerated meshes back into
the KO file formats (.dxt, .n3pmesh, .n3cpart, etc.) for use in-game.
"""

import hashlib
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ..formats.d3dformat import D3DFormat
from ..mesh.pmesh import N3PMesh, Vertex, write_pmesh
from ..ntf.texture import KOTexture, read_dxt_from_bytes, write_dxt


def enhanced_png_to_dxt(
    png_path: str | Path,
    original_dxt_data: bytes,
    output_path: str | Path,
    target_size: tuple[int, int] | None = None,
) -> Path:
    """Convert an enhanced PNG back to DXT matching the original format.

    Args:
        png_path: Path to the enhanced PNG file.
        original_dxt_data: Raw bytes of the original .dxt file (for format/name).
        output_path: Where to write the new .dxt file.
        target_size: Override output dimensions (w, h). Defaults to original size.

    Returns:
        Path to the written .dxt file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Read original to extract format metadata
    original = read_dxt_from_bytes(original_dxt_data)
    fmt = original.header.format
    name = original.header.resource_name
    is_encrypted = original.header.is_encrypted

    # Load enhanced image
    img = Image.open(str(png_path)).convert("RGBA")

    # Resize to target or original dimensions
    w, h = target_size or (original.width, original.height)
    if img.size != (w, h):
        img = img.resize((w, h), Image.LANCZOS)

    # Create KOTexture from PIL image and write
    texture = KOTexture.from_pil_image(img, name=name, fmt=fmt)
    write_dxt(texture, output_path, encrypt=is_encrypted)

    return output_path


def pil_image_to_dxt(
    img: Image.Image,
    output_path: str | Path,
    name: str = "",
    fmt: D3DFormat = D3DFormat.DXT3,
    encrypt: bool = False,
) -> Path:
    """Convert a PIL Image directly to a .dxt file.

    Args:
        img: PIL Image to convert.
        output_path: Where to write the .dxt file.
        name: Resource name embedded in the NTF header.
        fmt: DXT format (DXT1, DXT3, DXT5).
        encrypt: Whether to encrypt (v7 format).

    Returns:
        Path to the written .dxt file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    texture = KOTexture.from_pil_image(img.convert("RGBA"), name=name, fmt=fmt)
    write_dxt(texture, output_path, encrypt=encrypt)

    return output_path


def trimesh_to_n3pmesh(
    mesh,
    output_path: str | Path,
    name: str = "mesh",
) -> Path:
    """Convert a trimesh.Trimesh object to N3PMesh and write it.

    Args:
        mesh: trimesh.Trimesh object.
        output_path: Where to write the .n3pmesh file.
        name: Mesh resource name.

    Returns:
        Path to the written .n3pmesh file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    verts = mesh.vertices
    faces = mesh.faces
    normals = mesh.vertex_normals if hasattr(mesh, 'vertex_normals') else np.zeros_like(verts)

    # Get UVs if available
    uvs = None
    if hasattr(mesh, 'visual') and hasattr(mesh.visual, 'uv') and mesh.visual.uv is not None:
        uvs = mesh.visual.uv

    vertices = []
    for i in range(len(verts)):
        v = verts[i]
        n = normals[i]
        tu = uvs[i][0] if uvs is not None and i < len(uvs) else 0.0
        tv = uvs[i][1] if uvs is not None and i < len(uvs) else 0.0
        vertices.append(Vertex(
            x=float(v[0]), y=float(v[1]), z=float(v[2]),
            nx=float(n[0]), ny=float(n[1]), nz=float(n[2]),
            tu=float(tu), tv=float(tv),
        ))

    indices = faces.flatten().tolist()

    pmesh = N3PMesh(
        name=name,
        vertices=vertices,
        indices=indices,
        collapses=[],
        index_changes=[],
        lod_ctrl=[],
    )

    write_pmesh(pmesh, output_path)
    return output_path


LOD_NAMES = ("high", "middle", "low", "lowest")


def original_skin_looks_corrupted(
    original_positions: np.ndarray,
    part_name_hint: str = "",
) -> str | None:
    """Heuristic check: does this "original" bbox look like a previously-broken export?

    The usual failure mode is a Hunyuan mesh that was packed with its centroid
    at the world origin instead of at body-part height. When that broken file
    becomes the archive's "original", re-exporting against it just propagates
    the corruption forever. Detect this up-front and return a human-readable
    reason string so the caller can surface the problem to the user.

    Heuristics:
      * Y centroid < 0.25: real KO parts (heads, torsos, arms, even feet) all
        have vertices in positive Y. Centroid near zero almost always means the
        mesh was packed in Hunyuan-local space without alignment.
      * Bbox straddles Y = 0 symmetrically: the hallmark of an unaligned
        Hunyuan output (e.g. Y=[-0.66, +0.66]) — no real body part has this
        because characters stand on +Y axis with the ground at Y=0.

    Returns ``None`` when the bbox looks plausible.
    """
    if original_positions is None or len(original_positions) == 0:
        return "original skin is empty"

    mn = original_positions.min(axis=0)
    mx = original_positions.max(axis=0)
    ctr = original_positions.mean(axis=0)

    # Feet/shoes parts can legitimately dip to Y≈0. Only flag when centroid
    # sits near the ground AND the bbox straddles origin — both signals.
    straddles = mn[1] < -0.15 and mx[1] > 0.15
    low_centroid = ctr[1] < 0.25

    name_hint = (part_name_hint or "").lower()
    is_feet_like = any(tag in name_hint for tag in ("foot", "feet", "shoe", "_50_0", "_60_0"))

    if straddles and low_centroid and not is_feet_like:
        return (
            f"Original skin looks corrupted: bbox Y=[{mn[1]:.3f}, {mx[1]:.3f}], "
            f"centroid Y={ctr[1]:.3f}. Real character parts sit in positive Y "
            f"(torso ≈ +1.3, head ≈ +1.6, feet ≈ +0.3). This suggests a previous "
            f"export packed a Hunyuan mesh without alignment into the archive. "
            f"Restore the game's Item/item.hdr + item.src from a clean KO install "
            f"(there is no .hdr.bak to auto-restore from) and export again."
        )
    return None


def align_trimesh_to_original(mesh, original_positions: np.ndarray):
    """Pre-align a Hunyuan trimesh to the original skin's bbox in-place.

    Hunyuan3D produces meshes centered at world origin with arbitrary scale
    (often extending "arms" beyond the target part's bounds). Nearest-neighbor
    weight transfer then maps new vertices onto wildly wrong original bones.

    This routine does a **per-axis** scale + translate so the new mesh's bbox
    matches the original part's bbox exactly. It intentionally distorts the
    mesh's proportions — the goal is to place every new vertex close to the
    original region that carries the correct bone weights, not to preserve
    visual proportions. Without this step, a full-body Hunyuan output tries to
    borrow bone weights from a torso-only original and the result is garbage.

    Args:
        mesh: trimesh.Trimesh to modify in-place.
        original_positions: (N, 3) float array of the original skin's vertices
            (in KO DX space).
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    if len(verts) == 0 or len(original_positions) == 0:
        return mesh

    orig = np.asarray(original_positions, dtype=np.float64)
    orig_min = orig.min(axis=0)
    orig_max = orig.max(axis=0)
    orig_size = orig_max - orig_min
    orig_center = (orig_min + orig_max) * 0.5

    new_min = verts.min(axis=0)
    new_max = verts.max(axis=0)
    new_size = new_max - new_min
    new_center = (new_min + new_max) * 0.5

    # Guard against zero-extent axes (flat meshes)
    scale = np.ones(3, dtype=np.float64)
    for ax in range(3):
        if new_size[ax] > 1e-8 and orig_size[ax] > 1e-8:
            scale[ax] = orig_size[ax] / new_size[ax]

    # Center → scale → recenter to original's center
    new_verts = (verts - new_center) * scale + orig_center
    mesh.vertices = new_verts.astype(np.float32)
    return mesh


def _transfer_weights_nearest(
    target_positions: np.ndarray,
    original_skin: "N3Skin",
) -> list[tuple[list[int], list[float]]]:
    """Fallback: copy (joint_indices, weights) from the nearest original vertex.

    Used only when the input GLB lacks JOINTS_0 / WEIGHTS_0 (e.g. Hunyuan output).
    Produces visible distortion proportional to the topology mismatch — prefer
    reading weights from the GLB whenever possible.
    """
    from scipy.spatial import cKDTree

    orig_positions = np.array(
        [(v.x, v.y, v.z) for v in original_skin.mesh.vertices],
        dtype=np.float32,
    )
    tree = cKDTree(orig_positions)
    _, nearest_idx = tree.query(target_positions, k=1)

    out: list[tuple[list[int], list[float]]] = []
    for i in range(len(target_positions)):
        orig_sv = original_skin.skin_vertices[nearest_idx[i]]
        jj = list(orig_sv.joint_indices)
        ww = list(orig_sv.weights)
        total = sum(ww)
        if total > 0:
            ww = [w / total for w in ww]
        elif jj:
            ww = [1.0] + [0.0] * (len(jj) - 1)
        # Ensure at least one influence — matches the retail writer's behaviour.
        if not jj:
            jj = [0]
            ww = [1.0]
        out.append((jj, ww))
    return out


def _build_per_vertex_skin_from_glb(
    glb_joints: np.ndarray,
    glb_weights: np.ndarray,
    trimesh_positions: np.ndarray,
    dedup_positions: np.ndarray,
    old_to_new_vert: list[int],
) -> list[tuple[list[int], list[float]]]:
    """Collapse GLB per-vertex joints/weights onto the deduplicated KO vertex list.

    trimesh may reorder or merge GLB vertices. We map each dedup vertex back to
    the nearest GLB vertex by position (since dedup_positions come directly from
    trimesh positions), then pack the up-to-4 non-zero influences per vertex.
    """
    from scipy.spatial import cKDTree

    # Each dedup vertex N came from all trimesh vertices that mapped to it.
    # Pick the first one (their positions are within the dedup epsilon anyway).
    dedup_to_trimesh: list[int] = [-1] * len(dedup_positions)
    for tri_idx, dedup_idx in enumerate(old_to_new_vert):
        if dedup_to_trimesh[dedup_idx] == -1:
            dedup_to_trimesh[dedup_idx] = tri_idx

    # If trimesh reordered/merged versus the raw GLB accessors, fall back to
    # nearest-position lookup between dedup positions and GLB positions.
    if len(glb_joints) != len(trimesh_positions):
        tree = cKDTree(trimesh_positions[: len(glb_joints)]
                       if len(glb_joints) < len(trimesh_positions)
                       else trimesh_positions)
        _, nearest_glb = tree.query(dedup_positions, k=1)
        get_glb_idx = lambda i: int(nearest_glb[i])
    else:
        get_glb_idx = lambda i: dedup_to_trimesh[i]

    out: list[tuple[list[int], list[float]]] = []
    for i in range(len(dedup_positions)):
        gi = get_glb_idx(i)
        j4 = glb_joints[gi]
        w4 = glb_weights[gi]
        # Keep only non-zero-weight influences, sorted by descending weight
        pairs = [(int(j4[k]), float(w4[k])) for k in range(4) if float(w4[k]) > 0.0]
        pairs.sort(key=lambda p: p[1], reverse=True)
        if not pairs:
            out.append(([0], [1.0]))
            continue
        total = sum(w for _, w in pairs)
        if total > 0:
            pairs = [(j, w / total) for j, w in pairs]
        out.append(([j for j, _ in pairs], [w for _, w in pairs]))
    return out


def trimesh_to_n3cskins(
    mesh,
    original_cskins,
    original_skin: "N3Skin",
    name: str = "part",
    flip_winding: bool = True,
    glb_path: "str | Path | None" = None,
) -> "N3CPartSkins":
    """Convert a trimesh (optionally backed by a GLB with skin data) to N3CPartSkins.

    Two paths:
      1. GLB has JOINTS_0/WEIGHTS_0 (Blender round-trip) — bone weights are read
         per-vertex from the GLB and transferred 1:1. This preserves skinning
         exactly and is the correct path for manual edits.
      2. GLB has no skin data (Hunyuan output) — falls back to nearest-vertex
         weight transfer from ``original_skin``. Lossy; quality degrades as the
         mesh topology diverges from the original.

    Default ``flip_winding=True`` matches the reference exporter: glTF's
    CCW front-face convention is the opposite of KO/DirectX CW, so triangle
    corners 1 and 2 must be swapped or the mesh renders as all-backfaces and
    disappears under backface culling.

    All four KO LODs ("high", "middle", "low", "lowest") are written with the
    same mesh — the game's LOD system selects them by distance, and leaving any
    LOD empty makes the part vanish at that range.

    Args:
        mesh: trimesh.Trimesh. For Blender round-trips, prefer passing
            ``glb_path`` too so real bone weights are used.
        original_cskins: Original N3CPartSkins (used only for the part name).
        original_skin: Original highest-LOD N3Skin (fallback bone weight source).
        name: Ignored — LOD names are hardcoded per KO convention.
        flip_winding: Swap triangle indices 1 & 2. Leave True unless the caller
            has already converted to KO winding upstream.
        glb_path: Optional path to the GLB that ``mesh`` was loaded from. When
            the GLB has JOINTS_0/WEIGHTS_0 they are used directly (path 1 above).
    """
    from ..mesh.imesh import N3IMesh, VertexXyzNormal
    from ..mesh.skin import N3Skin, SkinVertex
    from ..character.cskins import N3CPartSkins

    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    normals = (
        np.asarray(mesh.vertex_normals)
        if hasattr(mesh, "vertex_normals") and mesh.vertex_normals is not None
        else np.zeros_like(verts)
    )

    # Flip triangle winding (glTF CCW front → KO/DirectX CW front).
    if flip_winding:
        faces = faces[:, [0, 2, 1]]

    # Get UVs
    uvs = None
    if hasattr(mesh, "visual") and hasattr(mesh.visual, "uv") and mesh.visual.uv is not None:
        uvs = np.asarray(mesh.visual.uv)

    # --- Deduplicate vertices by position (OBJ-style split indices) ---
    # A trimesh/glTF mesh has one vertex per unique (position, UV) combo.
    # N3IMesh supports separate vertex and UV index arrays, so positions that
    # differ only in UV (texture seams) can share one vertex entry.
    pos_map: dict[tuple, int] = {}
    old_to_new_vert: list[int] = []
    dedup_verts: list[VertexXyzNormal] = []

    for i in range(len(verts)):
        v = verts[i]
        key = (round(float(v[0]), 5), round(float(v[1]), 5), round(float(v[2]), 5))
        if key not in pos_map:
            pos_map[key] = len(dedup_verts)
            n = normals[i]
            dedup_verts.append(VertexXyzNormal(
                x=float(v[0]), y=float(v[1]), z=float(v[2]),
                nx=float(n[0]), ny=float(n[1]), nz=float(n[2]),
            ))
        old_to_new_vert.append(pos_map[key])

    # Deduplicate UVs. trimesh flips V on load; KO uses glTF convention
    # (V=0 at top), so flip back here to preserve the original layout.
    uv_map: dict[tuple, int] = {}
    old_to_new_uv: list[int] = []
    dedup_uvs: list[tuple[float, float]] = []

    if uvs is not None and len(uvs) == len(verts):
        for i in range(len(uvs)):
            u_val = float(uvs[i][0])
            v_val = 1.0 - float(uvs[i][1])
            key = (round(u_val, 6), round(v_val, 6))
            if key not in uv_map:
                uv_map[key] = len(dedup_uvs)
                dedup_uvs.append((u_val, v_val))
            old_to_new_uv.append(uv_map[key])

    # Build per-face-corner index arrays
    vertex_indices = []
    uv_indices = []
    for face in faces:
        for vi in face:
            vertex_indices.append(old_to_new_vert[int(vi)])
            if old_to_new_uv:
                uv_indices.append(old_to_new_uv[int(vi)])

    dedup_positions = np.array(
        [(v.x, v.y, v.z) for v in dedup_verts],
        dtype=np.float32,
    )

    # --- Per-vertex bone weights ---
    skin_data: list[tuple[list[int], list[float]]] | None = None

    if glb_path is not None:
        try:
            from .glb_reader import read_glb_primitive
            prim = read_glb_primitive(glb_path)
            if prim.joints is not None and prim.weights is not None:
                skin_data = _build_per_vertex_skin_from_glb(
                    glb_joints=prim.joints,
                    glb_weights=prim.weights,
                    trimesh_positions=verts,
                    dedup_positions=dedup_positions,
                    old_to_new_vert=old_to_new_vert,
                )
        except Exception:
            # Any failure falls through to nearest-neighbor — GLB might be from
            # Hunyuan (no skin) or corrupted, but we still produce a valid file.
            skin_data = None

    if skin_data is None:
        skin_data = _transfer_weights_nearest(dedup_positions, original_skin)

    skin_verts = [
        SkinVertex(
            origin=(dedup_verts[i].x, dedup_verts[i].y, dedup_verts[i].z),
            joint_indices=jj,
            weights=ww,
        )
        for i, (jj, ww) in enumerate(skin_data)
    ]

    # Write all 4 LODs with the same mesh — keeping lower LODs empty makes the
    # mesh disappear when the client switches LOD by distance.
    lod_skins = []
    for i in range(4):
        lod_imesh = N3IMesh(
            name=LOD_NAMES[i],
            face_count=len(faces),
            vertex_count=len(dedup_verts),
            uv_count=len(dedup_uvs),
            vertices=list(dedup_verts),
            vertex_indices=list(vertex_indices),
            uvs=list(dedup_uvs),
            uv_indices=list(uv_indices),
        )
        lod_skins.append(N3Skin(mesh=lod_imesh, skin_vertices=list(skin_verts)))

    return N3CPartSkins(
        name=original_cskins.name,
        lod_skins=lod_skins,
    )


def get_enhanced_png_path(texture_path: str, cache_dir: Path) -> Optional[Path]:
    """Find the enhanced PNG for a given KO texture path in the cache.

    Args:
        texture_path: Original KO texture path (e.g., "chr/woman_karus/upper.dxt").
        cache_dir: The enhanced_cache directory.

    Returns:
        Path to the PNG if it exists, None otherwise.
    """
    key = hashlib.md5(texture_path.encode()).hexdigest()
    png_path = cache_dir / f"{key}.png"
    return png_path if png_path.exists() else None

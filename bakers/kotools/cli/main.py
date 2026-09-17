"""CLI entry point: kotools <subcommand>."""

import argparse
import os
import sys
import io

from pathlib import Path


def cmd_ntf_info(args):
    from ..ntf.header import NTFHeader
    from ..io.binary_reader import BinaryReader
    for filepath in args.files:
        with open(filepath, "rb") as f:
            data = f.read(512)
        header = NTFHeader.from_reader(BinaryReader(data))
        size = os.path.getsize(filepath)
        print(f"\n{os.path.basename(filepath)} ({size:,} bytes)")
        print(f"  Name    : {header.resource_name!r}")
        print(f"  Version : {header.version} {'(encrypted)' if header.is_encrypted else ''}")
        print(f"  Size    : {header.width}x{header.height}")
        print(f"  Format  : {header.format.name}")
        print(f"  Mipmaps : {header.has_mipmaps}")


def cmd_ntf_decode(args):
    from ..ntf.texture import read_dxt
    os.makedirs(args.output, exist_ok=True)
    for filepath in args.files:
        try:
            tex = read_dxt(filepath)
            out = Path(args.output) / f"{Path(filepath).stem}.png"
            tex.save_png(out)
            print(f"  {os.path.basename(filepath)} -> {out}")
        except Exception as e:
            print(f"  ERROR {os.path.basename(filepath)}: {e}")


def cmd_ntf_encode(args):
    from ..ntf.texture import KOTexture, write_dxt
    from ..formats.d3dformat import D3DFormat
    fmt = D3DFormat[args.format]
    tex = KOTexture.from_png(args.input, fmt=fmt)
    write_dxt(tex, args.output, with_mipmaps=args.mipmaps)
    print(f"  {args.input} -> {args.output} ({fmt.name})")


def cmd_hdr_list(args):
    from ..hdr.archive import read_hdr
    archive = read_hdr(args.file)
    print(f"Archive: {archive.hdr_path.name} ({len(archive.entries)} entries, encrypted={archive.is_encrypted})")
    pattern = args.filter or "*"
    entries = archive.find(pattern)
    for e in entries:
        print(f"  {e.name:<60} offset={e.offset:>10}  size={e.size:>10}")
    print(f"\nShowing {len(entries)} of {len(archive.entries)} entries")


def cmd_hdr_extract(args):
    from ..hdr.archive import read_hdr, extract_all
    archive = read_hdr(args.file)
    out = Path(args.output)
    pattern = args.filter or "*"
    paths = extract_all(archive, out, pattern)
    print(f"Extracted {len(paths)} files to {out}")


def cmd_gtt_list(args):
    from ..gtt.gtt import read_gtt
    textures = read_gtt(args.file)
    print(f"GTT: {os.path.basename(args.file)} ({len(textures)} textures)")
    for i, tex in enumerate(textures):
        h = tex.header
        print(f"  [{i}] {h.resource_name!r} {h.width}x{h.height} {h.format.name}")


def cmd_gtt_unpack(args):
    from ..gtt.gtt import read_gtt
    os.makedirs(args.output, exist_ok=True)
    textures = read_gtt(args.file)
    stem = Path(args.file).stem
    for i, tex in enumerate(textures):
        out = Path(args.output) / f"{stem}_{i}.png"
        tex.save_png(out)
        print(f"  [{i}] -> {out}")


def cmd_chr_info(args):
    from ..character.chr import read_chr
    c = read_chr(args.file)
    print(f"Character: {c.name}")
    print(f"  Joint   : {c.joint_path}")
    print(f"  Anim    : {c.anim_ctrl_path}")
    print(f"  Parts   : {len(c.part_paths)}")
    for p in c.part_paths:
        print(f"    - {p}")
    print(f"  Plugs   : {len(c.plug_paths)}")
    for p in c.plug_paths:
        print(f"    - {p}")
    if c.fx_plug_path:
        print(f"  FXPlug  : {c.fx_plug_path}")


def cmd_chr_to_gltf(args):
    from ..character.chr import read_chr
    from ..character.cpart import N3CPart
    from ..character.cskins import N3CPartSkins
    from ..skeleton.joint import read_joint
    from ..animation.animcontrol import read_anim
    from ..ntf.texture import read_dxt
    from ..mesh.skin import N3Skin
    from ..exchange.gltf_export import export_character_to_gltf
    from ..io.binary_reader import BinaryReader

    game_dir = Path(args.game_dir)
    chr_data = read_chr(args.file)
    print(f"Character: {chr_data.name}")

    # Load skeleton
    joint_path = game_dir / chr_data.joint_path.replace("\\", "/")
    print(f"  Loading skeleton: {joint_path}")
    joint = read_joint(str(joint_path))
    print(f"  Bones: {joint.bone_count()}")

    # Load animation control
    anim_ctrl = None
    if chr_data.anim_ctrl_path:
        anim_path = game_dir / chr_data.anim_ctrl_path.replace("\\", "/")
        if anim_path.exists():
            try:
                anim_ctrl = read_anim(str(anim_path))
                print(f"  Animations: {len(anim_ctrl.animations)}")
                for ad in anim_ctrl.animations:
                    print(f"    {ad.name}: {ad.frame_start:.0f}-{ad.frame_end:.0f}")
            except Exception as e:
                print(f"  Animation load failed (likely encrypted): {e}")
                anim_ctrl = None

    # Load parts (skins + textures)
    skins = []
    textures = []
    for part_path_str in chr_data.part_paths:
        pp = game_dir / part_path_str.replace("\\", "/")
        if not pp.exists():
            print(f"  SKIP part (not found): {pp}")
            continue
        print(f"  Loading part: {pp}")
        with open(str(pp), "rb") as f:
            part = N3CPart.from_reader(BinaryReader(f.read()))

        # Load texture
        tex = None
        if part.texture_path:
            tp = game_dir / part.texture_path.replace("\\", "/")
            if tp.exists():
                try:
                    tex = read_dxt(str(tp))
                    print(f"    Texture: {tp.name} ({tex.width}x{tex.height})")
                except Exception as e:
                    print(f"    Texture error: {e}")
        textures.append(tex)

        # Load skins
        if part.skins_path:
            sp = game_dir / part.skins_path.replace("\\", "/")
            if sp.exists():
                try:
                    with open(str(sp), "rb") as f:
                        cskins = N3CPartSkins.from_reader(BinaryReader(f.read()))
                    highest = cskins.highest_lod
                    if highest:
                        skins.append(highest)
                        print(f"    Skin: {highest.mesh.vertex_count} verts, {highest.mesh.face_count} faces")
                    else:
                        skins.append(N3Skin(mesh=__import__('kotools.mesh.imesh', fromlist=['N3IMesh']).N3IMesh(name="")))
                except Exception as e:
                    print(f"    Skin error: {e}")
            else:
                print(f"    SKIP skin (not found): {sp}")

    out = args.output or f"{chr_data.name}.gltf"
    print(f"\n  Exporting to: {out}")
    export_character_to_gltf(joint, skins, textures, anim_ctrl, out, chr_data.name)
    print(f"  Done! Open {out} in Blender or any glTF viewer.")


def cmd_mesh_info(args):
    from ..mesh.pmesh import read_pmesh
    m = read_pmesh(args.file)
    print(f"Mesh: {m.name}")
    print(f"  Vertices  : {len(m.vertices)} (min={m.min_vertices})")
    print(f"  Indices   : {len(m.indices)} (min={m.min_indices})")
    print(f"  Collapses : {len(m.collapses)}")
    print(f"  LOD levels: {len(m.lod_ctrl)}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="kotools", description="Knight Online N3 Engine file tools")
    sub = parser.add_subparsers(dest="command")

    # ntf
    ntf = sub.add_parser("ntf", help="NTF texture operations")
    ntf_sub = ntf.add_subparsers(dest="ntf_cmd")

    p = ntf_sub.add_parser("info", help="Show texture header info")
    p.add_argument("files", nargs="+")

    p = ntf_sub.add_parser("decode", help="Decode .dxt to PNG")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--output", default="output")

    p = ntf_sub.add_parser("encode", help="Encode PNG to .dxt")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--format", default="DXT1", choices=["DXT1", "DXT3", "DXT5"])
    p.add_argument("--mipmaps", action="store_true", default=True)

    # hdr
    hdr = sub.add_parser("hdr", help="HDR/SRC archive operations")
    hdr_sub = hdr.add_subparsers(dest="hdr_cmd")

    p = hdr_sub.add_parser("list", help="List archive contents")
    p.add_argument("file")
    p.add_argument("--filter", default=None)

    p = hdr_sub.add_parser("extract", help="Extract archive contents")
    p.add_argument("file")
    p.add_argument("-o", "--output", default="output")
    p.add_argument("--filter", default=None)

    # gtt
    gtt = sub.add_parser("gtt", help="GTT ground texture operations")
    gtt_sub = gtt.add_subparsers(dest="gtt_cmd")

    p = gtt_sub.add_parser("list", help="List textures in GTT")
    p.add_argument("file")

    p = gtt_sub.add_parser("unpack", help="Extract GTT textures to PNG")
    p.add_argument("file")
    p.add_argument("-o", "--output", default="output")

    # chr
    ch = sub.add_parser("chr", help="Character operations")
    ch_sub = ch.add_subparsers(dest="chr_cmd")

    p = ch_sub.add_parser("info", help="Show character info")
    p.add_argument("file")

    p = ch_sub.add_parser("to-gltf", help="Export character to glTF")
    p.add_argument("file")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("--game-dir", default="<retail install>")

    # mesh
    me = sub.add_parser("mesh", help="Mesh operations")
    me_sub = me.add_subparsers(dest="mesh_cmd")

    p = me_sub.add_parser("info", help="Show mesh info")
    p.add_argument("file")

    args = parser.parse_args()

    dispatch = {
        ("ntf", "info"): cmd_ntf_info,
        ("ntf", "decode"): cmd_ntf_decode,
        ("ntf", "encode"): cmd_ntf_encode,
        ("hdr", "list"): cmd_hdr_list,
        ("hdr", "extract"): cmd_hdr_extract,
        ("gtt", "list"): cmd_gtt_list,
        ("gtt", "unpack"): cmd_gtt_unpack,
        ("chr", "info"): cmd_chr_info,
        ("chr", "to-gltf"): cmd_chr_to_gltf,
        ("mesh", "info"): cmd_mesh_info,
    }

    key = (args.command, getattr(args, f"{args.command}_cmd", None)) if args.command else (None, None)
    fn = dispatch.get(key)
    if fn:
        fn(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

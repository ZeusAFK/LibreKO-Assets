"""High-level character loader — resolves all referenced files and assembles a character."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..resolver import AssetResolver
from ..io.binary_reader import BinaryReader
from ..ntf.texture import KOTexture, read_dxt_from_bytes
from ..mesh.skin import N3Skin
from ..mesh.imesh import N3IMesh
from ..skeleton.joint import N3Joint
from ..animation.animcontrol import N3AnimControl
from .chr import N3Chr
from .cpart import N3CPart
from .cplug import N3CPlug
from .cskins import N3CPartSkins


@dataclass
class LoadedPart:
    """A fully loaded character part with mesh and texture."""
    name: str
    part: N3CPart
    skin: Optional[N3Skin] = None
    texture: Optional[KOTexture] = None


@dataclass
class LoadedPlug:
    """A fully loaded character plug (weapon/shield)."""
    name: str
    plug: N3CPlug
    texture: Optional[KOTexture] = None


@dataclass
class LoadedCharacter:
    """Fully resolved and loaded character."""
    chr: N3Chr
    joint: Optional[N3Joint] = None
    anim_ctrl: Optional[N3AnimControl] = None
    parts: list[LoadedPart] = field(default_factory=list)
    plugs: list[LoadedPlug] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.chr.name

    @property
    def skins(self) -> list[N3Skin]:
        return [p.skin for p in self.parts if p.skin]

    @property
    def textures(self) -> list[Optional[KOTexture]]:
        return [p.texture for p in self.parts]


def load_character(chr_path: str, resolver: AssetResolver) -> LoadedCharacter:
    """Load a complete character, resolving all referenced files."""
    data = resolver.read(chr_path)
    if data is None:
        # Try as absolute path
        p = Path(chr_path)
        if p.exists():
            data = p.read_bytes()
        else:
            raise FileNotFoundError(f"Character file not found: {chr_path}")

    chr_data = N3Chr.from_reader(BinaryReader(data))
    result = LoadedCharacter(chr=chr_data)

    # Load skeleton
    if chr_data.joint_path:
        joint_data = resolver.read(chr_data.joint_path)
        if joint_data:
            try:
                result.joint = N3Joint.from_reader(BinaryReader(joint_data))
            except Exception as e:
                result.warnings.append(f"Joint load failed: {e}")
        else:
            result.warnings.append(f"Joint not found: {chr_data.joint_path}")

    # Load animation control
    if chr_data.anim_ctrl_path:
        anim_data = resolver.read(chr_data.anim_ctrl_path)
        if anim_data:
            try:
                result.anim_ctrl = N3AnimControl.from_reader(BinaryReader(anim_data))
            except Exception as e:
                result.warnings.append(f"Animation load failed: {e}")

    # Load parts
    for part_path in chr_data.part_paths:
        part_data = resolver.read(part_path)
        if not part_data:
            result.warnings.append(f"Part not found: {part_path}")
            continue

        try:
            part = N3CPart.from_reader(BinaryReader(part_data))
        except Exception as e:
            result.warnings.append(f"Part parse failed {part_path}: {e}")
            continue

        loaded = LoadedPart(name=part.name or Path(part_path).stem, part=part)

        # Load texture
        if part.texture_path:
            tex_data = resolver.read(part.texture_path)
            if tex_data:
                try:
                    loaded.texture = read_dxt_from_bytes(tex_data)
                except Exception as e:
                    result.warnings.append(f"Texture failed {part.texture_path}: {e}")

        # Load skins
        if part.skins_path:
            skins_data = resolver.read(part.skins_path)
            if skins_data:
                try:
                    cskins = N3CPartSkins.from_reader(BinaryReader(skins_data))
                    loaded.skin = cskins.highest_lod
                except Exception as e:
                    result.warnings.append(f"Skin failed {part.skins_path}: {e}")

        result.parts.append(loaded)

    # Load plugs
    for plug_path in chr_data.plug_paths:
        plug_data = resolver.read(plug_path)
        if not plug_data:
            result.warnings.append(f"Plug not found: {plug_path}")
            continue

        try:
            plug = N3CPlug.from_reader(BinaryReader(plug_data))
        except Exception as e:
            result.warnings.append(f"Plug parse failed {plug_path}: {e}")
            continue

        loaded_plug = LoadedPlug(name=plug.name or Path(plug_path).stem, plug=plug)

        if plug.texture_path:
            tex_data = resolver.read(plug.texture_path)
            if tex_data:
                try:
                    loaded_plug.texture = read_dxt_from_bytes(tex_data)
                except Exception as e:
                    result.warnings.append(f"Plug texture failed: {e}")

        result.plugs.append(loaded_plug)

    return result

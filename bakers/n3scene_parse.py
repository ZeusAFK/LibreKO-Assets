"""Convert Knight Online cutscenes (.n3scene + .n3scenex + .n3camera + .n3light + .n3chr) to JSON.

usage: python n3scene_parse.py <client_root> <out_dir>

All coordinates are written as stored (KO / Direct3D: left-handed, +Y up, metres, x,y,z; quaternions x,y,z,w).
Frames are scene frames at 30 per second.
"""

import glob
import json
import math
import os
import struct
import sys

SCENE_FPS = 30.0
KEY_VECTOR3 = 0
KEY_QUATERNION = 1
LIGHT_TYPES = {1: "point", 2: "spot", 3: "directional"}


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, data, path):
        self.data = data
        self.path = path
        self.pos = 0

    def take(self, size):
        if self.pos + size > len(self.data):
            raise ParseError("%s: read of %d bytes at %d runs past the end (%d)" % (self.path, size, self.pos, len(self.data)))
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def i32(self):
        return self.unpack("i")[0]

    def u32(self):
        return self.unpack("I")[0]

    def f32(self):
        return self.unpack("f")[0]

    def u8(self):
        return self.unpack("B")[0]

    def vec3(self):
        return list(self.unpack("3f"))

    def quat(self):
        return list(self.unpack("4f"))

    def string(self):
        length = self.i32()
        if length <= 0:
            return None
        return self.take(length).decode("latin-1")

    def remaining(self):
        return len(self.data) - self.pos


def clean(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, tuple):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def color_argb(value):
    return {
        "argb": "0x%08X" % value,
        "a": (value >> 24) & 0xFF,
        "r": (value >> 16) & 0xFF,
        "g": (value >> 8) & 0xFF,
        "b": value & 0xFF,
    }


def resolve(root, relative):
    if not relative:
        return None
    return os.path.join(root, relative.replace("/", "\\").replace("\\", os.sep))


def read_file(path):
    with open(path, "rb") as handle:
        return handle.read()


PACK_CACHE = {}


def pack_names(root, folder):
    key = folder.lower()
    if key in PACK_CACHE:
        return PACK_CACHE[key]
    names = None
    hdr = os.path.join(root, folder, folder + ".hdr")
    if os.path.isfile(hdr):
        data = read_file(hdr)
        version = struct.unpack_from("<I", data, 0)[0]
        short_names = (version & 0xFF0000) != 0
        pos = 8 if short_names else 4
        found = set()
        try:
            while pos < len(data):
                if short_names:
                    length = struct.unpack_from("<H", data, pos)[0]
                    pos += 2
                else:
                    length = struct.unpack_from("<I", data, pos)[0]
                    pos += 4
                name = data[pos:pos + length].decode("ascii")
                pos += length + 8
                found.add(name.lower())
            names = found
        except (struct.error, UnicodeDecodeError):
            names = None
    PACK_CACHE[key] = names
    return names


def locate(root, relative):
    if not relative:
        return None
    if os.path.isfile(resolve(root, relative)):
        return "disk"
    parts = relative.replace("/", "\\").split("\\")
    if len(parts) >= 2:
        names = pack_names(root, parts[0])
        if names and parts[-1].lower() in names:
            return "%s\\%s.src" % (parts[0], parts[0])
    return None


def read_anim_key(reader):
    count = reader.i32()
    if count <= 0:
        return {"count": 0}
    key_type = reader.u32()
    rate = reader.f32()
    if key_type == KEY_VECTOR3:
        keys = [reader.vec3() for _ in range(count)]
        type_name = "vector3"
    elif key_type == KEY_QUATERNION:
        keys = [reader.quat() for _ in range(count)]
        type_name = "quaternion"
    else:
        raise ParseError("%s: unknown animation key type %d" % (reader.path, key_type))
    return {
        "count": count,
        "type": type_name,
        "samplingRate": rate,
        "framesPerKey": SCENE_FPS / rate if rate else None,
        "lastKeyFrame": (count - 1) * SCENE_FPS / rate if rate else None,
        "keys": keys,
    }


def skip_anim_key(reader):
    count = reader.i32()
    if count <= 0:
        return {"count": 0}
    key_type = reader.u32()
    rate = reader.f32()
    reader.take(count * (12 if key_type == KEY_VECTOR3 else 16))
    return {"count": count, "type": "vector3" if key_type == KEY_VECTOR3 else "quaternion", "samplingRate": rate}


def read_transform(reader, keep_keys=True):
    name = reader.string()
    position = reader.vec3()
    rotation = reader.quat()
    scale = reader.vec3()
    read_key = read_anim_key if keep_keys else skip_anim_key
    key_position = read_key(reader)
    key_rotation = read_key(reader)
    key_scale = read_key(reader)
    return {
        "name": name,
        "position": position,
        "rotation": rotation,
        "scale": scale,
        "keyPosition": key_position,
        "keyRotation": key_rotation,
        "keyScale": key_scale,
    }


def parse_camera(path):
    reader = Reader(read_file(path), path)
    transform = read_transform(reader)
    at = reader.vec3()
    data_eye = reader.vec3()
    data_at = reader.vec3()
    data_up = reader.vec3()
    fov, aspect, near_plane, far_plane = reader.unpack("4f")
    viewport = list(reader.unpack("4I2f"))
    reader.take(64 * 3)
    reader.take(4 * 24)
    fog_use = reader.i32()
    fog_color = reader.u32()
    if reader.remaining():
        raise ParseError("%s: %d bytes left after camera" % (path, reader.remaining()))

    key_rotation = transform["keyRotation"]
    at_from_keys = key_rotation["count"] > 0 and key_rotation["type"] == "vector3"
    return {
        "name": transform["name"],
        "fovRadians": fov,
        "fovDegrees": math.degrees(fov),
        "fovAxis": "vertical",
        "aspectSaved": aspect,
        "near": near_plane,
        "farSaved": far_plane,
        "viewportSaved": viewport,
        "fogUse": fog_use,
        "fogColor": color_argb(fog_color),
        "staticEye": transform["position"],
        "staticAt": at,
        "staticUp": transform["scale"],
        "unusedRotation": transform["rotation"],
        "savedCameraData": {"eye": data_eye, "at": data_at, "up": data_up},
        "eyeTrack": transform["keyPosition"],
        "atTrack": key_rotation if at_from_keys else {"count": 0},
        "atSource": "keyRotation (vector3)" if at_from_keys else "staticAt",
        "ignoredQuaternionTrack": key_rotation if key_rotation["count"] > 0 and not at_from_keys else None,
        "upTrack": transform["keyScale"],
    }


def parse_light(path):
    reader = Reader(read_file(path), path)
    transform = read_transform(reader)
    light_type = reader.u32()
    diffuse = reader.quat()
    specular = reader.quat()
    ambient = reader.quat()
    position = reader.vec3()
    direction = reader.vec3()
    light_range, falloff, attenuation0, attenuation1, attenuation2, theta, phi = reader.unpack("7f")
    enabled = reader.i32()
    number = reader.i32()
    if reader.remaining():
        raise ParseError("%s: %d bytes left after light" % (path, reader.remaining()))
    return {
        "name": transform["name"],
        "transformPosition": transform["position"],
        "positionTrack": transform["keyPosition"],
        "type": LIGHT_TYPES.get(light_type, light_type),
        "diffuse": diffuse,
        "specular": specular,
        "ambient": ambient,
        "position": position,
        "direction": direction,
        "range": light_range,
        "falloff": falloff,
        "attenuation": [attenuation0, attenuation1, attenuation2],
        "thetaRadians": theta,
        "phiRadians": phi,
        "enabled": bool(enabled),
        "slot": number,
    }


def joint_summary(path):
    reader = Reader(read_file(path), path)
    joints = []

    def read_joint():
        name = reader.string()
        reader.take(40)
        keys = [skip_anim_key(reader) for _ in range(4)]
        joints.append((name, keys))
        for _ in range(reader.i32()):
            read_joint()

    read_joint()
    if reader.remaining():
        raise ParseError("%s: %d bytes left after joints" % (path, reader.remaining()))
    animated = [k for _, keys in joints for k in keys[:3] if k["count"] > 0]
    frames = max((k["count"] - 1) * SCENE_FPS / k["samplingRate"] for k in animated) if animated else 0.0
    rates = sorted({k["samplingRate"] for k in animated})
    return {"rootName": joints[0][0], "jointCount": len(joints), "lastKeyFrame": frames, "keyRates": rates}


def parse_chr(root, path):
    reader = Reader(read_file(path), path)
    transform = read_transform(reader, keep_keys=False)
    collision_mesh = reader.string()
    climb_mesh = reader.string()
    joint = reader.string()
    parts = [reader.string() for _ in range(reader.i32())]
    plugs = [reader.string() for _ in range(reader.i32())]
    anim = reader.string()
    joint_part_starts = list(reader.unpack("2i"))
    joint_part_ends = list(reader.unpack("2i"))
    fx_plug = reader.string()
    extra = reader.string() if reader.remaining() >= 4 else None
    if reader.remaining():
        raise ParseError("%s: %d bytes left after chr" % (path, reader.remaining()))

    def described(relative):
        if not relative:
            return None
        location = locate(root, relative)
        return {"file": relative, "exists": location is not None, "location": location}

    joint_info = described(joint)
    if joint_info and joint_info["location"] == "disk":
        try:
            joint_info.update(joint_summary(resolve(root, joint)))
        except ParseError as error:
            joint_info["error"] = str(error)
    return {
        "name": transform["name"],
        "position": transform["position"],
        "rotation": transform["rotation"],
        "scale": transform["scale"],
        "transformKeyCounts": [transform[k]["count"] for k in ("keyPosition", "keyRotation", "keyScale")],
        "collisionMesh": collision_mesh,
        "climbMesh": climb_mesh,
        "joint": joint_info,
        "parts": [described(p) for p in parts],
        "plugs": [described(p) for p in plugs],
        "animControl": described(anim),
        "jointPartStarts": joint_part_starts,
        "jointPartEnds": joint_part_ends,
        "fxPlug": described(fx_plug),
        "extraFile": described(extra),
    }


def parse_scene_file(path):
    reader = Reader(read_file(path), path)
    scene = {
        "activeCamera": reader.i32(),
        "frameSaved": reader.f32(),
        "frameStart": reader.f32(),
        "frameEnd": reader.f32(),
    }
    for kind in ("cameras", "lights", "shapes", "characters"):
        scene[kind] = [reader.string() for _ in range(reader.i32())]
    if reader.remaining():
        raise ParseError("%s: %d bytes left after scene" % (path, reader.remaining()))
    return scene


def parse_scenex_file(path):
    reader = Reader(read_file(path), path)
    version = reader.i32()
    cameras = []
    for _ in range(reader.i32()):
        offset = reader.vec3()
        yaw = reader.f32()
        next_camera = reader.i32()
        switch_frame = reader.f32()
        cameras.append({"worldOffset": offset, "yawDegrees": yaw, "nextCamera": next_camera, "switchFrame": switch_frame})
    shape_fx = []
    for _ in range(reader.i32()):
        fx_path = reader.string()
        if fx_path is None:
            shape_fx.append(None)
            continue
        position = reader.vec3()
        yaw = reader.f32()
        scale = reader.f32()
        start_frame = reader.f32()
        shape_fx.append({"fx": fx_path, "offset": position, "yawDegrees": yaw, "fxScale": scale, "startFrame": start_frame})
    characters = []
    for _ in range(reader.i32()):
        appear = reader.f32()
        disappear = reader.f32()
        unknown = reader.u32()
        record = {"appearFrame": appear, "disappearFrame": disappear, "objectSnapFlag": unknown}
        if version >= 2:
            record["fadeOutRate"] = reader.f32()
            record["position"] = reader.vec3()
            record["scale"] = reader.vec3()
            record["rotation"] = reader.quat()
        record["fxPlugPartStartFrames"] = [reader.f32() for _ in range(reader.i32())]
        characters.append(record)
    sounds = []
    for _ in range(reader.i32()):
        sound_path = reader.string()
        start_frame = reader.f32()
        volume = reader.f32()
        parameter = reader.u32()
        flag = reader.u8()
        sounds.append({"file": sound_path, "startFrame": start_frame, "volume": volume, "parameter": parameter, "flag": flag})
    fades = []
    for _ in range(reader.i32()):
        start_frame = reader.f32()
        fade_in = reader.f32()
        fade_out = reader.f32()
        hold = reader.f32()
        color = reader.u32()
        fades.append({
            "startFrame": start_frame,
            "fadeInFrames": fade_in,
            "holdFrames": hold,
            "fadeOutFrames": fade_out,
            "color": color_argb(color),
        })
    if reader.remaining():
        raise ParseError("%s: %d bytes left after scenex" % (path, reader.remaining()))
    return {"version": version, "cameras": cameras, "shapeFx": shape_fx, "characters": characters, "sounds": sounds, "fades": fades}


def rotate_y(vector, yaw_degrees):
    angle = math.radians(yaw_degrees)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    x, y, z = vector
    return [x * cos_a + z * sin_a, y, -x * sin_a + z * cos_a]


def place(vector, placement, translate):
    rotated = rotate_y(vector, placement["yawDegrees"])
    if not translate:
        return rotated
    offset = placement["worldOffset"]
    return [rotated[0] + offset[0], rotated[1] + offset[1], rotated[2] + offset[2]]


def world_tracks(camera, placement):
    def track(source, static, translate):
        if source["count"] > 0:
            return [place(v, placement, translate) for v in source["keys"]]
        return [place(static, placement, translate)]

    return {
        "eye": track(camera["eyeTrack"], camera["staticEye"], True),
        "at": track(camera["atTrack"], camera["staticAt"], True),
        "up": track(camera["upTrack"], camera["staticUp"], False),
    }


def camera_schedule(active, cameras, frame_start):
    schedule = []
    current, frame, seen = active, frame_start, set()
    while 0 <= current < len(cameras) and current not in seen:
        seen.add(current)
        schedule.append({"camera": current, "fromFrame": frame})
        placement = cameras[current].get("placement")
        if not placement:
            break
        nxt, switch = placement["nextCamera"], placement["switchFrame"]
        if nxt == -1 or switch == -1.0 or not (0 <= nxt < len(cameras)):
            break
        current, frame = nxt, max(switch, frame)
    return schedule


def convert_scene(root, scene_path):
    stem = os.path.splitext(os.path.basename(scene_path))[0]
    warnings = []
    scene = parse_scene_file(scene_path)
    scenex_path = os.path.splitext(scene_path)[0] + ".n3scenex"
    extra = parse_scenex_file(scenex_path) if os.path.isfile(scenex_path) else None
    if extra is None:
        warnings.append("no .n3scenex: cameras, characters and shapes keep the placement stored in their own files")

    cameras = []
    for relative in scene["cameras"]:
        path = resolve(root, relative)
        if not relative or not os.path.isfile(path):
            warnings.append("camera %s missing; the client drops it and later indexes shift" % relative)
            continue
        camera = parse_camera(path)
        camera["file"] = relative
        cameras.append(camera)

    lights = []
    for relative in scene["lights"]:
        path = resolve(root, relative)
        if not relative or not os.path.isfile(path):
            warnings.append("light %s missing" % relative)
            continue
        light = parse_light(path)
        light["file"] = relative
        lights.append(light)

    shapes = []
    for relative in scene["shapes"]:
        exists = locate(root, relative) is not None
        if not exists:
            warnings.append("shape %s missing; the client drops it, so any shape FX for it is never spawned" % relative)
            continue
        shapes.append({"file": relative})

    characters = []
    for relative in scene["characters"]:
        path = resolve(root, relative)
        if not relative or not os.path.isfile(path):
            warnings.append("character %s missing; the client drops it and later indexes shift" % relative)
            continue
        try:
            chr_data = parse_chr(root, path)
        except ParseError as error:
            warnings.append(str(error))
            chr_data = None
        characters.append({"file": relative, "location": locate(root, relative), "chr": chr_data})

    shape_fx_unapplied = []
    if extra:
        for index, placement in enumerate(extra["cameras"]):
            if index < len(cameras):
                cameras[index]["placement"] = placement
            else:
                warnings.append("scenex camera record %d has no loaded camera" % index)
        for index, record in enumerate(extra["shapeFx"]):
            if record is None:
                continue
            record["fxLocation"] = locate(root, record["fx"])
            if index < len(shapes):
                shapes[index]["fx"] = record
            else:
                shape_fx_unapplied.append(dict(record, shapeIndex=index))
                warnings.append("scenex shape FX %d (%s) has no loaded shape; the client skips it" % (index, record["fx"]))
        for index, record in enumerate(extra["characters"]):
            if index < len(characters):
                characters[index]["placement"] = record
            else:
                warnings.append("scenex character record %d has no loaded character" % index)
        if len(extra["characters"]) != len(characters):
            warnings.append("scenex has %d character records for %d characters" % (len(extra["characters"]), len(characters)))

    for camera in cameras:
        placement = camera.get("placement") or {"worldOffset": [0.0, 0.0, 0.0], "yawDegrees": 0.0}
        camera["world"] = world_tracks(camera, placement)

    for character in characters:
        placement = character.get("placement")
        chr_data = character.get("chr")
        if placement and "position" in placement:
            character["effective"] = {"position": placement["position"], "rotation": placement["rotation"], "scale": placement["scale"]}
        elif chr_data:
            character["effective"] = {"position": chr_data["position"], "rotation": chr_data["rotation"], "scale": chr_data["scale"]}

    return {
        "scene": stem,
        "source": {
            "n3scene": os.path.relpath(scene_path, root),
            "n3scenex": os.path.relpath(scenex_path, root) if extra else None,
            "scenexVersion": extra["version"] if extra else None,
        },
        "framesPerSecond": SCENE_FPS,
        "frameStart": scene["frameStart"],
        "frameEnd": scene["frameEnd"],
        "frameSavedInEditor": scene["frameSaved"],
        "activeCamera": scene["activeCamera"],
        "cameraSchedule": camera_schedule(scene["activeCamera"], cameras, scene["frameStart"]),
        "cameras": cameras,
        "lights": lights,
        "shapes": shapes,
        "shapeFxWithoutShape": shape_fx_unapplied,
        "characters": characters,
        "sounds": extra["sounds"] if extra else [],
        "fades": extra["fades"] if extra else [],
        "listedFiles": {k: scene[k] for k in ("cameras", "lights", "shapes", "characters")},
        "warnings": warnings,
    }


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    root, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    scene_paths = sorted(glob.glob(os.path.join(root, "Scene", "*.n3scene")))
    scenex_paths = sorted(glob.glob(os.path.join(root, "Scene", "*.n3scenex")))
    failures = 0
    summary = []
    for scene_path in scene_paths:
        stem = os.path.splitext(os.path.basename(scene_path))[0]
        try:
            result = convert_scene(root, scene_path)
        except ParseError as error:
            failures += 1
            print("FAIL %s: %s" % (stem, error))
            continue
        with open(os.path.join(out_dir, stem + ".json"), "w", encoding="utf-8") as handle:
            json.dump(clean(result), handle, indent=1)
        summary.append(result)
        print("%-28s frames %5.0f-%-5.0f cams %d (active %d, schedule %s) lights %d shapes %d chrs %d fades %d%s" % (
            stem, result["frameStart"], result["frameEnd"], len(result["cameras"]), result["activeCamera"],
            [s["camera"] for s in result["cameraSchedule"]], len(result["lights"]), len(result["shapes"]),
            len(result["characters"]), len(result["fades"]),
            "" if not result["warnings"] else "  WARN: " + " | ".join(result["warnings"])))
    orphan_scenex = [p for p in scenex_paths if not os.path.isfile(os.path.splitext(p)[0] + ".n3scene")]
    for path in orphan_scenex:
        print("scenex without scene: %s" % path)
    print("%d scenes, %d scenex, %d failures" % (len(scene_paths), len(scenex_paths), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

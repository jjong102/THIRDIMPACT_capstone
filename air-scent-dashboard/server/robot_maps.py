#!/usr/bin/env python3
"""List, preview, and select maps on the driving Jetson."""

import json
import re
import struct
import subprocess
import sys
import zlib

HOST = "robot"
MAP_DIR = "/home/moonshot/capstone_final_ws/src/third_impact/maps"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")

LIST_SCRIPT = r"""
import json
from pathlib import Path

root = Path("/home/moonshot/capstone_final_ws/src/third_impact/maps")
selected_path = Path.home() / ".ros" / "third_impact" / "selected_map.json"
view_path = Path.home() / ".ros" / "third_impact" / "map_view.json"
selected = None
try:
    selected = json.loads(selected_path.read_text()).get("name")
except Exception:
    selected = None
rotations = {}
zooms = {}
centers = {}
try:
    loaded = json.loads(view_path.read_text())
    if isinstance(loaded.get("rotations"), dict):
        rotations = loaded["rotations"]
    if isinstance(loaded.get("zooms"), dict):
        zooms = loaded["zooms"]
    if isinstance(loaded.get("centers"), dict):
        centers = loaded["centers"]
except Exception:
    rotations = {}
    zooms = {}
    centers = {}

def header(path):
    data = path.read_bytes()[:512]
    if not data.startswith(b"P5"):
        return None
    i = 2
    tokens = []
    while len(tokens) < 3 and i < len(data):
        if data[i] in b" \t\r\n":
            i += 1
            continue
        if data[i] == ord("#"):
            while i < len(data) and data[i] not in b"\n\r":
                i += 1
            continue
        start = i
        while i < len(data) and data[i] not in b" \t\r\n":
            i += 1
        tokens.append(data[start:i].decode())
    if len(tokens) < 2:
        return None
    return {"width": int(tokens[0]), "height": int(tokens[1])}

maps = []
if root.is_dir():
    names = set()
    for path in root.iterdir():
        if path.is_file() and path.suffix.lower() in {".pgm", ".yaml", ".yml"}:
            names.add(path.stem)
    for name in sorted(names):
        pgm = root / f"{name}.pgm"
        yaml_path = root / f"{name}.yaml"
        if not yaml_path.is_file():
            alt = root / f"{name}.yml"
            yaml_path = alt if alt.is_file() else yaml_path
        item = {
            "name": name,
            "pgm": pgm.name if pgm.is_file() else None,
            "yaml": yaml_path.name if yaml_path.is_file() else None,
            "bytes": pgm.stat().st_size if pgm.is_file() else 0,
            "updatedAt": int((pgm if pgm.is_file() else yaml_path).stat().st_mtime) if pgm.is_file() or yaml_path.is_file() else None,
        }
        if pgm.is_file():
            size = header(pgm)
            if size:
                item.update(size)
        if yaml_path.is_file():
            try:
                import yaml
                data = yaml.safe_load(yaml_path.read_text()) or {}
                item["resolution"] = data.get("resolution")
                item["origin"] = data.get("origin")
                item["mode"] = data.get("mode")
                item["image"] = data.get("image")
            except Exception:
                pass
        try:
            item["rotation"] = round(float(rotations.get(name, 0)), 1)
        except Exception:
            item["rotation"] = 0
        try:
            item["zoom"] = int(round(float(zooms.get(name, 100))))
        except Exception:
            item["zoom"] = 100
        center = centers.get(name) if isinstance(centers.get(name), dict) else {}
        try:
            item["centerX"] = round(float(center.get("x", 50)), 1)
            item["centerY"] = round(float(center.get("y", 50)), 1)
        except Exception:
            item["centerX"] = 50
            item["centerY"] = 50
        maps.append(item)

active = None
names = {item["name"] for item in maps if item.get("yaml") and item.get("pgm")}
if selected in names:
    active = selected
elif "third_impact_map" in names:
    active = "third_impact_map"

print(json.dumps({
    "ok": True,
    "folder": str(root),
    "selected": selected if selected in names else None,
    "active": active,
    "maps": maps,
}, ensure_ascii=False))
"""


def fail(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def ssh(args, payload=None, timeout=20):
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", HOST, *args],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("주행 젯슨 응답이 없습니다")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        fail(detail or "주행 젯슨에 연결하지 못했습니다")
    return result.stdout


def valid_name(name):
    if not NAME_RE.fullmatch(name or ""):
        fail("맵 이름이 올바르지 않습니다")
    return name


def parse_p5(data):
    if not data.startswith(b"P5"):
        fail("PGM 이미지가 아닙니다")
    index = 2
    tokens = []
    while len(tokens) < 3:
        if index >= len(data):
            fail("PGM 헤더가 짧습니다")
        if data[index] in b" \t\r\n":
            index += 1
            continue
        if data[index] == ord("#"):
            while index < len(data) and data[index] not in b"\n\r":
                index += 1
            continue
        start = index
        while index < len(data) and data[index] not in b" \t\r\n":
            index += 1
        tokens.append(data[start:index].decode())
    width, height, maxval = (int(tokens[0]), int(tokens[1]), int(tokens[2]))
    if index < len(data) and data[index] in b" \t\r\n":
        index += 2 if data[index:index + 2] == b"\r\n" else 1
    if maxval <= 0 or maxval > 255:
        fail("8비트 PGM만 표시할 수 있습니다")
    count = width * height
    pixels = data[index:index + count]
    if len(pixels) < count:
        fail("PGM 픽셀이 부족합니다")
    return width, height, pixels


def display_rgba(width, height, pixels):
    """빈 공간은 투명하게 두고, 벽 경계만 선으로 남긴다."""
    occupied = [value <= 128 for value in pixels]
    out = bytearray(width * height * 4)
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            if not occupied[index]:
                continue
            edge = (
                (x > 0 and not occupied[index - 1])
                or (x + 1 < width and not occupied[index + 1])
                or (y > 0 and not occupied[index - width])
                or (y + 1 < height and not occupied[index + width])
            )
            if not edge:
                continue
            start = index * 4
            out[start:start + 4] = b"\x37\x37\x37\xff"
    return bytes(out)


def png_bytes(width, height, rgba):
    def chunk(tag, payload):
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    stride = width * 4
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def command_list():
    sys.stdout.buffer.write(ssh(["python3", "-"], LIST_SCRIPT.encode()))


def command_png(name):
    valid_name(name)
    remote = f"{MAP_DIR}/{name}.pgm"
    data = ssh(["cat", "--", remote], timeout=30)
    width, height, pixels = parse_p5(data)
    sys.stdout.buffer.write(png_bytes(width, height, display_rgba(width, height, pixels)))


def command_rotate(name, degrees):
    valid_name(name)
    try:
        deg = float(degrees)
    except ValueError:
        fail("회전 각도가 올바르지 않습니다")
    if deg != deg or abs(deg) > 180.0001:
        fail("회전 각도가 올바르지 않습니다")
    deg = round(deg, 1)
    script = f"""
import json
from pathlib import Path
name = {json.dumps(name)}
deg = {deg}
path = Path.home() / ".ros" / "third_impact" / "map_view.json"
path.parent.mkdir(parents=True, exist_ok=True)
data = {{}}
try:
    data = json.loads(path.read_text())
except Exception:
    data = {{}}
rotations = data.get("rotations")
if not isinstance(rotations, dict):
    rotations = {{}}
rotations[name] = deg
data["rotations"] = rotations
path.write_text(json.dumps(data, ensure_ascii=False) + "\\n")
print(json.dumps({{"ok": True, "name": name, "rotation": deg}}, ensure_ascii=False))
"""
    sys.stdout.buffer.write(ssh(["python3", "-"], script.encode()))


def command_view(name, degrees, zoom, center_x=50, center_y=50):
    valid_name(name)
    try:
        deg = float(degrees)
        zoom_value = int(round(float(zoom)))
        cx = round(float(center_x), 1)
        cy = round(float(center_y), 1)
    except ValueError:
        fail("맵 보기 값이 올바르지 않습니다")
    if (
        deg != deg
        or abs(deg) > 180.0001
        or zoom_value < 50
        or zoom_value > 250
        or cx < 0
        or cx > 100
        or cy < 0
        or cy > 100
    ):
        fail("맵 보기 값이 올바르지 않습니다")
    deg = round(deg, 1)
    script = f"""
import json
from pathlib import Path
name = {json.dumps(name)}
deg = {deg}
zoom = {zoom_value}
center = {{"x": {cx}, "y": {cy}}}
path = Path.home() / ".ros" / "third_impact" / "map_view.json"
path.parent.mkdir(parents=True, exist_ok=True)
data = {{}}
try:
    data = json.loads(path.read_text())
except Exception:
    data = {{}}
rotations = data.get("rotations")
zooms = data.get("zooms")
centers = data.get("centers")
if not isinstance(rotations, dict):
    rotations = {{}}
if not isinstance(zooms, dict):
    zooms = {{}}
if not isinstance(centers, dict):
    centers = {{}}
rotations[name] = deg
zooms[name] = zoom
centers[name] = center
data["rotations"] = rotations
data["zooms"] = zooms
data["centers"] = centers
path.write_text(json.dumps(data, ensure_ascii=False) + "\\n")
print(json.dumps({{"ok": True, "name": name, "rotation": deg, "zoom": zoom, "centerX": center["x"], "centerY": center["y"]}}, ensure_ascii=False))
"""
    sys.stdout.buffer.write(ssh(["python3", "-"], script.encode()))


def command_select(name):
    valid_name(name)
    script = f"""
import json
from pathlib import Path
name = {json.dumps(name)}
root = Path({json.dumps(MAP_DIR)})
yaml_path = root / f"{{name}}.yaml"
pgm_path = root / f"{{name}}.pgm"
if not yaml_path.is_file() or not pgm_path.is_file():
    raise SystemExit("맵 파일 쌍이 없습니다")
dest = Path.home() / ".ros" / "third_impact" / "selected_map.json"
dest.parent.mkdir(parents=True, exist_ok=True)
payload = {{"name": name, "yaml": str(yaml_path.resolve())}}
dest.write_text(json.dumps(payload, ensure_ascii=False) + "\\n")
print(json.dumps({{"ok": True, **payload}}, ensure_ascii=False))
"""
    sys.stdout.buffer.write(ssh(["python3", "-"], script.encode()))


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "list":
        command_list()
        return
    if action == "png" and len(sys.argv) == 3:
        command_png(sys.argv[2])
        return
    if action == "select" and len(sys.argv) == 3:
        command_select(sys.argv[2])
        return
    if action == "rotate" and len(sys.argv) == 4:
        command_rotate(sys.argv[2], sys.argv[3])
        return
    if action == "view" and len(sys.argv) == 5:
        command_view(sys.argv[2], sys.argv[3], sys.argv[4])
        return
    if action == "view" and len(sys.argv) == 7:
        command_view(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6])
        return
    fail("사용법: robot_maps.py list|png NAME|select NAME|rotate NAME DEGREES|view NAME DEGREES ZOOM [CENTER_X CENTER_Y]")


if __name__ == "__main__":
    main()

import os
import sys
import re
import json
import base64
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import cadquery as cq
from groq import Groq

# ================== ENV ==================
load_dotenv()

# Add cq_gears to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cq_gears"))
from cq_gears import SpurGear, RingGear, BevelGear

# ================== APP ==================
app = Flask(__name__)
CORS(app, origins="*")

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ================== DEFAULTS ==================
DEFAULTS = {
    "cuboid": {"length": 100, "width": 60, "height": 40},
    "hollow_cylinder": {"od": 50, "id": 30, "height": 100},
    "shaft": {"diameter": 20, "length": 100},
    "flange": {"od": 100, "id": 40, "thickness": 20},
    "flange_holes": {"od": 100, "id": 40, "thickness": 20, "pcd": 70, "holes": 6, "hole_dia": 10},
    "washer": {"od": 40, "id": 20, "thickness": 5},
    "nut": {"af": 30, "thickness": 10, "hole_dia": 16},
    "pulley": {"od": 80, "width": 30, "bore": 20},
}

PART_KEYWORDS = {
    "cuboid": "cuboid",
    "block": "cuboid",
    "hollow cylinder": "hollow_cylinder",
    "pipe": "hollow_cylinder",
    "shaft": "shaft",
    "flange with holes": "flange_holes",
    "flange": "flange",
    "washer": "washer",
    "nut": "nut",
    "pulley": "pulley",
}

DIMENSION_PATTERNS = {
    "od": r"(?:outer\s*diameter|od)\s*[:=]?\s*(\d+\.?\d*)",
    "id": r"(?:inner\s*diameter|id)\s*[:=]?\s*(\d+\.?\d*)",
    "diameter": r"(?:diameter|dia|d)\s*[:=]?\s*(\d+\.?\d*)",
    "length": r"(?:length|long|l)\s*[:=]?\s*(\d+\.?\d*)",
    "thickness": r"(?:thickness|thick|t)\s*[:=]?\s*(\d+\.?\d*)",
    "pcd": r"(?:bolt\s*circle\s*diameter|pcd)\s*[:=]?\s*(\d+\.?\d*)",
    "holes": r"(?:number\s*of\s*holes|holes)\s*[:=]?\s*(\d+)",
    "hole_dia": r"(?:hole\s*diameter|hole\s*size|hole_dia)\s*[:=]?\s*(\d+\.?\d*)",
    "af": r"(?:across\s*flats|af)\s*[:=]?\s*(\d+\.?\d*)",
    "width": r"(?:width|w)\s*[:=]?\s*(\d+\.?\d*)",
    "bore": r"(?:bore|inner\s*hole)\s*[:=]?\s*(\d+\.?\d*)",
    "height": r"(?:height|h)\s*[:=]?\s*(\d+\.?\d*)",
}

# ================== TEMPLATE FUNCTIONS ==================
def make_cuboid(L, W, H):
    return cq.Workplane("XY").box(L, W, H)

def make_hollow_cylinder(od, id, height):
    result = cq.Workplane("XY").circle(od/2).extrude(height)
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()
    return result

def make_shaft(length, diameter):
    return cq.Workplane("XY").circle(diameter/2).extrude(length)

def make_flange(od, id, thickness):
    result = cq.Workplane("XY").circle(od/2).extrude(thickness)
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()
    return result

def make_flange_with_holes(od, id, thickness, pcd, holes, hole_dia):
    result = cq.Workplane("XY").circle(od/2).extrude(thickness)
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()
    result = result.faces(">Z").workplane().polarArray(pcd/2, 0, 360, holes).circle(hole_dia/2).cutThruAll()
    return result

def make_washer(od, id, thickness):
    result = cq.Workplane("XY").circle(od/2).extrude(thickness)
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()
    return result

def make_hex_nut(af, thickness, hole_dia):
    hexagon = cq.Workplane("XY").polygon(6, af).extrude(thickness)
    result = hexagon.faces(">Z").workplane().circle(hole_dia/2).cutThruAll()
    return result

def make_pulley(od, width, bore):
    result = cq.Workplane("XY").circle(od/2).extrude(width)
    result = result.faces(">Z").workplane().circle(bore/2).cutThruAll()
    return result

# ================== PART CLASSIFIER & DIMENSION PARSER ==================
def classify_part(prompt):
    p = prompt.lower()
    for kw, part in PART_KEYWORDS.items():
        if kw in p:
            return part
    return None

def extract_dimensions(prompt):
    dims = {}
    p = prompt.lower()
    for key, pattern in DIMENSION_PATTERNS.items():
        match = re.search(pattern, p)
        if match:
            if key in ["holes"]:
                dims[key] = int(match.group(1))
            else:
                dims[key] = float(match.group(1))
    return dims

def fill_defaults(part, extracted):
    final = DEFAULTS.get(part, {}).copy()
    final.update(extracted)
    return final

# ================== GENERATOR ==================
def generate_part(prompt):
    part = classify_part(prompt)
    if not part:
        return None, "Part type not recognized"

    dims = extract_dimensions(prompt)
    params = fill_defaults(part, dims)

    if part == "cuboid":
        return make_cuboid(params["length"], params["width"], params["height"]), None
    elif part == "hollow_cylinder":
        return make_hollow_cylinder(params["od"], params["id"], params["height"]), None
    elif part == "shaft":
        return make_shaft(params["length"], params["diameter"]), None
    elif part == "flange":
        return make_flange(params["od"], params["id"], params["thickness"]), None
    elif part == "flange_holes":
        return make_flange_with_holes(params["od"], params["id"], params["thickness"], params["pcd"], params["holes"], params["hole_dia"]), None
    elif part == "washer":
        return make_washer(params["od"], params["id"], params["thickness"]), None
    elif part == "nut":
        return make_hex_nut(params["af"], params["thickness"], params["hole_dia"]), None
    elif part == "pulley":
        return make_pulley(params["od"], params["width"], params["bore"]), None
    return None, "Part template not implemented"

# ================== CONNECTING ROD TEMPLATE ==================
def make_connecting_rod(length, big_end_od, big_end_id, small_end_od, small_end_id, thickness):
    big_end = cq.Workplane("XY").circle(big_end_od/2).extrude(thickness)
    big_end = big_end.faces(">Z").workplane().circle(big_end_id/2).cutThruAll()
    small_end = cq.Workplane("XY").center(length, 0).circle(small_end_od/2).extrude(thickness)
    small_end = small_end.faces(">Z").workplane().circle(small_end_id/2).cutThruAll()
    rib = cq.Workplane("XY").box(length, thickness, thickness).translate((length/2, 0, thickness/2))
    result = big_end.union(small_end).union(rib)
    return result

# ================== GENERATE ROUTE ==================
@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get("prompt", "")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Generate CAD
    try:
        result, error_msg = generate_part(prompt)
        if not result:
            raise ValueError(error_msg)

        os.makedirs("outputs", exist_ok=True)
        step_path = f"outputs/part_{timestamp}.step"
        stl_path = f"outputs/part_{timestamp}.stl"
        cq.exporters.export(result, step_path)
        cq.exporters.export(result, stl_path)

        # Encode for API
        with open(step_path, "rb") as f:
            step_b64 = base64.b64encode(f.read()).decode("utf-8")
        with open(stl_path, "rb") as f:
            stl_b64 = base64.b64encode(f.read()).decode("utf-8")

        status = "success"
    except Exception as e:
        step_b64, stl_b64 = None, None
        status = "failed"
        error_msg = str(e)

    # ========== LOGGING ==========
    log_entry = {
        "timestamp": timestamp,
        "prompt": prompt,
        "status": status,
        "error": error_msg if status=="failed" else None,
        "output": {"step_file": step_path if status=="success" else None,
                   "stl_file": stl_path if status=="success" else None}
    }
    os.makedirs("logs", exist_ok=True)
    with open(f"logs/log_{timestamp}.json", "w") as f:
        json.dump(log_entry, f, indent=2)

    # ========== RESPONSE ==========
    return jsonify({
        "step_b64": step_b64,
        "stl_b64": stl_b64,
        "status": status,
        "error": error_msg if status=="failed" else None
    })

# ================== MAIN ==================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
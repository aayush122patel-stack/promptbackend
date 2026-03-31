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
from groq import Groq  # AI fallback

# ================== ENV ==================
load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cq_gears"))
from cq_gears import SpurGear, RingGear, BevelGear

# ================== APP ==================
app = Flask(__name__)
CORS(app, origins="*")  # Allow any origin

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
    "connecting_rod": {"length": 200, "big_end_od": 30, "big_end_id": 15, "small_end_od": 20, "small_end_id": 10, "thickness": 10},
    "spur_gear": {"module": 2, "teeth": 20, "width": 30, "bore": 0},
    "ring_gear": {"module": 2, "teeth": 40, "width": 30},
    "bevel_gear": {"module": 2, "teeth": 20, "width": 20},
}

PART_KEYWORDS = {
    "connecting rod": "connecting_rod",
    "conrod": "connecting_rod",
    "ring gear": "ring_gear",
    "bevel gear": "bevel_gear",
    "spur gear": "spur_gear",
    "mechanical gear": "spur_gear",
    "gear with bore": "spur_gear",
    "gear with hole": "spur_gear",
    "gear": "spur_gear",
    "flange with holes": "flange_holes",
    "flange with bolt": "flange_holes",
    "flange": "flange",
    "hollow cylinder": "hollow_cylinder",
    "pipe": "hollow_cylinder",
    "tube": "hollow_cylinder",
    "cuboid": "cuboid",
    "block": "cuboid",
    "rectangular block": "cuboid",
    "shaft": "shaft",
    "washer": "washer",
    "nut": "nut",
    "pulley": "pulley",
}

DIMENSION_PATTERNS = {
    "od": r"(?:outer\s*diameter|od)\s*[:=]?\s*(\d+\.?\d*)",
    "id": r"(?:inner\s*diameter|id)\s*[:=]?\s*(\d+\.?\d*)",
    "diameter": r"(?:diameter|dia)\s*[:=]?\s*(\d+\.?\d*)",
    "length": r"(?:length|long)\s*[:=]?\s*(\d+\.?\d*)",
    "thickness": r"(?:thickness|thick)\s*[:=]?\s*(\d+\.?\d*)",
    "pcd": r"(?:bolt\s*circle\s*diameter|bcd|pcd)\s*[:=]?\s*(\d+\.?\d*)",
    "holes": r"(\d+)\s*(?:bolt\s*holes|holes)",
    "hole_dia": r"(?:hole\s*diameter|hole\s*dia)\s*[:=]?\s*(\d+\.?\d*)",
    "af": r"(?:across\s*flats|af)\s*[:=]?\s*(\d+\.?\d*)",
    "width": r"(?:width|wide)\s*[:=]?\s*(\d+\.?\d*)",
    "bore": r"(\d+\.?\d*)\s*mm\s*bore|(?:bore|bore\s*diameter|bore\s*dia)\s*[:=]?\s*(\d+\.?\d*)",
    "height": r"(?:height|tall)\s*[:=]?\s*(\d+\.?\d*)",
    "big_end_od": r"big\s*end\s*(?:od|outer|diameter)\s*[:=]?\s*(\d+\.?\d*)",
    "big_end_id": r"big\s*end\s*(?:id|inner|bore)\s*[:=]?\s*(\d+\.?\d*)",
    "small_end_od": r"small\s*end\s*(?:od|outer|diameter)\s*[:=]?\s*(\d+\.?\d*)",
    "small_end_id": r"small\s*end\s*(?:id|inner|bore)\s*[:=]?\s*(\d+\.?\d*)",
    "module": r"module\s*[:=]?\s*(\d+\.?\d*)",
    "teeth": r"(\d+)\s*teeth",
}

# ================== VALIDATIONS ==================
def validate_hollow(od, id, label="Part"):
    if id >= od:
        raise ValueError(f"{label}: Inner diameter ({id}mm) must be less than outer diameter ({od}mm)")

def validate_positive(*args):
    for name, val in args:
        if val <= 0:
            raise ValueError(f"{name} must be greater than 0, got {val}")

def validate_flange_holes(od, pcd, hole_dia):
    if pcd/2 + hole_dia/2 >= od/2:
        raise ValueError(f"Bolt holes extend beyond flange outer diameter. Reduce PCD or hole diameter.")

def validate_gear_bore(pitch_diameter, bore):
    if bore > 0 and bore >= pitch_diameter:
        raise ValueError(f"Bore diameter ({bore}mm) must be smaller than gear pitch diameter ({pitch_diameter}mm)")

# ================== TEMPLATE FUNCTIONS ==================
def make_cuboid(length, width, height):
    validate_positive(("length", length), ("width", width), ("height", height))
    return cq.Workplane("XY").box(length, width, height)

def make_flange_with_holes(od, id, thickness, pcd, holes, hole_dia):
    validate_positive(("od", od), ("id", id), ("thickness", thickness), ("pcd", pcd), ("holes", holes), ("hole_dia", hole_dia))
    validate_hollow(od, id, "Flange")
    validate_flange_holes(od, pcd, hole_dia)
    result = cq.Workplane("XY").circle(od/2).extrude(thickness)
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()
    result = result.faces(">Z").workplane().polarArray(pcd/2, 0, 360, int(holes)).circle(hole_dia/2).cutThruAll()
    return result

def make_spur_gear(module, teeth, width, bore=0):
    validate_positive(("module", module), ("teeth", teeth), ("width", width))
    pitch_diameter = module * teeth
    if bore > 0:
        validate_gear_bore(pitch_diameter, bore)
        gear = SpurGear(module=module, teeth_number=int(teeth), width=width, bore_d=bore)
    else:
        gear = SpurGear(module=module, teeth_number=int(teeth), width=width)
    return gear.build()

# ================== CLASSIFIER & PARSER ==================
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
            if key == "bore":
                val = match.group(1) or match.group(2)
                dims[key] = float(val)
            elif key in ["holes", "teeth"]:
                dims[key] = int(match.group(1))
            else:
                dims[key] = float(match.group(1))
    return dims

def fill_defaults(part, extracted):
    final = DEFAULTS.get(part, {}).copy()
    final.update(extracted)
    return final

def generate_from_template(prompt):
    part = classify_part(prompt)
    if not part:
        return None, "No template match"

    dims = extract_dimensions(prompt)
    p = fill_defaults(part, dims)

    try:
        if part == "cuboid":
            return make_cuboid(p["length"], p["width"], p["height"]), None
        elif part == "flange_holes":
            return make_flange_with_holes(p["od"], p["id"], p["thickness"], p["pcd"], p["holes"], p["hole_dia"]), None
        elif part == "spur_gear":
            return make_spur_gear(p["module"], p["teeth"], p["width"], p.get("bore", 0)), None
        else:
            return None, "Template not implemented"
    except Exception as e:
        return None, str(e)

# ================== AI FALLBACK ==================
def generate_with_ai(prompt, error=None):
    error_context = f"\nPrevious error: {error}\nFix it." if error else ""
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": f"""
You are a CadQuery expert. Write Python code using CadQuery to build this part:
{prompt}
{error_context}

Rules:
- Start with: import cadquery as cq
- Store final shape in variable called 'result'
- Do NOT use import_cadquery() or any other import
- Do NOT export, show, or add input()
- Return ONLY Python code, no explanation, no backticks, no markdown

Available:
- cadquery as cq
- from cq_gears import SpurGear, RingGear, BevelGear
- SpurGear(module=2, teeth_number=20, width=30, bore_d=20).build()
"""}]
    )
    return response.choices[0].message.content

def run_ai_with_retry(prompt):
    error = None
    for attempt in range(3):
        code = generate_with_ai(prompt, error)
        try:
            exec_globals = {"cq": cq, "SpurGear": SpurGear, "RingGear": RingGear, "BevelGear": BevelGear}
            exec(code, exec_globals)
            return exec_globals["result"], None
        except Exception as e:
            error = str(e)
    return None, f"AI failed after 3 attempts: {error}"

# ================== GENERATE ROUTE ==================
@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get("prompt", "")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    error_msg = None
    step_b64 = None
    stl_b64 = None
    status = "failed"
    method = None

    try:
        result, err = generate_from_template(prompt)
        if result:
            method = "template"
        else:
            result, err = run_ai_with_retry(prompt)
            method = "ai"

        if not result:
            raise ValueError(err)

        os.makedirs("outputs", exist_ok=True)
        step_path = f"outputs/part_{timestamp}.step"
        stl_path = f"outputs/part_{timestamp}.stl"
        cq.exporters.export(result, step_path)
        cq.exporters.export(result, stl_path)

        with open(step_path, "rb") as f:
            step_b64 = base64.b64encode(f.read()).decode("utf-8")
        with open(stl_path, "rb") as f:
            stl_b64 = base64.b64encode(f.read()).decode("utf-8")

        status = "success"

    except Exception as e:
        error_msg = str(e)

    return jsonify({
        "step_b64": step_b64,
        "stl_b64": stl_b64,
        "status": status,
        "error": error_msg,
        "method": method
    })

# ================== MAIN ==================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
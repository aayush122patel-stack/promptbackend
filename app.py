import os
import sys
import re
import json
import base64
import math
import uuid
from datetime import datetime

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import cadquery as cq
from groq import Groq

# ================== ENV ==================
load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cq_gears"))
from cq_gears import SpurGear, RingGear, BevelGear

# ================== APP ==================
app = Flask(__name__)
CORS(app, origins="*")

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# ================== DEFAULTS ==================
DEFAULTS = {
    "flange_holes": {"od": 100, "id": 40, "thickness": 20, "pcd": 70, "holes": 6, "hole_dia": 10},
    "spur_gear": {"module": 2, "teeth": 20, "width": 30, "bore": None},
}

# ================== KEYWORDS (ORDER MATTERS) ==================
PART_KEYWORDS = {
    "flange with holes": "flange_holes",
    "spur gear": "spur_gear",
    "gear": "spur_gear",  # keep generic LAST
}

# ================== REGEX ==================
DIMENSION_PATTERNS = {
    "od": r"(?:outer\s*diameter|od)\s*[:=]?\s*(\d+\.?\d*)",
    "id": r"(?:inner\s*diameter|id)\s*[:=]?\s*(\d+\.?\d*)",
    "thickness": r"(?:thickness)\s*[:=]?\s*(\d+\.?\d*)",
    "pcd": r"(?:pcd|bcd)\s*[:=]?\s*(\d+\.?\d*)",
    "holes": r"(\d+)\s*(?:x\s*)?(?:holes|bolt\s*holes)",
    "hole_dia": r"(?:hole\s*(?:diameter|dia)|of)\s*(\d+\.?\d*)",
    "module": r"module\s*[:=]?\s*(\d+\.?\d*)",
    "teeth": r"(\d+)\s*teeth",
    "width": r"(?:width)\s*[:=]?\s*(\d+\.?\d*)",
    "bore": r"(?:bore|bore\s*diameter)?\s*[:=]?\s*(\d+\.?\d*)\s*mm",
}

# ================== VALIDATION ==================
def validate_positive(*args):
    for name, val in args:
        if val is None or val <= 0:
            raise ValueError(f"{name} must be > 0")

def validate_hollow(od, id):
    if id >= od:
        raise ValueError("Inner diameter must be less than outer diameter")

def validate_flange_holes(od, pcd, hole_dia):
    if pcd/2 + hole_dia/2 > od/2:
        raise ValueError("Holes exceed flange boundary")

# ================== GEOMETRY ==================
def make_flange_with_holes(od, id, thickness, pcd, holes, hole_dia):
    validate_positive(
        ("od", od), ("id", id), ("thickness", thickness),
        ("pcd", pcd), ("holes", holes), ("hole_dia", hole_dia)
    )
    validate_hollow(od, id)
    validate_flange_holes(od, pcd, hole_dia)

    result = cq.Workplane("XY").circle(od/2).extrude(thickness)

    # inner bore
    result = result.faces(">Z").workplane().circle(id/2).cutThruAll()

    # hole positions
    wp = result.faces(">Z").workplane(centerOption="CenterOfMass")

    points = [
        (
            (pcd/2) * math.cos(2 * math.pi * i / holes),
            (pcd/2) * math.sin(2 * math.pi * i / holes)
        )
        for i in range(int(holes))
    ]

    result = wp.pushPoints(points).circle(hole_dia/2).cutThruAll()
    return result

def make_spur_gear(module, teeth, width, bore=None):
    validate_positive(("module", module), ("teeth", teeth), ("width", width))
    pitch_diameter = module * teeth

    if bore:
        if bore >= pitch_diameter * 0.6:
            raise ValueError("Bore too large")
        gear = SpurGear(module=module, teeth_number=int(teeth), width=width, bore_d=bore)
    else:
        gear = SpurGear(module=module, teeth_number=int(teeth), width=width)

    return gear.build()

# ================== PARSER ==================
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
            if key in ["holes", "teeth"]:
                dims[key] = int(match.group(1))
            else:
                dims[key] = float(match.group(1))

    return dims

def fill_defaults(part, extracted):
    final = DEFAULTS.get(part, {}).copy()
    final.update(extracted)
    return final

# ================== TEMPLATE ==================
def generate_from_template(prompt):
    part = classify_part(prompt)
    if not part:
        return None, "No template match"

    dims = extract_dimensions(prompt)
    params = fill_defaults(part, dims)

    print("[DEBUG] Part:", part)
    print("[DEBUG] Extracted:", dims)
    print("[DEBUG] Final:", params)

    try:
        if part == "flange_holes":
            return make_flange_with_holes(**params), None
        elif part == "spur_gear":
            return make_spur_gear(**params), None
    except Exception as e:
        return None, str(e)

    return None, "Template not implemented"

# ================== AI ==================
def generate_with_ai(prompt, error=None):
    error_context = f"\nError: {error}\nFix it." if error else ""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{
            "role": "user",
            "content": f"""
Write CadQuery code.

{prompt}
{error_context}

Rules:
- import cadquery as cq
- result variable only
- no explanation
"""
        }]
    )

    return response.choices[0].message.content

def run_ai_with_retry(prompt):
    error = None

    for _ in range(3):
        code = generate_with_ai(prompt, error)

        try:
            exec_globals = {"cq": cq, "SpurGear": SpurGear}
            safe_globals = {"__builtins__": {}}

            exec(code, {**safe_globals, **exec_globals})

            if "result" not in exec_globals:
                raise ValueError("No result returned")

            return exec_globals["result"], None

        except Exception as e:
            error = str(e)

    return None, error

# ================== ROUTE ==================
@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get("prompt", "").strip()

    if not prompt:
        return jsonify({"error": "Empty prompt"}), 400

    timestamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

    try:
        result, err = generate_from_template(prompt)

        if not result:
            result, err = run_ai_with_retry(prompt)

        if not result:
            raise ValueError(err)

        os.makedirs("outputs", exist_ok=True)

        step_path = f"outputs/{timestamp}.step"
        stl_path = f"outputs/{timestamp}.stl"

        cq.exporters.export(result, step_path)
        cq.exporters.export(result, stl_path)

        with open(step_path, "rb") as f:
            step_b64 = base64.b64encode(f.read()).decode()

        with open(stl_path, "rb") as f:
            stl_b64 = base64.b64encode(f.read()).decode()

        return jsonify({
            "status": "success",
            "step_b64": step_b64,
            "stl_b64": stl_b64
        })

    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})

# ================== MAIN ==================
if __name__ == "__main__":
    app.run(debug=True)
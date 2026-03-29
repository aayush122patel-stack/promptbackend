from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from groq import Groq
from dotenv import load_dotenv
import cadquery as cq
import sys
from datetime import datetime
import os
import base64

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "cq_gears"))
from cq_gears import SpurGear, RingGear, BevelGear

app = Flask(__name__)
CORS(app, origins="*")

# ===== TEMPLATES =====
def make_connecting_rod(length, big_end_od, big_end_id, small_end_od, small_end_id, thickness):
    big_end = cq.Workplane("XY").circle(big_end_od/2).extrude(thickness)
    big_end = big_end.faces(">Z").workplane().circle(big_end_id/2).cutThruAll()
    small_end = cq.Workplane("XY").center(length, 0).circle(small_end_od/2).extrude(thickness)
    small_end = small_end.faces(">Z").workplane().circle(small_end_id/2).cutThruAll()
    rib = cq.Workplane("XY").box(length, thickness, thickness).translate((length/2, 0, thickness/2))
    result = big_end.union(small_end).union(rib)
    return result
# =====================

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

def generate_cad_code(prompt, error=None):
    error_context = ""
    if error:
        error_context = f"\nThe previous code had this error: {error}\nFix it."
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "user", "content": f"""
You are a CadQuery expert. Write Python code using CadQuery to build this part:

{prompt}
{error_context}

Rules:
- Start with: import cadquery as cq
- Store final shape in variable called 'result'
- Do NOT use import_cadquery() or any other import style
- Do NOT export, show, or add input()
- Return ONLY Python code, no explanation, no backticks, no markdown

Available libraries and functions:
- cadquery as cq (for all standard parts)
- from cq_gears import SpurGear (for spur gears)
- from cq_gears import RingGear (for ring gears)
- from cq_gears import BevelGear (for bevel gears)
- make_connecting_rod(length, big_end_od, big_end_id, small_end_od, small_end_id, thickness)

Gear example:
from cq_gears import SpurGear
import cadquery as cq
gear = SpurGear(module=2, teeth_number=20, width=30)
result = gear.build()

Flange example:
import cadquery as cq
result = cq.Workplane("XY").circle(50).extrude(20)
result = result.faces(">Z").workplane().circle(25).cutThruAll()
result = result.faces(">Z").workplane().polarArray(40, 0, 360, 6).circle(5).cutThruAll()

Connecting rod example:
result = make_connecting_rod(200, 30, 15, 20, 10, 10)
"""}
        ]
    )
    return response.choices[0].message.content

@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    prompt = data.get('prompt', '')
    error = None
    for attempt in range(3):
        code = generate_cad_code(prompt, error)
        try:
            exec_globals = {
                "cq": cq,
                "SpurGear": SpurGear,
                "RingGear": RingGear,
                "BevelGear": BevelGear,
                "make_connecting_rod": make_connecting_rod
            }
            exec(code, exec_globals)
            result = exec_globals["result"]
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.makedirs("outputs", exist_ok=True)
            step_path = f"outputs/part_{timestamp}.step"
            stl_path = f"outputs/part_{timestamp}.stl"
            cq.exporters.export(result, step_path)
            cq.exporters.export(result, stl_path)
            with open(step_path, "rb") as f:
                step_b64 = base64.b64encode(f.read()).decode("utf-8")
            with open(stl_path, "rb") as f:
                stl_b64 = base64.b64encode(f.read()).decode("utf-8")
            return jsonify({
                "step_b64": step_b64,
                "stl_b64": stl_b64
            })
        except Exception as e:
            error = str(e)
    return jsonify({"error": "Failed after 3 attempts"}), 500

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
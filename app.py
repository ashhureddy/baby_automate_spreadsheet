"""
Streamlit app for Advanced Cellular Template Processing - GEMINI 2.0 FLASH EDITION
Place this file as app.py in your repository.
"""

import os
import io
import re
import json
import time
import tempfile
from pathlib import Path
from typing import Optional, List

import streamlit as st
import openpyxl
from PIL import Image
from google import genai
from google.genai import types

# ---------------- Schemas ----------------
SERVICE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "nr_arfcn": {"type": "NUMBER"},
        "nr_band": {"type": "NUMBER"},
        "nr_pci": {"type": "NUMBER"},
        "nr_bw": {"type": "NUMBER"},
        "nr5g_rsrp": {"type": "NUMBER"},
        "nr5g_rsrq": {"type": "NUMBER"},
        "nr5g_sinr": {"type": "NUMBER"},
        "lte_band": {"type": "NUMBER"},
        "lte_earfcn": {"type": "NUMBER"},
        "lte_pci": {"type": "NUMBER"},
        "lte_bw": {"type": "NUMBER"},
        "lte_rsrp": {"type": "NUMBER"},
        "lte_rsrq": {"type": "NUMBER"},
        "lte_sinr": {"type": "NUMBER"},
    }
}

SPEEDTEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "download_mbps": {"type": "NUMBER"},
        "upload_mbps": {"type": "NUMBER"},
        "ping_ms": {"type": "NUMBER"},
        "jitter_ms": {"type": "NUMBER"}
    }
}

VIDEOTEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "max_resolution": {"type": "STRING"},
        "load_time_ms": {"type": "NUMBER"},
        "buffering_percentage": {"type": "NUMBER"}
    }
}

VOICETEST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "time": {"type": "STRING"},
        "call_duration_seconds": {"type": "NUMBER"},
        "call_status": {"type": "STRING"}
    }
}

# ---------------- Globals ----------------
alpha_service, beta_service, gamma_service = {}, {}, {}
alpha_speedtest, beta_speedtest, gamma_speedtest = {}, {}, {}
alpha_video, beta_video, gamma_video = {}, {}, {}
voice_test, extract_text, avearge = {}, [], {}

def log_append(log_placeholder, logs_list: list, msg: str):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    logs_list.append(line)
    display = "\n".join(logs_list[-2000:])
    try:
        log_placeholder.text_area("Logs", value=display, height=360)
    except Exception:
        pass

def get_sector_from_col(col_index: int) -> str:
    if 0 <= col_index < 4: return "alpha"
    if 4 <= col_index < 8: return "beta"
    if 8 <= col_index < 12: return "gamma"
    if 12 <= col_index < 18: return "voicetest"
    return "unknown"

# ---------------- Gemini API Analysis Modules ----------------
def process_service_images_gemini(client: genai.Client, image_paths: list, log_placeholder, logs: list) -> dict:
    valid_paths = [p for p in image_paths if p and os.path.exists(p)]
    if not valid_paths: return {}
    
    images = [Image.open(p) for p in valid_paths]
    prompt = (
        "You are a Senior RF Engineer validating 5G/LTE drive test screenshots. "
        "Analyze the provided ServiceMode screenshots and extract all cellular engineering metrics. "
        "Return ONLY a JSON object matching the requested schema. Use null for missing values."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt] + images,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SERVICE_SCHEMA
            )
        )
        return json.loads(response.text)
    except Exception as e:
        log_append(log_placeholder, logs, f"[ERROR] ServiceMode extraction failed: {e}")
        return {}

def analyze_speed_test_gemini(client: genai.Client, image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    if not os.path.exists(image_path): return None
    img = Image.open(image_path)
    
    prompt = (
        "Extract SPEED TEST metrics (download_mbps, upload_mbps, ping_ms, jitter_ms) from this Ookla Speedtest screenshot.\n"
        "RULES:\n"
        "1. Download speed is under the 'Download' heading, Upload speed is under 'Upload'. Do NOT confuse them.\n"
        "2. Do NOT report 2160, 1080, 720, or 1440 video resolutions as speed.\n"
        "3. Ignore commas in numbers (e.g. 1,071 = 1071)."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=SPEEDTEST_SCHEMA
            )
        )
        data = json.loads(response.text)
        
        # Sanity check: Reject video resolutions misidentified as download speed
        dl = data.get("download_mbps")
        if dl in [2160, 1080, 720, 1440, 2160.0, 1080.0]: return None
        if dl is None and data.get("upload_mbps") is None: return None
        
        return {"image_type": "speed_test", "data": data}
    except Exception as e:
        log_append(log_placeholder, logs, f"[ERROR] Speedtest failed: {e}")
        return None

def analyze_video_test_gemini(client: genai.Client, image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    if not os.path.exists(image_path): return None
    img = Image.open(image_path)
    
    prompt = "Extract Video Test metrics (max_resolution like '2160p', load_time_ms, buffering_percentage) from this screenshot."
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VIDEOTEST_SCHEMA
            )
        )
        data = json.loads(response.text)
        if not data.get("max_resolution") and not data.get("load_time_ms"): return None
        return {"image_type": "video_test", "data": data}
    except Exception as e:
        return None

def analyze_voice_test_gemini(client: genai.Client, image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    if not os.path.exists(image_path): return None
    img = Image.open(image_path)
    
    prompt = (
        "Extract Voice Call metrics from this dialer screenshot.\n"
        "1. 'time': Read the phone clock displayed in the status bar/top header (e.g. '12:48' or '01:39').\n"
        "2. 'call_duration_seconds': Convert the active call timer (e.g. '01:05') to total seconds (65). If there is no timer, return null.\n"
        "3. 'call_status': Return 'active' if a timer is running, otherwise 'dialing'."
    )
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VOICETEST_SCHEMA
            )
        )
        data = json.loads(response.text)
        return {"image_type": "voice_call", "data": data}
    except Exception as e:
        return None

def dispatch_image_analysis_gemini(client: genai.Client, image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    try: idx = int(Path(image_path).stem.split("_")[-1])
    except: idx = 0

    if 3 <= idx <= 7:
        priority = [analyze_speed_test_gemini, analyze_video_test_gemini, analyze_voice_test_gemini]
    elif idx >= 8:
        priority = [analyze_video_test_gemini, analyze_speed_test_gemini, analyze_voice_test_gemini]
    else:
        priority = [analyze_speed_test_gemini, analyze_video_test_gemini, analyze_voice_test_gemini]

    for func in priority:
        res = func(client, image_path, log_placeholder, logs)
        if res and res.get("data"):
            if res["image_type"] == "speed_test" and res["data"].get("download_mbps") is None: continue
            return res
    return None

# ---------------- Excel Extraction & Injection ----------------
def extract_images_from_excel(xlsx_path: str, output_folder: str, log_placeholder, logs: list) -> List[str]:
    wb = openpyxl.load_workbook(xlsx_path)
    sheet = wb.active
    images = getattr(sheet, "_images", [])
    os.makedirs(output_folder, exist_ok=True)
    images_with_locations = []
    
    for image in images:
        try: row, col = image.anchor._from.row + 1, image.anchor._from.col
        except: row, col = 0, 0
        images_with_locations.append({"image": image, "row": row, "col": col})

    images_sorted = sorted(images_with_locations, key=lambda i: (i["row"], i["col"]))
    saved_paths = []
    counters = {"alpha": 0, "beta": 0, "gamma": 0, "voicetest": 0, "unknown": 0}

    for itm in images_sorted:
        sector = get_sector_from_col(itm["col"])
        counters[sector] += 1
        filename = f"{sector}_image_{counters[sector]}.png"
        out_path = os.path.join(output_folder, filename)
        img_data = itm["image"]._data()
        pil = Image.open(io.BytesIO(img_data)).convert("RGB")
        pil.save(out_path, "PNG")
        saved_paths.append(out_path)
    return saved_paths

def _normalize_name(s: str) -> str: return re.sub(r"[^0-9a-zA-Z]", "", s).lower()
key_pattern = re.compile(r"\[['\"]?([^'\"\]]+)['\"]?\]")

def resolve_expression_with_vars(expr: str, allowed_vars: dict):
    expr = expr.strip()
    m = re.match(r"^([A-Za-z_]\w*)(.*)$", expr)
    if not m: return None
    base_raw, rest = m.group(1), m.group(2) or ""

    norm_map = {_normalize_name(k): k for k in allowed_vars.keys()}
    base_key = norm_map.get(_normalize_name(base_raw))
    if not base_key: return None

    obj = allowed_vars[base_key]
    if rest.strip() == "": return obj

    keys = key_pattern.findall(rest)
    try:
        for k in keys:
            found = None
            for real_k in obj.keys():
                if real_k.lower() == k.lower() or _normalize_name(real_k) == _normalize_name(k):
                    found = real_k
                    break
            if found: obj = obj[found]
            else: return None
        return obj
    except: return None

def process_file_streamlit(user_file_path: str, api_key: str, temp_dir: str, logs: list, text_area_placeholder) -> Optional[str]:
    global alpha_service, beta_service, gamma_service, alpha_speedtest, beta_speedtest, gamma_speedtest
    global alpha_video, beta_video, gamma_video, voice_test, avearge

    client = genai.Client(api_key=api_key)
    images_temp = os.path.join(temp_dir, "images")
    image_paths = extract_images_from_excel(user_file_path, images_temp, text_area_placeholder, logs)

    images_by_sector = {"alpha": [], "beta": [], "gamma": [], "voicetest": [], "unknown": []}
    for p in image_paths: images_by_sector[Path(p).stem.split("_")[0]].append(p)

    log_append(text_area_placeholder, logs, "[LOG] Starting Gemini 2.0 Flash VLM processing...")
    
    # Process Sector Service & Test Images
    for sector in ["alpha", "beta", "gamma"]:
        sector_images = images_by_sector[sector]
        img1 = next((p for p in sector_images if Path(p).stem.endswith("_1")), None)
        img2 = next((p for p in sector_images if Path(p).stem.endswith("_2")), None)

        svc = process_service_images_gemini(client, [img1, img2], text_area_placeholder, logs)
        if sector == "alpha": alpha_service = svc
        elif sector == "beta": beta_service = svc
        elif sector == "gamma": gamma_service = svc

        for img in [p for p in sector_images if p not in (img1, img2)]:
            res = dispatch_image_analysis_gemini(client, img, text_area_placeholder, logs)
            if res:
                name = Path(img).stem
                if res["image_type"] == "speed_test":
                    {"alpha": alpha_speedtest, "beta": beta_speedtest, "gamma": gamma_speedtest}[sector][name] = res["data"]
                elif res["image_type"] == "video_test":
                    {"alpha": alpha_video, "beta": beta_video, "gamma": gamma_video}[sector][name] = res["data"]

    # Process Voice Sector
    for img in images_by_sector["voicetest"]:
        res = analyze_voice_test_gemini(client, img, text_area_placeholder, logs)
        if res: voice_test[Path(img).stem] = res["data"]

    # Map extracted values back into Excel
    wb = openpyxl.load_workbook(user_file_path)
    sheet = wb.active
    
    allowed_vars = {
        "alpha_service": alpha_service, "beta_service": beta_service, "gamma_service": gamma_service,
        "alpha_speedtest": alpha_speedtest, "beta_speedtest": beta_speedtest, "gamma_speedtest": gamma_speedtest,
        "alpha_video": alpha_video, "beta_video": beta_video, "gamma_video": gamma_video,
        "voice_test": voice_test, "avearge": avearge,
    }

    def _is_red(font):
        if not font or not getattr(font, "bold", False): return False
        col = getattr(font, "color", None)
        return str(getattr(col, "rgb", "")).upper().endswith("FF0000")

    for row in sheet.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and _is_red(cell.font):
                expr = cell.value.strip()
                if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
                    expr = expr[1:-1].strip()
                
                resolved = resolve_expression_with_vars(expr, allowed_vars)
                
                if resolved is not None:
                    if isinstance(resolved, (dict, list, tuple)):
                        try: cell.value = json.dumps(resolved)
                        except Exception: cell.value = str(resolved)
                    else:
                        cell.value = resolved
                else:
                    cell.value = "NULL"

    wb.save(user_file_path)
    log_append(text_area_placeholder, logs, "[SUCCESS] Processing complete with Gemini Flash. File saved.")
    return user_file_path

# ---------------- Streamlit UI ----------------
def main_ui():
    st.set_page_config(page_title="Cellular Template Processor (Gemini 2.0)", layout="wide")
    st.title("Advanced Cellular Template Processor (Gemini 2.0 Flash Edition)")
    
    st.sidebar.header("Google AI Studio Settings")
    api_key_input = st.sidebar.text_input("Google AI Studio API Key", type="password", placeholder="AIzaSy...")
    st.sidebar.caption("Get a free key at https://aistudio.google.com/")

    if "logs" not in st.session_state: st.session_state["logs"] = []
    log_placeholder = st.empty()
    
    uploaded_file = st.file_uploader("Upload .xlsx template", type=["xlsx"])

    if uploaded_file:
        if not api_key_input:
            st.warning("⚠️ Please enter your Google AI Studio API key in the sidebar to proceed.")
            return

        tmp_dir = tempfile.mkdtemp()
        saved_path = os.path.join(tmp_dir, uploaded_file.name)
        with open(saved_path, "wb") as f: f.write(uploaded_file.read())

        if st.button("Process file now"):
            out_path = process_file_streamlit(saved_path, api_key_input, tmp_dir, st.session_state["logs"], log_placeholder)
            if out_path:
                with open(out_path, "rb") as f:
                    st.download_button("Download Processed File", data=f, file_name=f"Processed_{uploaded_file.name}")

if __name__ == "__main__":
    main_ui()

"""
Streamlit app for Advanced Cellular Template Processing - EASYOCR (OFFLINE) EDITION
Place this file as app.py in your repository. No API keys required.
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
import cv2
import easyocr

# ---------------- Initialization ----------------
# Cache the deep learning model so it doesn't reload on every button click
@st.cache_resource
def get_ocr_reader():
    # gpu=False ensures it runs smoothly on standard CPU environments like GitHub Codespaces
    return easyocr.Reader(['en'], gpu=False)

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

# ---------------- OCR Engine Helpers ----------------
def extract_text_with_easyocr(img_path: str) -> str:
    """Reads all text from the image dynamically without bounding boxes."""
    if not os.path.exists(img_path): return ""
    reader = get_ocr_reader()
    
    # Read text and return a list of strings
    results = reader.readtext(img_path, detail=0)
    
    # Join with newlines to help Regex distinguish between lines of text
    return "\n".join(results)

# ---------------- Analysis Modules ----------------
def process_service_images_local(image_paths: list, log_placeholder, logs: list) -> dict:
    data = {}
    full_text = ""
    for path in image_paths:
        if path: full_text += extract_text_with_easyocr(path) + "\n"
        
    # LTE Regex (Jumps over noisy colons/spaces)
    m = re.search(r'Earfcn[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['lte_earfcn'] = int(m.group(1))
    m = re.search(r'PCI[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['lte_pci'] = int(m.group(1))
    m = re.search(r'LTE.*?BAND[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['lte_band'] = int(m.group(1))
    m = re.search(r'LTE.*?BW[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['lte_bw'] = int(m.group(1))
    m = re.search(r'RSRP[^\d\-]*(-?\d+)', full_text, re.IGNORECASE)
    if m: data['lte_rsrp'] = int(m.group(1))
    m = re.search(r'RSRQ[^\d\-]*(-?\d+)', full_text, re.IGNORECASE)
    if m: data['lte_rsrq'] = int(m.group(1))
    m = re.search(r'SNR[^\d\-]*([\d\.-]+)', full_text, re.IGNORECASE)
    if m: data['lte_sinr'] = float(m.group(1))
        
    # NR Regex
    m = re.search(r'NR5G_RSRP[^\d\-]*(-?\d+)', full_text, re.IGNORECASE)
    if m: data['nr5g_rsrp'] = int(m.group(1))
    m = re.search(r'NR5G_SINR[^\d\-]*([\d\.-]+)', full_text, re.IGNORECASE)
    if m: data['nr5g_sinr'] = float(m.group(1))
    m = re.search(r'NR5G RSRQ[^\d\-]*(-?\d+)', full_text, re.IGNORECASE)
    if m: data['nr5g_rsrq'] = int(m.group(1))
    m = re.search(r'NR_ARFCN[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['nr_arfcn'] = int(m.group(1))
    m = re.search(r'NR_PCI[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['nr_pci'] = int(m.group(1))
    m = re.search(r'NR_BAND[^\d]*[nN]?(\d+)', full_text, re.IGNORECASE)
    if m: data['nr_band'] = int(m.group(1))
    m = re.search(r'NR_BW[^\d]*(\d+)', full_text, re.IGNORECASE)
    if m: data['nr_bw'] = int(m.group(1))

    return data

def analyze_speed_test_local(image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    full_text = extract_text_with_easyocr(image_path)
    clean_text = full_text.replace(',', '')
    
    dl_val, ul_val, ping_val = None, None, None
    
    # Primary check for side-by-side layout (Download and Upload headers appear before any numbers)
    m1 = re.search(r'Download.*?Upload[^\d]*([\d\.]+)[^\d]+([\d\.]+)', clean_text, re.IGNORECASE | re.DOTALL)
    
    # Secondary check for standard stacked layout
    dl_match = re.search(r'Download(?:[^\d]+)?([\d\.]+)', clean_text, re.IGNORECASE | re.DOTALL)
    ul_match = re.search(r'Upload(?:[^\d]+)?([\d\.]+)', clean_text, re.IGNORECASE | re.DOTALL)
    
    # Detect if standard layout accidentally duplicated the numbers
    is_duplicate = False
    if dl_match and ul_match and dl_match.group(1) == ul_match.group(1):
        is_duplicate = True
        
    # Apply the correct extraction logic based on the layout
    if m1 and (not dl_match or is_duplicate):
        dl_val = float(m1.group(1))
        ul_val = float(m1.group(2))
    else:
        if dl_match: dl_val = float(dl_match.group(1))
        if ul_match: ul_val = float(ul_match.group(1))
        
    ping_match = re.search(r'Ping(?:[^\d]+)?(\d+)', clean_text, re.IGNORECASE | re.DOTALL)
    if ping_match: ping_val = int(ping_match.group(1))

    # Duplicate bug prevention & Video Test rejection
    if dl_val == ul_val: ul_val = None 
    if dl_val in [2160, 1080, 720, 1440, 480, 2160.0, 1080.0]: return None
    if dl_val is None and ul_val is None: return None
        
    return {
        "image_type": "speed_test",
        "data": {
            "download_mbps": dl_val,
            "upload_mbps": ul_val,
            "ping_ms": ping_val
        }
    }

def analyze_video_test_local(image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    full_text = extract_text_with_easyocr(image_path)
    
    # Catch resolutions regardless of formatting
    res_match = re.search(r'(2160|1080|720|1440|4K)', full_text, re.IGNORECASE)
    load_match = re.search(r'Load.*?Time[^\d]*(\d+)', full_text, re.IGNORECASE)
    buf_match = re.search(r'Buffering[^\d]*(\d+)', full_text, re.IGNORECASE)
    
    if not res_match and not load_match: return None
    
    resolution = res_match.group(1) if res_match else None
    if resolution == "4K": resolution = "2160"
    
    return {
        "image_type": "video_test",
        "data": {
            "max_resolution": f"{resolution}p" if resolution else None,
            "load_time_ms": int(load_match.group(1)) if load_match else None,
            "buffering_percentage": int(buf_match.group(1)) if buf_match else 0
        }
    }

def analyze_voice_test_local(image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    full_text = extract_text_with_easyocr(image_path)
    
    # Find all timestamps (e.g. 12:48 or 01:05) in the text
    time_matches = re.findall(r'(\d{1,2})[;:\.](\d{2})', full_text)
    
    extracted_time = None
    duration = None
    
    if len(time_matches) >= 2:
        # First match is usually the phone clock (Time), second is the Call Duration
        extracted_time = f"{time_matches[0][0]}:{time_matches[0][1]}"
        duration = int(time_matches[1][0]) * 60 + int(time_matches[1][1])
    elif len(time_matches) == 1:
        # If only one is found, assume it's the duration
        duration = int(time_matches[0][0]) * 60 + int(time_matches[0][1])
            
    return {
        "image_type": "voice_call",
        "data": {
            "time": extracted_time,
            "call_duration_seconds": duration, 
            "call_status": "active" if duration else "dialing"
        }
    }

def dispatch_image_analysis_local(image_path: str, log_placeholder, logs: list) -> Optional[dict]:
    try: idx = int(Path(image_path).stem.split("_")[-1])
    except: idx = 0

    if 3 <= idx <= 7:
        priority = [analyze_speed_test_local, analyze_video_test_local, analyze_voice_test_local]
    elif idx >= 8:
        priority = [analyze_video_test_local, analyze_speed_test_local, analyze_voice_test_local]
    else:
        priority = [analyze_speed_test_local, analyze_video_test_local, analyze_voice_test_local]

    for func in priority:
        res = func(image_path, log_placeholder, logs)
        if res and res.get("data"):
            if res["image_type"] == "speed_test" and res["data"].get("download_mbps") is None: continue
            return res
    return None

# ---------------- Excel Processing logic ----------------
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

def process_file_streamlit(user_file_path: str, temp_dir: str, logs: list, text_area_placeholder) -> Optional[str]:
    global alpha_service, beta_service, gamma_service, alpha_speedtest, beta_speedtest, gamma_speedtest
    global alpha_video, beta_video, gamma_video, voice_test, avearge

    images_temp = os.path.join(temp_dir, "images")
    image_paths = extract_images_from_excel(user_file_path, images_temp, text_area_placeholder, logs)

    images_by_sector = {"alpha": [], "beta": [], "gamma": [], "voicetest": [], "unknown": []}
    for p in image_paths: images_by_sector[Path(p).stem.split("_")[0]].append(p)

    log_append(text_area_placeholder, logs, "[LOG] Starting Local EasyOCR mapping...")
    
    for sector in ["alpha", "beta", "gamma"]:
        sector_images = images_by_sector[sector]
        img1 = next((p for p in sector_images if Path(p).stem.endswith("_1")), None)
        img2 = next((p for p in sector_images if Path(p).stem.endswith("_2")), None)

        svc = process_service_images_local([img1, img2], text_area_placeholder, logs)
        if sector == "alpha": alpha_service = svc
        elif sector == "beta": beta_service = svc
        elif sector == "gamma": gamma_service = svc

        for img in [p for p in sector_images if p not in (img1, img2)]:
            res = dispatch_image_analysis_local(img, text_area_placeholder, logs)
            if res:
                name = Path(img).stem
                if res["image_type"] == "speed_test":
                    {"alpha": alpha_speedtest, "beta": beta_speedtest, "gamma": gamma_speedtest}[sector][name] = res["data"]
                elif res["image_type"] == "video_test":
                    {"alpha": alpha_video, "beta": beta_video, "gamma": gamma_video}[sector][name] = res["data"]

    for img in images_by_sector["voicetest"]:
        res = analyze_voice_test_local(img, text_area_placeholder, logs)
        if res: voice_test[Path(img).stem] = res["data"]

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
                    # Convert dicts/lists to strings so Excel doesn't crash
                    if isinstance(resolved, (dict, list, tuple)):
                        try:
                            cell.value = json.dumps(resolved)
                        except Exception:
                            cell.value = str(resolved)
                    else:
                        cell.value = resolved
                else:
                    cell.value = "NULL"

    wb.save(user_file_path)
    log_append(text_area_placeholder, logs, "[SUCCESS] EasyOCR Complete. File saved.")
    return user_file_path

# ---------------- UI ----------------
def main_ui():
    st.set_page_config(page_title="Cellular Template Processor (Offline OCR)", layout="wide")
    st.title("Advanced Cellular Template Processor (100% Offline EasyOCR)")
    st.caption("No API keys required. First run may take an extra 60 seconds to load the OCR models into memory.")

    if "logs" not in st.session_state: st.session_state["logs"] = []
    log_placeholder = st.empty()
    
    uploaded_file = st.file_uploader("Upload .xlsx template", type=["xlsx"])

    if uploaded_file:
        tmp_dir = tempfile.mkdtemp()
        saved_path = os.path.join(tmp_dir, uploaded_file.name)
        with open(saved_path, "wb") as f: f.write(uploaded_file.read())

        if st.button("Process file now"):
            out_path = process_file_streamlit(saved_path, tmp_dir, st.session_state["logs"], log_placeholder)
            if out_path:
                with open(out_path, "rb") as f:
                    st.download_button("Download Processed File", data=f, file_name=f"Processed_{uploaded_file.name}")

if __name__ == "__main__":
    main_ui()

import requests
from bs4 import BeautifulSoup
import json
import subprocess
import re
import time
import os
import urllib3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# --- 1. SETUP RESILIENT SESSION ---
session = requests.Session()
session.trust_env = False 
session.verify = False 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAX_WORKERS = 4  # Reduced for tiny 5-model speed check

# --- 2. CONFIGURATION MAPS ---
REGION_MAP = {
    "GLO":       ["10100111", "0", ""],
    "CN":        ["10010111", "1", ""],
    "IND":       ["00011011", "2", ""],
    "IN":        ["00011011", "2", ""],    
    "EU":        ["01000100", "3", ""],    
    "EEA":       ["01000100", "3", ""],    
    "EU-OR":     ["01010111", "3", ""],    
    "APC":       ["10100100", "0", ""],    
    "MM":        ["00111010", "0", ""],    
    "BD":        ["01010101", "0", ""],    
    "PK":        ["01010110", "0", ""],    
    "LK":        ["01100000", "0", ""],    
    "NP":        ["01100001", "0", ""],    
    "KH":        ["00111111", "0", ""],    
    "KZ":        ["01011010", "0", ""],    
    "EG":        ["01110101", "0", ""],    
    "TR":        ["01010001", "3", ""],    
    "RU":        ["00110111", "3", ""],    
    "MEA":       ["10100110", "0", ""],    
    "SA":        ["10000011", "0", ""],    
    "TH":        ["00111001", "0", ""],    
    "LATAM":     ["10011010", "0", ""],
    "BR":        ["10011110", "0", ""],    
    "TW":        ["00011010", "0", ""],    
    "ID":        ["00110011", "0", ""],    
    "MY":        ["00111000", "0", ""],    
    "PH":        ["00111110", "0", ""],    
    "GB":        ["10001010", "3", "GB"],
    "SG":        ["00101100", "0", ""],    
    "VN":        ["00111100", "0", ""],    
    "OCA":       ["10100101", "0", ""],
    "MX":        ["01111011", "0", ""],    
    "MX-TELCEL": ["01111010", "0", ""],
    "MX-ATT":    ["10001110", "0", ""]
}

BASE_URL = "https://oosdownloader-gui.fly.dev/api"
OUTPUT_FILE = "oneplus_ota_final.json"

BINARY_NAME = "func.exe"
if not os.path.exists(BINARY_NAME):
    BINARY_NAME = os.path.join(os.path.dirname(__file__), "func.exe")

memory_lock = Lock()
GLOBAL_FIRMWARE_MAP = {}

# --- 3. HELPER UTILITIES ---
def parse_version_digits(version_str):
    match = re.search(r'_(\d+(?:\.\d+)+)', version_str)
    if match:
        return [int(x) for x in match.group(1).split('.')]
    return [0]

def init_memory_cache():
    global GLOBAL_FIRMWARE_MAP
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for entry in data:
                        key = f"{entry['codename']}_{entry['region'].upper()}"
                        GLOBAL_FIRMWARE_MAP[key] = entry
        except Exception:
            pass

def process_and_merge_memory(new_entries):
    if not new_entries:
        return
    with memory_lock:
        for entry in new_entries:
            key = f"{entry['codename']}_{entry['region'].upper()}"
            if key not in GLOBAL_FIRMWARE_MAP:
                GLOBAL_FIRMWARE_MAP[key] = entry
            else:
                saved_ver = GLOBAL_FIRMWARE_MAP[key]['version']
                challenger_ver = entry['version']
                if parse_version_digits(challenger_ver) > parse_version_digits(saved_ver):
                    GLOBAL_FIRMWARE_MAP[key] = entry

def flush_memory_to_disk():
    with memory_lock:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(list(GLOBAL_FIRMWARE_MAP.values()), f, indent=4, ensure_ascii=False)

def scrape_github_cn_models(url, skip_prefixes):
    model_map = {}
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        for element in soup.find_all(['p', 'li']):
            code_tag = element.find('code')
            if code_tag:
                codename = code_tag.text.strip().upper()
                if len(codename) == 6 and any(char.isdigit() for char in codename) and not codename.startswith(skip_prefixes):
                    full_text = element.get_text()
                    if ':' in full_text:
                        real_name = full_text.split(':', 1)[1].strip()
                        real_name = re.sub(r'\s+', ' ', real_name)
                        model_map[codename] = real_name
                    else:
                        model_map[codename] = codename
        return model_map
    except Exception as e:
        print(f"\n    [!] GitHub Scraper Error for {url.split('/')[-1]}: {e}")
        return {}

def run_fetch_binary(codename, rev, nv_id, server_id, model_string):
    complex_arg = f"{codename.upper()}_11.{rev}.01_0001_100001010000"
    command = [
        BINARY_NAME,
        "--model", model_string,
        "--carrier", nv_id,
        "--mode", "0",
        "--server", server_id,
        complex_arg
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        match = re.search(r'(\{.*\})', result.stdout, re.DOTALL)
        if match:
            data = json.loads(match.group(1))
            if data.get("responseCode") == 200:
                body = data.get("body", {})
                components = body.get("components", [])
                if components:
                    return body, components[0].get("componentPackets", {})
    except Exception:
        pass
    return None

def fetch_with_smart_fallbacks(codename, clean_name, region_name, rev):
    region_key = region_name.upper()
    nv_id, server_id, suffix = REGION_MAP.get(region_key, REGION_MAP["GLO"])
    
    model_variants = [codename.upper() + suffix]
    if region_key in ["IN", "IND"] and (codename.upper() + "IN") not in model_variants:
        model_variants.append(codename.upper() + "IN")
    elif region_key in ["EU", "EEA"] and (codename.upper() + "EEA") not in model_variants:
        model_variants.append(codename.upper() + "EEA")
        model_variants.append(codename.upper() + "EU")

    for model_string in model_variants:
        res = run_fetch_binary(codename, rev, nv_id, server_id, model_string)
        if res:
            body, pkg = res
            version_str = body.get("realVersionName") or body.get("versionName") or "Unknown"
            raw_bytes = int(pkg.get("size", 0))
            size_mb = f"{round(raw_bytes / (1024**2), 2)} MB" if raw_bytes else "Unknown Size"
            
            return {
                "model": clean_name,
                "version": version_str,
                "codename": codename.upper(),
                "rom_type": "OTA",
                "size": size_mb,
                "md5": pkg.get("md5") or pkg.get("md5sum"),
                "url": pkg.get("manualUrl") or pkg.get("url"),
                "region": region_name.lower(),
                "working_model_string": model_string
            }
    return None

def worker_scan_global_device(device, index, total):
    d_id = device.get('id')
    full_name = device.get('name', 'Unknown')
    if not d_id: return "SKIPPED"
    clean_model = re.sub(r'\s*\([^)]*\)', '', full_name).strip()
    region = re.search(r'\((.*?)\)', full_name).group(1) if re.search(r'\((.*?)\)', full_name) else "GLO"

    try:
        res = session.get(f"{BASE_URL}/link/{d_id}/1", timeout=10)
        if res.status_code != 200: return f"    [{index}/{total}] Global Item: SKIPPED API error"
        info = res.json()
        api_version = info.get("version_number", "")
        if not api_version: return f"    [{index}/{total}] Global Item: SKIPPED Missing details"
        codename = api_version.split("_")[0] if "_" in api_version else api_version[:8]
        found_for_device = []
        
        entry_c = fetch_with_smart_fallbacks(codename, clean_model, region, "C")
        if entry_c:
            working_string = entry_c.get("working_model_string")
            found_for_device.append(entry_c)
            for next_rev in ["F", "H"]:
                nv_id, server_id, _ = REGION_MAP.get(region.upper(), REGION_MAP["GLO"])
                res_next = run_fetch_binary(codename, next_rev, nv_id, server_id, working_string)
                if res_next:
                    b_next, p_next = res_next
                    v_str = b_next.get("realVersionName") or b_next.get("versionName") or "Unknown"
                    r_bytes = int(p_next.get("size", 0))
                    s_mb = f"{round(r_bytes / (1024**2), 2)} MB" if r_bytes else "Unknown Size"
                    found_for_device.append({
                        "model": clean_model, "version": v_str, "codename": codename.upper(),
                        "rom_type": "OTA", "size": s_mb, "md5": p_next.get("md5") or p_next.get("md5sum"),
                        "url": p_next.get("manualUrl") or p_next.get("url"), "region": region.lower()
                    })
        else:
            entry_a = fetch_with_smart_fallbacks(codename, clean_model, region, "A")
            if entry_a: found_for_device.append(entry_a)

        if found_for_device:
            process_and_merge_memory(found_for_device)
            return f"    [{index}/{total}] Testing {clean_model} ({region})... SUCCESS ✅"
        return f"    [{index}/{total}] Testing {clean_model} ({region})... NO MATCH ❌"
    except Exception as e:
        return f"    [{index}/{total}] Testing {clean_model} ({region})... ERROR ⚠️ ({e})"

def worker_scan_china_device(code, clean_name, index, total):
    try:
        found_for_cn = []
        entry_c = run_fetch_binary(code, "C", "10010111", "1", code)
        if entry_c:
            body_c, pkg_c = entry_c
            v_str_c = body_c.get("realVersionName") or body_c.get("versionName") or "Unknown"
            r_bytes_c = int(pkg_c.get("size", 0))
            s_mb_c = f"{round(r_bytes_c / (1024**2), 2)} MB" if r_bytes_c else "Unknown Size"
            found_for_cn.append({
                "model": clean_name, "version": v_str_c, "codename": code, "rom_type": "OTA",
                "size": s_mb_c, "md5": pkg_c.get("md5") or pkg_c.get("md5sum"),
                "url": pkg_c.get("manualUrl") or pkg_c.get("url"), "region": "cn"
            })
            for next_rev in ["F", "H"]:
                entry_next = run_fetch_binary(code, next_rev, "10010111", "1", code)
                if entry_next:
                    body_n, pkg_n = entry_next
                    v_str_n = body_n.get("realVersionName") or body_n.get("versionName") or "Unknown"
                    r_bytes_n = int(pkg_n.get("size", 0))
                    s_mb_n = f"{round(r_bytes_n / (1024**2), 2)} MB" if r_bytes_n else "Unknown Size"
                    found_for_cn.append({
                        "model": clean_name, "version": v_str_n, "codename": code, "rom_type": "OTA",
                        "size": s_mb_n, "md5": pkg_n.get("md5") or pkg_n.get("md5sum"),
                        "url": pkg_n.get("manualUrl") or pkg_n.get("url"), "region": "cn"
                    })
        else:
            entry_a = run_fetch_binary(code, "A", "10010111", "1", code)
            if entry_a:
                body_a, pkg_a = entry_a
                v_str_a = body_a.get("realVersionName") or body_a.get("versionName") or "Unknown"
                r_bytes_a = int(pkg_a.get("size", 0))
                s_mb_a = f"{round(r_bytes_a / (1024**2), 2)} MB" if r_bytes_a else "Unknown Size"
                found_for_cn.append({
                    "model": clean_name, "version": v_str_a, "codename": code, "rom_type": "OTA",
                    "size": s_mb_a, "md5": pkg_a.get("md5") or pkg_a.get("md5sum"),
                    "url": pkg_a.get("manualUrl") or pkg_a.get("url"), "region": "cn"
                })

        if found_for_cn:
            process_and_merge_memory(found_for_cn)
            return f"    [{index}/{total}] Testing CN: {code} ({clean_name})... SUCCESS ✅"
        return f"    [{index}/{total}] Testing CN: {code} ({clean_name})... FAIL ❌"
    except Exception as e:
        return f"    [{index}/{total}] Testing CN: {code} ({clean_name})... ERROR ⚠️ ({e})"

# --- 5. MAIN MASTER RUNNER SCROLLER ---
def main():
    init_memory_cache()
    if not os.path.exists(BINARY_NAME): return

    # ==========================================
    # STAGE 1: TEST ONLY THE FIRST 5 GLOBAL ITEMS
    # ==========================================
    print("[+] Fetching Global/Regional device repository from Fly.dev...")
    try:
        res_devices = session.get(f"{BASE_URL}/devices", timeout=15)
        if "application/json" in res_devices.headers.get("Content-Type", ""):
            # ⏱️ FAST TEST FILTER: Keep only the first 5 devices
            global_devices = res_devices.json()[:5] 
            total_global = len(global_devices)
            print(f"[⏱️ FAST MODE] Scanning only {total_global} devices for testing...")
            
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(worker_scan_global_device, dev, i+1, total_global) for i, dev in enumerate(global_devices)]
                for f in as_completed(futures): print(f.result())
            flush_memory_to_disk()
    except Exception as e:
        print(f"[-] Global API error: {e}")

    # ==========================================
    # STAGE 2: TEST ONLY THE FIRST 5 CN ITEMS
    # ==========================================
    print("\n[+] Scoping and parsing China repositories from GitHub...")
    oneplus_cn_models = scrape_github_cn_models("https://github.com/KHwang9883/MobileModels/blob/master/brands/oneplus.md", ('IN', 'A2', 'ONE A2', 'KB', 'LE', 'OPW', 'PE', 'CPH', 'BE'))
    oppo_cn_models = scrape_github_cn_models("https://github.com/KHwang9883/MobileModels/blob/master/brands/oppo_cn.md", ('OB', 'OW', 'OR', 'PA', 'PB', 'PC', 'PE', 'PF', 'PD'))
    
    cn_merged = {**oneplus_cn_models, **oppo_cn_models}
    # ⏱️ FAST TEST FILTER: Slice down to keep only the first 5 models
    sorted_cn_codes = sorted(cn_merged.keys())[:5]
    total_cn = len(sorted_cn_codes)
    print(f"[⏱️ FAST MODE] Scanning only {total_cn} China devices for testing...")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(worker_scan_china_device, code, cn_merged[code], i+1, total_cn) for i, code in enumerate(sorted_cn_codes)]
        for f in as_completed(futures): print(f.result())

    flush_memory_to_disk()
    print(f"\n[DONE] Fast test completed. {len(GLOBAL_FIRMWARE_MAP)} models processed into {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

import requests
from bs4 import BeautifulSoup
import json
import subprocess
import re
import time
import os
import urllib3

# --- 1. SETUP RESILIENT SESSION ---
session = requests.Session()
session.trust_env = False 
session.verify = False 
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

# --- 3. HELPER UTILITIES ---
def parse_version_digits(version_str):
    """Extracts numeric arrays from version names for true sequential sorting logic."""
    match = re.search(r'_(\d+(?:\.\d+)+)', version_str)
    if match:
        return [int(x) for x in match.group(1).split('.')]
    return [0]

def load_local_json():
    """Safely loads existing progress from disk to prevent data overwrites."""
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []

def save_and_filter_latest_realtime(new_entries):
    """
    Saves data to the JSON file instantly. 
    Compares new entries against existing file data to ensure only the LATEST version is kept.
    """
    if not new_entries:
        return
        
    current_saved_data = load_local_json()
    
    # Map out what we already have saved by group_key
    firmware_map = {}
    for entry in current_saved_data:
        key = f"{entry['codename']}_{entry['region'].upper()}"
        firmware_map[key] = entry
        
    # Introduce our newly collected versions to the map
    for entry in new_entries:
        key = f"{entry['codename']}_{entry['region'].upper()}"
        if key not in firmware_map:
            firmware_map[key] = entry
        else:
            saved_ver = firmware_map[key]['version']
            challenger_ver = entry['version']
            # If the new one is newer than the saved one, replace it!
            if parse_version_digits(challenger_ver) > parse_version_digits(saved_ver):
                firmware_map[key] = entry

    # Write the updated map straight back to disk
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(list(firmware_map.values()), f, indent=4, ensure_ascii=False)

def scrape_github_cn_models(url, skip_prefixes):
    """Scrapes code identifiers and clean names from markdown tables on GitHub."""
    model_map = {}
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for element in soup.find_all(['p', 'li']):
            code_tag = element.find('code')
            if code_tag:
                codename = code_tag.text.strip().upper()
                if len(codename) == 6 and not codename.startswith(skip_prefixes):
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
    """Low-level orchestrator that formats flags cleanly and fires func.exe."""
    complex_arg = f"{codename.upper()}_11.{rev}.01_0001_100001010000"
    command = [
        "func.exe",
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
    """Loops over specific model flags ensuring target regions receive proper suffixes."""
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

# --- 4. MAIN MASTER SCROLLER ---
def main():
    # Initialize/Clear or preserve existing output structural format
    if not os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)

    # ==========================================
    # STAGE 1: PARSE GLOBAL/REGIONAL API
    # ==========================================
    print("[+] Fetching Global/Regional device repository from Fly.dev...")
    try:
        res_devices = session.get(f"{BASE_URL}/devices", timeout=15)
        if "application/json" in res_devices.headers.get("Content-Type", ""):
            global_devices = res_devices.json()
            print(f"[+] Loaded {len(global_devices)} items from Global API. Starting scanner...")
            
            for index, device in enumerate(global_devices):
                d_id = device.get('id')
                full_name = device.get('name', 'Unknown')
                if not d_id: continue

                clean_model = re.sub(r'\s*\([^)]*\)', '', full_name).strip()
                region_match = re.search(r'\((.*?)\)', full_name)
                region = region_match.group(1) if region_match else "GLO"

                print(f"    [{index + 1}/{len(global_devices)}] Testing {clean_model} ({region})...", end=" ", flush=True)
                
                try:
                    res = session.get(f"{BASE_URL}/link/{d_id}/1", timeout=10)
                    if res.status_code != 200 or "application/json" not in res.headers.get("Content-Type", ""):
                        print("SKIPPED (API dead/timeout)")
                        continue
                        
                    info = res.json()
                    api_version = info.get("version_number", "")
                    if not api_version:
                        print("SKIPPED (No baseline string)")
                        continue

                    codename = api_version.split("_")[0] if "_" in api_version else api_version[:8]
                    found_for_device = []
                    
                    # Logic C to F and A branch sequence
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
                        if entry_a:
                            found_for_device.append(entry_a)

                    if found_for_device:
                        # Write the data for this device to file IMMEDIATELY
                        save_and_filter_latest_realtime(found_for_device)
                        print(f"SUCCESS ✅ ({len(found_for_device)} versions captured & saved)")
                    else:
                        print("NO MATCH ❌")
                    
                    time.sleep(0.02)
                except Exception as e:
                    print(f"ERROR ⚠️ ({e})")
        else:
            print("[-] Global API failed to yield proper JSON data. Falling back onto China scraper...")
    except Exception as e:
        print(f"[-] Global API completely unreachable: {e}")

    # ==========================================
    # STAGE 2: PARSE CHINA GIT REPOSITORIES
    # ==========================================
    print("\n" + "="*50)
    print("[+] Scoping and parsing China repositories from GitHub...")
    print("="*50)
    
    oneplus_git = "https://github.com/KHwang9883/MobileModels/blob/master/brands/oneplus.md"
    oneplus_skip = ('IN', 'A2', 'ONE A2', 'KB', 'LE', 'OPW', 'PE', 'CPH', 'BE','DN','EB','GM','GN','HD','IV','DE','AC')
    print("--- Extracting OnePlus CN Catalog ---")
    oneplus_cn_models = scrape_github_cn_models(oneplus_git, oneplus_skip)
    print(f"Mapped {len(oneplus_cn_models)} valid modern OnePlus CN models.")

    oppo_git = "https://github.com/KHwang9883/MobileModels/blob/master/brands/oppo_cn.md"
    oppo_skip = ('OB', 'OW', 'OR', 'PA', 'PB', 'PC', 'PE', 'PF', 'PD')
    print("\n--- Extracting OPPO CN Catalog ---")
    oppo_cn_models = scrape_github_cn_models(oppo_git, oppo_skip)
    print(f"Mapped {len(oppo_cn_models)} valid modern OPPO CN models.")

    cn_merged = {**oneplus_cn_models, **oppo_cn_models}
    sorted_cn_codes = sorted(cn_merged.keys())
    print(f"\nProcessing matrix scanner across {len(sorted_cn_codes)} unique CN hardware models...")

    for idx, code in enumerate(sorted_cn_codes):
        clean_name = cn_merged[code]
        print(f"    [{idx+1}/{len(sorted_cn_codes)}] Testing CN: {code} ({clean_name})...", end=" ", flush=True)
        
        found_for_cn = []
        
        # Logic C to F and A branch sequence
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
            # Write the data for this CN device to file IMMEDIATELY
            save_and_filter_latest_realtime(found_for_cn)
            print(f"SUCCESS ✅ ({len(found_for_cn)} versions captured & saved)")
        else:
            print("FAIL ❌")
            
        time.sleep(0.02)

    print(f"\n[DONE] Entire script completed safely. Output saved to: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

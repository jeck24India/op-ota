import requests
import json
import subprocess
import re
import time
import os

# --- 1. CONFIGURATION MAPPINGS ---
REGION_MAP = {
    "GLO": ["10100111", 0, ""], "CN":  ["10010111", 1, ""], "IND": ["00011011", 2, ""],
    "IN":  ["00011011", 2, "IN"], "EU":  ["01000100", 3, "EU"], "EEA": ["01000100", 3, "EEA"],
    "TR":  ["01010001", 3, ""], "RU":  ["00110111", 3, ""], "MEA": ["10100110", 0, ""],
    "SA":  ["10000011", 0, ""], "TH":  ["00111001", 0, ""], "LATAM": ["10011010", 0, ""],
    "BR":  ["10011110", 0, ""], "TW":  ["00011010", 0, ""], "ID":  ["00110011", 0, ""],
    "MY":  ["00111000", 0, ""], "PH":  ["00111110", 0, ""], "GB":  ["10001010", 3, "GB"],
    "SG":  ["00101100", 0, ""], "VN":  ["00111100", 0, ""], "OCA": ["10100101", 0, ""],
}

OS_VERSION_MAP = {"A": "11.A.01", "C": "11.C.01", "F": "11.F.01", "H": "11.H.01"}

BASE_URL = "https://oosdownloader-gui.fly.dev/api"
OUTPUT_FILE = "oneplus_ota_final.json"

# --- 2. CORE LOGIC FUNCTIONS ---

def get_permanent_url_and_size(base_code, os_char, region):
    """Executes func.exe to fetch the unsigned manualUrl and the REAL size in MB"""
    region_key = region.upper()
    nv_id, server_id, suffix = REGION_MAP.get(region_key, REGION_MAP["GLO"])
    
    model_flag = base_code.upper() + suffix
    os_suffix = OS_VERSION_MAP.get(os_char.upper(), "11.A.01")
    complex_arg = f"{base_code.upper()}_{os_suffix}_0001_100001010000"

    command = [
        "func", "--model", model_flag, "--carrier", nv_id,
        "--imei", "", "--mode", "0", complex_arg, "--proxy", "", 
        "--server", str(server_id)
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, shell=True, encoding='utf-8')
        data = json.loads(result.stdout.strip())
        
        if data.get('responseCode') == 200:
            body = data.get('body', {})
            packet = body['components'][0]['componentPackets']
            
            url = packet['manualUrl']
            # Get actual size in bytes from the tool and convert to MB
            raw_bytes = int(packet.get('size', 0))
            size_mb = f"{round(raw_bytes / (1024**2), 2)} MB"
            
            return url, size_mb
    except Exception as e:
        print(f"      [!] Func.exe error: {e}")
    return None, None

def main_scraper():
    print("[+] Fetching Device List...")
    try:
        devices = requests.get(f"{BASE_URL}/devices").json()
    except:
        print("[-] Could not connect to API.")
        return

    final_data = []

    for device in devices:
        d_id, full_name = device.get('id'), device.get('name', 'Unknown')
        if not d_id: continue

        print(f"[*] Checking: {full_name}")
        try:
            res = requests.get(f"{BASE_URL}/link/{d_id}/1", timeout=10)
            if res.status_code == 200:
                info = res.json()
                version = info.get("version_number", "")
                if not version: continue

                # CLEANING LOGIC
                clean_model = re.sub(r'\s*\([^)]*\)', '', full_name).strip()
                region_match = re.search(r'\((.*?)\)', full_name)
                region = region_match.group(1) if region_match else "Global"
                codename = version.split("_")[0] if "_" in version else version[:8]

                # A16 BRANCH: Fetch permanent URL + Real Size via func.exe
                if "_16.0." in version:
                    print(f"    [>] A16 Detected. Fetching permanent link and real size...")
                    os_char = "F" if "F." in version else "A" 
                    url, size = get_permanent_url_and_size(codename, os_char, region)
                    # Fallback to API size if tool fails
                    if not size:
                        size = f"{round(info.get('download_size', 0) / (1024**2), 2)} MB"
                else:
                    url = info.get("download_url")
                    # Calculate MB from the standard API result
                    size = f"{round(info.get('download_size', 0) / (1024**2), 2)} MB"

                if url:
                    final_data.append({
                        "model": clean_model,
                        "version": version,
                        "codename": codename,
                        "rom_type": "OTA",
                        "size": size, # Now reports in MB (e.g., 7163.04 MB)
                        "md5": info.get("md5sum"),
                        "url": url,
                        "region": region
                    })
                    print(f"    [OK] Added {clean_model} ({region}) - {size}")

            time.sleep(0.3)
        except Exception as e:
            print(f"    [!] Skip {full_name}: {e}")

    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        json.dump(final_data, f, indent=4)
    print(f"\n[DONE] Saved {len(final_data)} entries to {OUTPUT_FILE}")

if __name__ == "__main__":
    main_scraper()
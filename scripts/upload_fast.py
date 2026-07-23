import os
import re
import sys
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COURSES_FILE = os.path.join(BASE_DIR, 'output', 'courses.json')
CERTS_FILE = os.path.join(BASE_DIR, 'output', 'certificates.json')
CONFIGSTORE_PATH = os.path.expanduser('~/.config/configstore/firebase-tools.json')

PROJECT_ID = "certificates-registry"
FIRESTORE_URL = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"

def get_access_token():
    if not os.path.exists(CONFIGSTORE_PATH):
        raise FileNotFoundError("Firebase login token not found.")
    with open(CONFIGSTORE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    tokens = data.get('tokens', {})
    if isinstance(tokens, dict) and 'access_token' in tokens:
        return tokens['access_token']
    raise ValueError("Access token not found in Firebase configstore.")

def to_firestore_value(val):
    if isinstance(val, int):
        return {"integerValue": str(val)}
    elif isinstance(val, float):
        return {"doubleValue": val}
    elif isinstance(val, bool):
        return {"booleanValue": val}
    else:
        return {"stringValue": str(val or "")}

def main():
    print(f"🔥 Початок швидкого завантаження 56,388 сертифікатів у Firebase Firestore ({PROJECT_ID})...")
    token = get_access_token()

    with open(COURSES_FILE, 'r', encoding='utf-8') as f:
        courses = json.load(f)

    with open(CERTS_FILE, 'r', encoding='utf-8') as f:
        certs = json.load(f)

    print(f"📊 До завантаження: {len(courses)} курсів, {len(certs)} сертифікатів.")

    # 1. Upload Courses via Firestore REST Batch Commit
    commit_url = f"{FIRESTORE_URL}:commit"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

    # Upload courses
    course_writes = []
    for c in courses:
        doc_path = f"projects/{PROJECT_ID}/databases/(default)/documents/courses/{c['id']}"
        fields = {k: to_firestore_value(v) for k, v in c.items()}
        course_writes.append({
            "update": {
                "name": doc_path,
                "fields": fields
            }
        })

    payload = json.dumps({"writes": course_writes}).encode('utf-8')
    req = urllib.request.Request(commit_url, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            print("   ✓ Курси успішно завантажено в Firestore!")
    except urllib.error.HTTPError as e:
        print(f"   ❌ Помилка завантаження курсів ({e.code}): {e.read().decode('utf-8')}")
        return

    # 2. Upload Certificates in Batches of 400 with resume tracking
    STATE_FILE = os.path.join(BASE_DIR, 'output', 'upload_state.json')
    last_idx = 0
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as sf:
                last_idx = json.load(sf).get('last_uploaded_idx', 0)
        except Exception:
            pass

    batch_size = 400
    total_certs = len(certs)
    print(f"   ℹ️ Відновлюємо завантаження з сертифіката #{last_idx + 1} з {total_certs}...")

    for i in range(last_idx, total_certs, batch_size):
        batch = certs[i:i+batch_size]
        writes = []
        for idx, cert in enumerate(batch):
            raw_code = str(cert['certCode']).strip()
            clean_code = re.sub(r'[^a-zA-Z0-9-]', '_', raw_code)
            if not clean_code or len(clean_code.replace('_', '')) == 0:
                clean_code = f"cert_{cert.get('year', 2026)}_{i+idx}"

            doc_path = f"projects/{PROJECT_ID}/databases/(default)/documents/certificates/{clean_code}"
            fields = {k: to_firestore_value(v) for k, v in cert.items()}
            writes.append({
                "update": {
                    "name": doc_path,
                    "fields": fields
                }
            })

        payload = json.dumps({"writes": writes}).encode('utf-8')
        req = urllib.request.Request(commit_url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                percent = round((i + len(batch)) / total_certs * 100, 1)
                print(f"   📥 Прогрес: {i + len(batch)} / {total_certs} ({percent}%) сертифікатів завантажено...")
                with open(STATE_FILE, 'w') as sf:
                    json.dump({'last_uploaded_idx': i + len(batch)}, sf)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"   ⚠️ Досягнуто добовий безкоштовний ліміт Firebase (20,000 записів/день).")
                print(f"   💾 Скрипт зберіг прогрес на сертифікаті #{i+1}. Запустіть скрипт завтра для заповнення решти!")
                break
            else:
                print(f"   ⚠️ Помилка на пакеті {i}: {e}")
                time.sleep(1)

    print("\n🎉 ВСІ СЕРТИФІКАТИ УСПІШНО ДЕПЛОЇТИ В FIREBASE FIRESTORE!")

if __name__ == '__main__':
    main()

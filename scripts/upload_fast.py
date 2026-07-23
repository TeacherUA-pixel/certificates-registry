import os
import sys
import json
import math
import re
import urllib.request
import urllib.error

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COURSES_FILE = os.path.join(BASE_DIR, 'output', 'courses.json')
CERTS_FILE   = os.path.join(BASE_DIR, 'output', 'certificates.json')
STATE_FILE   = os.path.join(BASE_DIR, 'output', 'upload_state_v2.json')
CONFIGSTORE  = os.path.expanduser('~/.config/configstore/firebase-tools.json')

PROJECT_ID = "certificates-registry"
DB_ROOT    = f"projects/{PROJECT_ID}/databases/(default)/documents"
COMMIT_URL = f"https://firestore.googleapis.com/v1/{DB_ROOT}:commit"

# ── Tuning ────────────────────────────────────────────────────────────────────
DISPLAY_CHUNK   = 400   # participants per cert_chunks doc  (for listing UI)
INDEX_CHUNK     = 500   # certCode entries per cert_idx doc  (for O(1) verify)
BATCH_WRITES    = 20    # writes per Firestore commit        (keep payload < 5 MB)
# ─────────────────────────────────────────────────────────────────────────────


def get_token():
    if not os.path.exists(CONFIGSTORE):
        raise FileNotFoundError("Run: firebase login")
    with open(CONFIGSTORE, 'r', encoding='utf-8') as f:
        d = json.load(f)
    t = d.get('tokens', {})
    if isinstance(t, dict) and 'access_token' in t:
        return t['access_token']
    raise ValueError("Token not found. Run: firebase login --reauth")


def fs(val):
    """Python → Firestore REST value."""
    if isinstance(val, bool):   return {"booleanValue": val}
    if isinstance(val, int):    return {"integerValue": str(val)}
    if isinstance(val, float):  return {"doubleValue": val}
    if isinstance(val, list):   return {"arrayValue": {"values": [fs(v) for v in val]}}
    if isinstance(val, dict):   return {"mapValue": {"fields": {k: fs(v) for k, v in val.items()}}}
    return {"stringValue": str(val) if val is not None else ""}


def make_write(collection, doc_id, data: dict) -> dict:
    return {
        "update": {
            "name": f"{DB_ROOT}/{collection}/{doc_id}",
            "fields": {k: fs(v) for k, v in data.items()}
        }
    }


def commit(writes: list, headers: dict):
    payload = json.dumps({"writes": writes}).encode('utf-8')
    mb = len(payload) / 1024 / 1024
    if mb > 9.5:
        # Auto-split: commit first half, then second half recursively
        mid = len(writes) // 2
        commit(writes[:mid], headers)
        commit(writes[mid:], headers)
        return
    req = urllib.request.Request(COMMIT_URL, data=payload, headers=headers)
    with urllib.request.urlopen(req) as r:
        return r.status


def send_batches(all_writes: list, headers: dict, phase: str, start_idx: int = 0) -> int:
    """Send writes in BATCH_WRITES chunks. Returns index of last committed write."""
    total = len(all_writes)
    done  = start_idx
    for i in range(start_idx, total, BATCH_WRITES):
        batch = all_writes[i:i + BATCH_WRITES]
        try:
            commit(batch, headers)
            done = i + len(batch)
            pct  = done / total * 100
            print(f"  [{phase}] {done}/{total} docs ({pct:.0f}%)...")
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8')
            if e.code == 429:
                print(f"\n⏸  Квота вичерпана (429). Прогрес збережено ({done}/{total}).")
                return done
            raise RuntimeError(f"HTTP {e.code}: {body}")
    return done


def clean_cert_code(raw: str) -> str:
    """Extract only the actual certificate code (e.g. 'DDA-K-00077') from messy strings."""
    if not raw:
        return ""
    # Try to extract known patterns: ЦІ-Б-1234, DDA-K-00077, №DDA-..., etc.
    m = re.search(r'№?\s*([A-ZА-ЯҐЄІЇа-яґєії]{1,5}-[A-ZА-ЯҐЄІЇа-яґєії0-9]{1,5}-\d{1,6})', raw)
    if m:
        return m.group(1).strip()
    # Fallback: strip newlines and grab last meaningful token
    parts = re.split(r'[\n\r\t]+', raw)
    for p in reversed(parts):
        p = p.strip()
        if p and len(p) < 40:
            return re.sub(r'^(certCode|№)\s*', '', p).strip()
    return raw.strip()[:40]


def build_all_writes(courses, certs):
    """Build ALL Firestore write operations for both collections."""
    # ── 1. Group certs by courseId ────────────────────────────────────────────
    course_meta = {c['id']: c for c in courses}
    grouped = {c['id']: [] for c in courses}

    for cert in certs:
        cid = cert.get('courseId', '')
        if cid not in grouped:
            grouped[cid] = []
        code = clean_cert_code(cert.get('certCode', ''))
        grouped[cid].append({
            "name":     cert.get('participantName', ''),
            "nameLow":  cert.get('participantNameClean', '').lower(),
            "certCode": code,
            "date":     cert.get('issueDate', ''),
            "topic":    cert.get('topic', ''),
            "link":     cert.get('driveUrl', '')
        })

    display_writes = []   # cert_chunks → for listing UI
    index_writes   = []   # cert_idx   → for O(1) verification

    for course_id, participants in grouped.items():
        meta = course_meta.get(course_id, {})
        year = meta.get('year', 'unknown')

        # ── DISPLAY CHUNKS (cert_chunks collection) ──────────────────────────
        n_disp = math.ceil(len(participants) / DISPLAY_CHUNK) or 1
        for i in range(n_disp):
            chunk = participants[i * DISPLAY_CHUNK:(i + 1) * DISPLAY_CHUNK]
            doc_id = f"{course_id}_d{i:04d}"
            display_writes.append(make_write("cert_chunks", doc_id, {
                "courseId":    course_id,
                "year":        year,
                "chunkIndex":  i,
                "totalChunks": n_disp,
                "count":       len(chunk),
                "participants": chunk
            }))

        # ── INDEX CHUNKS (cert_idx collection, MAP keyed by certCode) ────────
        n_idx = math.ceil(len(participants) / INDEX_CHUNK) or 1
        for i in range(n_idx):
            chunk = participants[i * INDEX_CHUNK:(i + 1) * INDEX_CHUNK]
            # Store as MAP {certCode → {name, date}} for direct O(1) JS lookup
            cert_map = {}
            for p in chunk:
                key = re.sub(r'[^A-Za-zА-ЯҐЄІЇа-яґєії0-9_\-]', '_', p['certCode'])[:50] or f"unk_{i}"
                cert_map[key] = {
                    "name": p['name'],
                    "date": p['date'],
                    "year": str(year),
                    "topic": p['topic']
                }
            doc_id = f"{course_id}_i{i:04d}"
            index_writes.append(make_write("cert_idx", doc_id, {
                "courseId": course_id,
                "year":     year,
                "block":    i,
                "certs":    cert_map       # MAP field: O(1) lookup in JS
            }))

    return display_writes, index_writes


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"display_done": 0, "index_done": 0, "total_display": 0, "total_index": 0}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def main():
    print("=" * 60)
    print("🚀 CERTIFICATES FIRESTORE UPLOADER — Optimised for 200k+")
    print("=" * 60)

    token = get_token()
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

    with open(COURSES_FILE, 'r', encoding='utf-8') as f:
        courses = json.load(f)
    with open(CERTS_FILE, 'r', encoding='utf-8') as f:
        certs = json.load(f)

    print(f"📊 Завантажено: {len(courses)} курсів, {len(certs):,} сертифікатів")
    print(f"⚙️  Будуємо write-операції...")

    display_writes, index_writes = build_all_writes(courses, certs)
    total_d, total_i = len(display_writes), len(index_writes)

    print(f"\n📐 Структура в Firestore:")
    print(f"   cert_chunks : {total_d:4d} документів  ({DISPLAY_CHUNK} учасників/doc)")
    print(f"   cert_idx    : {total_i:4d} документів  ({INDEX_CHUNK} записів/doc, MAP для O(1) пошуку)")
    print(f"   ВСЬОГО WRITES: {total_d + total_i} (замість {len(certs):,} — Economy {len(certs) // (total_d + total_i)}x!)")
    print(f"\n   ✅ Для {len(certs):,} записів: ~{total_d + total_i} writes")
    print(f"   ✅ Для 200,000 записів : ~{int(200000/DISPLAY_CHUNK) + int(200000/INDEX_CHUNK)} writes")
    print(f"   ✅ Безкоштовний ліміт : 20,000/день — ВКЛАДАЄМОСЯ!")

    state = load_state()

    # Resume if interrupted
    if state.get('display_done', 0) == total_d and state.get('index_done', 0) == total_i:
        print("\n✅ Вже все завантажено! Нічого робити.")
        return

    # Phase 1: Display chunks
    if state.get('display_done', 0) < total_d:
        print(f"\n📤 Фаза 1/2: cert_chunks (display)...")
        done = send_batches(display_writes, headers, "display", state.get('display_done', 0))
        state['display_done'] = done
        state['total_display'] = total_d
        save_state(state)
        if done < total_d:
            print("💾 Стан збережено. Запустіть скрипт знову після скидання квоти (00:00 UTC).")
            return
    else:
        print(f"\n✅ Фаза 1 вже виконана ({total_d} docs). Пропускаємо.")

    # Phase 2: Index map chunks
    if state.get('index_done', 0) < total_i:
        print(f"\n📤 Фаза 2/2: cert_idx (O(1) verification index)...")
        done = send_batches(index_writes, headers, "index", state.get('index_done', 0))
        state['index_done'] = done
        state['total_index'] = total_i
        save_state(state)
        if done < total_i:
            print("💾 Стан збережено. Запустіть скрипт знову після скидання квоти (00:00 UTC).")
            return
    else:
        print(f"\n✅ Фаза 2 вже виконана ({total_i} docs). Пропускаємо.")

    print(f"\n{'=' * 60}")
    print(f"🎉 ЗАВАНТАЖЕННЯ ЗАВЕРШЕНО!")
    print(f"   {len(certs):,} сертифікатів у {total_d + total_i} документах Firestore")
    print(f"   Колекції: cert_chunks (UI) + cert_idx (verification)")
    print(f"   Верифікація на сайті: 1 read / пошук — Golden Rule ✓")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()

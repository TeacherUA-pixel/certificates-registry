"""
consolidate_sheets.py — Інтелектуальний парсер Google Таблиць
Підтримує будь-яку структуру аркушів, fuzzy mapping колонок,
збереження маппінгу для повторного використання, та діагностику якості.
"""
import os, re, sys, json, glob, urllib.parse, urllib.request
import pandas as pd
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'): sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data')
OUTPUT_DIR   = os.path.join(BASE_DIR, 'output')
URLS_FILE    = os.path.join(BASE_DIR, 'sheets_urls.txt')
MAPPINGS_FILE= os.path.join(BASE_DIR, 'output', 'col_mappings.json')  # збережені маппінги

os.makedirs(RAW_DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── Розширений словник синонімів ────────────────────────────────────────────
SYNONYMS = {
    'name': [
        'піб', 'п.і.б', 'пiб', 'п і б', 'учасник', 'випускник', 'слухач',
        'фіо', 'фio', 'full name', 'name', 'прізвище', "прізвище ім'я",
        'прізвище та ім', 'слухачі', 'учасники', 'пед', 'вчитель', 'педагог',
        'ім\'я', 'имя', 'фамилия', 'ф.і.о', 'ф.и.о', 'вихованець', 'студент'
    ],
    'certCode': [
        'сертифікат', 'номер сертифіката', 'код сертифіката', 'номер', 'код',
        '№ сертифіката', 'сертифікат №', 'cert', 'code', '№', 'номер документа',
        'реєстраційний номер', 'свідоцтво', 'посвідчення', 'id', 'serial',
        'реєстр', 'диплом', 'посвідч', 'номер посвідчення', 'реєстраційний'
    ],
    'issueDate': [
        'дата', 'дата видачі', 'дата видачи', 'date', 'issue_date',
        'дата видачі сертифіката', 'видано', 'коли видано', 'місяць',
        'дата видачі посвідчення', 'дата підписання', 'рік видачі'
    ],
    'driveUrl': [
        'посилання', 'посилання на сертифікат', 'сертифікат (pdf)', 'drive',
        'link', 'url', 'pdf', 'файли', 'скачати', 'завантажити', 'скан',
        'гугл диск', 'google drive', 'файл', 'документ'
    ],
    'courseTitle': [
        'курс', 'назва курсу', 'тема курсу', 'програма', 'назва програми',
        'course', 'назва', 'найменування', 'предмет', 'дисципліна',
        'тема навчання', 'навчальна програма', 'кваліфікація'
    ],
    'topic': [
        'напрям', 'тема', 'категорія', 'topic', 'модуль', 'спеціалізація',
        'профіль', 'напрямок', 'фах', 'спрямованість', 'тип'
    ],
    'hours': [
        'години', 'год', 'обсяг', 'hours', 'кількість годин', 'тривалість',
        'год.', 'кред', 'астрономічних'
    ],
    'credits': [
        'кредити', 'єктс', 'credits', 'кредити єктс', 'ects', 'залікових'
    ],
    'year': [
        'рік', 'рік видачі', 'year', 'навчальний рік', 'рiк'
    ],
    'lastName': [
        'прізвище', 'last name', 'surname', 'фамілія', 'фамилия'
    ],
    'firstName': [
        "ім'я", 'імя', 'first name', 'имя'
    ],
    'middleName': [
        'по батькові', 'по батькови', 'middle name', 'отчество', 'по-батькові'
    ]
}

# ─── Ukrainian month names for date parsing ──────────────────────────────────
UA_MONTHS = {
    'січ': 1, 'лют': 2, 'бер': 3, 'кві': 4, 'тра': 5, 'чер': 6,
    'лип': 7, 'сер': 8, 'вер': 9, 'жов': 10, 'лис': 11, 'гру': 12,
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
    'sep':9,'oct':10,'nov':11,'dec':12
}

# ─── Fuzzy column identification ─────────────────────────────────────────────
def fuzzy_score(col_name: str, synonym: str) -> float:
    """Returns 0..1 similarity. Uses substring + token matching."""
    col = col_name.strip().lower()
    syn = synonym.strip().lower()
    if syn == col:         return 1.0
    if syn in col:         return 0.85
    if col in syn:         return 0.75
    # Token overlap
    col_tokens = set(re.split(r'[\s._/\\()\-]+', col))
    syn_tokens = set(re.split(r'[\s._/\\()\-]+', syn))
    overlap = col_tokens & syn_tokens
    if overlap:
        return 0.6 * len(overlap) / max(len(col_tokens), len(syn_tokens))
    return 0.0

def identify_column(col_name: str, threshold=0.6) -> str | None:
    best_key, best_score = None, 0.0
    for key, synonyms in SYNONYMS.items():
        for syn in synonyms:
            score = fuzzy_score(col_name, syn)
            if score > best_score:
                best_score, best_key = score, key
    return best_key if best_score >= threshold else None

# ─── Scalar extractor (must be first — used by all clean_ functions) ─────────
def _scalar(val):
    """Safely extract a scalar from a pandas Series (duplicate col) or plain value."""
    if isinstance(val, pd.Series):
        return val.iloc[0] if not val.empty else None
    return val

# ─── Date cleaning ────────────────────────────────────────────────────────────
def clean_date(val) -> str:
    val = _scalar(val)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if not s or s.lower() in ('nan', 'none', ''):
        return ""

    # Excel serial number (float like 45291.0)
    if re.match(r'^\d{5}(\.\d+)?$', s):
        try:
            from datetime import date as _date, timedelta
            base = _date(1899, 12, 30)
            dt = base + timedelta(days=int(float(s)))
            return dt.strftime('%d.%m.%Y')
        except: pass

    # Standard numeric formats
    for fmt in ('%d.%m.%Y','%Y-%m-%d','%d/%m/%Y','%d.%m.%y','%Y.%m.%d',
                '%d-%m-%Y','%m/%d/%Y','%d %m %Y'):
        try:
            return datetime.strptime(s.split(' ')[0], fmt).strftime('%d.%m.%Y')
        except: pass

    # Ukrainian/English text months: "01 вересня 2024", "вересень 2024", "Sep-24"
    s_low = s.lower()
    for month_str, month_num in UA_MONTHS.items():
        if month_str in s_low:
            year_m = re.search(r'\b(20\d{2}|\d{2})\b', s)
            day_m  = re.search(r'\b(\d{1,2})\b', s)
            year   = int(year_m.group(1)) if year_m else datetime.now().year
            if year < 100: year += 2000
            day    = int(day_m.group(1)) if day_m and int(day_m.group(1)) <= 31 else 1
            try:
                return datetime(year, month_num, day).strftime('%d.%m.%Y')
            except: pass

    return s  # return as-is if nothing matched

# ─── Name cleaning ────────────────────────────────────────────────────────────
def clean_name(val) -> str:
    val = _scalar(val)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = re.sub(r'\s+', ' ', str(val)).strip()
    # Remove leading numbers/dots (e.g. "1. Іваненко")
    s = re.sub(r'^\d+[.)]\s*', '', s)
    return s

# ─── CertCode cleaning ────────────────────────────────────────────────────────
def clean_cert_code(raw) -> str:
    raw = _scalar(raw)
    if not raw: return ""
    # Extract known code patterns first
    m = re.search(r'№?\s*([A-ZА-ЯҐЄІЇ]{1,5}[-–][A-ZА-ЯҐЄІЇ0-9]{1,5}[-–]\d{1,6})', str(raw), re.IGNORECASE)
    if m: return m.group(1).strip()
    # Strip multiline garbage (Excel export artifacts)
    parts = re.split(r'[\n\r\t]+', str(raw))
    for p in reversed(parts):
        p = re.sub(r'^(certCode|№|no\.?)\s*', '', p.strip(), flags=re.IGNORECASE).strip()
        if p and 2 < len(p) < 50: return p
    return str(raw).strip()[:50]

# ─── Saved mapping cache ──────────────────────────────────────────────────────
def load_mappings() -> dict:
    if os.path.exists(MAPPINGS_FILE):
        with open(MAPPINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_mappings(m: dict):
    with open(MAPPINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False, indent=2)

def make_mapping_key(source: str, cols: list) -> str:
    return f"{source}::{','.join(sorted(str(c) for c in cols))}"

# ─── Column mapping with save/restore ────────────────────────────────────────
def map_columns(df: pd.DataFrame, source: str, saved_mappings: dict) -> dict:
    """Returns {original_col: standard_key}. Uses saved mapping if available."""
    cols = list(df.columns)
    cache_key = make_mapping_key(source, cols)

    if cache_key in saved_mappings:
        print(f"      💾 Використовую збережений маппінг для '{source}'")
        raw = saved_mappings[cache_key]
        # Dedup: first mapped column per standard key wins; skip blank col names
        deduped, seen_keys = {}, set()
        for col, key in raw.items():
            if not str(col).strip():          # skip blank/space-only column names
                continue
            if key not in seen_keys:
                deduped[col] = key
                seen_keys.add(key)
        return deduped

    mapping = {}
    used_keys = set()
    for col in cols:
        if not str(col).strip():              # skip blank/space-only column names
            continue
        key = identify_column(str(col))
        if key and key not in used_keys:
            mapping[col] = key
            used_keys.add(key)

    # Regex fallback on data samples for unmapped required fields
    for col in cols:
        if col in mapping: continue
        samples = [str(v).strip() for v in df[col].dropna().head(20) if str(v).strip()]
        if not samples: continue

        if 'certCode' not in mapping.values():
            if any(re.search(r'[А-ЯA-Z]{1,5}[-–]\w{1,5}[-–]\d{2,}', v, re.I) for v in samples):
                mapping[col] = 'certCode'
                print(f"      🔍 Regex: '{col}' → certCode")
                continue

        if 'name' not in mapping.values():
            if any(re.match(r'^[А-ЯІЇЄҐA-Z][а-яіїєґa-z\']+\s+[А-ЯІЇЄҐA-Z]', v) for v in samples):
                mapping[col] = 'name'
                print(f"      🔍 Regex: '{col}' → name")
                continue

        if 'issueDate' not in mapping.values():
            if any(re.search(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}', v) for v in samples):
                mapping[col] = 'issueDate'
                print(f"      🔍 Regex: '{col}' → issueDate")
                continue

        if 'driveUrl' not in mapping.values():
            if any(v.startswith('http') for v in samples):
                mapping[col] = 'driveUrl'
                print(f"      🔍 URL detect: '{col}' → driveUrl")
                continue

    # Save for future runs
    saved_mappings[cache_key] = mapping
    save_mappings(saved_mappings)
    return mapping

# ─── Google Sheets tab discovery ─────────────────────────────────────────────
def get_sheet_tabs(sheet_url: str) -> list:
    m = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_url)
    if not m: return []
    sheet_id = m.group(1)

    html_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview"
    tabs = []
    try:
        req = urllib.request.Request(html_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode('utf-8')
        matches = re.findall(r'name:\s*["\']([^"\']+)["\'].*?gid:\s*["\']?([0-9]+)', html, re.DOTALL)
        for name, gid in matches:
            tabs.append({'gid': gid, 'name': name.strip(), 'sheet_id': sheet_id})
        if not tabs:
            matches = re.findall(r'item-([0-9]+)["\'][^>]*>([^<]+)<', html)
            for gid, name in matches:
                tabs.append({'gid': gid, 'name': name.strip(), 'sheet_id': sheet_id})
    except Exception as e:
        print(f"   ⚠️ Не вдалося прочитати список аркушів: {e}")

    if not tabs:
        tabs.append({'gid': '0', 'name': 'Sheet1', 'sheet_id': sheet_id})

    seen = {}
    for t in tabs:
        seen[t['gid']] = t
    return list(seen.values())

# ─── Header row detection ─────────────────────────────────────────────────────
HEADER_KEYWORDS = [
    'піб', 'п.і.б', 'сертифікат', 'код', "ім'я", 'дата', 'назва',
    'учасник', 'слухач', 'прізвище', 'номер', 'name', 'date', 'cert'
]

def find_header_row(df_raw: pd.DataFrame) -> int:
    for i in range(min(20, len(df_raw))):
        row_str = ' '.join(str(v).lower() for v in df_raw.iloc[i].values if pd.notna(v))
        if sum(1 for kw in HEADER_KEYWORDS if kw in row_str) >= 2:
            return i
    return 0

# ─── Quality report ───────────────────────────────────────────────────────────
def quality_report(certs: list):
    if not certs: return
    total = len(certs)
    fields = ['certCode', 'participantName', 'issueDate', 'driveUrl', 'topic']
    print(f"\n{'─'*55}")
    print(f"📊 ЗВІТ ЯКОСТІ  ({total:,} сертифікатів)")
    print(f"{'─'*55}")
    for f in fields:
        filled = sum(1 for c in certs if c.get(f, '').strip() not in ('', 'nan', 'None'))
        pct = filled / total * 100
        bar = '█' * int(pct // 5) + '░' * (20 - int(pct // 5))
        flag = '' if pct >= 80 else (' ⚠️' if pct >= 40 else ' ❌')
        print(f"  {f:<20} {bar} {pct:5.1f}%{flag}")

    # Detect suspicious certCodes
    dirty = [c for c in certs if '\n' in c.get('certCode','') or len(c.get('certCode','')) > 50]
    if dirty:
        print(f"\n  ⚠️  {len(dirty)} certCode з можливим 'брудом' (multiline або >50 символів)")
        print(f"     Приклад: {repr(dirty[0]['certCode'][:80])}")

    # Year distribution
    from collections import Counter
    years = Counter(str(c.get('year','?')) for c in certs)
    print(f"\n  📅 Розподіл по роках: {dict(sorted(years.items()))}")
    print(f"{'─'*55}")

# ─── Main processor ───────────────────────────────────────────────────────────
def process_sheets(urls_list=None):
    print("=" * 55)
    print("🚀 Інтелектуальний парсер Google Таблиць v2")
    print("=" * 55)

    saved_mappings = load_mappings()
    data_frames = []

    target_urls = list(urls_list or [])
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    target_urls.append(line)

    # ── Google Sheets ──────────────────────────────────────────────────────────
    for url in target_urls:
        print(f"\n🔎 Google Sheet: {url[:60]}...")
        tabs = get_sheet_tabs(url)
        print(f"   ✓ Аркушів знайдено: {len(tabs)}")
        for tab in tabs:
            csv_url = (f"https://docs.google.com/spreadsheets/d/{tab['sheet_id']}"
                       f"/export?format=csv&gid={tab['gid']}")
            tab_name = tab['name']
            print(f"   📥 '{tab_name}' (gid={tab['gid']})...")
            try:
                df_raw = pd.read_csv(csv_url, header=None, dtype=str)
                if df_raw.empty: continue
                hi = find_header_row(df_raw)
                df = pd.read_csv(csv_url, skiprows=hi, dtype=str)
                data_frames.append({'source': f"GS_{tab_name}", 'tab': tab_name, 'df': df})
                print(f"      ✓ {len(df)} рядків (заголовок у рядку {hi})")
            except Exception as e:
                print(f"      ❌ {e}")

    # ── Local files ────────────────────────────────────────────────────────────
    raw_files = (glob.glob(os.path.join(RAW_DATA_DIR, '*.csv')) +
                 glob.glob(os.path.join(RAW_DATA_DIR, '*.xlsx')) +
                 glob.glob(os.path.join(RAW_DATA_DIR, '*.xls')))

    for fp in raw_files:
        fname = os.path.basename(fp)
        print(f"\n📄 Локальний файл: {fname}")
        try:
            if fp.endswith('.csv'):
                df_raw = pd.read_csv(fp, header=None, dtype=str)
                hi = find_header_row(df_raw)
                df = pd.read_csv(fp, skiprows=hi, dtype=str)
                data_frames.append({'source': fname, 'tab': fname, 'df': df})
                print(f"   ✓ {len(df)} рядків")
            else:
                xl = pd.ExcelFile(fp)
                for sname in xl.sheet_names:
                    df_raw = xl.parse(sname, header=None, dtype=str)
                    hi = find_header_row(df_raw)
                    df = xl.parse(sname, skiprows=hi, dtype=str)
                    data_frames.append({'source': f"{fname}::{sname}", 'tab': sname, 'df': df})
                    print(f"   ✓ '{sname}': {len(df)} рядків")
        except Exception as e:
            print(f"   ❌ {e}")

    if not data_frames:
        print("\n⚠️  Джерел даних не знайдено.")
        print("Додайте URL до sheets_urls.txt або передайте як аргумент:")
        print('python scripts/consolidate_sheets.py "https://docs.google.com/..."')
        return

    # ── Consolidation ──────────────────────────────────────────────────────────
    print(f"\n{'─'*55}")
    print(f"🔄 Зведення {len(data_frames)} аркушів до єдиної структури...")
    print(f"{'─'*55}")

    all_certs = []
    all_courses = {}
    skipped = []

    for item in data_frames:
        source, tab, df = item['source'], item['tab'], item['df']

        # Year from tab name — supports '2025', '072025' (MMYYYY), '2025-07'
        ym = re.search(r'\b(20\d{2})\b', tab)
        if not ym:
            # MMYYYY or MMYYYY embedded in string e.g. '072025'
            ym2 = re.search(r'\d{2}(20\d{2})', tab)
            tab_year = int(ym2.group(1)) if ym2 else datetime.now().year
        else:
            tab_year = int(ym.group(1))

        # issueDate fallback from tab name (e.g. '072025' → '01.07.2025')
        tab_date = ''
        mm_match = re.search(r'(\d{2})(20\d{2})', tab)
        if mm_match:
            mm, yyyy = int(mm_match.group(1)), int(mm_match.group(2))
            if 1 <= mm <= 12:
                tab_date = f'01.{mm:02d}.{yyyy}'

        # Get column mapping (fuzzy + regex + saved cache)
        col_map = map_columns(df, source, saved_mappings)
        df = df.rename(columns=col_map)

        # Merge split name columns if full 'name' column is absent
        if 'name' not in df.columns:
            has_last  = 'lastName'   in df.columns
            has_first = 'firstName'  in df.columns
            has_mid   = 'middleName' in df.columns
            if has_last and has_first:
                parts = [df['lastName'].fillna(''), df['firstName'].fillna('')]
                if has_mid: parts.append(df['middleName'].fillna(''))
                df['name'] = parts[0].str.cat(parts[1:], sep=' ').str.strip()
                print(f"      🔗 Об'єднано колонки Прізвище+Ім'я+Поб в 'name'")

        has_name = 'name' in df.columns
        has_code = 'certCode' in df.columns

        if not has_name or not has_code:
            missing = []
            if not has_name: missing.append('name (ПІБ)')
            if not has_code: missing.append('certCode (Номер)')
            print(f"\n  ⚠️  '{source}' — не розпізнано: {', '.join(missing)}")
            print(f"     Наявні колонки: {list(df.columns)[:10]}")
            print(f"     Зразок рядка:   {df.iloc[0].to_dict() if len(df) > 0 else 'порожньо'}")
            skipped.append(source)
            continue

        course_title = f"Курс підвищення кваліфікації ({tab_year})"
        ok_rows = 0

        for _, row in df.iterrows():
            name = clean_name(_scalar(row.get('name', '')))
            code = clean_cert_code(_scalar(row.get('certCode', '')))

            if not name or not code or code.lower() in ('nan', 'none', '0', ''):
                continue

            # Year priority: cell > tab
            row_year_s = str(_scalar(row.get('year', '')) or '')
            row_year = int(row_year_s) if re.match(r'^20\d{2}$', row_year_s) else tab_year

            ctitle = str(_scalar(row.get('courseTitle', course_title)) or course_title).strip()
            if ctitle.lower() in ('nan', 'none', ''): ctitle = course_title

            topic  = str(_scalar(row.get('topic', 'Загальні програми')) or '').strip()
            if topic.lower() in ('nan', 'none', ''): topic = 'Загальні програми'

            idate  = clean_date(_scalar(row.get('issueDate', '')))
            if not idate: idate = tab_date  # fallback: derive from tab name
            durl   = str(_scalar(row.get('driveUrl', '')) or '').strip()
            if durl.lower() in ('nan', 'none'): durl = ''

            try:
                hrs_raw = str(_scalar(row.get('hours', '30')) or '30').strip()
                hrs = int(float(hrs_raw)) if hrs_raw not in ('nan','none','') else 30
            except: hrs = 30

            cred = round(hrs / 30, 1)

            # Stable course_id: year + sanitized title (same title = same course)
            cid_base = re.sub(r'[^a-zA-ZА-ЯҐЄІЇа-яґєії0-9]', '', ctitle)[:20]
            cid = f"course_{row_year}_{cid_base}" if cid_base else f"course_{row_year}_{tab}"

            if cid not in all_courses:
                all_courses[cid] = {
                    "id": cid, "name": ctitle, "topic": topic,
                    "year": row_year, "hours": hrs, "credits": cred,
                    "period": idate or str(row_year), "orderNo": f"Реєстр {row_year}"
                }

            all_certs.append({
                "certCode":            code,
                "participantName":     name,
                "participantNameClean":name.lower(),
                "courseId":            cid,
                "courseTitle":         ctitle,
                "topic":               topic,
                "year":                row_year,
                "issueDate":           idate,
                "driveUrl":            durl
            })
            ok_rows += 1

        print(f"  ✅ '{source}': {ok_rows:,} сертифікатів")

    # ── Save ───────────────────────────────────────────────────────────────────
    with open(os.path.join(OUTPUT_DIR, 'courses.json'), 'w', encoding='utf-8') as f:
        json.dump(list(all_courses.values()), f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUTPUT_DIR, 'certificates.json'), 'w', encoding='utf-8') as f:
        json.dump(all_certs, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*55}")
    print(f"✅ ЗВЕДЕННЯ ЗАВЕРШЕНО")
    print(f"   Аркушів оброблено:  {len(data_frames) - len(skipped)}")
    print(f"   Аркушів пропущено:  {len(skipped)}")
    print(f"   Курсів збережено:   {len(all_courses)}")
    print(f"   Сертифікатів:       {len(all_certs):,}")
    if skipped:
        print(f"\n   ⚠️ Пропущені аркуші (не вдалося розпізнати структуру):")
        for s in skipped: print(f"      • {s}")
        print(f"   👉 Додайте маппінг вручну у: output/col_mappings.json")
    print(f"{'='*55}")

    quality_report(all_certs)


if __name__ == '__main__':
    urls = sys.argv[1:] if len(sys.argv) > 1 else None
    process_sheets(urls)

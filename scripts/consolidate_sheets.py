import os
import re
import json
import glob
import sys
import urllib.parse
import urllib.request
import pandas as pd
from datetime import datetime

# Set stdout/stderr encoding to UTF-8 for Windows compatibility
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Directory paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
URLS_FILE = os.path.join(BASE_DIR, 'sheets_urls.txt')

os.makedirs(RAW_DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Extended synonym dictionary for AI-like column mapping across different structures
COLUMN_SYNONYMS = {
    'name': [
        'піб', 'п.і.б.', 'пiб', 'учасник', 'випускник', 'слухач', 'фіо', 'фiо', 
        'full name', 'name', 'прізвище ім\'я', 'прізвище', 'ім\'я', 'слухачі', 'учасники'
    ],
    'certCode': [
        'сертифікат', 'номер сертифіката', 'код сертифіката', 'номер', 'код', 
        '№ сертифіката', 'сертифікат №', 'cert_code', 'code', '№', 'номер документа', 'реєстраційний номер'
    ],
    'issueDate': [
        'дата', 'дата видачі', 'дата видачи', 'date', 'issue_date', 'дата видачі сертифіката'
    ],
    'driveUrl': [
        'посилання', 'посилання на сертифікат', 'сертифікат (pdf)', 'drive', 'link', 'url', 'pdf', 'файли', 'скачати'
    ],
    'courseTitle': [
        'курс', 'назва курсу', 'тема курсу', 'програма', 'назва програми', 'course', 'назва', 'найменування'
    ],
    'year': [
        'рік', 'рік видачі', 'year'
    ],
    'topic': [
        'напрям', 'тема', 'категорія', 'topic', 'модуль'
    ],
    'hours': [
        'години', 'год', 'обсяг', 'hours', 'кількість годин'
    ],
    'credits': [
        'кредити', 'єктс', 'credits', 'кредити єктс'
    ]
}

def identify_column(col_name):
    clean = str(col_name).strip().lower()
    for std_key, synonyms in COLUMN_SYNONYMS.items():
        for syn in synonyms:
            if syn in clean:
                return std_key
    return None

def clean_name(name_str):
    if not isinstance(name_str, str) or pd.isna(name_str):
        return ""
    cleaned = re.sub(r'\s+', ' ', str(name_str)).strip()
    return cleaned.title()

def clean_date(date_val):
    if pd.isna(date_val) or not date_val:
        return ""
    date_str = str(date_val).strip()
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d.%m.%y', '%Y.%m.%d'):
        try:
            dt = datetime.strptime(date_str.split(' ')[0], fmt)
            return dt.strftime('%d.%m.%Y')
        except ValueError:
            pass
    return date_str

def get_google_sheet_tabs(sheet_url):
    """
    Сканує сторінку Google Таблиці і знаходить всі аркуші (tabs/gids) та їхні назви (наприклад, роки).
    """
    sheet_id_match = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_url)
    if not sheet_id_match:
        return []
    sheet_id = sheet_id_match.group(1)

    html_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview"
    tabs = []

    try:
        req = urllib.request.Request(html_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            html = response.read().decode('utf-8')
            
            # Find all sheet tabs from HTML javascript bootstrap data
            matches = re.findall(r'name:\s*["\']([^"\']+)["\'].*?gid:\s*["\']?([0-9]+)["\']?', html, re.DOTALL)
            if not matches:
                # Alternative regex for sheet structure
                matches = re.findall(r'item-([0-9]+)["\'][^>]*>([^<]+)<', html)
                for gid, name in matches:
                    tabs.append({'gid': gid, 'name': name.strip(), 'sheet_id': sheet_id})
            else:
                for name, gid in matches:
                    tabs.append({'gid': gid, 'name': name.strip(), 'sheet_id': sheet_id})

    except Exception as e:
        print(f"   ⚠️ Не вдалося прочитати список аркушів через HTML: {e}")

    # Fallback to main gid=0 if no sub-tabs found
    if not tabs:
        tabs.append({'gid': '0', 'name': 'Головний аркуш', 'sheet_id': sheet_id})

    # Deduplicate tabs by GID
    unique_tabs = {}
    for t in tabs:
        unique_tabs[t['gid']] = t
    return list(unique_tabs.values())

def find_best_header_row(df_raw):
    """
    Інтелектуально шукає рядок заголовка у таблиці (наприклад, якщо перші рядки порожні або містять шапку).
    """
    for row_idx in range(min(15, len(df_raw))):
        row_values = [str(val).lower() for val in df_raw.iloc[row_idx].values if pd.notna(val)]
        row_str = " ".join(row_values)
        if any(keyword in row_str for keyword in ['піб', 'сертифікат', 'код', 'ім\'я', 'дата', 'назва', 'учасник']):
            return row_idx
    return 0

def process_sheets(urls_list=None):
    print("🚀 Розпочинаємо інтелектуальне сканування Google Таблиць та локальних файлів...")

    data_frames = []

    target_urls = urls_list or []
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    target_urls.append(line)

    # 1. Сканування та витягування всіх аркушів із Google Таблиць
    for url in target_urls:
        print(f"🔎 Скануємо Google Таблицю: {url[:70]}...")
        tabs = get_google_sheet_tabs(url)
        print(f"   ✓ Знайдено {len(tabs)} аркушів!")

        for tab in tabs:
            csv_url = f"https://docs.google.com/spreadsheets/d/{tab['sheet_id']}/export?format=csv&gid={tab['gid']}"
            tab_name = tab['name']
            print(f"   📥 Завантаження аркуша: '{tab_name}' (GID: {tab['gid']})...")
            try:
                # Read raw without headers first to detect actual table header
                df_raw = pd.read_csv(csv_url, header=None)
                if df_raw.empty:
                    continue

                header_idx = find_best_header_row(df_raw)
                df = pd.read_csv(csv_url, skiprows=header_idx)

                data_frames.append({
                    'source': f"GoogleSheet_{tab_name}",
                    'tab_name': tab_name,
                    'df': df
                })
                print(f"      ✓ Отримано {len(df)} рядків з аркуша '{tab_name}'")
            except Exception as e:
                print(f"      ❌ Не вдалося прочитати аркуш '{tab_name}': {e}")

    # 2. Локальні Excel та CSV файли з папки data/
    raw_files = glob.glob(os.path.join(RAW_DATA_DIR, '*.csv')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xlsx')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xls'))

    for file_path in raw_files:
        filename = os.path.basename(file_path)
        print(f"📄 Локальний файл: {filename}")
        try:
            if file_path.endswith('.csv'):
                df_raw = pd.read_csv(file_path, header=None)
                header_idx = find_best_header_row(df_raw)
                df = pd.read_csv(file_path, skiprows=header_idx)
                data_frames.append({'source': filename, 'tab_name': filename, 'df': df})
            else:
                xl = pd.ExcelFile(file_path)
                for sheet_name in xl.sheet_names:
                    df_raw = xl.parse(sheet_name, header=None)
                    header_idx = find_best_header_row(df_raw)
                    df = xl.parse(sheet_name, skiprows=header_idx)
                    data_frames.append({'source': f"{filename}_{sheet_name}", 'tab_name': sheet_name, 'df': df})
        except Exception as e:
            print(f"   ❌ Помилка читання локального файлу {file_path}: {e}")

    if not data_frames:
        print(f"\n⚠️ Джерел даних не знайдено.")
        print("Надішліть посилання на Google Таблицю в чат або запустіть:")
        print("python scripts/consolidate_sheets.py \"https://docs.google.com/spreadsheets/d/...\"")
        return

    consolidated_certs = []
    consolidated_courses = {}

    print("\n🔄 Зведення всіх різноструктурних аркушів до єдиного формату...")

    for item in data_frames:
        source_name = item['source']
        tab_name = item['tab_name']
        df = item['df']

        # Determine year from tab name if present (e.g. "2024", "2025")
        year_match = re.search(r'\b(20[0-9]{2})\b', tab_name)
        extracted_year = int(year_match.group(1)) if year_match else datetime.now().year

        col_mapping = {}
        for col in df.columns:
            identified = identify_column(col)
            if identified:
                col_mapping[col] = identified

        df = df.rename(columns=col_mapping)

        # Fallback detection by data patterns if name or certCode are missing
        if 'name' not in df.columns or 'certCode' not in df.columns:
            for col in df.columns:
                sample_vals = [str(v).strip() for v in df[col].dropna().head(10)]
                # Check for certCode pattern (e.g. ЦІ-Б-1460, CERT-123, 1460, etc.)
                if 'certCode' not in df.columns and any(re.search(r'[А-ЯA-Z0-9]{1,5}-[0-9]{3,7}', v, re.IGNORECASE) for v in sample_vals):
                    df = df.rename(columns={col: 'certCode'})
                # Check for name pattern (e.g. 2 or 3 Ukrainian/Roman words)
                elif 'name' not in df.columns and any(re.match(r'^[А-ЯІЇЄA-Z][а-яіїєa-z\']+\s+[А-ЯІЇЄA-Z][а-яіїєa-z\']+', v) for v in sample_vals):
                    df = df.rename(columns={col: 'name'})

        if 'name' not in df.columns or 'certCode' not in df.columns:
            print(f"⚠️ Пропущено аркуш '{tab_name}': не вдалося розпізнати стовпчики ПІБ та Номер Сертифіката")
            continue

        default_course_title = f"Курс підвищення кваліфікації ({extracted_year})"

        for idx, row in df.iterrows():
            name = clean_name(row.get('name', ''))
            cert_code = str(row.get('certCode', '')).strip()

            if not name or not cert_code or cert_code.lower() == 'nan' or cert_code == '0':
                continue

            course_title = str(row.get('courseTitle', default_course_title)).strip()
            issue_date = clean_date(row.get('issueDate', ''))
            drive_url = str(row.get('driveUrl', '')).strip() if pd.notna(row.get('driveUrl')) else ''
            
            # Determine year priority: row year -> tab year -> current year
            row_year_val = str(row.get('year', ''))
            row_year = int(row_year_val) if row_year_val.isdigit() else extracted_year

            topic = str(row.get('topic', 'Загальні програми')).strip()
            hours = int(row.get('hours', 30)) if pd.notna(row.get('hours')) and str(row.get('hours')).isdigit() else 30
            credits_val = float(row.get('credits', round(hours / 30, 1))) if pd.notna(row.get('credits')) else round(hours / 30, 1)

            course_id = f"course_{row_year}_{idx+1}" if not course_title else f"course_{row_year}_{re.sub(r'[^a-zA-Z0-9]', '', course_title)[:20]}"
            if len(course_id.replace('_', '')) == 0 or course_id.endswith('_'):
                course_id = f"course_{row_year}_{idx+1}"

            if course_id not in consolidated_courses:
                consolidated_courses[course_id] = {
                    "id": course_id,
                    "title": course_title,
                    "topic": topic,
                    "year": row_year,
                    "hours": hours,
                    "credits": credits_val,
                    "period": issue_date or f"{row_year} рік",
                    "orderNo": f"Реєстр {row_year}"
                }

            consolidated_certs.append({
                "certCode": cert_code,
                "participantName": name,
                "participantNameClean": name.lower(),
                "courseId": course_id,
                "courseTitle": course_title,
                "topic": topic,
                "year": row_year,
                "issueDate": issue_date,
                "driveUrl": drive_url
            })

    # Save output
    courses_out = os.path.join(OUTPUT_DIR, 'courses.json')
    certs_out = os.path.join(OUTPUT_DIR, 'certificates.json')

    with open(courses_out, 'w', encoding='utf-8') as f:
        json.dump(list(consolidated_courses.values()), f, ensure_ascii=False, indent=2)

    with open(certs_out, 'w', encoding='utf-8') as f:
        json.dump(consolidated_certs, f, ensure_ascii=False, indent=2)

    print(f"\n🎉 ЗВЕДЕННЯ ЗАВЕРШЕНО УСПІШНО!")
    print(f"   • Оброблено аркушів: {len(data_frames)}")
    print(f"   • Збережено курсів: {len(consolidated_courses)}")
    print(f"   • Збережено сертифікатів: {len(consolidated_certs)}")
    print(f"📁 Збережено у: {os.path.abspath(OUTPUT_DIR)}")

if __name__ == '__main__':
    import sys
    urls = sys.argv[1:] if len(sys.argv) > 1 else None
    process_sheets(urls)

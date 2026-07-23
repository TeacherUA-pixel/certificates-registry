import os
import re
import json
import glob
import urllib.parse
import urllib.request
import pandas as pd
from datetime import datetime

# Directory paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_DIR = os.path.join(BASE_DIR, 'data')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
URLS_FILE = os.path.join(BASE_DIR, 'sheets_urls.txt')

os.makedirs(RAW_DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Synonym dictionary for column matching across different years/formats
COLUMN_SYNONYMS = {
    'name': ['піб', 'п.і.б.', 'пiб', 'учасник', 'випускник', 'слухач', 'фіо', 'фiо', 'full name', 'name', 'прізвище ім\'я'],
    'certCode': ['сертифікат', 'номер сертифіката', 'код сертифіката', 'номер', 'код', '№ сертифіката', 'сертифікат №', 'cert_code', 'code'],
    'issueDate': ['дата', 'дата видачі', 'дата видачи', 'date', 'issue_date'],
    'driveUrl': ['посилання', 'посилання на сертифікат', 'сертифікат (pdf)', 'drive', 'link', 'url', 'pdf'],
    'courseTitle': ['курс', 'назва курсу', 'тема курсу', 'програма', 'назва програми', 'course'],
    'year': ['рік', 'рік видачі', 'year'],
    'topic': ['напрям', 'тема', 'категорія', 'topic'],
    'hours': ['години', 'год', 'обсяг', 'hours'],
    'credits': ['кредити', 'єктс', 'credits']
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

def convert_gsheet_url_to_csv(url):
    """
    Перетворює звичайне посилання Google Таблиці у посилання на завантаження CSV.
    """
    sheet_id_match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if not sheet_id_match:
        return None
    sheet_id = sheet_id_match.group(1)
    
    gid_match = re.search(r'[#&?]gid=([0-9]+)', url)
    gid = gid_match.group(1) if gid_match else '0'

    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

def process_sheets(urls_list=None):
    print("🚀 Розпочинаємо процес обробки та консолідації Google Таблиць...")

    data_frames = []

    # 1. Завантаження за посиланнями, якщо вони надані
    target_urls = urls_list or []
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    target_urls.append(line)

    for url in target_urls:
        csv_url = convert_gsheet_url_to_csv(url)
        if csv_url:
            print(f"🌐 Завантаження Google Таблиці за посиланням: {url[:60]}...")
            try:
                df = pd.read_csv(csv_url)
                data_frames.append((f"GoogleSheet_{len(data_frames)+1}", df))
                print(f"   ✓ Успішно завантажено {len(df)} рядків.")
            except Exception as e:
                print(f"   ❌ Не вдалося завантажити Google Таблицю. Перевірте, чи доступ надано 'Усім, у кого є посилання'. Помилка: {e}")

    # 2. Локальні файли з папки data/
    raw_files = glob.glob(os.path.join(RAW_DATA_DIR, '*.csv')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xlsx')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xls'))

    for file_path in raw_files:
        filename = os.path.basename(file_path)
        print(f"📄 Читання локального файлу: {filename}")
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            data_frames.append((os.path.splitext(filename)[0], df))
        except Exception as e:
            print(f"   ❌ Помилка читання файлу {file_path}: {e}")

    if not data_frames:
        print(f"\n⚠️ Не знайдено жодного джерела даних!")
        print("Ви можете:")
        print("1. Вставити посилання на Google Таблиці у файл `sheets_urls.txt` (або передати в чат).")
        print("2. Або покласти локальні CSV / Excel файли у папку `data/`.")
        return

    consolidated_certs = []
    consolidated_courses = {}

    for source_name, df in data_frames:
        col_mapping = {}
        for col in df.columns:
            identified = identify_column(col)
            if identified:
                col_mapping[col] = identified

        df = df.rename(columns=col_mapping)

        if 'name' not in df.columns or 'certCode' not in df.columns:
            print(f"⚠️ Пропущено джерело '{source_name}': не знайдено колонок ПІБ чи Номер Сертифіката")
            continue

        default_course_title = source_name
        default_year = datetime.now().year

        for idx, row in df.iterrows():
            name = clean_name(row.get('name', ''))
            cert_code = str(row.get('certCode', '')).strip()

            if not name or not cert_code or cert_code.lower() == 'nan':
                continue

            course_title = str(row.get('courseTitle', default_course_title)).strip()
            issue_date = clean_date(row.get('issueDate', ''))
            drive_url = str(row.get('driveUrl', '')).strip() if pd.notna(row.get('driveUrl')) else ''
            year = int(row.get('year', default_year)) if pd.notna(row.get('year')) and str(row.get('year')).isdigit() else default_year
            topic = str(row.get('topic', 'Загальні теми')).strip()
            hours = int(row.get('hours', 30)) if pd.notna(row.get('hours')) and str(row.get('hours')).isdigit() else 30
            credits_val = float(row.get('credits', round(hours / 30, 1))) if pd.notna(row.get('credits')) else round(hours / 30, 1)

            course_id = re.sub(r'[^a-zA-Z0-9-]', '_', course_title.lower())[:30]

            if course_id not in consolidated_courses:
                consolidated_courses[course_id] = {
                    "id": course_id,
                    "title": course_title,
                    "topic": topic,
                    "year": year,
                    "hours": hours,
                    "credits": credits_val,
                    "period": issue_date or f"{year} рік",
                    "orderNo": f"Реєстр {year}"
                }

            consolidated_certs.append({
                "certCode": cert_code,
                "participantName": name,
                "participantNameClean": name.lower(),
                "courseId": course_id,
                "courseTitle": course_title,
                "topic": topic,
                "year": year,
                "issueDate": issue_date,
                "driveUrl": drive_url
            })

    # Збереження результату
    courses_out = os.path.join(OUTPUT_DIR, 'courses.json')
    certs_out = os.path.join(OUTPUT_DIR, 'certificates.json')

    with open(courses_out, 'w', encoding='utf-8') as f:
        json.dump(list(consolidated_courses.values()), f, ensure_ascii=False, indent=2)

    with open(certs_out, 'w', encoding='utf-8') as f:
        json.dump(consolidated_certs, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Успішно оброблено та консолідовано:")
    print(f"   • Курсів: {len(consolidated_courses)}")
    print(f"   • Сертифікатів: {len(consolidated_certs)}")
    print(f"📁 Збережено у: {os.path.abspath(OUTPUT_DIR)}")

if __name__ == '__main__':
    import sys
    urls = sys.argv[1:] if len(sys.argv) > 1 else None
    process_sheets(urls)

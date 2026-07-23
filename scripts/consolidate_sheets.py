import os
import re
import json
import glob
import pandas as pd
from datetime import datetime

# Directory for raw exported sheets (CSV or XLSX)
RAW_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'output')

os.makedirs(RAW_DATA_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Synonym dictionary for column matching across different years/formats
COLUMN_SYNONYMS = {
    'name': ['піб', 'п.і.б.', 'пiб', 'учасник', 'випускник', 'слухач', 'фіо', 'фiо', 'full name', 'name', 'прізвище ім'я'],
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
    # Replace multiple spaces, strip
    cleaned = re.sub(r'\s+', ' ', str(name_str)).strip()
    # Capitalize proper name
    return cleaned.title()

def clean_date(date_val):
    if pd.isna(date_val) or not date_val:
        return ""
    date_str = str(date_val).strip()
    
    # Try parsing common formats
    for fmt in ('%d.%m.%Y', '%Y-%m-%d', '%d/%m/%Y', '%d.%m.%y', '%Y.%m.%d'):
        try:
            dt = datetime.strptime(date_str.split(' ')[0], fmt)
            return dt.strftime('%d.%m.%Y')
        except ValueError:
            pass
    return date_str

def process_sheets():
    print("🚀 Розпочинаємо процес обробки та консолідації Google Таблиць...")
    
    raw_files = glob.glob(os.path.join(RAW_DATA_DIR, '*.csv')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xlsx')) + \
                glob.glob(os.path.join(RAW_DATA_DIR, '*.xls'))
    
    if not raw_files:
        print(f"⚠️ Папка '{RAW_DATA_DIR}' порожня.")
        print("Покладіть файли Excel або CSV з Google Таблиць у папку 'data' та запустіть скрипт знову.")
        return

    consolidated_certs = []
    consolidated_courses = {}

    for file_path in raw_files:
        print(f"📄 Обробка файлу: {os.path.basename(file_path)}")
        try:
            if file_path.endswith('.csv'):
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
        except Exception as e:
            print(f"❌ Помилка читання файлу {file_path}: {e}")
            continue

        # Map column names
        col_mapping = {}
        for col in df.columns:
            identified = identify_column(col)
            if identified:
                col_mapping[col] = identified

        df = df.rename(columns=col_mapping)
        
        # Check required fields
        if 'name' not in df.columns or 'certCode' not in df.columns:
            print(f"⚠️ Пропущено {os.path.basename(file_path)}: не знайдено обов'язкових колонок ПІБ або Номер Сертифіката")
            continue

        default_course_title = os.path.splitext(os.path.basename(file_path))[0]
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

    # Save to JSON
    courses_out = os.path.join(OUTPUT_DIR, 'courses.json')
    certs_out = os.path.join(OUTPUT_DIR, 'certificates.json')

    with open(courses_out, 'w', encoding='utf-8') as f:
        json.dump(list(consolidated_courses.values()), f, ensure_ascii=False, indent=2)

    with open(certs_out, 'w', encoding='utf-8') as f:
        json.dump(consolidated_certs, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Успішно консолідовано:")
    print(f"   • Курсів: {len(consolidated_courses)}")
    print(f"   • Сертифікатів: {len(consolidated_certs)}")
    print(f"📁 Файли збережено в: {os.path.abspath(OUTPUT_DIR)}")

if __name__ == '__main__':
    process_sheets()

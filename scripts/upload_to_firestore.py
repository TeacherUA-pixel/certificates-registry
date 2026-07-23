import os
import json

def upload_guide():
    """
    Інструкція та скрипт для завантаження консолідованих даних у Firebase Firestore.
    """
    courses_path = os.path.join(os.path.dirname(__file__), '..', 'output', 'courses.json')
    certs_path = os.path.join(os.path.dirname(__file__), '..', 'output', 'certificates.json')

    if not os.path.exists(courses_path) or not os.path.exists(certs_path):
        print("⚠️ Спочатку виконйте консолідацію таблиць через: python scripts/consolidate_sheets.py")
        return

    with open(courses_path, 'r', encoding='utf-8') as f:
        courses = json.load(f)

    with open(certs_path, 'r', encoding='utf-8') as f:
        certs = json.load(f)

    print(f"📊 Дані підготовлені для завантаження у Firestore:")
    print(f"   • Курсів: {len(courses)}")
    print(f"   • Сертифікатів: {len(certs)}")

    # Check for firebase_admin package
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        cred_file = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
        if not os.path.exists(cred_file):
            print("\n🔑 Для автоматичного завантаження у Firestore покладіть файл ключа `serviceAccountKey.json` у папку `scripts/`.")
            print("Ви можете завантажити цей файл з Firebase Console -> Project Settings -> Service Accounts -> Generate new private key.")
            return

        cred = credentials.Certificate(cred_file)
        firebase_admin.initialize_app(cred)
        db = firestore.client()

        print("\n⏳ Завантаження курсів у Firestore...")
        for c in courses:
            db.collection('courses').document(c['id']).set(c, merge=True)

        print("⏳ Завантаження сертифікатів у Firestore...")
        batch = db.batch()
        count = 0
        for cert in certs:
            doc_ref = db.collection('certificates').document(cert['certCode'])
            batch.set(doc_ref, cert, merge=True)
            count += 1
            if count % 400 == 0:  # Firestore batch limit is 500
                batch.commit()
                batch = db.batch()
        batch.commit()

        print("🎉 Завантаження у Firebase Firestore успішно завершено!")

    except ImportError:
        print("\n💡 Порада: для автоматичного завантаження встановіть модуль firebase-admin:")
        print("   pip install firebase-admin")

if __name__ == '__main__':
    upload_guide()

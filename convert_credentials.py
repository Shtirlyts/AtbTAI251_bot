import base64
import json

# Читаем credentials.json и конвертируем в base64
with open('credentials.json', 'r') as f:
    creds_data = json.load(f)
    creds_json_str = json.dumps(creds_data)
    creds_base64 = base64.b64encode(creds_json_str.encode()).decode()
    
    print("=" * 50)
    print("ВАША BASE64 СТРОКА ДЛЯ BOTHOST:")
    print("=" * 50)
    print(creds_base64)
    print("=" * 50)
    print("Скопируйте эту строку полностью и вставьте в Bothost как переменную GOOGLE_CREDENTIALS_JSON")
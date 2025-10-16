import os
import json
import base64
from dotenv import load_dotenv

load_dotenv()

# Настройки из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_URL = os.getenv("SPREADSHEET_URL", "https://docs.google.com/spreadsheets/d/1ZGtSy2eiUao5Ig08NzY9IrFaFSDr5GCjs2hV1hxhVQ8/edit?usp=sharing")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1885783905"))

# Настройки эмодзи для отметок
EMOJI_MAP = {
    'present': '✅',
    'absent': '❌', 
    'excused': '⚠️'
}

def get_google_credentials():
    """Загружает credentials из переменной окружения"""
    creds_base64 = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_base64:
        try:
            creds_json = base64.b64decode(creds_base64).decode()
            return json.loads(creds_json)
        except Exception as e:
            print(f"❌ Ошибка декодирования credentials: {e}")
            return None
    return None

def get_google_credentials():
    """Загружает credentials из переменной окружения"""
    import base64
    import json
    
    creds_base64 = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_base64:
        try:
            print("🔧 Загружаем credentials из переменных окружения...")
            creds_json = base64.b64decode(creds_base64).decode()
            return json.loads(creds_json)
        except Exception as e:
            print(f"❌ Ошибка декодирования credentials: {e}")
            return None
    print("⚠️ GOOGLE_CREDENTIALS_JSON не найден в переменных окружения")
    return None
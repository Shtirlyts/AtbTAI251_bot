import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1ZGtSy2eiUao5Ig08NzY9IrFaFSDr5GCjs2hV1hxhVQ8/edit?usp=sharing"

# Эмодзи для отметок
ATTENDANCE_MARKS = {
    'present': '✅',
    'absent': '❌', 
    'excused': '⚠️'
}
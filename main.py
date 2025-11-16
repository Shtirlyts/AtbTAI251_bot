import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import gspread
from datetime import datetime, timezone, timedelta
import requests
import json
from threading import Thread
import os
from functools import wraps
import time
import asyncio
from collections import deque
import psutil

# Импортируем настройки из config.py
from config import BOT_TOKEN, SPREADSHEET_URL, ADMIN_ID, EMOJI_MAP, get_google_credentials

# Настройка логирования в файл
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Отключаем логирование для httpx и httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

def log_execution_time(func_name, slow_threshold=2.0):
    """Декоратор для логирования времени выполнения с настраиваемым порогом"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                execution_time = time.time() - start_time
                
                # Логируем только если дольше порога
                if execution_time > slow_threshold:
                    logger.warning(f"🐌 {func_name}: {execution_time:.3f}с (медленно)")
                    send_log_to_server(f"🐌 {func_name}: {execution_time:.3f}с", "performance_slow", "warning")
                elif execution_time > 1.0:
                    logger.info(f"⏱️ {func_name}: {execution_time:.3f}с")
                    send_log_to_server(f"⏱️ {func_name}: {execution_time:.3f}с", "performance", "info")
                
                return result
            except Exception as e:
                execution_time = time.time() - start_time
                logger.error(f"❌ {func_name} ошибка после {execution_time:.3f}с: {e}")
                send_log_to_server(f"❌ {func_name} ошибка после {execution_time:.3f}с: {e}", "performance_error", "error")
                raise
        return wrapper
    return decorator

def send_log_to_server(log_message, log_type="bot", level="info"):
    """Отправка логов на наш сервер с московским временем"""
    def send_async():
        try:
            # Московское время (UTC+3)
            moscow_tz = timezone(timedelta(hours=3))
            
            log_data = {
                'log': str(log_message),
                'type': str(log_type),
                'level': str(level),
                'timestamp': datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')
            }
            
            response = requests.post(
                'http://redleg30607.fvds.ru/logger.php',
                json=log_data,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            
            if response.status_code == 200:
                print(f"✅ Лог отправлен: {log_message}")
            else:
                print(f"❌ Ошибка: {response.status_code} - {log_message}")
                
        except Exception as e:
            print(f"💥 Ошибка отправки: {e}")
    
    import threading
    thread = threading.Thread(target=send_async)
    thread.daemon = True
    thread.start()

def log_user_action(user_id, username, action, details="", level="info"):
    """Логирование действий пользователя ТОЛЬКО НА СЕРВЕР"""
    user_info = f"ID:{user_id} (@{username})"
    log_message = f"👤 {user_info} | {action}"
    if details:
        log_message += f" | {details}"
    
    send_log_to_server(log_message, "user_action", level)
    logger.info(f"📝 {log_message}")  # В консоль для отладки

def get_week_info(week_offset=0):
    """
    Получить информацию о неделе со смещением
    week_offset = 0 - текущая неделя
    week_offset = -1 - предыдущая неделя
    """
    try:
        moscow_tz = timezone(timedelta(hours=3))
        now = datetime.now(moscow_tz)
        
        # Начало семестра - 1 сентября 2025
        semester_start = datetime(2025, 9, 1, tzinfo=moscow_tz)
        days_diff = (now - semester_start).days
        
        # Учитываем смещение
        week_number = (days_diff // 7) + 1 + week_offset
        
        # Проверяем что неделя в пределах семестра (1-17)
        if week_number < 1 or week_number > 17:
            return None
        
        week_type = "Знаменатель" if week_number % 2 == 0 else "Числитель"
        
        return {
            'number': week_number,
            'type': week_type,
            'string': f"{week_type} - {week_number} неделя"
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка в определении недели: {e}")
        return None

def get_current_week_type():
    """Текущая неделя"""
    week_info = get_week_info(0)
    if week_info:
        return week_info['string']
    else:
        # Fallback
        return "Числитель - 8 неделя"

def retry_google_operation(max_attempts=3, delay=1, backoff=2):
    """Декоратор для повторных попыток при ошибках Google API"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if "Quota exceeded" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                        wait_time = min(30, current_delay * 3)
                        logger.warning(f"📊 Превышена квота Google API, ждем {wait_time}сек...")
                        time.sleep(wait_time)
                        current_delay *= backoff
                    elif attempt == max_attempts - 1:
                        logger.error(f"❌ Все попытки не удались для {func.__name__}: {e}")
                        raise e
                    else:
                        logger.warning(f"🔄 Попытка {attempt + 1}/{max_attempts} не удалась: {e}")
                        time.sleep(current_delay)
                        current_delay *= backoff
            return None
        return wrapper
    return decorator

@retry_google_operation(max_attempts=3, delay=2)
def connect_google_sheets():
    try:
        creds_dict = get_google_credentials()
        if creds_dict:
            gc = gspread.service_account_from_dict(creds_dict)
            logger.info("✅ Подключение к Google Sheets через переменные окружения")
        else:
            gc = gspread.service_account(filename='credentials.json')
            logger.info("✅ Подключение к Google Sheets через файл credentials.json")
        return gc.open_by_url(SPREADSHEET_URL)
    except Exception as e:
        error_msg = f"❌ КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Google Sheets: {str(e)}"
        logger.error(error_msg)
        send_log_to_server(error_msg, "error", "critical")
        return None

# Глобальные переменные
db = None
user_data = {}
user_states = {}

# Кеш
cache = {
    'week_strings': {},
    'blacklist': [],
    'admins': [],
}

# Предзагруженные данные для ускорения
preloaded_data = {
    'students': None,
    'schedule_1': None,
    'schedule_2': None,
    'blacklist': None,
    'last_loaded': 0
}

def is_user_blacklisted(user_id):
    """Проверка, находится ли пользователь в черном списке"""
    try:
        # Админа никогда не блокируем
        if user_id == ADMIN_ID:
            return False
            
        # Используем кэшированные данные
        blacklist = cache['blacklist']
        if not blacklist:
            return False
            
        # Проверяем ID в черном списке
        user_id_str = str(user_id).strip()
        for blacklisted_id in blacklist:
            if str(blacklisted_id).strip() == user_id_str:
                return True
                
        return False
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки черного списка: {e}")
        # В случае ошибки разрешаем доступ (безопаснее)
        return False

@retry_google_operation(max_attempts=3, delay=2)
def get_blacklist_data(force_refresh=False):
    """Получение данных черного списка с кэшированием"""
    # Если force_refresh=True, игнорируем кэш
    if not force_refresh and (preloaded_data.get('blacklist') is not None and 
        time.time() - preloaded_data['last_loaded'] < 300):
        return preloaded_data['blacklist']
    
    try:
        logger.info("📋 Загрузка черного списка из Google Sheets")
        blacklist_sheet = db.worksheet("Черный список")
        data = blacklist_sheet.col_values(1)  # Получаем только первую колонку
        
        # Пропускаем заголовок (A1) и берем данные с A2, фильтруем пустые значения
        blacklist_ids = []
        if len(data) > 1:
            blacklist_ids = [id_str.strip() for id_str in data[1:] if id_str.strip()]
            
        preloaded_data['blacklist'] = blacklist_ids
        preloaded_data['last_loaded'] = time.time()
        
        logger.info(f"✅ Загружено {len(blacklist_ids)} ID в черном списке")
        return blacklist_ids
        
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки черного списка: {e}")
        # Возвращаем старые данные или пустой список
        return preloaded_data.get('blacklist', [])

def check_blacklist(func):
    """Декоратор для проверки черного списка перед выполнением функции"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        username = update.effective_user.username or "Без username"
        
        # Проверяем черный список
        if is_user_blacklisted(user_id):
            log_user_action(user_id, username, "ПОПЫТКА ДОСТУПА ИЗ ЧЕРНОГО СПИСКА", "блокировка", "warning")
            # Не отправляем сообщение - просто игнорируем
            return
            
        return await func(update, context, *args, **kwargs)
    return wrapper

def preload_frequent_data():
    """Предзагрузка часто используемых данных включая черный список"""
    try:
        logger.info("🔄 Предзагрузка частых данных...")
        send_log_to_server("🔄 Предзагрузка частых данных...", "preload", "info")
        
        preloaded_data['students'] = get_students_data_optimized()
        preloaded_data['schedule_1'] = get_schedule_data_optimized(1)
        preloaded_data['schedule_2'] = get_schedule_data_optimized(2)
        preloaded_data['blacklist'] = get_blacklist_data()
        preloaded_data['last_loaded'] = time.time()
        
        # ОБНОВЛЯЕМ ЕДИНЫЙ КЕШ
        cache['blacklist'] = preloaded_data['blacklist']
        
        logger.info("✅ Предзагрузка завершена")
        send_log_to_server("✅ Предзагрузка завершена", "preload", "info")
        
        # Логируем размер загруженных данных
        students_count = len(preloaded_data['students']) if preloaded_data['students'] else 0
        schedule1_count = len(preloaded_data['schedule_1']) if preloaded_data['schedule_1'] else 0
        schedule2_count = len(preloaded_data['schedule_2']) if preloaded_data['schedule_2'] else 0
        blacklist_count = len(preloaded_data['blacklist']) if preloaded_data['blacklist'] else 0
        
        logger.info(f"📊 Загружено: {students_count} студентов, "
                   f"{schedule1_count} строк расписания 1, "
                   f"{schedule2_count} строк расписания 2, "
                   f"{blacklist_count} ID в черном списке")
                   
        send_log_to_server(f"📊 Загружено: {students_count} студентов, {schedule1_count} строк расписания 1, {schedule2_count} строк расписания 2, {blacklist_count} ID в черном списке", "preload_stats", "info")
                   
    except Exception as e:
        logger.error(f"❌ Ошибка предзагрузки: {e}")
        send_log_to_server(f"❌ Ошибка предзагрузки: {e}", "preload_error", "error")

@retry_google_operation(max_attempts=2, delay=1)
def get_students_data_optimized():
    """Оптимизированное получение данных студентов"""
    if preloaded_data['students'] is not None:
        return preloaded_data['students']
    else:
        logger.info("📚 Загрузка данных студентов из Google Sheets")
        students_sheet = db.worksheet("Студенты")
        data = students_sheet.get_all_records()
        preloaded_data['students'] = data
        return data

@retry_google_operation(max_attempts=2, delay=1) 
def get_schedule_data_optimized(subgroup):
    cache_key = f'schedule_{subgroup}'
    
    # Проверяем актуальность кэша (10 минут)
    if (preloaded_data.get(cache_key) is not None and 
        time.time() - preloaded_data['last_loaded'] < 600):
        return preloaded_data[cache_key]
    else:
        logger.info(f"📅 Загрузка расписания подгруппы {subgroup} из Google Sheets")
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        data = schedule_sheet.get_all_values()
        preloaded_data[cache_key] = data
        preloaded_data['last_loaded'] = time.time()
        return data

def get_week_status(user_id, week_string):
    """Получить статус недели для пользователя"""
    if user_id not in user_data:
        return '❓'
    
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    student_number = student_data['number']
    
    try:
        schedule_data = get_schedule_data_optimized(subgroup)
        header = schedule_data[0]
        
        # Находим колонку студента
        student_col = None
        for idx, cell in enumerate(header):
            if str(cell).strip() == str(student_number):
                student_col = idx
                break
        
        if student_col is None:
            return '❓'
        
        total_classes = 0
        marked_classes = 0
        
        # Быстрый подсчет пар
        for row in schedule_data[1:]:
            if len(row) > 2 and row[0] == week_string:
                total_classes += 1
                if len(row) > student_col and row[student_col].strip() in EMOJI_MAP.values():
                    marked_classes += 1
        
        if total_classes == 0:
            return '⚫'
        elif marked_classes == total_classes:
            return '✅'
        elif marked_classes > 0:
            return '🟡'
        else:
            return '❌'
            
    except Exception as e:
        logger.error(f"❌ Ошибка в get_week_status: {e}")
        return '❓'

def update_cache():
    """Обновление всего кеша"""
    try:
        logger.info("🔄 Начало обновления кеша...")
        
        # Обновляем черный список с принудительным обновлением
        old_blacklist_count = len(cache['blacklist'])
        new_blacklist = get_blacklist_data(force_refresh=True)  # ← ДОБАВИЛ force_refresh=True
        cache['blacklist'] = new_blacklist
        new_blacklist_count = len(new_blacklist)
        
        # Обновляем week_strings (очищаем старые данные)
        old_week_strings_count = len(cache['week_strings'])
        cache['week_strings'] = {}
        
        # Получаем актуальные данные для логирования
        students_data = get_students_data_optimized()
        schedule_1_data = get_schedule_data_optimized(1)
        schedule_2_data = get_schedule_data_optimized(2)
        
        students_count = len(students_data) if students_data else 0
        schedule1_count = len(schedule_1_data) if schedule_1_data else 0
        schedule2_count = len(schedule_2_data) if schedule_2_data else 0
        
        logger.info("🔄 Кеш успешно обновлен")
        logger.info(f"📊 Загружено: {students_count} студентов, "
                   f"{schedule1_count} строк расписания 1, "
                   f"{schedule2_count} строк расписания 2, "
                   f"{new_blacklist_count} ID в черном списке")
        
        # Логируем изменения
        if old_blacklist_count != new_blacklist_count:
            logger.info(f"📈 Изменения в черном списке: было {old_blacklist_count}, стало {new_blacklist_count}")
        
        send_log_to_server(
            f"🔄 Кеш обновлен: {students_count} студентов, {schedule1_count} строк расписания 1, {schedule2_count} строк расписания 2, {new_blacklist_count} ID в черном списке", 
            "cache_update", 
            "info"
        )
        
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка обновления кеша: {e}")
        send_log_to_server(f"❌ Ошибка обновления кеша: {e}", "cache_error", "error")
        return False

# RATE LIMITER 
class SmartRateLimiter:
    """Умный ограничитель для активных пользователей бота"""
    
    def __init__(self, max_requests=50, period=60, burst_allowance=10):
        self.requests = {}
        self.max_requests = max_requests
        self.period = period
        self.burst_allowance = burst_allowance
        self.lock = asyncio.Lock()
    
    async def is_allowed(self, user_id):
        async with self.lock:
            now = time.time()
            
            if user_id not in self.requests:
                self.requests[user_id] = deque(maxlen=self.max_requests + self.burst_allowance)
            
            user_requests = self.requests[user_id]
            
            # Удаляем старые запросы (старше периода)
            while user_requests and now - user_requests[0] > self.period:
                user_requests.popleft()
            
            # Проверяем лимит
            if len(user_requests) < self.max_requests:
                user_requests.append(now)
                return True
            elif len(user_requests) < self.max_requests + self.burst_allowance:
                if user_requests and now - user_requests[-1] < 0.5:
                    return False
                else:
                    user_requests.append(now)
                    return True
            else:
                return False
    
    async def get_wait_time(self, user_id):
        """Время до освобождения слота"""
        async with self.lock:
            if user_id in self.requests and self.requests[user_id]:
                oldest_request = self.requests[user_id][0]
                return max(0, self.period - (time.time() - oldest_request))
            return 0
    
    async def cleanup_old_users(self, max_age=3600):
        """Очистка неактивных пользователей (раз в час)"""
        async with self.lock:
            now = time.time()
            to_remove = []
            for user_id, requests in self.requests.items():
                if not requests or now - requests[-1] > max_age:
                    to_remove.append(user_id)
            
            for user_id in to_remove:
                del self.requests[user_id]

# Инициализация rate limiters
button_limiter = SmartRateLimiter(
    max_requests=60,      # 60 запросов в минуту
    period=60,            # период 60 секунд  
    burst_allowance=15    # разрешить 15 быстрых запросов подряд
)

message_limiter = SmartRateLimiter(
    max_requests=20,      # 20 сообщений в минуту
    period=60,
    burst_allowance=5     # 5 быстрых сообщений подряд
)

# Функции  
async def background_cleanup():     
    """Фоновая очистка старых записей rate limiter"""
    while True:
        await asyncio.sleep(3600)  # Каждый час
        await button_limiter.cleanup_old_users()
        await message_limiter.cleanup_old_users()
        logger.info("🧹 Очистка старых записей rate limiter")
        send_log_to_server("🧹 Очистка старых записей rate limiter", "cleanup", "info")

async def background_blacklist_update():
    """Фоновая задача для периодического обновления черного списка"""
    while True:
        await asyncio.sleep(300)  # Обновляем каждые 5 минут
        try:
            old_count = len(cache['blacklist'])

            new_blacklist = get_blacklist_data()
            
            cache['blacklist'] = new_blacklist
            preloaded_data['blacklist'] = new_blacklist
            new_count = len(new_blacklist)
            
            if old_count != new_count:
                logger.info(f"🔄 Черный список обновлен: было {old_count}, стало {new_count} записей")
                send_log_to_server(f"🔄 Черный список обновлен: {old_count} → {new_count} записей", "blacklist_update", "info")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обновления черного списка: {e}")

@check_blacklist
@log_execution_time("start")
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    log_user_action(user_id, username, "Команда /start")
    send_log_to_server(f"🟢 /start от {user_id} (@{username})", "command")
    
    try:
        students_data = get_students_data_optimized()

        user_found = False
        student_data = None
        
        for student in students_data:
            existing_id = str(student.get('Telegram ID', '')).strip()
            if existing_id and existing_id.isdigit() and int(existing_id) == user_id:
                user_found = True
                student_data = {
                    'fio': student['ФИО'],
                    'number': student['№'],
                    'subgroup': student['Подгруппа']
                }
                break
        
        if user_found:
            user_data[user_id] = student_data
            user_states[user_id] = "registered"
            log_user_action(user_id, username, "Автоматический вход", f"ФИО: {student_data['fio']}")
            
            keyboard = [[InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")]]
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"✅ С возвращением, {student_data['fio']}!\nПодгруппа: {student_data['subgroup']}",
                reply_markup=reply_markup
            )
        else:
            user_states[user_id] = "waiting_for_fio"
            await update.message.reply_text("Добро пожаловать! Введите ваше ФИО (Фамилия Имя Отчество):")
            
    except Exception as e:
        error_msg = f"❌ Ошибка в start для {user_id}: {str(e)}"
        logger.error(error_msg)
        send_log_to_server(error_msg, "error", "error")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

@check_blacklist
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    text = update.message.text
    
    if user_states.get(user_id) == "waiting_for_fio":
        await handle_fio(update, context)
    else:
        log_user_action(user_id, username, "НЕЗАРЕГИСТРИРОВАННОЕ СООБЩЕНИЕ", text, "warning")
        await update.message.reply_text("Сначала отправьте /start для регистрации")

@check_blacklist
@log_execution_time("handle_fio")
async def handle_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db is None:
        await update.message.reply_text("❌ Ошибка подключения к базе данных.")
        return
        
    fio = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    log_user_action(user_id, username, "Поиск ФИО", f"'{fio}'")
    
    try:
        students_data = get_students_data_optimized()
        
        user_found = False
        student_number = None
        subgroup = None
        
        for student in students_data:
            if student['ФИО'].lower() == fio.lower():
                existing_id = str(student.get('Telegram ID', '')).strip()
                if existing_id and existing_id.isdigit() and int(existing_id) != user_id:
                    log_user_action(user_id, username, "Попытка повторной регистрации", f"ФИО: '{fio}'")
                    await update.message.reply_text("❌ Этот аккаунт уже зарегистрирован на другого пользователя!")
                    return
                else:
                    user_found = True
                    student_number = student['№']
                    subgroup = student['Подгруппа']
                    break
        
        if not user_found:
            log_user_action(user_id, username, "ФИО не найдено", f"'{fio}'")
            await update.message.reply_text("❌ ФИО не найдено в базе! Обратитесь к администратору.")
            return
        
        # Сохраняем Telegram ID - получаем доступ к таблице
        students_sheet = db.worksheet("Студенты")
        cell = students_sheet.find(str(student_number))
        students_sheet.update_cell(cell.row, 4, str(user_id))
        
        user_data[user_id] = {
            'fio': fio,
            'number': student_number,
            'subgroup': subgroup
        }
        user_states[user_id] = "registered"
        
        log_user_action(user_id, username, "Регистрация успешна", f"№{student_number}, подгруппа {subgroup}")
        send_log_to_server(f"✅ Регистрация: {user_id} -> {fio}", "registration")
        keyboard = [[InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")]]
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"✅ Регистрация успешна!\nФИО: {fio}\nПодгруппа: {subgroup}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка регистрации {user_id}: {str(e)}"
        logger.error(error_msg)
        log_user_action(user_id, username, "ОШИБКА РЕГИСТРАЦИИ", str(e), "error")
        await update.message.reply_text("❌ Произошла ошибка при регистрации. Попробуйте позже.")

# АДМИН-ФУНКЦИИ
@check_blacklist
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        log_user_action(user_id, username, "Попытка доступа к админ-панели", "", "warning")
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    log_user_action(user_id, username, "Открытие админ-панели")
    
    keyboard = [
        [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
        [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],
        [InlineKeyboardButton("📊 Наличие пар", callback_data="admin_class_presence")],
        [InlineKeyboardButton("⚫ Черный список", callback_data="admin_blacklist")],
        [InlineKeyboardButton("🔄 Обновить кэш", callback_data="admin_refresh_cache")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ Админ-панель:", reply_markup=reply_markup)

async def admin_show_students(query):
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    
    log_user_action(user_id, username, "Запрос списка студентов")
    
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_values()
        
        text = "👥 Список студентов:\n\n"
        for student in students_data[1:]:
            if len(student) >= 4:
                status = "✅ Зарегистрирован" if student[3] else "❌ Не зарегистрирован"
                text += f"{student[0]}. {student[1]} - {status}\n"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при получении списка студентов: {e}")
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def admin_show_status(query):
    """Статус сервера с системной информацией"""
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    log_user_action(user_id, username, "Запрос статуса сервера")
    
    try:
        await query.edit_message_text("📊 Сбор данных о системе...")
        
        # 1. СТАТУС ПОДКЛЮЧЕНИЙ
        connections_status = "**🔗 СТАТУС ПОДКЛЮЧЕНИЙ**\n"
        if db is None:
            connections_status += "❌ **Google Sheets**: НЕТ ПОДКЛЮЧЕНИЯ\n"
        else:
            connections_status += "✅ **Google Sheets**: подключено\n"
            
            try:
                students_sheet = db.worksheet("Студенты")
                students_count = len(students_sheet.get_all_values()) - 1
                connections_status += f"✅ **Студенты**: {students_count} записей\n"
                
                subgroup1 = db.worksheet("1 подгруппа")
                subgroup2 = db.worksheet("2 подгруппа") 
                connections_status += "✅ **Расписание**: доступно\n"
            except Exception as e:
                connections_status += f"❌ **Ошибка таблиц**: {str(e)[:50]}...\n"
        
        # 2. СТАТИСТИКА БОТА
        bot_stats = "\n**🤖 СТАТИСТИКА БОТА**\n"
        bot_stats += f"• Пользователей: {len(user_data)}\n"
        bot_stats += f"• Активных сессий: {len(user_states)}\n"
        
        # Статистика по состояниям
        state_counts = {}
        for state in user_states.values():
            state_counts[state] = state_counts.get(state, 0) + 1
        
        for state, count in state_counts.items():
            bot_stats += f"• {state}: {count}\n"
        
        # 3. КЭШ И ПРОИЗВОДИТЕЛЬНОСТЬ
        cache_info = "\n**💾 КЭШ ДАННЫХ**\n"
        if preloaded_data['last_loaded'] > 0:
            cache_age = time.time() - preloaded_data['last_loaded']
            cache_minutes = int(cache_age // 60)
            cache_seconds = int(cache_age % 60)
            cache_info += f"• Возраст: {cache_minutes}м {cache_seconds}с\n"
            
            students_cached = len(preloaded_data.get('students', []))
            schedule1_cached = len(preloaded_data.get('schedule_1', []))
            schedule2_cached = len(preloaded_data.get('schedule_2', []))
            
            cache_info += f"• Студентов: {students_cached}\n"
            cache_info += f"• Расписание 1: {schedule1_cached} строк\n"
            cache_info += f"• Расписание 2: {schedule2_cached} строк\n"
            
            if cache_age > 600:
                cache_info += "⚠️ **Кэш устарел** (>10 минут)\n"
            else:
                cache_info += "✅ **Кэш актуален**\n"
        else:
            cache_info += "❌ **Кэш не загружен**\n"
        
        # 4. RATE LIMITER
        rate_info = "\n**🚦 RATE LIMITING**\n"
        try:
            rate_info += f"• Отслеживается: {len(button_limiter.requests)} пользователей\n"
        except:
            rate_info += "• Статистика недоступна\n"
        
        # 5. СИСТЕМНАЯ ИНФОРМАЦИЯ (ТЕКУЩИЕ ЗНАЧЕНИЯ)
        system_info = "\n**💻 СИСТЕМНАЯ ИНФОРМАЦИЯ**\n"
        try:
            # Процессор
            cpu_percent = psutil.cpu_percent(interval=0.1)
            system_info += f"• CPU: {cpu_percent:.1f}%\n"
            
            # Память
            memory = psutil.virtual_memory()
            system_info += f"• RAM: {memory.percent:.1f}% ({memory.used // (1024**3)}/{memory.total // (1024**3)} GB)\n"
            
            # Диск
            disk = psutil.disk_usage('/')
            system_info += f"• Disk: {disk.percent:.1f}% ({disk.used // (1024**3)}/{disk.total // (1024**3)} GB)\n"
            
            # Процесс бота
            process = psutil.Process()
            memory_info = process.memory_info()
            system_info += f"• Бот RAM: {memory_info.rss // (1024**2)} MB\n"
            
        except Exception as e:
            system_info += f"• Ошибка: {str(e)[:50]}\n"
        
        # 6. ОБЩИЙ СТАТУС
        status_text = (
            "**🖥️ СТАТУС СИСТЕМЫ**\n\n"
            f"{connections_status}"
            f"{bot_stats}" 
            f"{cache_info}"
            f"{rate_info}"
            f"{system_info}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(status_text, parse_mode='Markdown', reply_markup=reply_markup)
        
    except Exception as e:
        error_text = f"❌ Ошибка при получении статуса: {str(e)}"
        logger.error(error_text)
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(error_text, reply_markup=reply_markup)

async def admin_class_presence(query):
    """Меню управления наличием пар"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    keyboard = [
        [InlineKeyboardButton("📅 Выбрать неделю", callback_data="admin_presence_week")],
        [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("📊 Управление наличием пар:", reply_markup=reply_markup)

async def admin_show_presence_week_selection(query):
    """Показ выбора недели для управления наличием пар"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    try:
        # Получаем информацию о неделях
        current_week_info = get_week_info(0)
        previous_week_info = get_week_info(-1)
        
        keyboard = []
        
        if current_week_info:
            week_encoded = encode_week_string(current_week_info['string'])
            keyboard.append([
                InlineKeyboardButton(
                    f"📅 {current_week_info['string']}", 
                    callback_data=f"apw_{week_encoded}"
                )
            ])
        
        if previous_week_info:
            week_encoded = encode_week_string(previous_week_info['string'])
            keyboard.append([
                InlineKeyboardButton(
                    f"↩️ {previous_week_info['string']}", 
                    callback_data=f"apw_{week_encoded}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_class_presence")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите неделю для управления наличием пар:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_show_presence_week_selection: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке недель")

async def admin_show_presence_days(query, week_string):
    """Показ дней недели со статусом отмененных пар"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    try:
        # Нормализуем строку недели
        week_string = ' '.join(week_string.split())
        logger.info(f"🔍 АДМИН: Поиск пар для недели '{week_string}'")
        
        # Получаем все подгруппы
        subgroup1_sheet = db.worksheet("1 подгруппа")
        subgroup1_data = subgroup1_sheet.get_all_values()
        
        subgroup2_sheet = db.worksheet("2 подгруппа") 
        subgroup2_data = subgroup2_sheet.get_all_values()
        
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
        day_status = {}
        
        found_any_classes = False
        
        # Собираем ВСЕ пары для каждого дня
        for day in days:
            day_status[day] = {'total': 0, 'cancelled': 0}
            
            # 1 подгруппа
            for row in subgroup1_data[1:]:
                table_week = ' '.join(str(row[0]).split()) if len(row) > 0 else ""
                if len(row) > 2 and table_week == week_string and row[1] == day:
                    day_status[day]['total'] += 1
                    found_any_classes = True
                    
                    # Проверяем отмену
                    is_cancelled = any('⚙️' in str(cell) for cell in row[3:])
                    if is_cancelled:
                        day_status[day]['cancelled'] += 1
            
            # 2 подгруппа
            for row in subgroup2_data[1:]:
                table_week = ' '.join(str(row[0]).split()) if len(row) > 0 else ""
                if len(row) > 2 and table_week == week_string and row[1] == day:
                    day_status[day]['total'] += 1
                    found_any_classes = True
                    
                    is_cancelled = any('⚙️' in str(cell) for cell in row[3:])
                    if is_cancelled:
                        day_status[day]['cancelled'] += 1
        
        if not found_any_classes:
            await query.edit_message_text(f"❌ На неделе '{week_string}' нет занятий")
            return
        
        keyboard = []
        for day in days:
            status_text = ""
            
            if day_status[day]['total'] > 0:
                cancelled = day_status[day]['cancelled']
                total = day_status[day]['total']
                
                if cancelled == 0:
                    status_text = " ✅"
                elif cancelled == total:
                    status_text = " ❌" 
                else:
                    status_text = " 🟡"
            else:
                status_text = " ⚫"
            
            week_encoded = encode_week_string(week_string)
            callback_data = f"apd_{week_encoded}_{day}"
            keyboard.append([InlineKeyboardButton(f"{day}{status_text}", callback_data=callback_data)])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="admin_class_presence")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_explanation = (
            "✅ - нет отмененных пар\n"
            "🟡 - часть пар отменена\n" 
            "❌ - все пары отменены\n"
            "⚫ - нет пар в этот день"
        )
        
        await query.edit_message_text(
            f"📅 Выберите день недели ({week_string}):\n\n{status_explanation}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_show_presence_days: {e}")
        await query.edit_message_text(f"❌ Ошибка при загрузке расписания: {str(e)}")

async def admin_show_presence_subgroups(query, week_string, day):
    """Показ выбора подгруппы для управления наличием пар"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    keyboard = [
        [
            InlineKeyboardButton("1 подгруппа", callback_data=f"apsg_{encode_week_string(week_string)}_{day}_1"),
            InlineKeyboardButton("2 подгруппа", callback_data=f"apsg_{encode_week_string(week_string)}_{day}_2")
        ],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"apw_{encode_week_string(week_string)}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        f"📅 {week_string}\n"
        f"📅 {day}\n\n"
        "Выберите подгруппу:",
        reply_markup=reply_markup
    )

async def admin_show_presence_subjects(query, week_string, day, subgroup, context=None):
    """Показ предметов для управления отменой для конкретной подгруппы"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    logger.info(f"🔍 АДМИН: Загрузка предметов для {day} недели '{week_string}', подгруппа {subgroup}")

    try:
        # Получаем данные только для выбранной подгруппы
        sheet = db.worksheet(f"{subgroup} подгруппа")
        data = sheet.get_all_values()
        
        subjects_with_status = []
        
        # Проверяем временные изменения
        temp_cancellations = {}
        week_key = f"{week_string}_{day}_{subgroup}"
        if context and 'temp_cancellations' in context.user_data and week_key in context.user_data['temp_cancellations']:
            temp_cancellations = context.user_data['temp_cancellations'][week_key]
        
        # Обрабатываем выбранную подгруппу
        for row_num, row in enumerate(data[1:], start=2):
            table_week = ' '.join(str(row[0]).split()) if len(row) > 0 else ""
            if len(row) > 2 and table_week == week_string and row[1] == day:
                subject = row[2]
                
                # Проверяем статус - сначала временный, потом из таблицы
                temp_status = temp_cancellations.get(str(row_num), None)
                if temp_status is not None:
                    is_cancelled = (temp_status == "cancel")
                else:
                    is_cancelled = any('⚙️' in str(cell) for cell in row[3:])
                
                # Эмодзи шестеренки ПЕРЕД названием пары
                button_text = f"⚙️ {subject}" if is_cancelled else f"{subject}"
                subjects_with_status.append((subject, button_text, row_num, subgroup, is_cancelled))
        
        if not subjects_with_status:
            await query.edit_message_text(f"❌ На {day} ({week_string}) в {subgroup} подгруппе нет занятий")
            return
            
        keyboard = []
        for subject, button_text, row_num, subgroup, is_cancelled in subjects_with_status:
            action = "uncancel" if is_cancelled else "cancel"
            week_encoded = encode_week_string(week_string)
            
            # Создаем callback_data
            callback_data = f"apst_{week_encoded}_{day}_{subgroup}_{row_num}_{action}"
            
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
        
        # Показываем количество временных изменений
        temp_count = len(temp_cancellations)
        save_button_text = f"💾 Сохранить ({temp_count})" if temp_count > 0 else "💾 Сохранить"
        
        keyboard.append([InlineKeyboardButton("———", callback_data="separator")])
        keyboard.append([
            InlineKeyboardButton("🔙 Назад", callback_data=f"apd_{week_encoded}_{day}"),
            InlineKeyboardButton(save_button_text, callback_data=f"apss_{week_encoded}_{day}_{subgroup}")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        status_text = "⚙️ - пара отменена (временное изменение)" if temp_count > 0 else "⚙️ - пара отменена"
        
        await query.edit_message_text(
            f"📚 {day} - {week_string}:\n"
            f"Подгруппа - {subgroup}\n\n"
            f"Нажмите на предмет чтобы отменить/восстановить пару\n"
            f"{status_text}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_show_presence_subjects: {e}")
        await query.edit_message_text(f"❌ Ошибка при загрузке расписания: {str(e)}")

async def admin_temp_toggle_class_cancellation(query, week_string, day, subgroup, row_num, action, context):
    """Временное изменение статуса пары (без сохранения в таблицу)"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    try:
        # Инициализируем временное хранилище
        if 'temp_cancellations' not in context.user_data:
            context.user_data['temp_cancellations'] = {}
        
        week_key = f"{week_string}_{day}_{subgroup}"
        if week_key not in context.user_data['temp_cancellations']:
            context.user_data['temp_cancellations'][week_key] = {}
        
        # Сохраняем временное изменение
        context.user_data['temp_cancellations'][week_key][str(row_num)] = action
        
        message = "✅ Временное изменение применено (нажмите 'Сохранить' для подтверждения)"
        await query.answer(message, show_alert=False)
        
        # Возвращаемся к списку предметов с обновленными статусами
        await admin_show_presence_subjects(query, week_string, day, subgroup, context)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_temp_toggle_class_cancellation: {e}")
        await query.answer("❌ Ошибка при изменении статуса пары", show_alert=True)

async def admin_blacklist_menu(query):
    """Меню управления черным списком"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    log_user_action(user_id, query.from_user.username or "Без username", "Открытие меню черного списка")
    
    keyboard = [
        [InlineKeyboardButton("📋 Показать черный список", callback_data="admin_show_blacklist")],
        [InlineKeyboardButton("🔄 Обновить список", callback_data="admin_refresh_blacklist")],
        [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚫ Управление черным списком:", reply_markup=reply_markup)

async def admin_show_blacklist(query):
    """Показать черный список с username"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    log_user_action(user_id, query.from_user.username or "Без username", "Просмотр черного списка")
    
    try:
        blacklist = cache['blacklist']
        
        if not blacklist:
            await query.edit_message_text("📝 Черный список пуст")
            return
        
        await query.edit_message_text("🔄 Получаю информацию о пользователях...")
        
        message = "🚫 Заблокированные пользователи:\n\n"
        valid_users = 0
        failed_users = 0
        
        for i, user_id_str in enumerate(blacklist, 1):
            try:
                user_id_int = int(user_id_str.strip())
                
                # Пытаемся получить информацию о пользователе через бота
                try:
                    user = await query.bot.get_chat(user_id_int)
                    username = f"@{user.username}" if user.username else "нет username"
                    first_name = f" {user.first_name}" if user.first_name else ""
                    last_name = f" {user.last_name}" if user.last_name else ""
                    
                    message += f"{i}. {username}{first_name}{last_name} - ID: {user_id_str}\n"
                    valid_users += 1
                    
                except Exception as user_error:
                    # Если не получается через бота, пробуем альтернативные методы
                    message += f"{i}. ID: {user_id_str} (информация недоступна)\n"
                    failed_users += 1
                
                # Делаем небольшую задержку чтобы не превысить лимиты Telegram
                if i % 3 == 0:
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                # Если не получается обработать ID - показываем как есть
                message += f"{i}. ID: {user_id_str} (ошибка обработки)\n"
                failed_users += 1
        
        # Добавляем статистику
        message += f"\n📊 Статистика:\n"
        message += f"• Успешно: {valid_users} пользователей\n"
        message += f"• Недоступно: {failed_users} пользователей\n"
        message += f"• Всего: {len(blacklist)} записей"
        
        # Добавляем пояснение
        message += f"\n\n💡 Примечание: Информация может быть недоступна если:\n"
        message += f"• Пользователь никогда не писал боту\n"
        message += f"• Пользователь заблокировал бота\n"
        message += f"• Пользователь удалил аккаунт"
        
        keyboard = [
            [InlineKeyboardButton("🔄 Обновить список", callback_data="admin_refresh_blacklist")],
            [InlineKeyboardButton("🔙 Назад", callback_data="admin_blacklist")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при показе черного списка: {e}")
        await query.edit_message_text(f"❌ Ошибка при загрузке черного списка: {str(e)}")

async def admin_refresh_blacklist(query):
    """Обновить черный список"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    log_user_action(user_id, query.from_user.username or "Без username", "Обновление черного списка")
    
    try:
        await query.edit_message_text("🔄 Обновляю черный список...")
        
        # ПРИНУДИТЕЛЬНО обновляем черный список с флагом force_refresh
        old_count = len(cache['blacklist'])
        
        # Очищаем кэш чтобы гарантировать обновление
        preloaded_data['blacklist'] = None
        preloaded_data['last_loaded'] = 0
        
        # Загружаем заново
        new_blacklist = get_blacklist_data(force_refresh=True)
        cache['blacklist'] = new_blacklist
        new_count = len(new_blacklist)
        
        logger.info(f"✅ Черный список обновлен: было {old_count}, стало {new_count}")
        
        keyboard = [
            [InlineKeyboardButton("📋 Показать черный список", callback_data="admin_show_blacklist")],
            [InlineKeyboardButton("🔄 Обновить список", callback_data="admin_refresh_blacklist")],
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if old_count != new_count:
            message = f"✅ Черный список обновлен!\n\n📊 Было: {old_count} пользователей\n📊 Стало: {new_count} пользователей"
        else:
            message = f"✅ Черный список обновлен!\n\n📊 Количество пользователей не изменилось: {new_count}"
        
        await query.edit_message_text(message, reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении черного списка: {e}")
        await query.edit_message_text(f"❌ Ошибка при обновлении черного списка: {str(e)}")

async def admin_refresh_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для обновления кеша (только для админа)"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    message = await update.message.reply_text("🔄 Обновляю кеш...")
    
    if update_cache():
        await message.edit_text("✅ Кеш успешно обновлен!")
    else:
        await message.edit_text("❌ Ошибка при обновлении кеша")

async def admin_save_class_cancellations(query, week_string, day, subgroup, context):
    """Сохранение всех временных изменений статуса пар"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    try:
        week_key = f"{week_string}_{day}_{subgroup}"
        
        # Проверяем есть ли временные изменения
        if 'temp_cancellations' not in context.user_data or week_key not in context.user_data['temp_cancellations']:
            await query.answer("Нет изменений для сохранения", show_alert=True)
            await admin_show_presence_subjects(query, week_string, day, subgroup, context)
            return
        
        temp_cancellations = context.user_data['temp_cancellations'][week_key]
        if not temp_cancellations:
            await query.answer("Нет изменений для сохранения", show_alert=True)
            await admin_show_presence_subjects(query, week_string, day, subgroup, context)
            return
        
        # Показываем сообщение о начале сохранения
        await query.edit_message_text("💾 Сохранение изменений...")
        
        sheet = db.worksheet(f"{subgroup} подгруппа")
        
        # Используем batch update для ускорения
        updates = []
        updated_count = 0
        
        for row_num_str, action in temp_cancellations.items():
            row_num = int(row_num_str)
            header = sheet.row_values(1)
            
            if action == "cancel":
                # Отменяем пару - ставим ⚙️ всем студентам
                for col in range(4, len(header) + 1):
                    updates.append({
                        'range': f"{gspread.utils.rowcol_to_a1(row_num, col)}",
                        'values': [['⚙️']]
                    })
            else:
                # Восстанавливаем пару - убираем отметки у всех студентов
                for col in range(4, len(header) + 1):
                    updates.append({
                        'range': f"{gspread.utils.rowcol_to_a1(row_num, col)}", 
                        'values': [['']]
                    })
            
            updated_count += 1
        
        # Выполняем все обновления одним batch-запросом
        if updates:
            sheet.batch_update(updates)
        
        # Очищаем временные изменения
        del context.user_data['temp_cancellations'][week_key]
        
        logger.info(f"✅ АДМИН: Сохранено {updated_count} изменений для {day} {week_string}, подгруппа {subgroup}")
        
        # Показываем уведомление об успехе
        await query.answer(f"✅ Сохранено {updated_count} изменений", show_alert=True)
        
        # Возвращаемся к списку предметов (редактируем текущее сообщение)
        await admin_show_presence_subjects(query, week_string, day, subgroup, context)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_save_class_cancellations: {e}")
        await query.answer("❌ Ошибка при сохранении изменений", show_alert=True)
        # Пытаемся вернуться к списку предметов даже при ошибке
        try:
            await admin_show_presence_subjects(query, week_string, day, subgroup, context)
        except:
            await query.edit_message_text(f"❌ Ошибка при сохранении изменений: {str(e)}")

# ОСНОВНЫЕ ФУНКЦИИ БОТА
@log_execution_time("show_week_selection")
async def show_week_selection(query, user_id):
    """Показ выбора недели с статусами"""
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    try:
        student_data = user_data[user_id]
        username = query.from_user.username or "Без username"
        
        log_user_action(user_id, username, "Выбор недели для отметки")
        
        # Получаем информацию о неделях
        current_week_info = get_week_info(0)  # Текущая неделя
        previous_week_info = get_week_info(-1)  # Предыдущая неделя
        
        keyboard = []
        
        # Текущая неделя - всегда доступна
        if current_week_info:
            week_status = get_week_status(user_id, current_week_info['string'])
            keyboard.append([
                InlineKeyboardButton(
                    f"📅 {current_week_info['string']} {week_status}", 
                    callback_data=f"week_{current_week_info['string']}"
                )
            ])
        
        # Предыдущая неделя
        if previous_week_info:
            week_status = get_week_status(user_id, previous_week_info['string'])
            keyboard.append([
                InlineKeyboardButton(
                    f"↩️ {previous_week_info['string']} {week_status}", 
                    callback_data=f"week_{previous_week_info['string']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите неделю для отметки посещаемости:\n\n"
            "✅ - все пары недели отмечены\n"
            "🟡 - часть пар недели отмечена\n" 
            "❌ - пары недели не отмечены\n"
            "⚫ - нет пар на неделе",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в show_week_selection: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

@log_execution_time("show_days_with_status")
async def show_days_with_status(query, user_id, week_string=None, context=None):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    
    if week_string:
        week_type = week_string
        if context:
            context.user_data['week_string'] = week_string
    else:
        week_type = get_current_week_type()
    
    try:
        # Используем кэшированные данные
        schedule_data = get_schedule_data_optimized(subgroup)
        
        day_status = {}
        
        for row in schedule_data[1:]:
            if len(row) > 2 and row[0] == week_type:
                day = row[1]
                if day not in day_status:
                    day_status[day] = {'total': 0, 'marked': 0}
                
                day_status[day]['total'] += 1
                
                # Проверяем отметку студента
                header = schedule_data[0]
                student_col = None
                for idx, cell in enumerate(header):
                    if str(cell).strip() == str(student_data['number']):
                        student_col = idx
                        break
                
                if student_col and len(row) > student_col and row[student_col].strip() in EMOJI_MAP.values():
                    day_status[day]['marked'] += 1
        
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
        keyboard = []
        
        for day in days:
            status_text = ""
            if day in day_status:
                marked = day_status[day]['marked']
                total = day_status[day]['total']
                if total > 0:
                    if marked == total:
                        status_text = " ✅"
                    elif marked > 0:
                        status_text = " 🟡"
                    else:
                        status_text = " ❌"
            
            keyboard.append([InlineKeyboardButton(f"{day}{status_text}", callback_data=f"day_{day}")])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="mark_attendance")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await query.edit_message_text(
                f"📅 Выберите день недели ({week_type}):\n\n"
                "✅ - все пары отмечены\n"
                "🟡 - часть пар отмечена\n"
                "❌ - пары не отмечены",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                logger.info(f"Сообщение не изменилось (неделя: {week_type}), пропускаем")
            else:
                raise e
                
    except Exception as e:
        logger.error(f"❌ Ошибка в show_days_with_status: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

@log_execution_time("show_subjects")
async def show_subjects(query, day, user_id, week_string=None, context=None):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    student_number = student_data['number']
    
    if week_string:
        week_type = week_string
        if context:
            context.user_data['week_string'] = week_string
    else:
        week_type = get_current_week_type()
    
    username = query.from_user.username or "Без username"
    log_user_action(user_id, username, f"Просмотр предметов", f"день: {day}")
    
    try:
        schedule_data = get_schedule_data_optimized(subgroup)
        subjects_with_status = []
        header = schedule_data[0]
        
        student_col = None
        for idx, cell in enumerate(header):
            if str(cell).strip() == str(student_number):
                student_col = idx
                break
        
        # Собираем все строки для этого дня
        day_rows = []
        for row_num, row in enumerate(schedule_data[1:], start=2):
            if len(row) > 2 and row[0] == week_type and row[1] == day:
                day_rows.append((row_num, row))
        
        # Проверяем временные отметки
        temp_marks = {}
        day_key = f"{week_type}_{day}"
        if context and 'temp_marks' in context.user_data and day_key in context.user_data['temp_marks']:
            temp_marks = context.user_data['temp_marks'][day_key]
        
        for row_num, row in day_rows:
            subject = row[2]
            
            # Определяем тип занятия
            subject_lower = subject.lower()
            if "лекцион" in subject_lower:
                subject_type = "Лекция"
            elif "практическ" in subject_lower:
                subject_type = "Практика" 
            elif "лабораторн" in subject_lower:
                subject_type = "Лабораторная"
            else:
                subject_type = "Занятие"
            
            # Проверяем отметку - сначала временную, потом из таблицы
            mark = temp_marks.get(str(row_num), "")
            if not mark and student_col and len(row) > student_col:
                mark = row[student_col].strip()
                
             # ПРОВЕРЯЕМ, ОТМЕНЕНА ЛИ ПАРА (⚙️ у любого студента)
            is_cancelled = any('⚙️' in str(cell) for cell in row[3:])  # Проверяем колонки студентов
            
            # Инициализируем status пустой строкой
            status = ""
            if mark in EMOJI_MAP.values():
                status = f' {mark}'
            elif is_cancelled:
                status = ' ⚙️'
            
            button_text = f"{subject_type}{status}"
            subjects_with_status.append((subject, button_text, row_num, status, is_cancelled))
        
        if not subjects_with_status:
            await query.edit_message_text(f"❌ На {day} ({week_type}) нет занятий")
            return
            
        keyboard = []
        # Формируем клавиатуру с учетом отмененных пар
        for item in subjects_with_status:
            # Обрабатываем оба формата данных
            if len(item) == 5:
                subject, button_text, row_num, status, is_cancelled = item
            else: 
                subject, button_text, row_num, status = item
                is_cancelled = False
            
            if is_cancelled:
                # Для отмененных пар делаем кнопку неактивной
                keyboard.append([InlineKeyboardButton(button_text, callback_data="class_cancelled")])
            else:
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"subject_{day}_{row_num}")])
        
        keyboard.append([InlineKeyboardButton("———", callback_data="separator")])
        keyboard.append([
            InlineKeyboardButton("✅ Прис. на всех", callback_data=f"temp_all_{day}_present"),
            InlineKeyboardButton("❌ Отсут. на всех", callback_data=f"temp_all_{day}_absent")
        ])
        keyboard.append([
            InlineKeyboardButton("⚠️ Отсут. на всех(У)", callback_data=f"temp_all_{day}_excused"),
        ])
        keyboard.append([InlineKeyboardButton("———", callback_data="separator")])
        # Показываем количество временных изменений
        
        temp_count = len(temp_marks)
        save_button = "💾 Завершить"
        
        keyboard.append([
            InlineKeyboardButton("🔙 Назад", callback_data="back_to_days"),
            InlineKeyboardButton(save_button, callback_data=f"save_{day}")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Формируем список предметов с эмодзи
        subject_lines = []
        for item in subjects_with_status:
            # Обрабатываем оба формата данных
            if len(item) == 5:  # Новый формат с 5 элементами
                subject, button_text, row_num, status, is_cancelled = item
            else:  # Старый формат с 4 элементами
                subject, button_text, row_num, status = item
            
            if status.strip():
                subject_lines.append(f"{status} {subject}")
            else:
                subject_lines.append(f"  {subject}")

        full_subjects_text = "\n".join(subject_lines)
        
        try:
            await query.edit_message_text(
                f"📚 {day} - {week_type}:\n\n{full_subjects_text}\n\nВыберите предмет для отметки:",
                reply_markup=reply_markup
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                # Игнорируем эту ошибку - сообщение уже имеет нужное содержимое
                logger.info(f"Сообщение не изменилось (день: {day}), пропускаем")
            else:
                raise e
        
    except Exception as e:
        logger.error(f"❌ Ошибка в show_subjects: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

@log_execution_time("show_subject_actions")
async def show_subject_actions(query, day, row_num):
    """Показать выбор действия для предмета"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Присутствовал", callback_data=f"action_{day}_{row_num}_present"),
            InlineKeyboardButton("❌ Отсутствовал", callback_data=f"action_{day}_{row_num}_absent")
        ],
        [
            InlineKeyboardButton("⚠️ Отсутствовал(У)", callback_data=f"action_{day}_{row_num}_excused"),
        ],
        [
            InlineKeyboardButton("🔙 Назад", callback_data=f"back_to_subjects_{day}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите действие для отметки:", reply_markup=reply_markup)

@log_execution_time("temp_mark_attendance")
async def temp_mark_attendance(query, day, row_num, action, user_id, context):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    username = query.from_user.username or "Без username"
    
    mark = EMOJI_MAP.get(action, '❓')
    
    log_user_action(user_id, username, f"Временная отметка", f"день: {day}, статус: {mark}")
    
    # Сохраняем во временное хранилище
    if 'temp_marks' not in context.user_data:
        context.user_data['temp_marks'] = {}
    
    week_string = context.user_data.get('week_string', get_current_week_type())
    day_key = f"{week_string}_{day}"
    
    if day_key not in context.user_data['temp_marks']:
        context.user_data['temp_marks'][day_key] = {}
    
    if row_num == "all":
        # Для массовой отметки используем кэшированные данные
        subgroup = student_data['subgroup']
        try:
            schedule_data = get_schedule_data_optimized(subgroup)
            
            found_rows = 0
            for i, row in enumerate(schedule_data[1:], start=2):
                if len(row) > 2 and row[0] == week_string and row[1] == day:
                    # Проверка на отмену пары из кэша
                    is_cancelled = any('⚙️' in str(cell) for cell in row[3:])
                    if not is_cancelled:
                        context.user_data['temp_marks'][day_key][str(i)] = mark
                        found_rows += 1
                    
            logger.info(f"✅ Массовая отметка: {found_rows} пар отмечено как '{mark}'")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при получении данных для массовой отметки: {e}")
            await query.answer("❌ Ошибка при массовой отметке", show_alert=True)
            return
    else:
        # Одиночная отметка - используем кэшированные данные
        subgroup = student_data['subgroup']
        schedule_data = get_schedule_data_optimized(subgroup)
        
        # Преобразуем row_num в индекс (row_num начинается с 2, данные с 1)
        row_index = int(row_num) - 1
        if row_index < len(schedule_data):
            row_data = schedule_data[row_index]
            is_cancelled = any('⚙️' in str(cell) for cell in row_data[3:])
            
            if is_cancelled:
                await query.answer("❌ Эта пара была отменена администратором", show_alert=True)
                return
            
            context.user_data['temp_marks'][day_key][row_num] = mark
            logger.info(f"✅ Одиночная отметка: строка {row_num} отмечена как '{mark}'")
    
    # Возвращаем к списку предметов с обновленными статусами
    await show_subjects(query, day, user_id, week_string, context)

@log_execution_time("save_attendance")
async def save_attendance(query, day, user_id, context):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    student_number = student_data['number']
    username = query.from_user.username or "Без username"
    subgroup = student_data['subgroup']
    
    week_string = context.user_data.get('week_string', get_current_week_type())
    day_key = f"{week_string}_{day}"
    
    # Проверяем есть ли временные отметки
    if 'temp_marks' not in context.user_data or day_key not in context.user_data['temp_marks']:
        await query.answer("Нет изменений для сохранения", show_alert=True)
        await show_days_with_status(query, user_id, week_string, context)
        return
    
    temp_marks = context.user_data['temp_marks'][day_key]
    if not temp_marks:
        await query.answer("Нет изменений для сохранения", show_alert=True)
        await show_days_with_status(query, user_id, week_string, context)
        return
    
    try:
        # Показываем сообщение о начале сохранения
        await query.edit_message_text("💾 Сохранение отметок...")
        
        # Асинхронно выполняем сохранение
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: save_attendance_sync(subgroup, student_number, temp_marks))
        
        # 🔄 ОБНОВЛЯЕМ КЭШ ПОСЛЕ СОХРАНЕНИЯ
        cache_key = f'schedule_{subgroup}'
        preloaded_data[cache_key] = None  # Инвалидируем кэш
        logger.info(f"🔄 Кэш расписания подгруппы {subgroup} обновлен после сохранения")
        
        # Очищаем временные отметки
        del context.user_data['temp_marks'][day_key]
        
        log_user_action(user_id, username, "Сохранение отметок", f"день: {day}, сохранено: {len(temp_marks)} (BATCH)")
        
        # Показываем уведомление об успехе
        await query.answer(f"✅ Сохранено {len(temp_marks)} отметок", show_alert=True)
        
        # Возвращаем к списку дней (с обновленными статусами)
        await show_days_with_status(query, user_id, week_string, context)
        
    except Exception as e:
        error_msg = f"❌ Ошибка сохранения отметок {user_id}: {str(e)}"
        logger.error(error_msg)
        log_user_action(user_id, username, "ОШИБКА СОХРАНЕНИЯ", f"{day} - {str(e)}", "error")
        await query.edit_message_text("❌ Ошибка при сохранении отметок")

def save_attendance_sync(subgroup, student_number, temp_marks):
    """Синхронная версия сохранения отметок"""
    schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
    header = schedule_sheet.row_values(1)
    
    student_col = None
    for idx, cell in enumerate(header):
        if str(cell).strip() == str(student_number):
            student_col = idx + 1
            break
    
    if student_col is None:
        raise ValueError("Студент не найден в таблице посещаемости")
    
    # Используем batch update для ускорения
    updates = []
    for row_num_str, mark in temp_marks.items():
        row_num = int(row_num_str)
        updates.append({
            'range': f"{gspread.utils.rowcol_to_a1(row_num, student_col)}",
            'values': [[mark]]
        })
    
    # Выполняем все обновления одним запросом
    if updates:
        schedule_sheet.batch_update(updates)

# УТИЛИТЫ
def encode_week_string(week_string):
    """Кодирование строки недели в короткий формат"""
    # Простой хэш для создания короткого идентификатора
    week_hash = hash(week_string) % 1000000
    cache['week_strings'][str(week_hash)] = week_string
    return str(week_hash)

def decode_week_string(encoded_week):
    """Декодирование строки недели из короткого формата"""
    # Ищем в кэше
    if encoded_week in cache['week_strings']:
        return cache['week_strings'][encoded_week]
    
    # Если не найдено, возвращаем текущую неделю как fallback
    return get_current_week_type()

# ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК
@check_blacklist
@log_execution_time("button_handler")
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    data = query.data
    
    # Проверка доступности базы данных
    if db is None:
        await query.edit_message_text("❌ Временная проблема с подключением. Попробуйте позже.")
        log_user_action(user_id, username, "ОШИБКА БАЗЫ ДАННЫХ", data, "error")
        return
    
    try:
        # Проверка RATE LIMIT
        if user_id != ADMIN_ID:
            try:
                if not await button_limiter.is_allowed(user_id):
                    wait_time = await button_limiter.get_wait_time(user_id)
                    log_user_action(user_id, username, "ПРЕВЫШЕНИЕ ЛИМИТА КНОПОК", 
                                  f"ожидание: {int(wait_time)}сек", "warning")
                    
                    await query.edit_message_text(
                        f"⏳ Слишком много действий за последнюю минуту.\n"
                        f"Подождите {int(wait_time)} секунд перед следующим действием."
                    )
                    return
            except Exception as e:
                logger.error(f"❌ Ошибка в rate limiter: {e}")
                # Продолжаем выполнение если rate limiter сломался
                send_log_to_server(f"❌ Ошибка rate limiter: {e}", "rate_limiter_error", "error")

        if data == "back_to_main":
            # Возвращаем главное меню
            keyboard = [[InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")]]
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Главное меню:", reply_markup=reply_markup)
            return
            
        elif data == "mark_attendance":
            await show_week_selection(query, user_id)
        elif data.startswith("week_"):
            if data == "week_none":
                await query.answer("Эта неделя недоступна для отметки", show_alert=True)
                return
            week_string = data[5:]
            context.user_data['week_string'] = week_string
            await show_days_with_status(query, user_id, week_string, context)
        elif data == "admin_panel":
            if user_id == ADMIN_ID:
                keyboard = [
                    [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
                    [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],
                    [InlineKeyboardButton("📊 Наличие пар", callback_data="admin_class_presence")],
                    [InlineKeyboardButton("⚫ Черный список", callback_data="admin_blacklist")],
                    [InlineKeyboardButton("🔄 Обновить кэш", callback_data="admin_refresh_cache")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text("🛠️ Админ-панель:", reply_markup=reply_markup)
            else:
                await query.edit_message_text("❌ У вас нет доступа к админ-панели")
        elif data == "week_none":
            await query.answer("Эта неделя недоступна для управления наличием пар", show_alert=True)
            return
        elif data == "admin_students":
            await admin_show_students(query)
        elif data == "admin_status":
            await admin_show_status(query)
        elif data == "admin_class_presence":
            await admin_class_presence(query)
        elif data == "admin_presence_week":
            await admin_show_presence_week_selection(query)
        elif data == "admin_blacklist":
            await admin_blacklist_menu(query)
        elif data == "admin_show_blacklist":
            await admin_show_blacklist(query)
        elif data == "admin_refresh_blacklist":
            await admin_refresh_blacklist(query)
        elif data == "admin_refresh_cache":
            if user_id == ADMIN_ID:
                await query.edit_message_text("🔄 Обновление кэша...")
                try:
                    # Сохраняем текущее сообщение
                    original_message = query.message.text

                    if update_cache():
                        # Показываем уведомление об успехе
                        await query.answer("✅ Кеш обновлен", show_alert=True)

                        # Возвращаем в админ-панель с обновленным сообщением
                        keyboard = [
                            [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
                            [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],
                            [InlineKeyboardButton("📊 Наличие пар", callback_data="admin_class_presence")],
                            [InlineKeyboardButton("⚫ Черный список", callback_data="admin_blacklist")],
                            [InlineKeyboardButton("🔄 Обновить кэш", callback_data="admin_refresh_cache")],
                            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await query.edit_message_text("🛠️ Админ-панель (кеш обновлен ✅):", reply_markup=reply_markup)
                    else:
                        await query.answer("❌ Ошибка обновления кеша", show_alert=True)
                        # Возвращаем в админ-панель даже при ошибке
                        keyboard = [
                            [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
                            [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],
                            [InlineKeyboardButton("📊 Наличие пар", callback_data="admin_class_presence")],
                            [InlineKeyboardButton("⚫ Черный список", callback_data="admin_blacklist")],
                            [InlineKeyboardButton("🔄 Обновить кэш", callback_data="admin_refresh_cache")],
                            [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await query.edit_message_text("🛠️ Админ-панель (ошибка обновления кеша ❌):", reply_markup=reply_markup)
                
                except Exception as e:
                    await query.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)
            else:
                await query.answer("❌ Нет доступа", show_alert=True)
        elif data.startswith("apw_"):
            week_encoded = data[4:]
            try:
                # Ищем неделю в кэше
                week_string = decode_week_string(week_encoded)
                if week_string:
                    await admin_show_presence_days(query, week_string)
                else:
                    await query.edit_message_text("❌ Неделя не найдена")
            except Exception as e:
                logger.error(f"❌ Ошибка обработки недели: {e}")
                await query.edit_message_text("❌ Ошибка при выборе недели")
        elif data.startswith("apd_"):
            parts = data.split("_")
            if len(parts) >= 3:
                week_encoded = parts[1]
                day = '_'.join(parts[2:])
        
                # Ищем неделю в кэше
                week_string = decode_week_string(week_encoded)
        
                if week_string:
                    logger.info(f"🔍 АДМИН: Переход к выбору подгруппы дня {day} недели '{week_string}'")
                    await admin_show_presence_subgroups(query, week_string, day)
                else:
                    await query.edit_message_text("❌ Неделя не найдена")
        elif data.startswith("apsg_"):  # Admin Presence SubGroup
            parts = data.split("_")
            if len(parts) >= 4:
                week_encoded = parts[1]
                # Объединяем части дня (кроме последней - подгруппы)
                day = '_'.join(parts[2:-1])
                subgroup = parts[-1]
        
                # Ищем неделю в кэше
                week_string = decode_week_string(week_encoded)
        
                if week_string:
                    logger.info(f"🔍 АДМИН: Переход к предметам {day} недели '{week_string}', подгруппа {subgroup}")
                    await admin_show_presence_subjects(query, week_string, day, subgroup, context)
                else:
                    await query.edit_message_text("❌ Неделя не найдена")
        elif data.startswith("apst_"):  # Admin Presence Subject Temporary (временное изменение)
            parts = data.split("_")
            if len(parts) >= 6:
                week_encoded = parts[1]
                # Объединяем части дня
                day_parts = parts[2:-3]  # Все части между week_encoded и subgroup
                day = '_'.join(day_parts)
                subgroup = parts[-3]
                row_num = parts[-2]
                action = parts[-1]
        
                # Ищем неделю в кэше
                week_string = decode_week_string(week_encoded)
        
                if week_string:
                    logger.info(f"🔍 АДМИН: Временное изменение статуса пары {day} недели '{week_string}', подгруппа {subgroup}")
                    await admin_temp_toggle_class_cancellation(query, week_string, day, subgroup, row_num, action, context)
                else:
                    await query.edit_message_text("❌ Неделя не найдена")
        elif data.startswith("apss_"):  # Admin Presence Save Subjects
            parts = data.split("_")
            if len(parts) >= 4:
                week_encoded = parts[1]
                # Объединяем части дня
                day_parts = parts[2:-1]  # Все части между week_encoded и subgroup
                day = '_'.join(day_parts)
                subgroup = parts[-1]
        
                # Ищем неделю в кэше
                week_string = decode_week_string(week_encoded)
        
                if week_string:
                    logger.info(f"🔍 АДМИН: Сохранение изменений для {day} недели '{week_string}', подгруппа {subgroup}")
                    await admin_save_class_cancellations(query, week_string, day, subgroup, context)
                else:
                    await query.edit_message_text("❌ Неделя не найдена")
        elif data.startswith("day_"):
            day = data.split("_")[1]
            week_string = context.user_data.get('week_string')
            await show_subjects(query, day, user_id, week_string, context)
        elif data.startswith("subject_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            await show_subject_actions(query, day, row_num)
        elif data.startswith("back_to_subjects_"):
            day = data.split("_")[3]
            week_string = context.user_data.get('week_string')
            await show_subjects(query, day, user_id, week_string, context)
        elif data == "back_to_days":
            week_string = context.user_data.get('week_string')
            await show_days_with_status(query, user_id, week_string, context)
        elif data == "class_cancelled":
            await query.answer("❌ Эта пара была отменена администратором", show_alert=True)
        elif data.startswith("action_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            action = parts[3]
            await temp_mark_attendance(query, day, row_num, action, user_id, context)
        elif data.startswith("temp_all_"):
            parts = data.split("_")
            day = parts[2]
            action = parts[3]
            await temp_mark_attendance(query, day, "all", action, user_id, context)
        elif data == "mark_complete":
            week_string = context.user_data.get('week_string')
            await show_days_with_status(query, user_id, week_string, context)
        elif data.startswith("save_"):
            day = data.split("_")[1]
            await save_attendance(query, day, user_id, context)
        else:
            await query.edit_message_text("❌ Неизвестная команда")
    except Exception as e:
        error_msg = f"❌ Ошибка в button_handler {user_id}: {str(e)} | callback: {data}"
        logger.error(error_msg)
        send_log_to_server(error_msg, "error", "error")
        
        # Пытаемся отправить понятное сообщение пользователю
        try:
            await query.edit_message_text("❌ Произошла внутренняя ошибка. Попробуйте позже или перезапустите бота через /start")
        except:
            try:
                await context.bot.send_message(user_id, "❌ Произошла ошибка. Используйте /start для перезапуска")
            except:
                pass

def main():
    global db
    logger.info(f"🚀 ЗАПУСК БОТА...")
    send_log_to_server("🚀 ЗАПУСК БОТА: Инициализация...", "system", "info")
    
    try:
        db = connect_google_sheets()
        if db is None:
            send_log_to_server("💥 КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Google Sheets", "system", "critical")
            
            # Попытка повторного подключения через 30 секунд
            logger.info("🔄 Попытка повторного подключения через 30 секунд...")
            time.sleep(30)
            db = connect_google_sheets()
            
            if db is None:
                logger.critical("💥 Бот не может работать без подключения к Google Sheets")
                return
        
        # Предзагрузка данных
        preload_frequent_data()
        
        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
        application.add_handler(CommandHandler("update_cache", admin_refresh_cache_command))
        application.add_handler(CallbackQueryHandler(button_handler))

        logger.info("🤖 Бот запускается...")
        
        # Запускаем фоновые задачи
        loop = asyncio.get_event_loop()
        loop.create_task(background_cleanup())
        loop.create_task(background_blacklist_update())
        
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        error_msg = f"💥 КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {str(e)}"
        logger.critical(error_msg)
        send_log_to_server(error_msg, "system", "critical")

if __name__ == "__main__":
    main()
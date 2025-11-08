import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import gspread
from datetime import datetime, timezone, timedelta
import requests
import json
from threading import Thread
import os

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

# Подключение к Google Sheets
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
week_strings_cache = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    log_user_action(user_id, username, "Команда /start")
    send_log_to_server(f"🟢 /start от {user_id} (@{username})", "command")
    
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()

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

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    text = update.message.text
    
    if user_states.get(user_id) == "waiting_for_fio":
        await handle_fio(update, context)
    else:
        log_user_action(user_id, username, "НЕЗАРЕГИСТРИРОВАННОЕ СООБЩЕНИЕ", text, "warning")
        await update.message.reply_text("Сначала отправьте /start для регистрации")

async def handle_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db is None:
        await update.message.reply_text("❌ Ошибка подключения к базе данных.")
        return
        
    fio = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    log_user_action(user_id, username, "Поиск ФИО", f"'{fio}'")
    
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()
        
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
        
        # Сохраняем Telegram ID
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
    """Показать статус сервера"""
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        await query.edit_message_text("❌ У вас нет доступа")
        return
    
    log_user_action(user_id, username, "Запрос статуса сервера")
    
    try:
        import psutil
        import platform
        
        # Информация о системе
        system_info = f"🖥️ **Система**: {platform.system()} {platform.release()}\n"
        
        # Использование CPU
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_info = f"⚡ **CPU**: {cpu_percent}%\n"
        
        # Использование памяти
        memory = psutil.virtual_memory()
        memory_info = f"💾 **Память**: {memory.percent}% ({memory.used//1024//1024}MB/{memory.total//1024//1024}MB)\n"
        
        # Использование диска
        disk = psutil.disk_usage('/')
        disk_info = f"💽 **Диск**: {disk.percent}% ({disk.used//1024//1024//1024}GB/{disk.total//1024//1024//1024}GB)\n"
        
        # Время работы
        boot_time = psutil.boot_time()
        uptime = datetime.now() - datetime.fromtimestamp(boot_time)
        uptime_info = f"⏱️ **Аптайм**: {str(uptime).split('.')[0]}\n"
        
        # Статистика бота
        bot_info = f"🤖 **Пользователей**: {len(user_data)}\n"
        
        status_text = (
            "**🖥️ Статус сервера**\n\n"
            f"{system_info}"
            f"{cpu_info}"
            f"{memory_info}"
            f"{disk_info}"
            f"{uptime_info}"
            f"{bot_info}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(status_text, parse_mode='Markdown', reply_markup=reply_markup)
        
    except Exception as e:
        error_text = f"❌ Ошибка при получении статуса сервера: {str(e)}"
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
            # Используем полное название дня в callback_data чтобы избежать путаницы
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

async def admin_show_presence_subjects_new(query, week_string, day, subgroup, context):
    """Показ предметов для управления отменой (новая версия с отправкой нового сообщения)"""
    user_id = query.from_user.id
    if user_id != ADMIN_ID:
        await query.message.reply_text("❌ У вас нет доступа")
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
            await query.message.reply_text(f"❌ На {day} ({week_string}) в {subgroup} подгруппе нет занятий")
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
        
        # Отправляем новое сообщение вместо редактирования
        await query.message.reply_text(
            f"📚 {day} - {week_string}:\n"
            f"Подгруппа - {subgroup}\n\n"
            f"Нажмите на предмет чтобы отменить/восстановить пару\n"
            f"{status_text}",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_show_presence_subjects: {e}")
        await query.message.reply_text(f"❌ Ошибка при загрузке расписания: {str(e)}")

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
        
        # Используем новую функцию для показа предметов
        await admin_show_presence_subjects_new(query, week_string, day, subgroup, context)
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_temp_toggle_class_cancellation: {e}")
        await query.answer("❌ Ошибка при изменении статуса пары", show_alert=True)


# ОСНОВНЫЕ ФУНКЦИИ БОТА
async def show_week_selection(query, user_id):
    """Показ выбора недели"""
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    try:
        student_data = user_data[user_id]
        subgroup = student_data['subgroup']
        username = query.from_user.username or "Без username"
        
        log_user_action(user_id, username, "Выбор недели для отметки")
        
        # Получаем данные расписания для проверки наличия недель
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        schedule_data = schedule_sheet.get_all_values()
        
        # Получаем информацию о неделях
        current_week_info = get_week_info(0)  # Текущая неделя
        previous_week_info = get_week_info(-1)  # Предыдущая неделя
        
        keyboard = []
        
        # Текущая неделя - всегда доступна
        if current_week_info:
            keyboard.append([
                InlineKeyboardButton(
                    f"📅 {current_week_info['string']}", 
                    callback_data=f"week_{current_week_info['string']}"
                )
            ])
        
        # Предыдущая неделя - проверяем наличие в расписании
        if previous_week_info:
            # Проверяем есть ли занятия на предыдущей неделе в расписании
            week_has_classes = any(
                len(row) > 2 and row[0] == previous_week_info['string'] 
                for row in schedule_data[1:]  # Пропускаем заголовок
            )
            
            if week_has_classes:
                keyboard.append([
                    InlineKeyboardButton(
                        f"↩️ {previous_week_info['string']}", 
                        callback_data=f"week_{previous_week_info['string']}"
                    )
                ])
            else:
                # Если недели нет в расписании
                keyboard.append([
                    InlineKeyboardButton(
                        "❌ Недели нет в расписании", 
                        callback_data="week_none"
                    )
                ])
        else:
            # Если предыдущей недели не существует (например, первая неделя семестра)
            keyboard.append([
                InlineKeyboardButton(
                    "❌ Недели нет", 
                    callback_data="week_none"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "Выберите неделю для отметки посещаемости:",
            reply_markup=reply_markup
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка в show_week_selection: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

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
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        schedule_data = schedule_sheet.get_all_values()
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
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        schedule_data = schedule_sheet.get_all_values()
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

async def temp_mark_attendance(query, day, row_num, action, user_id, context):
    """Временное сохранение отметки (без записи в таблицу)"""
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
        # Для массовой отметки нужно получить все row_num этого дня
        subgroup = student_data['subgroup']
        try:
            schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
            schedule_data = schedule_sheet.get_all_values()
            
            # Ищем строки для этого дня и недели
            found_rows = 0
            for i, row in enumerate(schedule_data[1:], start=2):
                if len(row) > 2 and row[0] == week_string and row[1] == day:
                    # проверка на отмену пары
                    is_cancelled = any('⚙️' in str(cell) for cell in row[3:])
                    if not is_cancelled:  # Только если пара не отменена
                        context.user_data['temp_marks'][day_key][str(i)] = mark
                        found_rows += 1
                    
            logger.info(f"✅ Массовая отметка: {found_rows} пар отмечено как '{mark}'")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при получении данных для массовой отметки: {e}")
            await query.answer("❌ Ошибка при массовой отметке", show_alert=True)
            return
    else:
        # Одиночная отметка
        subgroup = student_data['subgroup']
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        row_data = schedule_sheet.row_values(int(row_num))
        is_cancelled = any('⚙️' in str(cell) for cell in row_data[3:])
        
        if is_cancelled:
            await query.answer("❌ Эта пара была отменена администратором", show_alert=True)
            return
        
        context.user_data['temp_marks'][day_key][row_num] = mark
        logger.info(f"✅ Одиночная отметка: строка {row_num} отмечена как '{mark}'")
    
    # Возвращаем к списку предметов с обновленными статусами
    await show_subjects(query, day, user_id, week_string, context)

async def save_attendance(query, day, user_id, context):
    """Сохранение всех временных отметок в таблицу"""
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
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        header = schedule_sheet.row_values(1)
        
        student_col = None
        for idx, cell in enumerate(header):
            if str(cell).strip() == str(student_number):
                student_col = idx + 1
                break
        
        if student_col is None:
            await query.edit_message_text("❌ Ошибка: студент не найден в таблице посещаемости")
            return
        
        updated_count = 0
        for row_num_str, mark in temp_marks.items():
            row_num = int(row_num_str)
            schedule_sheet.update_cell(row_num, student_col, mark)
            updated_count += 1
        
        # Очищаем временные отметки
        del context.user_data['temp_marks'][day_key]
        
        log_user_action(user_id, username, "Сохранение отметок", f"день: {day}, сохранено: {updated_count}")
        
        # Возвращаем к списку дней
        await show_days_with_status(query, user_id, week_string, context)
        
    except Exception as e:
        error_msg = f"❌ Ошибка сохранения отметок {user_id}: {str(e)}"
        logger.error(error_msg)
        log_user_action(user_id, username, "ОШИБКА СОХРАНЕНИЯ", f"{day} - {str(e)}", "error")
        await query.edit_message_text("❌ Ошибка при сохранении отметок")

# УТИЛИТЫ
def encode_week_string(week_string):
    """Кодирование строки недели в короткий формат"""
    # Простой хэш для создания короткого идентификатора
    week_hash = hash(week_string) % 1000000
    week_strings_cache[week_string] = week_hash
    return str(week_hash)

def decode_week_string(encoded_week):
    """Декодирование строки недели из короткого формата"""
    # Ищем в кэше
    for week_str, week_hash in week_strings_cache.items():
        if str(week_hash) == encoded_week:
            return week_str
    
    # Если не найдено, возвращаем текущую неделю как fallback
    return get_current_week_type()

# ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    data = query.data
    
    try:
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
        elif data == "admin_presence_week":
            await admin_show_presence_week_selection(query)
        elif data.startswith("apw_"):
            week_encoded = data[4:]
            try:
                # Ищем неделю в кэше
                week_string = None
                for week_str, week_hash in week_strings_cache.items():
                    if str(week_hash) == week_encoded:
                        week_string = week_str
                        break
        
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
                week_string = None
                for week_str, week_hash in week_strings_cache.items():
                    if str(week_hash) == week_encoded:
                        week_string = week_str
                        break
        
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
                week_string = None
                for week_str, week_hash in week_strings_cache.items():
                    if str(week_hash) == week_encoded:
                        week_string = week_str
                        break
        
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
                week_string = None
                for week_str, week_hash in week_strings_cache.items():
                    if str(week_hash) == week_encoded:
                        week_string = week_str
                        break
        
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
                week_string = None
                for week_str, week_hash in week_strings_cache.items():
                    if str(week_hash) == week_encoded:
                        week_string = week_str
                        break
        
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
        await query.edit_message_text("❌ Произошла ошибка при обработке запроса")

#Недостающие простые функции
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        student_data = user_data[user_id]
        await update.message.reply_text(f"📊 Ваш статус:\nФИО: {student_data['fio']}\nПодгруппа: {student_data['subgroup']}")
    else:
        await update.message.reply_text("❌ Сначала зарегистрируйтесь через /start")

async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        logger.critical("🛑 Выключение бота по команде администратора")
        await update.message.reply_text("🛑 Бот выключается...")
        os._exit(0)
    else:
        await update.message.reply_text("❌ У вас нет прав для этой команды")

def main():
    global db
    logger.info(f"🚀 ЗАПУСК БОТА: Окружение - {'СЕРВЕР' if os.path.exists('/root/AtbTAI251_bot') else 'ЛОКАЛЬНОЕ'}")
    send_log_to_server("🚀 ЗАПУСК БОТА: Инициализация...", "system", "info")
    
    try:
        db = connect_google_sheets()
        if db is None:
            send_log_to_server("💥 КРИТИЧЕСКАЯ ОШИБКА: Не удалось подключиться к Google Sheets", "system", "critical")
            return
        
        send_log_to_server("✅ Бот успешно подключился к Google Sheets", "system", "info")
        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_panel))
        application.add_handler(CommandHandler("status", status_command))
        application.add_handler(CommandHandler("stop", stop_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
        application.add_handler(CallbackQueryHandler(button_handler))

        logger.info("🤖 Бот запускается...")
        application.run_polling()
        
    except Exception as e:
        error_msg = f"💥 КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {str(e)}"
        logger.critical(error_msg)
        send_log_to_server(error_msg, "system", "critical")

if __name__ == "__main__":
    main()
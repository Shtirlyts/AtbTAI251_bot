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
    """Исправленная отправка логов на сервер"""
    def send_async():
        try:
            print(f"🔧 ОТПРАВКА ЛОГА: {log_message}")
            
            log_data = {
                'log': log_message,
                'type': log_type,
                'level': level,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            response = requests.post(
                'http://45.150.8.223/logs.php',
                json=log_data,  # ИСПРАВЛЕНО: используем json вместо data
                timeout=5
            )
            
            if response.status_code == 200:
                print(f"✅ Лог успешно отправлен (статус {response.status_code})")
            else:
                print(f"❌ Ошибка отправки лога (статус {response.status_code}): {response.text}")
                
        except requests.exceptions.Timeout:
            print("⏰ Таймаут при отправке лога")
        except requests.exceptions.ConnectionError:
            print("🔌 Ошибка подключения к серверу логов")
        except Exception as e:
            print(f"💥 Неожиданная ошибка: {e}")
    
    Thread(target=send_async).start()

def log_user_action(user_id, username, action, details="", level="info"):
    """Логирование действий пользователя ТОЛЬКО НА СЕРВЕР"""
    user_info = f"ID:{user_id} (@{username})"
    log_message = f"👤 {user_info} | {action}"
    if details:
        log_message += f" | {details}"
    
    send_log_to_server(log_message, "user_action", level)
    logger.info(f"📝 {log_message}")  # В консоль для отладки

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

# ОСНОВНЫЕ ФУНКЦИИ БОТА
async def show_week_selection(query, user_id):
    """Показ выбора недели"""
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    username = query.from_user.username or "Без username"
    
    log_user_action(user_id, username, "Выбор недели для отметки")
    
    try:
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
                
                if student_col and len(row) > student_col and row[student_col].strip() in ['✅', '❌', '⚠️']:
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
        await query.edit_message_text(
            f"📅 Выберите день недели ({week_type}):\n\n"
            "✅ - все пары отмечены\n"
            "🟡 - часть пар отмечена\n"
            "❌ - пары не отмечены",
            reply_markup=reply_markup
        )
        
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
            
            status = ""
            if mark == '✅':
                status = ' ✅'
            elif mark == '❌':
                status = ' ❌'
            elif mark == '⚠️':
                status = ' ⚠️'
            
            button_text = f"{subject_type}{status}"
            subjects_with_status.append((subject, button_text, row_num, status))
        
        if not subjects_with_status:
            await query.edit_message_text(f"❌ На {day} ({week_type}) нет занятий")
            return
            
        keyboard = []
        for full_subject, button_text, row_num, status in subjects_with_status:
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"subject_{day}_{row_num}")])
        
        keyboard.append([InlineKeyboardButton("———", callback_data="separator")])
        keyboard.append([
            InlineKeyboardButton("✅ Прис. на всех", callback_data=f"temp_all_{day}_present"),
            InlineKeyboardButton("❌ Отсут. на всех", callback_data=f"temp_all_{day}_absent")
        ])
        keyboard.append([
            InlineKeyboardButton("⚠️ Отсут. на всех(У)", callback_data=f"temp_all_{day}_excused")
        ])
        
        # Показываем количество временных изменений
        temp_count = len(temp_marks)
        save_button = "💾 Завершить"
        
        keyboard.append([
            InlineKeyboardButton("🔙 Назад", callback_data="mark_attendance"),
            InlineKeyboardButton(save_button, callback_data=f"save_{day}")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Формируем список предметов с эмодзи
        full_subjects_text = "\n".join([f"{status} {subject}" if status.strip() else f"  {subject}" for subject, _, _, status in subjects_with_status])
        
        await query.edit_message_text(
            f"📚 {day} - {week_type}:\n\n{full_subjects_text}\n\nВыберите предмет для отметки:",
            reply_markup=reply_markup
        )
        
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
            InlineKeyboardButton("🔙 Назад", callback_data=f"day_{day}")
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
    
    emoji_map = {'present': '✅', 'absent': '❌', 'excused': '⚠️'}
    mark = emoji_map.get(action, '❓')
    
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
        student_data = user_data[user_id]
        subgroup = student_data['subgroup']
        try:
            schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
            schedule_data = schedule_sheet.get_all_values()
            
            for i, row in enumerate(schedule_data[1:], start=2):
                if len(row) > 2 and row[0] == week_string and row[1] == day:
                    context.user_data['temp_marks'][day_key][str(i)] = mark
        except Exception as e:
            logger.error(f"Ошибка при получении данных для массовой отметки: {e}")
    else:
        # Одиночная отметка
        context.user_data['temp_marks'][day_key][row_num] = mark
    
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
def get_current_week_type():
    """Текущая неделя"""
    try:
        # Московский часовой пояс (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        now_tz = datetime.now(moscow_tz)
        
        # Начало семестра - 1 сентября 2025
        semester_start = datetime(2025, 9, 1, tzinfo=moscow_tz)
        days_diff = (now_tz - semester_start).days
        week_number = (days_diff // 7) + 1
        
        # Определяем тип недели (четная/нечетная)
        week_type = "Знаменатель" if week_number % 2 == 0 else "Числитель"
        
        result = f"{week_type} - {week_number} неделя"
        
        logger.info(f"📅 Текущая неделя: {result} (дата: {now_tz.strftime('%d.%m.%Y %H:%M')})")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка в определении недели: {e}")
        # Fallback
        return "Знаменатель - 8 неделя"

def get_week_info(week_offset=0):
    """
    Получить информацию о неделе со смещением
    week_offset = 0 - текущая неделя
    week_offset = -1 - предыдущая неделя
    """
    try:
        moscow_tz = timezone(timedelta(hours=3))
        now = datetime.now(moscow_tz)
        
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

# ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    data = query.data
    
    log_user_action(user_id, username, "Callback", data)
    
    try:
        if data == "mark_attendance":
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
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text("🛠️ Админ-панель:", reply_markup=reply_markup)
            else:
                await query.edit_message_text("❌ У вас нет доступа к админ-панели")
        elif data == "admin_students":
            await admin_show_students(query)
        elif data == "admin_status":
            await admin_show_status(query)
        elif data == "back_to_main":
            keyboard = [[InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")]]
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Главное меню:", reply_markup=reply_markup)
        elif data.startswith("day_"):
            day = data.split("_")[1]
            week_string = context.user_data.get('week_string')
            await show_subjects(query, day, user_id, week_string, context)
        elif data.startswith("subject_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            await show_subject_actions(query, day, row_num)
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

async def test_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки отправки логов"""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет прав для этой команды")
        return
    
    test_messages = [
        "🧪 ТЕСТ: Тестовое сообщение 1",
        "🧪 ТЕСТ: Тестовое сообщение 2", 
        "🧪 ТЕСТ: Тестовое сообщение 3"
    ]
    
    for msg in test_messages:
        send_log_to_server(msg, "test", "info")
        log_user_action(user_id, username, "ТЕСТ ЛОГА", msg)
    
    await update.message.reply_text("✅ Тестовые логи отправлены. Проверьте сервер логов.")

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
        application.add_handler(CommandHandler("testlogs", test_logs))
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
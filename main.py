import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import gspread
from datetime import datetime, timezone, timedelta
import os
import logging
from datetime import datetime, timedelta

# Импортируем настройки из config.py
from config import BOT_TOKEN, SPREADSHEET_URL, ADMIN_ID, EMOJI_MAP, get_google_credentials

# Настройка логирования в файл
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/root/AtbTAI251_bot/bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# Отключаем логирование для httpx и httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Подключение к Google Sheets
def connect_google_sheets():
    try:
        creds_dict = get_google_credentials()
        if creds_dict:
            # Для продакшена - из переменных окружения
            gc = gspread.service_account_from_dict(creds_dict)
            logger.info("✅ Подключение к Google Sheets через переменные окружения")
        else:
            # Для локальной разработки - из файла
            gc = gspread.service_account(filename='credentials.json')
            logger.info("✅ Подключение к Google Sheets через файл credentials.json")
        return gc.open_by_url(SPREADSHEET_URL)
    except Exception as e:
        logger.error(f"❌ Ошибка подключения к Google Sheets: {e}")
        return None

# Кэширование данных
cache_data = {}
cache_timeout = timedelta(minutes=5)  # 5 минут кэш

def get_cached_sheet_data(sheet_name):
    """Получить данные листа с кэшированием"""
    global cache_data
    
    now = datetime.now()
    if sheet_name in cache_data:
        data, timestamp = cache_data[sheet_name]
        if now - timestamp < cache_timeout:
            logger.info(f"📦 Используем кэш для {sheet_name}")
            return data
    
    try:
        sheet = db.worksheet(sheet_name)
        data = sheet.get_all_values()
        cache_data[sheet_name] = (data, now)
        return data
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки данных для {sheet_name}: {e}")
        if sheet_name in cache_data:
            logger.warning(f"⚠️ Используем устаревший кэш для {sheet_name}")
            return cache_data[sheet_name][0]
        return []

# Глобальные переменные
db = None
user_data = {}
user_states = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    logger.info(f"🟢 Команда /start от пользователя {user_id} (@{username})")
    
    # Проверяем, зарегистрирован ли пользователь
    try:
        students_data_records = get_cached_sheet_data("Студенты")
        header = students_data_records[0]
        students_data = []
        for row in students_data_records[1:]:
            if len(row) >= len(header):
                student_dict = {header[i]: row[i] for i in range(len(header))}
                students_data.append(student_dict)

        # Ищем пользователя в базе по Telegram ID
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
            # Пользователь уже зарегистрирован - сразу показываем меню
            user_data[user_id] = student_data
            user_states[user_id] = "registered"
            
            logger.info(f"✅ Автоматический вход для пользователя {user_id}: {student_data['fio']}")
            
            # Показываем главное меню
            keyboard = []
            keyboard.append([InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")])
            
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"✅ С возвращением, {student_data['fio']}!\n"
                f"Подгруппа: {student_data['subgroup']}\n",
                reply_markup=reply_markup
            )
            return
        
        # Если пользователь не найден - просим ввести ФИО
        user_states[user_id] = "waiting_for_fio"
        await update.message.reply_text(
            "Добро пожаловать! Введите ваше ФИО (Фамилия Имя Отчество):"
        )
        logger.info(f"✅ Пользователю {user_id} отправлен запрос ФИО")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в start для пользователя {user_id}: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте позже.")

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    text = update.message.text
    logger.info(f"📨 Сообщение от {user_id} (@{username}): {text}")
    
    if user_states.get(user_id) == "waiting_for_fio":
        await handle_fio(update, context)
    else:
        logger.warning(f"⚠️ Пользователь {user_id} отправил сообщение без регистрации: {text}")
        await update.message.reply_text("Сначала отправьте /start для регистрации")

async def handle_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db is None:
        logger.error("❌ Попытка регистрации при отсутствующем подключении к БД")
        await update.message.reply_text("❌ Ошибка подключения к базе данных. Попробуйте позже.")
        return
        
    fio = update.message.text.strip()
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    logger.info(f"🔍 Поиск ФИО: '{fio}' для пользователя {user_id} (@{username})")
    
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()
        
        user_found = False
        student_number = None
        subgroup = None
        
        for student in students_data:
            if student['ФИО'].lower() == fio.lower():
                logger.info(f"✅ Найден студент: {student}")
                
                existing_id = str(student.get('Telegram ID', '')).strip()
                if existing_id and existing_id.isdigit() and int(existing_id) != user_id:
                    logger.warning(f"⚠️ Попытка повторной регистрации ФИО '{fio}' пользователем {user_id} (уже зарегистрирован на {existing_id})")
                    await update.message.reply_text("❌ Этот аккаунт уже зарегистрирован на другого пользователя!")
                    return
                else:
                    user_found = True
                    student_number = student['№']
                    subgroup = student['Подгруппа']
                    break
        
        if not user_found:
            logger.warning(f"❌ ФИО '{fio}' не найдено в базе для пользователя {user_id}")
            await update.message.reply_text("❌ ФИО не найдено в базе! Обратитесь к администратору.")
            return
        
        # Сохраняем Telegram ID
        cell = students_sheet.find(str(student_number))
        students_sheet.update_cell(cell.row, 4, str(user_id))
        logger.info(f"✅ Сохранен Telegram ID {user_id} для студента №{student_number} ({fio})")
        
        user_data[user_id] = {
            'fio': fio,
            'number': student_number,
            'subgroup': subgroup
        }
        
        user_states[user_id] = "registered"
        
        # СОЗДАЕМ КЛАВИАТУРУ С АДМИН-ПАНЕЛЬЮ ДЛЯ АДМИНА
        keyboard = []
        keyboard.append([InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")])
        
        # Если пользователь - админ, добавляем кнопку админ-панели
        if user_id == ADMIN_ID:
            keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"✅ Регистрация успешна!\n"
            f"ФИО: {fio}\n"
            f"Подгруппа: {subgroup}\n",
            reply_markup=reply_markup
        )
        logger.info(f"✅ Регистрация завершена для пользователя {user_id} (@{username}): {fio}, подгруппа {subgroup}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в handle_fio для пользователя {user_id}: {e}")
        await update.message.reply_text("❌ Произошла ошибка при регистрации. Попробуйте позже.")

# АДМИН-ФУНКЦИИ
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        logger.warning(f"⚠️ Попытка доступа к админ-панели пользователем {user_id} (@{username})")
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    logger.info(f"🛠️ Открытие админ-панели пользователем {user_id} (@{username})")
    
    keyboard = [
        [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
        [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],  # НОВАЯ КНОПКА
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ Админ-панель:", reply_markup=reply_markup)

async def admin_show_students(query):
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    
    logger.info(f"👥 Запрос списка студентов администратором {user_id} (@{username})")
    
    try:
        students_data_records = get_cached_sheet_data("Студенты")
        text = "👥 Список студентов:\n\n"
        for student in students_data_records:
            status = "✅ Зарегистрирован" if student.get('Telegram ID') else "❌ Не зарегистрирован"
            text += f"{student['№']}. {student['ФИО']} - {status}\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
        logger.info(f"✅ Список студентов отправлен администратору {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при получении списка студентов администратором {user_id}: {e}")
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистка кэша (только для админа)"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    
    global cache_data
    cache_data.clear()
    logger.info("🧹 Кэш очищен администратором")
    await update.message.reply_text("✅ Кэш очищен")

async def admin_server_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статус сервера (только для админа)"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа")
        return
    
    try:
        import psutil
        import subprocess
        
        # Безопасные метрики (не требуют прав)
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # Процессы бота
        bot_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                if 'python' in proc.info['name'] and 'main.py' in ' '.join(proc.cmdline()):
                    bot_processes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        
        bot_count = len(bot_processes)
        bot_memory = sum(proc.info['memory_info'].rss for proc in bot_processes) / 1024 / 1024  # MB
        
        # Формируем сообщение
        status_text = (
            "🖥️ **Статус сервера**\n\n"
            f"**CPU:** {cpu_percent}%\n"
            f"**Память:** {memory.percent}% ({memory.used//1024//1024}MБ / {memory.total//1024//1024}MБ)\n"
            f"**Диск:** {disk.percent}% ({disk.used//1024//1024//1024}GБ / {disk.total//1024//1024//1024}GБ)\n\n"
            f"**Процессы бота:** {bot_count}\n"
            f"**Память бота:** {bot_memory:.1f} MБ\n\n"
            f"**Время работы:** {get_uptime()}\n"
            f"**Лог файл:** {get_log_file_size()}"
        )
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
        logger.info(f"📊 Статус сервера запрошен администратором {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения статуса сервера: {e}")
        await update.message.reply_text("❌ Ошибка получения статуса сервера")

def get_uptime():
    """Время работы системы"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        return f"{days}д {hours}ч"
    except:
        return "Неизвестно"

def get_log_file_size():
    """Размер файла логов"""
    try:
        size = os.path.getsize('/root/AtbTAI251_bot/bot.log') / 1024 / 1024
        return f"{size:.1f} MБ"
    except:
        return "Неизвестно"

async def admin_server_status_from_query(query):
    """Статус сервера из callback"""
    user_id = query.from_user.id
    
    try:
        import psutil
        
        # Аналогично admin_server_status но для query
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        bot_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                if 'python' in proc.info['name'] and 'main.py' in ' '.join(proc.cmdline()):
                    bot_processes.append(proc)
            except:
                pass
        
        bot_count = len(bot_processes)
        
        status_text = (
            "🖥️ **Статус сервера**\n\n"
            f"**CPU:** {cpu_percent}%\n"
            f"**Память:** {memory.percent}%\n"
            f"**Процессы бота:** {bot_count}\n"
            f"**Время работы:** {get_uptime()}"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(status_text, parse_mode='Markdown')
        logger.info(f"📊 Статус сервера запрошен через панель администратором {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения статуса сервера: {e}")
        await query.edit_message_text("❌ Ошибка получения статуса сервера")

# ОСНОВНЫЕ ФУНКЦИИ БОТА
async def handle_mark_complete(query, user_id):
    username = query.from_user.username or "Без username"
    logger.info(f"🏁 Завершение отметки пользователем {user_id} (@{username})")
    # ИЗМЕНЕНИЕ: Просто возвращаем к дням недели без сообщения
    await show_days_with_status(query, user_id)

async def show_days_with_status(query, user_id, week_string=None):
    if user_id not in user_data:
        logger.warning(f"⚠️ Попытка отметки незарегистрированным пользователем {user_id}")
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    username = query.from_user.username or "Без username"
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    student_number = student_data['number']
    
    week_type = week_string if week_string else get_current_week_type()
    
    logger.info(f"📅 Показ дней недели для пользователя {user_id} (@{username}): {student_data['fio']}")
    
    try:
        schedule_data = get_cached_sheet_data(f"{subgroup} подгруппа")
        day_status = {}
        for row in schedule_data[1:]:
            if len(row) > 2 and row[0] == week_type:
                day = row[1]
                if day not in day_status:
                    day_status[day] = {'total': 0, 'marked': 0}
                
                day_status[day]['total'] += 1
                
                header = schedule_data[0]
                student_col = None
                for idx, cell in enumerate(header):
                    if str(cell).strip() == str(student_number):
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
        logger.info(f"✅ Дни недели с статусом отправлены пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в show_days_with_status для пользователя {user_id}: {e}")
        await show_days(query)

async def show_days(query):
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    logger.info(f"📅 Показ дней недели пользователю {user_id} (@{username})")
    
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    keyboard = []
    
    for day in days:
        keyboard.append([InlineKeyboardButton(day, callback_data=f"day_{day}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите день недели:", reply_markup=reply_markup)

async def show_subjects(query, day, user_id):
    if user_id not in user_data:
        logger.warning(f"⚠️ Попытка просмотра предметов незарегистрированным пользователем {user_id}")
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    username = query.from_user.username or "Без username"
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    student_number = student_data['number']
    week_type = get_current_week_type()
    
    logger.info(f"📚 Показ предметов для {day} пользователю {user_id} (@{username}): {student_data['fio']}")
    
    try:
        schedule_data = get_cached_sheet_data(f"{subgroup} подгруппа")
        subjects_with_status = []
        header = schedule_data[0]
        
        student_col = None
        for idx, cell in enumerate(header):
            if str(cell).strip() == str(student_number):
                student_col = idx
                break
        
        subject_count = 0
        # ИЗМЕНЕНИЕ: Собираем все строки для этого дня
        day_rows = []
        for row_num, row in enumerate(schedule_data[1:], start=2):
            if len(row) > 2 and row[0] == week_type and row[1] == day:
                day_rows.append((row_num, row))  # Сохраняем номер строки и данные
        
        # ИЗМЕНЕНИЕ: Обрабатываем каждую строку отдельно
        for row_num, row in day_rows:
            subject = row[2]
            # Упрощаем название для кнопки
            subject_type = "Лекция" if "лекцион" in subject.lower() else "Практика" if "практическ" in subject.lower() else "Лабораторная" if "лабораторн" in subject.lower() else "Занятие"
            
            status = ""
            if student_col and len(row) > student_col:
                mark = row[student_col].strip()
                if mark == '✅':
                    status = ' ✅'
                elif mark == '❌':
                    status = ' ❌'
                elif mark == '⚠️':
                    status = ' ⚠️'
            
            subject_count += 1
            button_text = f"{subject_type} {subject_count}{status}"
            # ИЗМЕНЕНИЕ: Сохраняем номер строки для уникальной идентификации
            subjects_with_status.append((subject, button_text, status, row_num))
        
        if not subjects_with_status:
            logger.info(f"ℹ️ На {day} ({week_type}) нет занятий для пользователя {user_id}")
            await query.edit_message_text(f"❌ На {day} ({week_type}) нет занятий")
            return
            
        keyboard = []
        for i, (full_subject, button_text, status, row_num) in enumerate(subjects_with_status):
            # ИЗМЕНЕНИЕ: Передаем номер строки вместо индекса
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"subject_{day}_{row_num}")])
        
        keyboard.append([InlineKeyboardButton("———", callback_data="separator")])
        keyboard.append([
            InlineKeyboardButton("✅ Прис. на всех", callback_data=f"all_{day}_present"),
            InlineKeyboardButton("❌ Отсут. на всех", callback_data=f"all_{day}_absent")
        ])
        keyboard.append([
            InlineKeyboardButton("⚠️ Отсут. на всех(У)", callback_data=f"all_{day}_excused")
        ])
        
        keyboard.append([
            InlineKeyboardButton("🔙 Назад", callback_data="mark_attendance"),
            InlineKeyboardButton("🏁 Завершить", callback_data="mark_complete")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Показываем полные названия в тексте сообщения
        full_subjects_text = "\n".join([f"• {full_subject}{status}" for full_subject, _, status, _ in subjects_with_status])
        
        await query.edit_message_text(
            f"📚 {day} - {week_type}:\n\n{full_subjects_text}\n\nВыберите предмет для отметки:",
            reply_markup=reply_markup
        )
        logger.info(f"✅ Предметы для {day} отправлены пользователю {user_id}")
    except Exception as e:
        logger.error(f"❌ Ошибка в show_subjects для пользователя {user_id}: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

async def show_subject_actions(query, day, row_num):
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    logger.info(f"🎯 Показ действий для предмета (день: {day}, строка: {row_num}) пользователю {user_id}")
    
    try:
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
    except Exception as e:
        logger.error(f"❌ Ошибка в show_subject_actions для пользователя {user_id}: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке действий")

async def show_week_selection(query, user_id):
    """Показ выбора недели"""
    if user_id not in user_data:
        logger.warning(f"⚠️ Попытка выбора недели незарегистрированным пользователем {user_id}")
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    subgroup = student_data['subgroup']
    
    logger.info(f"📅 Выбор недели пользователем {user_id}: {student_data['fio']}")
    
    try:
        # Получаем данные расписания
        schedule_data = get_cached_sheet_data(f"{subgroup} подгруппа")
        
        # Получаем информацию о неделях
        current_week = get_week_info(0)
        previous_week = get_week_info(-1)
        
        keyboard = []
        
        # Текущая неделя - всегда доступна
        if current_week:
            keyboard.append([
                InlineKeyboardButton(
                    f"📅 {current_week['string']}", 
                    callback_data=f"week_{current_week['string']}"
                )
            ])
        
        # Предыдущая неделя - проверяем наличие в расписании
        if previous_week:
            # Проверяем есть ли занятия на предыдущей неделе
            week_has_classes = any(
                len(row) > 2 and row[0] == previous_week['string'] 
                for row in schedule_data[1:]
            )
            
            if week_has_classes:
                keyboard.append([
                    InlineKeyboardButton(
                        f"↩️ {previous_week['string']}", 
                        callback_data=f"week_{previous_week['string']}"
                    )
                ])
            else:
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
        logger.info(f"✅ Выбор недели показан пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в show_week_selection для пользователя {user_id}: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

async def mark_attendance(query, day, row_num, action, user_id):
    if user_id not in user_data:
        logger.warning(f"⚠️ Попытка отметки незарегистрированным пользователем {user_id}")
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    student_number = student_data['number']
    username = query.from_user.username or "Без username"
    
    logger.info(f"🎯 Отметка пользователем {user_id} (@{username}): {student_data['fio']}, день: {day}, действие: {action}, строка: {row_num}")
    
    subgroup = student_data['subgroup']
    week_type = get_current_week_type()
    
    try:
        emoji_map = {
            'present': '✅',
            'absent': '❌',
            'excused': '⚠️'
        }
        mark = emoji_map.get(action, '❓')
        
        schedule_sheet = db.worksheet(f"{subgroup} подгруппа")
        
        # ИЗМЕНЕНИЕ: Получаем заголовки для определения столбца студента
        header = schedule_sheet.row_values(1)
        
        student_col = None
        for idx, cell in enumerate(header):
            if str(cell).strip() == str(student_number):
                student_col = idx + 1  # gspread использует 1-индексацию
                break
        
        if student_col is None:
            logger.error(f"❌ Не найден столбец для студента {student_number} (пользователь {user_id})")
            await query.edit_message_text("❌ Ошибка: студент не найден в таблице посещаемости")
            return
        
        if row_num == "all":
            # Отметка на всех предметах дня
            schedule_data = schedule_sheet.get_all_values()
            updated_count = 0
            
            for i, row in enumerate(schedule_data[1:], start=2):
                if len(row) > 2 and row[0] == week_type and row[1] == day:
                    if student_col <= len(row):
                        schedule_sheet.update_cell(i, student_col, mark)
                        updated_count += 1
            
            if updated_count > 0:
                logger.info(f"✅ Массовая отметка {mark} для дня {day} пользователем {user_id}: обновлено {updated_count} записей")
                await show_subjects(query, day, user_id)
            else:
                logger.warning(f"⚠️ Не удалось обновить отметки для дня {day} пользователем {user_id}")
                await query.edit_message_text("❌ Не удалось сохранить отметку")
        else:
            # Отметка на конкретном предмете
            row_num_int = int(row_num)
            schedule_sheet.update_cell(row_num_int, student_col, mark)
            
            logger.info(f"✅ Отметка {mark} для строки {row_num_int}, столбец {student_col} пользователем {user_id}")
            
            # ИЗМЕНЕНИЕ: После отметки сразу возвращаем к списку предметов дня
            await show_subjects(query, day, user_id)
            
    except Exception as e:
        logger.error(f"❌ Ошибка в mark_attendance для пользователя {user_id}: {e}")
        await query.edit_message_text("❌ Ошибка при сохранении отметки")

# Функция для остановки бота
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    if user_id != ADMIN_ID:
        logger.warning(f"⚠️ Попытка остановки бота пользователем {user_id} (@{username})")
        await update.message.reply_text("❌ У вас нет прав для этой команды")
        return
        
    logger.critical(f"🛑 Выключение бота по команде администратора {user_id} (@{username})")
    await update.message.reply_text("🛑 Бот выключается...")
    
    # Останавливаем приложение
    import os
    os._exit(0)

# УТИЛИТЫ
def get_current_week_type():
    try:
        # Московский часовой пояс (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        now = datetime.now(moscow_tz)
        
        semester_start = datetime(2025, 9, 1, tzinfo=moscow_tz)
        days_diff = (now - semester_start).days
        week_number = (days_diff // 7) + 1
        
        # Определяем тип недели (четная/нечетная)
        week_type = "Знаменатель" if week_number % 2 == 0 else "Числитель"
        
        result = f"{week_type} - {week_number} неделя"
        logger.info(f"📅 Текущая неделя: {result} (дата: {now.strftime('%d.%m.%Y %H:%M')})")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка в определении недели: {e}")
        # Fallback на фиксированное значение в случае ошибки
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

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Без username"
    
    logger.info(f"📊 Запрос статуса пользователем {user_id} (@{username})")
    
    if user_id in user_data:
        student_data = user_data[user_id]
        await update.message.reply_text(
            f"📊 Ваш статус:\n"
            f"ФИО: {student_data['fio']}\n"
            f"Подгруппа: {student_data['subgroup']}\n"
            f"Номер в списке: {student_data['number']}\n\n"
            f"Для отметки посещаемости нажмите /start"
        )
    else:
        await update.message.reply_text("❌ Сначала зарегистрируйтесь через /start")

# ГЛАВНЫЙ ОБРАБОТЧИК КНОПОК
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username or "Без username"
    data = query.data
    
    logger.info(f"🔄 Обработка callback: {data} от пользователя {user_id} (@{username})")
    
    try:
        if data == "mark_attendance":
            await show_week_selection(query, user_id)
        elif data.startswith("week_"):
            if data == "week_none":
                await query.answer("Эта неделя недоступна для отметки", show_alert=True)
                return
            week_string = data[5:]  # Извлекаем название недели
            await show_days_with_status(query, user_id, week_string)
        elif data == "admin_status":
            if user_id == ADMIN_ID:
                await admin_server_status_from_query(query)
            else:
                await query.edit_message_text("❌ У вас нет доступа")
        elif data == "mark_complete":
            await handle_mark_complete(query, user_id)
        elif data == "back_to_main":
            # Возврат в главное меню
            keyboard = []
            keyboard.append([InlineKeyboardButton("📝 Отметиться", callback_data="mark_attendance")])
            if user_id == ADMIN_ID:
                keyboard.append([InlineKeyboardButton("🛠️ Админ-панель", callback_data="admin_panel")])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text("Главное меню:", reply_markup=reply_markup)
            logger.info(f"🏠 Возврат в главное меню пользователем {user_id}")
        elif data == "admin_panel":
            if user_id == ADMIN_ID:
                keyboard = [
                    [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
                    [InlineKeyboardButton("🖥️ Статус сервера", callback_data="admin_status")],  # ✅ ДОБАВЛЕНО
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text("🛠️ Админ-панель:", reply_markup=reply_markup)
            else:
                await query.edit_message_text("❌ У вас нет доступа к админ-панели")
        elif data == "admin_students":
            await admin_show_students(query)
        elif data.startswith("day_"):
            day = data.split("_")[1]
            logger.info(f"📅 Пользователь {user_id} выбрал день: {day}")
            await show_subjects(query, day, user_id)
        elif data.startswith("subject_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            logger.info(f"📚 Пользователь {user_id} выбрал предмет в день {day}, строка {row_num}")
            await show_subject_actions(query, day, row_num)
        elif data.startswith("action_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            action = parts[3]
            logger.info(f"✅ Пользователь {user_id} отметил {action} на день {day}, строка {row_num}")
            await mark_attendance(query, day, row_num, action, user_id)
        elif data.startswith("all_"):
            parts = data.split("_")
            day = parts[1]
            action = parts[2]
            logger.info(f"✅ Пользователь {user_id} отметил {action} на ВСЕ предметы дня {day}")
            await mark_attendance(query, day, "all", action, user_id)
        else:
            logger.warning(f"❌ Неизвестный callback от пользователя {user_id}: {data}")
            await query.edit_message_text("❌ Неизвестная команда")
    except Exception as e:
        logger.error(f"❌ Ошибка в button_handler для пользователя {user_id}: {e}")
        await query.edit_message_text("❌ Произошла ошибка при обработке запроса")

def test_connection():
    try:
        gc = gspread.service_account(filename='credentials.json')
        spreadsheet = gc.open_by_url(SPREADSHEET_URL)
        
        students_sheet = spreadsheet.worksheet("Студенты")
        data = students_sheet.get_all_records()
        logger.info("✅ Тестовое подключение успешно!")
        logger.info("📊 Данные студентов:")
        for student in data:
            logger.info(f"  {student}")
            
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка тестового подключения: {e}")
        return False

def main():
    global db
    logger.info("🚀 Запуск бота...")
    db = connect_google_sheets()

    if db is None:
        logger.critical("❌ Не удалось подключиться к Google Sheets")
        return

    # Если подключение успешно, запускаем бота
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(CommandHandler("clearcache", clear_cache))

    logger.info("🤖 Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()
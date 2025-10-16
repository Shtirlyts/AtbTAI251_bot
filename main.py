import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import gspread
from datetime import datetime, timezone, timedelta
import os

# Импортируем настройки из config.py
from config import BOT_TOKEN, SPREADSHEET_URL, ADMIN_ID, EMOJI_MAP, get_google_credentials

# Подключение к Google Sheets
def connect_google_sheets():
    try:
        creds_dict = get_google_credentials()
        if creds_dict:
            # Для продакшена - из переменных окружения
            gc = gspread.service_account_from_dict(creds_dict)
            print("✅ Подключение к Google Sheets через переменные окружения")
        else:
            # Для локальной разработки - из файла
            gc = gspread.service_account(filename='credentials.json')
            print("✅ Подключение к Google Sheets через файл credentials.json")
        return gc.open_by_url(SPREADSHEET_URL)
    except Exception as e:
        print(f"❌ Ошибка подключения к Google Sheets: {e}")
        return None

# Глобальные переменные
db = None
user_data = {}
user_states = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    print(f"🟢 Получен /start от пользователя {user_id}")
    
    user_states[user_id] = "waiting_for_fio"
    
    await update.message.reply_text(
        "Добро пожаловать! Введите ваше ФИО (Фамилия Имя Отчество):"
    )
    print("✅ Сообщение отправлено пользователю")

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    print(f"📨 Сообщение от {user_id}: {text}")
    
    if user_states.get(user_id) == "waiting_for_fio":
        await handle_fio(update, context)
    else:
        await update.message.reply_text("Сначала отправьте /start для регистрации")

async def handle_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db is None:
        await update.message.reply_text("❌ Ошибка подключения к базе данных. Попробуйте позже.")
        return
        
    fio = update.message.text.strip()
    user_id = update.effective_user.id
    
    print(f"🔍 Поиск ФИО: {fio} для пользователя {user_id}")
    
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()
        
        user_found = False
        student_number = None
        subgroup = None
        
        for student in students_data:
            if student['ФИО'].lower() == fio.lower():
                print(f"✅ Найден студент: {student}")
                
                existing_id = str(student.get('Telegram ID', '')).strip()
                if existing_id and existing_id.isdigit() and int(existing_id) != user_id:
                    await update.message.reply_text("❌ Этот аккаунт уже зарегистрирован на другого пользователя!")
                    return
                else:
                    user_found = True
                    student_number = student['№']
                    subgroup = student['Подгруппа']
                    break
        
        if not user_found:
            await update.message.reply_text("❌ ФИО не найдено в базе! Обратитесь к администратору.")
            return
        
        # Сохраняем Telegram ID
        cell = students_sheet.find(str(student_number))
        students_sheet.update_cell(cell.row, 4, str(user_id))
        print(f"✅ Сохранен Telegram ID {user_id} для студента №{student_number}")
        
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
        print("✅ Регистрация завершена, кнопки отправлены")
        
    except Exception as e:
        print(f"❌ Ошибка в handle_fio: {e}")
        await update.message.reply_text("❌ Произошла ошибка при регистрации. Попробуйте позже.")

# АДМИН-ФУНКЦИИ
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ У вас нет доступа к админ-панели")
        return
    
    keyboard = [
        [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
        [InlineKeyboardButton("❌ Сбросить регистрацию", callback_data="admin_reset_confirm")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛠️ Админ-панель:", reply_markup=reply_markup)

async def admin_show_students(query):
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()
        
        text = "👥 Список студентов:\n\n"
        for student in students_data:
            status = "✅ Зарегистрирован" if student.get('Telegram ID') else "❌ Не зарегистрирован"
            text += f"{student['№']}. {student['ФИО']} - {status}\n"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def admin_reset_registration(query):
    try:
        students_sheet = db.worksheet("Студенты")
        students_sheet.batch_clear(["D2:D100"])
        
        user_data.clear()
        user_states.clear()
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text("✅ Регистрации всех студентов сброшены!", reply_markup=reply_markup)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def admin_show_stats(query):
    try:
        students_sheet = db.worksheet("Студенты")
        students_data = students_sheet.get_all_records()
        
        registered = sum(1 for s in students_data if s.get('Telegram ID'))
        total = len(students_data)
        
        text = f"📊 Статистика:\n\n"
        text += f"Всего студентов: {total}\n"
        text += f"Зарегистрировано: {registered}\n"
        text += f"Не зарегистрировано: {total - registered}\n"
        text += f"Процент регистрации: {registered/total*100:.1f}%"
        
        keyboard = [
            [InlineKeyboardButton("🔙 Назад в админ-панель", callback_data="admin_panel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup)
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")

async def admin_confirm_reset(query):
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, сбросить", callback_data="admin_reset_yes"),
            InlineKeyboardButton("❌ Нет, отмена", callback_data="admin_panel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "⚠️ Вы уверены, что хотите сбросить ВСЕ регистрации студентов?\n\n"
        "Это действие нельзя отменить!",
        reply_markup=reply_markup
    )

# ОСНОВНЫЕ ФУНКЦИИ БОТА
async def handle_mark_complete(query, user_id):
    # ИЗМЕНЕНИЕ: Просто возвращаем к дням недели без сообщения
    await show_days_with_status(query, user_id)

async def show_days_with_status(query, user_id):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    subgroup = user_data[user_id]['subgroup']
    student_number = user_data[user_id]['number']
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
        
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📅 Выберите день недели ({week_type}):\n\n"
            "✅ - все пары отмечены\n"
            "🟡 - часть пар отмечена\n"
            "❌ - пары не отмечены",
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"❌ Ошибка в show_days_with_status: {e}")
        await show_days(query)

async def show_days(query):
    days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница"]
    keyboard = []
    
    for day in days:
        keyboard.append([InlineKeyboardButton(day, callback_data=f"day_{day}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Выберите день недели:", reply_markup=reply_markup)

async def show_subjects(query, day, user_id):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    subgroup = user_data[user_id]['subgroup']
    student_number = user_data[user_id]['number']
    week_type = get_current_week_type()
    
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
    except Exception as e:
        print(f"❌ Ошибка в show_subjects: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке расписания")

async def show_subject_actions(query, day, row_num):
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
        print(f"❌ Ошибка в show_subject_actions: {e}")
        await query.edit_message_text("❌ Ошибка при загрузке действий")

async def mark_attendance(query, day, row_num, action, user_id):
    if user_id not in user_data:
        await query.edit_message_text("❌ Сначала зарегистрируйтесь через /start")
        return
        
    student_data = user_data[user_id]
    student_number = student_data['number']
    
    print(f"🎯 Пользователь {user_data[user_id]['fio']} отмечает: {day}, действие: {action}")
    
    subgroup = student_data['subgroup']
    week_type = get_current_week_type()
    
    print(f"🎯 Отмечаем: {day}, строка: {row_num}, действие: {action}")
    
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
            print(f"❌ Не найден столбец для студента {student_number}")
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
                        print(f"✅ Отметка {mark} для всех предметов, строка {i}")
            
            if updated_count > 0:
                await show_subjects(query, day, user_id)
            else:
                await query.edit_message_text("❌ Не удалось сохранить отметку")
        else:
            # Отметка на конкретном предмете
            row_num_int = int(row_num)
            schedule_sheet.update_cell(row_num_int, student_col, mark)
            print(f"✅ Отметка {mark} для строки {row_num_int}, столбец {student_col}")
            
            # ИЗМЕНЕНИЕ: После отметки сразу возвращаем к списку предметов дня
            await show_subjects(query, day, user_id)
            
    except Exception as e:
        print(f"❌ Ошибка в mark_attendance: {e}")
        await query.edit_message_text("❌ Ошибка при сохранении отметки")

# УТИЛИТЫ
def get_current_week_type():
    return "Знаменатель - 8 неделя"

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
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
    data = query.data
    
    print(f"🔄 Обработка callback: {data} от пользователя {user_id}")
    
    try:
        if data == "mark_attendance":
            await show_days_with_status(query, user_id)
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
        elif data == "admin_panel":
            if user_id == ADMIN_ID:
                keyboard = [
                    [InlineKeyboardButton("👥 Список студентов", callback_data="admin_students")],
                    [InlineKeyboardButton("❌ Сбросить регистрацию", callback_data="admin_reset_confirm")],  # Изменено
                    [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
                    [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text("🛠️ Админ-панель:", reply_markup=reply_markup)
            else:
                await query.edit_message_text("❌ У вас нет доступа к админ-панели")
        elif data == "admin_students":
            await admin_show_students(query)
        elif data == "admin_reset_confirm": 
            await admin_confirm_reset(query)
        elif data == "admin_reset_yes":
            await admin_reset_registration(query)
        elif data == "admin_stats":
            await admin_show_stats(query)
        elif data.startswith("day_"):
            day = data.split("_")[1]
            print(f"📅 Пользователь {user_id} выбрал день: {day}")
            await show_subjects(query, day, user_id)
        elif data.startswith("subject_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            print(f"📚 Пользователь {user_id} выбрал предмет в день {day}, строка {row_num}")
            await show_subject_actions(query, day, row_num)
        elif data.startswith("action_"):
            parts = data.split("_")
            day = parts[1]
            row_num = parts[2]
            action = parts[3]
            print(f"✅ Пользователь {user_id} отметил {action} на день {day}, строка {row_num}")
            await mark_attendance(query, day, row_num, action, user_id)
        elif data.startswith("all_"):
            parts = data.split("_")
            day = parts[1]
            action = parts[2]
            print(f"✅ Пользователь {user_id} отметил {action} на ВСЕ предметы дня {day}")
            await mark_attendance(query, day, "all", action, user_id)
        else:
            print(f"❌ Неизвестный callback: {data}")
            await query.edit_message_text("❌ Неизвестная команда")
    except Exception as e:
        print(f"❌ Ошибка в button_handler: {e}")
        await query.edit_message_text("❌ Произошла ошибка при обработке запроса")

def test_connection():
    try:
        gc = gspread.service_account(filename='credentials.json')
        spreadsheet = gc.open_by_url(SPREADSHEET_URL)
        
        students_sheet = spreadsheet.worksheet("Студенты")
        data = students_sheet.get_all_records()
        print("✅ Подключение успешно!")
        print("📊 Данные студентов:")
        for student in data:
            print(f"  {student}")
            
        return True
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def main():
    global db
    db = connect_google_sheets()

    if db is None:
        print("❌ Не удалось подключиться к Google Sheets")
        return

    # Если подключение успешно, запускаем бота
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    application.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Бот запускается...")
    application.run_polling()

if __name__ == "__main__":
    main()
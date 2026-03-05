import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramNotFound, TelegramForbiddenError, TelegramRetryAfter
from dotenv import load_dotenv
import pytz

from database import Database
from scheduler import ReminderScheduler

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk")  # По умолчанию Минск

# Проверка наличия токена
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден в файле .env!")
    print("Создайте файл .env со строкой: BOT_TOKEN=ваш_токен_здесь")
    sys.exit(1)

# Настройка подробного логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_debug.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(timezone=TIMEZONE)  # Передаём часовой пояс в базу данных

# Глобальная очередь для сообщений (будет создана в main)
message_queue = None

# Часовой пояс для использования в функциях
tz = pytz.timezone(TIMEZONE)

# Состояния для FSM
class TaskStates(StatesGroup):
    waiting_for_task_text = State()
    waiting_for_deadline = State()
    waiting_for_task_id = State()

# Клавиатуры
def get_main_keyboard():
    """Основная клавиатура"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="➕ Новая задача")],
            [KeyboardButton(text="✅ Выполненные"), KeyboardButton(text="❌ Удалить задачу")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard

def get_tasks_keyboard(tasks: list, action: str = "complete"):
    """Клавиатура со списком задач"""
    buttons = []
    for task in tasks:
        if task['status'] == 'active' or action == "delete":
            status_emoji = "✅" if action == "complete" else "❌"
            # Обрезаем длинный текст
            task_text = task['task_text'][:30] + "..." if len(task['task_text']) > 30 else task['task_text']
            button_text = f"{status_emoji} {task_text}"
            callback_data = f"{action}:{task['id']}"
            buttons.append([InlineKeyboardButton(text=button_text, callback_data=callback_data)])
    
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_calendar_keyboard():
    """Клавиатура для быстрого выбора даты"""
    now = datetime.now(tz)
    dates = []
    
    # Сегодня
    today = now.strftime("%d.%m.%Y")
    dates.append([InlineKeyboardButton(text=f"📅 Сегодня ({today})", callback_data="date:today")])
    
    # Завтра
    tomorrow = (now + timedelta(days=1)).strftime("%d.%m.%Y")
    dates.append([InlineKeyboardButton(text=f"📅 Завтра ({tomorrow})", callback_data="date:tomorrow")])
    
    # Послезавтра
    after_tomorrow = (now + timedelta(days=2)).strftime("%d.%m.%Y")
    dates.append([InlineKeyboardButton(text=f"📅 Послезавтра ({after_tomorrow})", callback_data="date:after_tomorrow")])
    
    # Через неделю
    next_week = (now + timedelta(days=7)).strftime("%d.%m.%Y")
    dates.append([InlineKeyboardButton(text=f"📅 Через неделю ({next_week})", callback_data="date:next_week")])
    
    # Свой вариант
    dates.append([InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="date:custom")])
    dates.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back")])
    
    return InlineKeyboardMarkup(inline_keyboard=dates)

# Воркер для очереди сообщений
async def message_sender_worker(queue):
    """Воркер для отправки сообщений из очереди"""
    logger.info("="*60)
    logger.info("🚀 ЗАПУСК ВОРКЕРА ОЧЕРЕДИ")
    logger.info("="*60)
    
    processed = 0
    errors = 0
    
    while True:
        try:
            # Ждем сообщение из очереди
            logger.info(f"⏳ Воркер ожидает сообщение... (обработано: {processed}, ошибок: {errors})")
            
            try:
                chat_id, text = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            
            processed += 1
            logger.info("="*50)
            logger.info(f"📨 ВОРКЕР: ПОЛУЧЕНО СООБЩЕНИЕ #{processed}")
            logger.info(f"   Кому: {chat_id}")
            logger.info(f"   Текст: {text[:100]}...")
            
            # Пытаемся отправить
            try:
                logger.info(f"   ➡️ Отправка...")
                await bot.send_message(chat_id=chat_id, text=text)
                logger.info(f"   ✅ УСПЕШНО ОТПРАВЛЕНО!")
                
            except Exception as e:
                errors += 1
                logger.error(f"   ❌ ОШИБКА ОТПРАВКИ: {e}")
                logger.error(traceback.format_exc())
                
                # Возвращаем в очередь для повторной попытки (до 3 раз)
                if not hasattr(message_sender_worker, 'retry_count'):
                    message_sender_worker.retry_count = {}
                
                key = f"{chat_id}_{text[:50]}"
                if key not in message_sender_worker.retry_count:
                    message_sender_worker.retry_count[key] = 0
                
                if message_sender_worker.retry_count[key] < 3:
                    message_sender_worker.retry_count[key] += 1
                    logger.info(f"   ↩️ Повторная попытка #{message_sender_worker.retry_count[key]} через 5 сек")
                    await asyncio.sleep(5)
                    await queue.put((chat_id, text))
                else:
                    logger.error(f"   ❌ Сообщение не доставлено после 3 попыток")
            
            finally:
                queue.task_done()
                logger.info(f"   ✅ Задача отмечена как выполненная")
                logger.info("="*50)
                
        except asyncio.CancelledError:
            logger.info("🛑 Воркер остановлен")
            break
        except Exception as e:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА ВОРКЕРА: {e}")
            logger.error(traceback.format_exc())
            await asyncio.sleep(1)

# Обработчики команд
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    user = message.from_user
    logger.info(f"Пользователь {user.id} (@{user.username}) запустил бота")
    
    try:
        db.add_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name
        )
        
        welcome_text = (
            f"👋 **Привет, {user.first_name}!**\n\n"
            f"Я бот для планирования задач. Я помогу тебе:\n"
            f"• 📝 Создавать задачи с дедлайнами\n"
            f"• ⏰ Напоминать о важных делах\n"
            f"• ✅ Отслеживать выполнение\n\n"
            f"Используй кнопки ниже или команды:\n"
            f"`/tasks` - все задачи\n"
            f"`/add` - новая задача\n"
            f"`/help` - помощь\n\n"
            f"📍 Часовой пояс: {TIMEZONE}"
        )
        
        await message.answer(welcome_text, reply_markup=get_main_keyboard())
        logger.info(f"✅ Приветствие отправлено пользователю {user.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в /start для пользователя {user.id}: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")

@dp.message(F.text == "➕ Новая задача")
@dp.message(Command("add"))
async def cmd_add(message: types.Message, state: FSMContext):
    """Добавление новой задачи"""
    logger.info(f"Пользователь {message.from_user.id} начал создание задачи")
    await state.set_state(TaskStates.waiting_for_task_text)
    await message.answer(
        "📝 Введите текст задачи:",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(TaskStates.waiting_for_task_text)
async def process_task_text(message: types.Message, state: FSMContext):
    """Обработка текста задачи"""
    task_text = message.text.strip()
    
    if len(task_text) > 200:
        await message.answer("❌ Текст задачи слишком длинный (максимум 200 символов). Попробуйте снова:")
        return
    
    if len(task_text) < 3:
        await message.answer("❌ Текст задачи слишком короткий. Попробуйте снова:")
        return
    
    await state.update_data(task_text=task_text)
    await state.set_state(TaskStates.waiting_for_deadline)
    
    await message.answer(
        "📅 Выберите дату дедлайна:",
        reply_markup=get_calendar_keyboard()
    )
    logger.info(f"Пользователь {message.from_user.id} ввел текст задачи: {task_text[:30]}...")

@dp.callback_query(TaskStates.waiting_for_deadline, F.data.startswith("date:"))
async def process_deadline_date(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора даты дедлайна из календаря"""
    await callback.message.delete()
    
    date_choice = callback.data.split(":")[1]
    now = datetime.now(tz)
    
    if date_choice == "today":
        selected_date = now.strftime("%Y-%m-%d")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"🕐 Теперь введите время дедлайна (в формате ЧЧ:ММ, например 18:00):"
        )
    elif date_choice == "tomorrow":
        selected_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"🕐 Теперь введите время дедлайна (в формате ЧЧ:ММ, например 18:00):"
        )
    elif date_choice == "after_tomorrow":
        selected_date = (now + timedelta(days=2)).strftime("%Y-%m-%d")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"🕐 Теперь введите время дедлайна (в формате ЧЧ:ММ, например 18:00):"
        )
    elif date_choice == "next_week":
        selected_date = (now + timedelta(days=7)).strftime("%Y-%m-%d")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"🕐 Теперь введите время дедлайна (в формате ЧЧ:ММ, например 18:00):"
        )
    elif date_choice == "custom":
        await callback.message.answer(
            "Введите дату в формате ДД.ММ.ГГГГ (например, 25.12.2024):"
        )
    
    await callback.answer()
    logger.info(f"Пользователь {callback.from_user.id} выбрал дату: {date_choice}")

@dp.message(TaskStates.waiting_for_deadline)
async def process_deadline_input(message: types.Message, state: FSMContext):
    """Обработка ввода даты вручную или времени"""
    
    # Получаем текущие данные состояния
    data = await state.get_data()
    
    # СЛУЧАЙ 1: Даты еще нет - значит пользователь вводит дату вручную
    if 'selected_date' not in data:
        try:
            # Парсинг даты из сообщения
            date_str = message.text.strip()
            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
            selected_date = date_obj.strftime("%Y-%m-%d")
            
            # Сохраняем дату и просим ввести время
            await state.update_data(selected_date=selected_date)
            await message.answer(
                f"🕐 Теперь введите время дедлайна (в формате ЧЧ:ММ, например 18:00):"
            )
            logger.info(f"Пользователь {message.from_user.id} ввел дату вручную: {date_str}")
            
        except ValueError:
            await message.answer(
                "❌ Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например, 25.12.2024):"
            )
    
    # СЛУЧАЙ 2: Дата уже есть - значит пользователь вводит время
    else:
        try:
            # Парсинг времени
            time_str = message.text.strip()
            # Проверяем формат времени
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            
            # Получаем данные из состояния
            task_text = data.get('task_text')
            selected_date = data.get('selected_date')
            
            # Создаем datetime объект в локальном часовом поясе
            date_part = datetime.strptime(selected_date, "%Y-%m-%d").date()
            deadline_local = datetime.combine(date_part, time_obj)
            
            # Добавляем часовой пояс
            deadline_local = tz.localize(deadline_local)
            
            # Проверяем, что дедлайн в будущем
            now = datetime.now(tz)
            if deadline_local <= now:
                await message.answer("❌ Дедлайн должен быть в будущем! Попробуйте снова:")
                return
            
            # Сохранение задачи
            task_id = db.add_task(
                user_id=message.from_user.id,
                task_text=task_text,
                deadline_input=deadline_local
            )
            
            # Очищаем состояние
            await state.clear()
            
            # Форматирование для красивого вывода
            deadline_formatted = deadline_local.strftime("%d.%m.%Y %H:%M")
            
            await message.answer(
                f"✅ **Задача успешно создана!**\n\n"
                f"📌 **Задача:** {task_text}\n"
                f"📅 **Дедлайн:** {deadline_formatted} (минское время)\n"
                f"🆔 **ID:** {task_id}\n\n"
                f"Я напомню о задаче за 3 дня, 24 часа, 1 час и 5 минут до дедлайна.",
                reply_markup=get_main_keyboard()
            )
            
            logger.info(f"✅ Пользователь {message.from_user.id} создал задачу {task_id} с дедлайном {deadline_formatted}")
            
        except ValueError:
            await message.answer(
                "❌ Неверный формат времени. Введите время в формате ЧЧ:ММ (например 18:00):"
            )

@dp.message(F.text == "📋 Мои задачи")
@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    """Просмотр активных задач"""
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} запросил список задач")
    
    tasks = db.get_user_tasks(user_id, status='active')
    
    if not tasks:
        await message.answer(
            "📭 **У вас нет активных задач!**\n\n"
            "Нажмите «➕ Новая задача» чтобы создать первую задачу.",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = "📋 **Активные задачи:**\n\n"
    for task in tasks:
        # Используем отформатированную дату из базы данных
        deadline_display = task.get('deadline_display', 'Неизвестно')
        
        # Добавляем информацию о статусе напоминаний
        reminders = []
        if task.get('reminder_3d'):
            reminders.append("📅 3д")
        if task.get('reminder_24h'):
            reminders.append("⏰ 24ч")
        if task.get('reminder_1h'):
            reminders.append("⏱ 1ч")
        if task.get('reminder_5m'):
            reminders.append("⏲ 5м")
        
        reminder_status = f" [🔔 {', '.join(reminders)}]" if reminders else " [⏳ ожидает]"
        
        response += f"🔹 **{task['task_text']}**\n"
        response += f"   🆔 ID: `{task['id']}` | 📅 {deadline_display}{reminder_status}\n\n"
    
    # Кнопка для отметки выполнения
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отметить выполненной", callback_data="show_complete")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
        ]
    )
    
    await message.answer(response, reply_markup=keyboard)

@dp.message(F.text == "✅ Выполненные")
async def cmd_completed(message: types.Message):
    """Просмотр выполненных задач"""
    user_id = message.from_user.id
    tasks = db.get_user_tasks(user_id, status='completed')
    
    if not tasks:
        await message.answer(
            "📭 У вас нет выполненных задач.",
            reply_markup=get_main_keyboard()
        )
        return
    
    response = "✅ **Выполненные задачи:**\n\n"
    for task in tasks:
        deadline_display = task.get('deadline_display', 'Неизвестно')
        response += f"✓ {task['task_text']}\n"
        response += f"   🆔 ID: `{task['id']}` | 📅 {deadline_display}\n\n"
    
    # Кнопка для удаления
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить задачу", callback_data="show_delete")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back")]
        ]
    )
    
    await message.answer(response, reply_markup=keyboard)

@dp.message(F.text == "❌ Удалить задачу")
async def cmd_delete_prompt(message: types.Message):
    """Запрос на удаление задачи"""
    user_id = message.from_user.id
    tasks = db.get_user_tasks(user_id)
    
    if not tasks:
        await message.answer("📭 У вас нет задач для удаления.", reply_markup=get_main_keyboard())
        return
    
    await message.answer(
        "Выберите задачу для удаления:",
        reply_markup=get_tasks_keyboard(tasks, action="delete")
    )

@dp.callback_query(F.data.startswith("complete:"))
async def process_complete(callback: types.CallbackQuery):
    """Отметка задачи как выполненной"""
    task_id = int(callback.data.split(":")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("❌ Задача не найдена!", show_alert=True)
        return
    
    db.complete_task(task_id)
    
    await callback.message.delete()
    await callback.message.answer(
        f"✅ Задача **\"{task['task_text']}\"** отмечена как выполненная!",
        reply_markup=get_main_keyboard()
    )
    await callback.answer("✅ Задача выполнена!")
    logger.info(f"Пользователь {callback.from_user.id} выполнил задачу {task_id}")

@dp.callback_query(F.data.startswith("delete:"))
async def process_delete(callback: types.CallbackQuery):
    """Удаление задачи"""
    task_id = int(callback.data.split(":")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("❌ Задача не найдена!", show_alert=True)
        return
    
    db.delete_task(task_id)
    
    await callback.message.delete()
    await callback.message.answer(
        f"🗑 Задача **\"{task['task_text']}\"** удалена!",
        reply_markup=get_main_keyboard()
    )
    await callback.answer("🗑 Задача удалена")
    logger.info(f"Пользователь {callback.from_user.id} удалил задачу {task_id}")

@dp.callback_query(F.data == "show_complete")
async def show_complete_tasks(callback: types.CallbackQuery):
    """Показать задачи для отметки выполнения"""
    tasks = db.get_user_tasks(callback.from_user.id, status='active')
    
    if not tasks:
        await callback.message.edit_text(
            "📭 Нет активных задач для отметки.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back")]])
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "Выберите задачу для отметки:",
        reply_markup=get_tasks_keyboard(tasks, action="complete")
    )
    await callback.answer()

@dp.callback_query(F.data == "show_delete")
async def show_delete_tasks(callback: types.CallbackQuery):
    """Показать задачи для удаления"""
    tasks = db.get_user_tasks(callback.from_user.id)
    
    if not tasks:
        await callback.message.edit_text(
            "📭 Нет задач для удаления.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back")]])
        )
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "Выберите задачу для удаления:",
        reply_markup=get_tasks_keyboard(tasks, action="delete")
    )
    await callback.answer()

@dp.callback_query(F.data == "back")
async def process_back(callback: types.CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(
        "Главное меню:",
        reply_markup=get_main_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "ℹ️ Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по командам"""
    help_text = (
        "📚 **Справка по командам:**\n\n"
        "**Основные команды:**\n"
        "• `/start` - запуск бота\n"
        "• `/add` - добавить новую задачу\n"
        "• `/tasks` - показать активные задачи\n"
        "• `/help` - эта справка\n\n"
        "**Кнопки меню:**\n"
        "• `➕ Новая задача` - создать задачу\n"
        "• `📋 Мои задачи` - активные задачи\n"
        "• `✅ Выполненные` - завершенные задачи\n"
        "• `❌ Удалить задачу` - удалить задачу\n"
        "• `ℹ️ Помощь` - справка\n\n"
        "**Как это работает:**\n"
        "1. Нажмите «➕ Новая задача»\n"
        "2. Введите текст задачи\n"
        "3. Выберите дату дедлайна\n"
        "4. Введите время\n"
        "5. Бот напомнит о задаче за 3 дня, 24 часа, 1 час и 5 минут\n\n"
        "**Примеры ввода:**\n"
        "• `Купить продукты`\n"
        "• `Сдать отчет`\n"
        "• Дата: `25.12.2024`\n"
        "• Время: `18:00`\n\n"
        f"📍 **Часовой пояс:** {TIMEZONE}"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@dp.message()
async def handle_unknown(message: types.Message):
    """Обработка неизвестных сообщений"""
    await message.answer(
        "Я не понимаю эту команду. Используйте кнопки или /help для справки.",
        reply_markup=get_main_keyboard()
    )

# Обработчик ошибок
@dp.error()
async def error_handler(event: types.ErrorEvent):
    """Глобальный обработчик ошибок"""
    logger.error(f"❌ Произошла ошибка: {event.exception}")
    logger.error(traceback.format_exc())
    
    # Пытаемся уведомить пользователя
    try:
        if hasattr(event, 'message') and event.message:
            await event.message.answer(
                "❌ Произошла внутренняя ошибка. Администратор уже уведомлен."
            )
    except:
        pass

async def main():
    """Главная функция запуска"""
    logger.info("🚀 ===== ЗАПУСК БОТА =====")
    logger.info(f"Токен: {BOT_TOKEN[:10]}... (скрыт)")
    logger.info(f"Часовой пояс: {TIMEZONE}")
    
    global message_queue
    
    try:
        # Создаем очередь для сообщений
        message_queue = asyncio.Queue()
        logger.info("✅ Очередь сообщений создана")
        
        # Запускаем воркер как отдельную задачу
        asyncio.create_task(message_sender_worker(message_queue))
        logger.info("✅ Воркер очереди запущен")
        
        # Создаем и запускаем планировщик с очередью
        scheduler = ReminderScheduler(db, bot, TIMEZONE, message_queue)
        scheduler.start()
        
        # Проверяем подключение к Telegram
        me = await bot.get_me()
        logger.info(f"✅ Бот авторизован: @{me.username} (ID: {me.id})")
        
        # Запускаем бота
        logger.info("✅ Запуск polling...")
        await dp.start_polling(bot)
        
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
    finally:
        # Останавливаем планировщик
        if 'scheduler' in locals():
            scheduler.stop()
        logger.info("👋 Бот завершил работу")

if __name__ == "__main__":
    asyncio.run(main())
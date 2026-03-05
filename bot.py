import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta

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
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk")

if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не найден!")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(timezone=TIMEZONE)

message_queue = None
tz = pytz.timezone(TIMEZONE)

# Функция для получения правильного минского времени
def get_minsk_time():
    """Возвращает текущее время в Минске с учетом всех настроек"""
    utc_now = datetime.now(pytz.UTC)
    minsk_now = utc_now.astimezone(pytz.timezone('Europe/Minsk'))
    return minsk_now

# Состояния FSM
class TaskStates(StatesGroup):
    waiting_for_task_text = State()
    waiting_for_category = State()
    waiting_for_new_category = State()
    waiting_for_priority = State()
    waiting_for_deadline = State()
    waiting_for_edit_task_id = State()
    waiting_for_edit_text = State()
    waiting_for_edit_deadline = State()
    waiting_for_edit_category = State()
    waiting_for_edit_priority = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="➕ Новая задача")],
            [KeyboardButton(text="✅ Выполненные"), KeyboardButton(text="❌ Удалить задачу")],
            [KeyboardButton(text="📁 Категории"), KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_priority_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="priority:high")],
        [InlineKeyboardButton(text="🟡 Средний", callback_data="priority:medium")],
        [InlineKeyboardButton(text="🔵 Низкий", callback_data="priority:low")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_category_keyboard(categories):
    buttons = []
    for cat in categories[:8]:
        buttons.append([InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"category:{cat}")])
    buttons.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="category:new")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_filter_keyboard(categories):
    buttons = []
    for cat in categories[:6]:
        buttons.append([InlineKeyboardButton(text=f"📁 {cat}", callback_data=f"filter:{cat}")])
    buttons.append([InlineKeyboardButton(text="📁 Все категории", callback_data="filter:all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_task_actions_keyboard(task_id):
    buttons = [
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{task_id}")],
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete:{task_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{task_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_edit_options_keyboard(task_id):
    buttons = [
        [InlineKeyboardButton(text="📝 Текст", callback_data=f"edit_text:{task_id}")],
        [InlineKeyboardButton(text="📅 Дедлайн", callback_data=f"edit_deadline:{task_id}")],
        [InlineKeyboardButton(text="🏷️ Категория", callback_data=f"edit_category:{task_id}")],
        [InlineKeyboardButton(text="⚡ Приоритет", callback_data=f"edit_priority:{task_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view:{task_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_calendar_keyboard():
    """Клавиатура с правильным минским временем"""
    now = get_minsk_time()  # Используем функцию с правильным временем
    buttons = [
        [InlineKeyboardButton(text=f"📅 Сегодня ({now.strftime('%d.%m.%Y')})", callback_data="date:today")],
        [InlineKeyboardButton(text=f"📅 Завтра ({(now+timedelta(days=1)).strftime('%d.%m.%Y')})", callback_data="date:tomorrow")],
        [InlineKeyboardButton(text=f"📅 Послезавтра ({(now+timedelta(days=2)).strftime('%d.%m.%Y')})", callback_data="date:after_tomorrow")],
        [InlineKeyboardButton(text=f"📅 Через неделю ({(now+timedelta(days=7)).strftime('%d.%m.%Y')})", callback_data="date:next_week")],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="date:custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Обработчики
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    
    # Диагностика времени
    minsk_now = get_minsk_time()
    logger.info(f"Пользователь {user.id} запустил бота. Минское время: {minsk_now}")
    
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    await message.answer(
        f"👋 **Привет, {user.first_name}!**\n\n"
        f"Я бот для планирования задач с новыми функциями:\n"
        f"• 📁 **Категории** (Работа, Личное, Учеба...)\n"
        f"• ⚡ **Приоритеты** (Высокий, Средний, Низкий)\n"
        f"• ✏️ **Редактирование** задач\n"
        f"• 🔍 **Фильтрация** по категориям\n\n"
        f"📍 Часовой пояс: {TIMEZONE}\n"
        f"🕐 Текущее время: {minsk_now.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "➕ Новая задача")
async def cmd_new_task(message: types.Message, state: FSMContext):
    logger.info(f"Пользователь {message.from_user.id} начал создание задачи")
    await state.set_state(TaskStates.waiting_for_task_text)
    await message.answer(
        "📝 Введите текст задачи:",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(TaskStates.waiting_for_task_text)
async def process_task_text(message: types.Message, state: FSMContext):
    task_text = message.text.strip()
    if len(task_text) < 3:
        await message.answer("❌ Слишком короткий текст. Попробуйте снова:")
        return
    
    await state.update_data(task_text=task_text)
    categories = db.get_user_categories(message.from_user.id)
    await state.set_state(TaskStates.waiting_for_category)
    await message.answer(
        "📁 Выберите категорию:",
        reply_markup=get_category_keyboard(categories)
    )

@dp.callback_query(TaskStates.waiting_for_category, F.data.startswith("category:"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[1]
    
    if category == "new":
        await callback.message.answer("Введите название новой категории:")
        await state.set_state(TaskStates.waiting_for_new_category)
    else:
        await state.update_data(category=category)
        await state.set_state(TaskStates.waiting_for_priority)
        await callback.message.answer(
            "⚡ Выберите приоритет:",
            reply_markup=get_priority_keyboard()
        )
    await callback.answer()

@dp.message(TaskStates.waiting_for_new_category)
async def process_new_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    db.add_category(message.from_user.id, category)
    await state.update_data(category=category)
    await state.set_state(TaskStates.waiting_for_priority)
    await message.answer(
        "⚡ Выберите приоритет:",
        reply_markup=get_priority_keyboard()
    )

@dp.callback_query(TaskStates.waiting_for_priority, F.data.startswith("priority:"))
async def process_priority(callback: types.CallbackQuery, state: FSMContext):
    priority_map = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
    priority = priority_map[callback.data.split(":")[1]]
    await state.update_data(priority=priority)
    await state.set_state(TaskStates.waiting_for_deadline)
    await callback.message.answer(
        "📅 Выберите дату дедлайна:",
        reply_markup=get_calendar_keyboard()
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_deadline, F.data.startswith("date:"))
async def process_deadline_date(callback: types.CallbackQuery, state: FSMContext):
    date_choice = callback.data.split(":")[1]
    now = get_minsk_time()  # Правильное минское время
    
    if date_choice == "today":
        selected_date = now.strftime("%Y-%m-%d")
        display_date = now.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Теперь введите время (ЧЧ:ММ):"
        )
    elif date_choice == "tomorrow":
        tomorrow = now + timedelta(days=1)
        selected_date = tomorrow.strftime("%Y-%m-%d")
        display_date = tomorrow.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Теперь введите время (ЧЧ:ММ):"
        )
    elif date_choice == "after_tomorrow":
        after_tomorrow = now + timedelta(days=2)
        selected_date = after_tomorrow.strftime("%Y-%m-%d")
        display_date = after_tomorrow.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Теперь введите время (ЧЧ:ММ):"
        )
    elif date_choice == "next_week":
        next_week = now + timedelta(days=7)
        selected_date = next_week.strftime("%Y-%m-%d")
        display_date = next_week.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Теперь введите время (ЧЧ:ММ):"
        )
    elif date_choice == "custom":
        await callback.message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
    
    await callback.answer()

@dp.message(TaskStates.waiting_for_deadline)
async def process_deadline_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    # Если даты еще нет - значит пользователь вводит дату вручную
    if 'selected_date' not in data:
        try:
            date_str = message.text.strip()
            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
            selected_date = date_obj.strftime("%Y-%m-%d")
            await state.update_data(selected_date=selected_date)
            await message.answer("🕐 Введите время (ЧЧ:ММ):")
        except ValueError:
            await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
    
    # Если дата уже есть - значит пользователь вводит время
    else:
        try:
            time_str = message.text.strip()
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            
            date_part = datetime.strptime(data['selected_date'], "%Y-%m-%d").date()
            deadline_naive = datetime.combine(date_part, time_obj)
            
            # Добавляем часовой пояс Минска
            deadline_local = tz.localize(deadline_naive)
            
            # Текущее время в Минске
            now = get_minsk_time()
            
            # Проверяем, что дедлайн в будущем
            if deadline_local <= now:
                await message.answer(
                    f"❌ Дедлайн должен быть в будущем!\n"
                    f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')}\n"
                    f"Попробуйте снова:"
                )
                return
            
            # Создаем задачу
            task_id = db.add_task(
                user_id=message.from_user.id,
                task_text=data['task_text'],
                deadline_input=deadline_local,
                category=data.get('category', 'Без категории'),
                priority=data.get('priority', 'Средний')
            )
            
            await state.clear()
            
            await message.answer(
                f"✅ **Задача создана!**\n\n"
                f"📌 **{data['task_text']}**\n"
                f"📁 Категория: {data.get('category', 'Без категории')}\n"
                f"⚡ Приоритет: {data.get('priority', 'Средний')}\n"
                f"📅 Дедлайн: {deadline_local.strftime('%d.%m.%Y %H:%M')}\n"
                f"🆔 ID: {task_id}",
                reply_markup=get_main_keyboard()
            )
            
        except ValueError:
            await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")

@dp.message(F.text == "📋 Мои задачи")
async def cmd_tasks(message: types.Message):
    categories = db.get_user_categories(message.from_user.id)
    await message.answer(
        "Выберите категорию для фильтрации:",
        reply_markup=get_filter_keyboard(categories)
    )

@dp.callback_query(F.data.startswith("filter:"))
async def process_filter(callback: types.CallbackQuery):
    category = callback.data.split(":")[1]
    category = None if category == "all" else category
    
    tasks = db.get_user_tasks(callback.from_user.id, status='active', category=category)
    
    if not tasks:
        await callback.message.edit_text("📭 Нет задач в этой категории.")
        await callback.answer()
        return
    
    text = f"📋 **Задачи в категории {category or 'Все'}:**\n\n"
    for task in tasks:
        priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🔵"}.get(task['priority'], "⚪")
        deadline = task['deadline_obj'].strftime('%d.%m.%Y %H:%M')
        text += f"{priority_emoji} **{task['task_text'][:30]}**\n"
        text += f"   📅 {deadline} | 🏷️ {task['category']}\n"
        text += f"   [Подробнее](tg://user?id={task['id']})\n\n"
    
    await callback.message.edit_text(text)
    await callback.answer()

@dp.callback_query(F.data.startswith("view_"))
async def view_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("❌ Задача не найдена!")
        return
    
    priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🔵"}.get(task['priority'], "⚪")
    deadline = task['deadline_obj'].strftime('%d.%m.%Y %H:%M')
    
    text = (
        f"📌 **{task['task_text']}**\n\n"
        f"{priority_emoji} Приоритет: {task['priority']}\n"
        f"📁 Категория: {task['category']}\n"
        f"📅 Дедлайн: {deadline}\n"
        f"🆔 ID: `{task['id']}`\n"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_task_actions_keyboard(task_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("edit:"))
async def edit_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        "Что хотите изменить?",
        reply_markup=get_edit_options_keyboard(task_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_task_text(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(TaskStates.waiting_for_edit_text)
    await callback.message.answer("📝 Введите новый текст задачи:")
    await callback.answer()

@dp.message(TaskStates.waiting_for_edit_text)
async def process_edit_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db.update_task(data['edit_task_id'], task_text=message.text)
    await state.clear()
    await message.answer("✅ Текст задачи обновлен!", reply_markup=get_main_keyboard())

@dp.callback_query(F.data.startswith("edit_deadline:"))
async def edit_task_deadline(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(TaskStates.waiting_for_edit_deadline)
    await callback.message.answer("📅 Введите новый дедлайн (ДД.ММ.ГГГГ ЧЧ:ММ):")
    await callback.answer()

@dp.message(TaskStates.waiting_for_edit_deadline)
async def process_edit_deadline(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        deadline_naive = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        deadline_local = tz.localize(deadline_naive)
        
        now = get_minsk_time()
        if deadline_local <= now:
            await message.answer(
                f"❌ Дедлайн должен быть в будущем!\n"
                f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')}"
            )
            return
        
        db.update_task(data['edit_task_id'], deadline=deadline_local)
        await state.clear()
        await message.answer("✅ Дедлайн обновлен!", reply_markup=get_main_keyboard())
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте ДД.ММ.ГГГГ ЧЧ:ММ")

@dp.callback_query(F.data.startswith("edit_category:"))
async def edit_task_category(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    categories = db.get_user_categories(callback.from_user.id)
    await state.set_state(TaskStates.waiting_for_edit_category)
    await callback.message.answer(
        "Выберите новую категорию:",
        reply_markup=get_category_keyboard(categories)
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_edit_category, F.data.startswith("category:"))
async def process_edit_category(callback: types.CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[1]
    data = await state.get_data()
    
    if category == "new":
        await callback.message.answer("Введите название новой категории:")
        await state.set_state(TaskStates.waiting_for_new_category)
    else:
        db.update_task(data['edit_task_id'], category=category)
        await state.clear()
        await callback.message.answer("✅ Категория обновлена!", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_priority:"))
async def edit_task_priority(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(TaskStates.waiting_for_edit_priority)
    await callback.message.answer(
        "Выберите новый приоритет:",
        reply_markup=get_priority_keyboard()
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_edit_priority, F.data.startswith("priority:"))
async def process_edit_priority(callback: types.CallbackQuery, state: FSMContext):
    priority_map = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
    priority = priority_map[callback.data.split(":")[1]]
    data = await state.get_data()
    db.update_task(data['edit_task_id'], priority=priority)
    await state.clear()
    await callback.message.answer("✅ Приоритет обновлен!", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("complete:"))
async def complete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    db.complete_task(task_id)
    await callback.message.edit_text("✅ Задача выполнена! Отличная работа! 🎉")
    await callback.answer()

@dp.callback_query(F.data.startswith("delete:"))
async def delete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    db.delete_task(task_id)
    await callback.message.edit_text("🗑 Задача удалена!")
    await callback.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.message(F.text == "📁 Категории")
async def cmd_categories(message: types.Message):
    categories = db.get_user_categories(message.from_user.id)
    text = "📁 **Ваши категории:**\n\n"
    for cat in categories:
        count = len(db.get_user_tasks(message.from_user.id, category=cat))
        text += f"• {cat} ({count} задач)\n"
    text += "\nЧтобы добавить категорию, создайте новую задачу."
    await message.answer(text)

@dp.message(F.text == "✅ Выполненные")
async def cmd_completed(message: types.Message):
    tasks = db.get_user_tasks(message.from_user.id, status='completed')
    
    if not tasks:
        await message.answer("📭 Нет выполненных задач.")
        return
    
    text = "✅ **Выполненные задачи:**\n\n"
    for task in tasks[:10]:
        deadline = task['deadline_obj'].strftime('%d.%m.%Y %H:%M')
        text += f"✓ {task['task_text'][:30]}... ({deadline})\n"
    
    await message.answer(text)

@dp.message(F.text == "ℹ️ Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    now = get_minsk_time()
    help_text = (
        "📚 **Справка по командам:**\n\n"
        "**Основные функции:**\n"
        "• `➕ Новая задача` - создать задачу с категорией и приоритетом\n"
        "• `📋 Мои задачи` - просмотр с фильтрацией по категориям\n"
        "• `✅ Выполненные` - завершенные задачи\n"
        "• `📁 Категории` - список ваших категорий\n"
        "• `❌ Удалить задачу` - удалить задачу\n\n"
        "**В деталях задачи можно:**\n"
        "• ✏️ Редактировать текст, дату, категорию, приоритет\n"
        "• ✅ Отметить выполненной\n"
        "• 🗑 Удалить\n\n"
        f"📍 **Часовой пояс:** {TIMEZONE}\n"
        f"🕐 **Текущее время:** {now.strftime('%d.%m.%Y %H:%M')}"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "❌ Удалить задачу")
async def cmd_delete_prompt(message: types.Message):
    tasks = db.get_user_tasks(message.from_user.id, status='active')
    
    if not tasks:
        await message.answer("📭 Нет активных задач для удаления.")
        return
    
    buttons = []
    for task in tasks[:5]:
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {task['task_text'][:30]}...",
            callback_data=f"delete:{task['id']}"
        )])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("Выберите задачу для удаления:", reply_markup=keyboard)

@dp.message()
async def handle_unknown(message: types.Message):
    await message.answer(
        "Используйте кнопки меню или /help",
        reply_markup=get_main_keyboard()
    )

# Обработчик ошибок
@dp.error()
async def error_handler(event: types.ErrorEvent):
    logger.error(f"❌ Ошибка: {event.exception}")
    logger.error(traceback.format_exc())

async def message_sender_worker(queue):
    """Воркер для отправки сообщений из очереди"""
    while True:
        try:
            chat_id, text = await queue.get()
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
        finally:
            queue.task_done()

async def main():
    logger.info("🚀 Запуск бота...")
    
    # Диагностика времени при запуске
    minsk_now = get_minsk_time()
    logger.info(f"Минское время при запуске: {minsk_now}")
    
    global message_queue
    message_queue = asyncio.Queue()
    asyncio.create_task(message_sender_worker(message_queue))
    
    scheduler = ReminderScheduler(db, bot, TIMEZONE, message_queue)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import List

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
    waiting_for_tags = State()
    waiting_for_recurring = State()
    waiting_for_recurring_interval = State()
    waiting_for_edit_task_id = State()
    waiting_for_edit_text = State()
    waiting_for_edit_deadline = State()
    waiting_for_edit_category = State()
    waiting_for_edit_priority = State()
    waiting_for_edit_tags = State()
    waiting_for_snooze = State()

# Клавиатуры
def get_main_keyboard():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="➕ Новая задача")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📅 Сегодня/Неделя")],
            [KeyboardButton(text="📁 Категории"), KeyboardButton(text="🏷️ Теги")],
            [KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )
    return keyboard

def get_today_week_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📅 Сегодня", callback_data="show:today")],
        [InlineKeyboardButton(text="📆 Завтра", callback_data="show:tomorrow")],
        [InlineKeyboardButton(text="📅 Эта неделя", callback_data="show:week")],
        [InlineKeyboardButton(text="📅 Следующая неделя", callback_data="show:next_week")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_priority_keyboard():
    buttons = [
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="priority:high")],
        [InlineKeyboardButton(text="🟡 Средний", callback_data="priority:medium")],
        [InlineKeyboardButton(text="🔵 Низкий", callback_data="priority:low")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_recurring_keyboard():
    buttons = [
        [InlineKeyboardButton(text="📅 Каждый день", callback_data="recurring:day")],
        [InlineKeyboardButton(text="📅 Каждую неделю", callback_data="recurring:week")],
        [InlineKeyboardButton(text="📅 Каждый месяц", callback_data="recurring:month")],
        [InlineKeyboardButton(text="⏭️ Не повторять", callback_data="recurring:none")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_priority")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_category_keyboard(categories):
    buttons = []
    for cat in categories[:8]:
        color = cat.get('color', '#3498db')
        buttons.append([InlineKeyboardButton(
            text=f"{cat['category_name']}",
            callback_data=f"category:{cat['category_name']}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="category:new")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_start")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_tags_keyboard(tags, selected_tags=None):
    if selected_tags is None:
        selected_tags = []
    
    buttons = []
    for tag in tags[:6]:
        check = "✅ " if tag['tag_name'] in selected_tags else ""
        buttons.append([InlineKeyboardButton(
            text=f"{check}#{tag['tag_name']}",
            callback_data=f"tag:toggle:{tag['tag_name']}"
        )])
    
    nav_buttons = []
    if selected_tags:
        nav_buttons.append(InlineKeyboardButton(text="✅ Готово", callback_data="tags:done"))
    nav_buttons.append(InlineKeyboardButton(text="➕ Новый тег", callback_data="tag:new"))
    nav_buttons.append(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_category"))
    
    buttons.append(nav_buttons)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_filter_keyboard(categories, tags):
    buttons = []
    
    # Категории
    buttons.append([InlineKeyboardButton(text="📁 ФИЛЬТРЫ:", callback_data="noop")])
    for cat in categories[:3]:
        buttons.append([InlineKeyboardButton(
            text=f"📁 {cat['category_name']}",
            callback_data=f"filter:category:{cat['category_name']}"
        )])
    
    # Теги
    buttons.append([InlineKeyboardButton(text="🏷️ ТЕГИ:", callback_data="noop")])
    for tag in tags[:3]:
        buttons.append([InlineKeyboardButton(
            text=f"#{tag['tag_name']}",
            callback_data=f"filter:tag:{tag['tag_name']}"
        )])
    
    buttons.append([InlineKeyboardButton(text="📋 Все задачи", callback_data="filter:all")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_task_actions_keyboard(task_id):
    buttons = [
        [
            InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete:{task_id}"),
            InlineKeyboardButton(text="⏰ Отложить", callback_data=f"snooze:{task_id}")
        ],
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{task_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{task_id}")
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_snooze_keyboard(task_id):
    buttons = [
        [InlineKeyboardButton(text="⏰ +15 минут", callback_data=f"snooze:{task_id}:15")],
        [InlineKeyboardButton(text="⏰ +30 минут", callback_data=f"snooze:{task_id}:30")],
        [InlineKeyboardButton(text="⏰ +1 час", callback_data=f"snooze:{task_id}:60")],
        [InlineKeyboardButton(text="⏰ +2 часа", callback_data=f"snooze:{task_id}:120")],
        [InlineKeyboardButton(text="⏰ +1 день", callback_data=f"snooze:{task_id}:1440")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view:{task_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_edit_options_keyboard(task_id):
    buttons = [
        [InlineKeyboardButton(text="📝 Текст", callback_data=f"edit_text:{task_id}")],
        [InlineKeyboardButton(text="📅 Дедлайн", callback_data=f"edit_deadline:{task_id}")],
        [InlineKeyboardButton(text="📁 Категория", callback_data=f"edit_category:{task_id}")],
        [InlineKeyboardButton(text="⚡ Приоритет", callback_data=f"edit_priority:{task_id}")],
        [InlineKeyboardButton(text="🏷️ Теги", callback_data=f"edit_tags:{task_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"view:{task_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_calendar_keyboard():
    now = get_minsk_time()
    buttons = [
        [InlineKeyboardButton(text=f"📅 Сегодня ({now.strftime('%d.%m.%Y')})", callback_data="date:today")],
        [InlineKeyboardButton(text=f"📅 Завтра ({(now+timedelta(days=1)).strftime('%d.%m.%Y')})", callback_data="date:tomorrow")],
        [InlineKeyboardButton(text=f"📅 Послезавтра ({(now+timedelta(days=2)).strftime('%d.%m.%Y')})", callback_data="date:after_tomorrow")],
        [InlineKeyboardButton(text=f"📅 Через неделю ({(now+timedelta(days=7)).strftime('%d.%m.%Y')})", callback_data="date:next_week")],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="date:custom")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_recurring")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# Обработчики
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    minsk_now = get_minsk_time()
    
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    
    welcome_text = (
        f"👋 **Привет, {user.first_name}!**\n\n"
        f"🚀 **Новые возможности:**\n"
        f"• 📅 **Повторяющиеся задачи** (каждый день/неделю/месяц)\n"
        f"• 🏷️ **Теги** для лучшей организации\n"
        f"• ⏰ **Отложить** напоминание\n"
        f"• 📊 **Статистика** выполнения\n"
        f"• 📆 **Задачи на сегодня/неделю**\n\n"
        f"📍 Часовой пояс: {TIMEZONE}\n"
        f"🕐 Текущее время: {minsk_now.strftime('%d.%m.%Y %H:%M')}"
    )
    
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "➕ Новая задача")
async def cmd_new_task(message: types.Message, state: FSMContext):
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
        # Переходим к выбору тегов
        tags = db.get_user_tags(callback.from_user.id)
        await state.set_state(TaskStates.waiting_for_tags)
        await callback.message.answer(
            "🏷️ Выберите теги (можно несколько):",
            reply_markup=get_tags_keyboard(tags, [])
        )
    await callback.answer()

@dp.message(TaskStates.waiting_for_new_category)
async def process_new_category(message: types.Message, state: FSMContext):
    category = message.text.strip()
    db.add_category(message.from_user.id, category)
    await state.update_data(category=category)
    tags = db.get_user_tags(message.from_user.id)
    await state.set_state(TaskStates.waiting_for_tags)
    await message.answer(
        "🏷️ Выберите теги (можно несколько):",
        reply_markup=get_tags_keyboard(tags, [])
    )

@dp.callback_query(TaskStates.waiting_for_tags, F.data.startswith("tag:toggle:"))
async def toggle_tag(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split(":")[2]
    data = await state.get_data()
    selected_tags = data.get('selected_tags', [])
    
    if tag in selected_tags:
        selected_tags.remove(tag)
    else:
        selected_tags.append(tag)
    
    await state.update_data(selected_tags=selected_tags)
    
    tags = db.get_user_tags(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=get_tags_keyboard(tags, selected_tags)
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_tags, F.data == "tag:new")
async def new_tag(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название нового тега:")
    await state.set_state(TaskStates.waiting_for_new_tag)
    await callback.answer()

@dp.message(TaskStates.waiting_for_new_tag)
async def process_new_tag(message: types.Message, state: FSMContext):
    tag = message.text.strip()
    db.add_tag(message.from_user.id, tag)
    
    data = await state.get_data()
    selected_tags = data.get('selected_tags', [])
    selected_tags.append(tag)
    await state.update_data(selected_tags=selected_tags)
    
    tags = db.get_user_tags(message.from_user.id)
    await state.set_state(TaskStates.waiting_for_tags)
    await message.answer(
        "🏷️ Выберите теги (можно несколько):",
        reply_markup=get_tags_keyboard(tags, selected_tags)
    )

@dp.callback_query(TaskStates.waiting_for_tags, F.data == "tags:done")
async def tags_done(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.waiting_for_priority)
    await callback.message.answer(
        "⚡ Выберите приоритет:",
        reply_markup=get_priority_keyboard()
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_priority, F.data.startswith("priority:"))
async def process_priority(callback: types.CallbackQuery, state: FSMContext):
    priority_map = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
    priority = priority_map[callback.data.split(":")[1]]
    await state.update_data(priority=priority)
    
    # Спрашиваем про повторение
    await state.set_state(TaskStates.waiting_for_recurring)
    await callback.message.answer(
        "🔄 Это повторяющаяся задача?",
        reply_markup=get_recurring_keyboard()
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_recurring, F.data.startswith("recurring:"))
async def process_recurring(callback: types.CallbackQuery, state: FSMContext):
    recurring = callback.data.split(":")[1]
    
    if recurring == "none":
        await state.update_data(recurring_type=None, recurring_interval=None)
        await state.set_state(TaskStates.waiting_for_deadline)
        await callback.message.answer(
            "📅 Выберите дату дедлайна:",
            reply_markup=get_calendar_keyboard()
        )
    else:
        await state.update_data(recurring_type=recurring)
        await state.set_state(TaskStates.waiting_for_recurring_interval)
        await callback.message.answer(
            f"Введите интервал (например, 2 для '{recurring}') или 1 для каждого:"
        )
    await callback.answer()

@dp.message(TaskStates.waiting_for_recurring_interval)
async def process_recurring_interval(message: types.Message, state: FSMContext):
    try:
        interval = int(message.text.strip())
        await state.update_data(recurring_interval=interval)
        await state.set_state(TaskStates.waiting_for_deadline)
        await message.answer(
            "📅 Выберите дату дедлайна:",
            reply_markup=get_calendar_keyboard()
        )
    except ValueError:
        await message.answer("❌ Введите число:")

@dp.callback_query(TaskStates.waiting_for_deadline, F.data.startswith("date:"))
async def process_deadline_date(callback: types.CallbackQuery, state: FSMContext):
    date_choice = callback.data.split(":")[1]
    now = get_minsk_time()
    
    if date_choice == "today":
        selected_date = now.strftime("%Y-%m-%d")
        display_date = now.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Введите время (ЧЧ:ММ):"
        )
    elif date_choice == "tomorrow":
        tomorrow = now + timedelta(days=1)
        selected_date = tomorrow.strftime("%Y-%m-%d")
        display_date = tomorrow.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Введите время (ЧЧ:ММ):"
        )
    elif date_choice == "after_tomorrow":
        after = now + timedelta(days=2)
        selected_date = after.strftime("%Y-%m-%d")
        display_date = after.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Введите время (ЧЧ:ММ):"
        )
    elif date_choice == "next_week":
        next_week = now + timedelta(days=7)
        selected_date = next_week.strftime("%Y-%m-%d")
        display_date = next_week.strftime("%d.%m.%Y")
        await state.update_data(selected_date=selected_date)
        await callback.message.answer(
            f"✅ Выбрана дата: {display_date}\n"
            f"🕐 Введите время (ЧЧ:ММ):"
        )
    elif date_choice == "custom":
        await callback.message.answer("Введите дату в формате ДД.ММ.ГГГ:")
    
    await callback.answer()

@dp.message(TaskStates.waiting_for_deadline)
async def process_deadline_input(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if 'selected_date' not in data:
        try:
            date_str = message.text.strip()
            date_obj = datetime.strptime(date_str, "%d.%m.%Y")
            selected_date = date_obj.strftime("%Y-%m-%d")
            await state.update_data(selected_date=selected_date)
            await message.answer("🕐 Введите время (ЧЧ:ММ):")
        except ValueError:
            await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
    else:
        try:
            time_str = message.text.strip()
            time_obj = datetime.strptime(time_str, "%H:%M").time()
            
            date_part = datetime.strptime(data['selected_date'], "%Y-%m-%d").date()
            deadline_naive = datetime.combine(date_part, time_obj)
            deadline_local = tz.localize(deadline_naive)
            
            now = get_minsk_time()
            if deadline_local <= now:
                await message.answer(
                    f"❌ Дедлайн должен быть в будущем!\n"
                    f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')}\n"
                    f"Попробуйте снова:"
                )
                return
            
            task_id = db.add_task(
                user_id=message.from_user.id,
                task_text=data['task_text'],
                deadline_input=deadline_local,
                category=data.get('category', 'Без категории'),
                priority=data.get('priority', 'Средний'),
                tags=data.get('selected_tags', []),
                recurring_type=data.get('recurring_type'),
                recurring_interval=data.get('recurring_interval')
            )
            
            await state.clear()
            
            # Формируем сообщение о создании
            recurring_text = ""
            if data.get('recurring_type'):
                rt = data['recurring_type']
                ri = data.get('recurring_interval', 1)
                recurring_text = f"\n🔄 Повтор: кажд{'ые' if ri>1 else 'ый'} {ri} {rt}"

            tags_text = ""
            if data.get('selected_tags'):
                tags_text = f"\n🏷️ Теги: #{', #'.join(data['selected_tags'])}"
            
            await message.answer(
                f"✅ **Задача создана!**\n\n"
                f"📌 **{data['task_text']}**\n"
                f"📁 Категория: {data.get('category', 'Без категории')}\n"
                f"⚡ Приоритет: {data.get('priority', 'Средний')}{tags_text}"
                f"{recurring_text}\n"
                f"📅 Дедлайн: {deadline_local.strftime('%d.%m.%Y %H:%M')}\n"
                f"🆔 ID: {task_id}",
                reply_markup=get_main_keyboard()
            )
            
        except ValueError:
            await message.answer("❌ Неверный формат времени. Используйте ЧЧ:ММ")

@dp.message(F.text == "📅 Сегодня/Неделя")
async def cmd_today_week(message: types.Message):
    await message.answer(
        "Выберите период:",
        reply_markup=get_today_week_keyboard()
    )

@dp.callback_query(F.data.startswith("show:"))
async def show_tasks_by_period(callback: types.CallbackQuery):
    period = callback.data.split(":")[1]
    now = get_minsk_time()
    user_id = callback.from_user.id
    
    if period == "today":
        start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
        end = tz.localize(datetime(now.year, now.month, now.day, 23, 59, 59))
        title = f"📅 Задачи на сегодня ({now.strftime('%d.%m.%Y')})"
    
    elif period == "tomorrow":
        tomorrow = now + timedelta(days=1)
        start = tz.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0))
        end = tz.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, 23, 59, 59))
        title = f"📆 Задачи на завтра ({tomorrow.strftime('%d.%m.%Y')})"
    
    elif period == "week":
        start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
        end = start + timedelta(days=7)
        title = f"📅 Задачи на неделю ({now.strftime('%d.%m')} - {(now+timedelta(days=6)).strftime('%d.%m.%Y')})"
    
    elif period == "next_week":
        next_week = now + timedelta(days=7)
        start = tz.localize(datetime(next_week.year, next_week.month, next_week.day, 0, 0, 0))
        end = start + timedelta(days=7)
        title = f"📅 Задачи на следующую неделю ({next_week.strftime('%d.%m')} - {(next_week+timedelta(days=6)).strftime('%d.%m.%Y')})"
    
    tasks = db.get_user_tasks(user_id, status='active', from_date=start, to_date=end)
    
    if not tasks:
        await callback.message.edit_text(f"{title}\n\n🎉 Нет задач на этот период!")
        await callback.answer()
        return
    
    text = f"{title}\n\n"
    for task in tasks:
        priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🔵"}.get(task['priority'], "⚪")
        deadline = task['deadline_obj'].strftime('%d.%m %H:%M')
        text += f"{priority_emoji} **{task['task_text'][:40]}**\n"
        text += f"   ⏰ {deadline} | 🏷️ {task['category']}\n"
        if task['tags']:
            tags = ' #' + ' #'.join(task['tags'])
            text += f"   {tags}\n"
        text += f"   [Подробнее](tg://user?id={task['id']})\n\n"
    
    await callback.message.edit_text(text)
    await callback.answer()

@dp.message(F.text == "📊 Статистика")
async def cmd_stats(message: types.Message):
    stats = db.get_stats(message.from_user.id)
    
    # Строим график категорий
    cat_text = ""
    for cat, count in stats['categories'].items():
        bar = "█" * min(count, 10)
        cat_text += f"  {cat}: {bar} {count}\n"
    
    # График приоритетов
    pri_text = ""
    for pri, count in stats['priorities'].items():
        bar = "█" * min(count, 10)
        pri_text += f"  {pri}: {bar} {count}\n"
    
    text = (
        f"📊 **Статистика**\n\n"
        f"📝 Всего задач: {stats['total']}\n"
        f"✅ Выполнено: {stats['completed']}\n"
        f"⏳ В работе: {stats['active']}\n"
        f"🎯 Прогресс: {stats['completion_rate']:.1f}%\n\n"
        f"📁 **По категориям:**\n{cat_text}\n"
        f"⚡ **По приоритетам:**\n{pri_text}"
    )
    
    await message.answer(text)

@dp.message(F.text == "📁 Категории")
async def cmd_categories(message: types.Message):
    categories = db.get_user_categories(message.from_user.id)
    
    text = "📁 **Ваши категории:**\n\n"
    for cat in categories:
        tasks = db.get_user_tasks(message.from_user.id, category=cat['category_name'])
        active = len([t for t in tasks if t['status'] == 'active'])
        completed = len([t for t in tasks if t['status'] == 'completed'])
        text += f"• {cat['category_name']}\n"
        text += f"  ⏳ {active} активных | ✅ {completed} выполненных\n\n"
    
    await message.answer(text)

@dp.message(F.text == "🏷️ Теги")
async def cmd_tags(message: types.Message):
    tags = db.get_user_tags(message.from_user.id)
    
    text = "🏷️ **Ваши теги:**\n\n"
    for tag in tags:
        tasks = db.get_user_tasks(message.from_user.id, tag=tag['tag_name'])
        text += f"• #{tag['tag_name']} ({len(tasks)} задач)\n"
    
    await message.answer(text)

@dp.message(F.text == "📋 Мои задачи")
async def cmd_tasks(message: types.Message):
    categories = db.get_user_categories(message.from_user.id)
    tags = db.get_user_tags(message.from_user.id)
    
    await message.answer(
        "Выберите фильтр:",
        reply_markup=get_filter_keyboard(categories, tags)
    )

@dp.callback_query(F.data.startswith("filter:"))
async def process_filter(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    
    if parts[1] == "all":
        tasks = db.get_user_tasks(callback.from_user.id, status='active')
        title = "📋 **Все задачи:**"
    
    elif parts[1] == "category":
        category = parts[2]
        tasks = db.get_user_tasks(callback.from_user.id, status='active', category=category)
        title = f"📋 **Задачи в категории {category}:**"
    
    elif parts[1] == "tag":
        tag = parts[2]
        tasks = db.get_user_tasks(callback.from_user.id, status='active', tag=tag)
        title = f"📋 **Задачи с тегом #{tag}:**"
    
    if not tasks:
        await callback.message.edit_text(f"{title}\n\n📭 Нет задач.")
        await callback.answer()
        return
    
    text = f"{title}\n\n"
    for task in tasks[:10]:
        priority_emoji = {"Высокий": "🔴", "Средний": "🟡", "Низкий": "🔵"}.get(task['priority'], "⚪")
        deadline = task['deadline_obj'].strftime('%d.%m %H:%M')
        text += f"{priority_emoji} **{task['task_text'][:40]}**\n"
        text += f"   ⏰ {deadline} | 🏷️ {task['category']}\n"
        if task['tags']:
            tags = ' #' + ' #'.join(task['tags'])
            text += f"   {tags}\n"
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
    
    recurring_text = ""
    if task['recurring_type']:
        recurring_text = f"\n🔄 Повтор: каждые {task['recurring_interval']} {task['recurring_type']}"
    
    tags_text = ""
    if task['tags']:
        tags_text = f"\n🏷️ Теги: #{', #'.join(task['tags'])}"
    
    text = (
        f"📌 **{task['task_text']}**\n\n"
        f"{priority_emoji} Приоритет: {task['priority']}\n"
        f"📁 Категория: {task['category']}{tags_text}"
        f"{recurring_text}\n"
        f"📅 Дедлайн: {deadline}\n"
        f"🆔 ID: `{task['id']}`\n"
    )
    
    await callback.message.edit_text(
        text,
        reply_markup=get_task_actions_keyboard(task_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("snooze:"))
async def snooze_task(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    
    if len(parts) == 2:
        # Показываем меню выбора времени
        task_id = int(parts[1])
        await callback.message.edit_text(
            "⏰ На сколько отложить?",
            reply_markup=get_snooze_keyboard(task_id)
        )
    else:
        # Откладываем
        task_id = int(parts[1])
        minutes = int(parts[2])
        
        if db.snooze_task(task_id, minutes):
            task = db.get_task(task_id)
            new_deadline = task['deadline_obj'].strftime('%d.%m.%Y %H:%M')
            await callback.message.edit_text(
                f"✅ Задача отложена на {minutes} минут\n"
                f"Новый дедлайн: {new_deadline}"
            )
        else:
            await callback.message.edit_text("❌ Не удалось отложить задачу")
    
    await callback.answer()

@dp.callback_query(F.data.startswith("complete:"))
async def complete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    
    if db.complete_task(task_id):
        # Проверяем, была ли задача повторяющейся
        task = db.get_task(task_id)
        if task and task.get('recurring_type'):
            await callback.message.edit_text(
                "✅ Задача выполнена! Создана новая повторяющаяся задача."
            )
        else:
            await callback.message.edit_text("✅ Задача выполнена! Отличная работа! 🎉")
    else:
        await callback.message.edit_text("❌ Не удалось выполнить задачу")
    
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

@dp.callback_query(F.data.startswith("edit_tags:"))
async def edit_task_tags(callback: types.CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    task = db.get_task(task_id)
    await state.update_data(edit_task_id=task_id)
    tags = db.get_user_tags(callback.from_user.id)
    await state.set_state(TaskStates.waiting_for_edit_tags)
    await callback.message.answer(
        "🏷️ Выберите теги:",
        reply_markup=get_tags_keyboard(tags, task['tags'] if task else [])
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_edit_tags, F.data.startswith("tag:toggle:"))
async def toggle_edit_tag(callback: types.CallbackQuery, state: FSMContext):
    tag = callback.data.split(":")[2]
    data = await state.get_data()
    selected_tags = data.get('selected_tags', [])
    
    if tag in selected_tags:
        selected_tags.remove(tag)
    else:
        selected_tags.append(tag)
    
    await state.update_data(selected_tags=selected_tags)
    
    tags = db.get_user_tags(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=get_tags_keyboard(tags, selected_tags)
    )
    await callback.answer()

@dp.callback_query(TaskStates.waiting_for_edit_tags, F.data == "tags:done")
async def edit_tags_done(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    db.update_task(data['edit_task_id'], tags=data.get('selected_tags', []))
    await state.clear()
    await callback.message.answer("✅ Теги обновлены!", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("delete:"))
async def delete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split(":")[1])
    db.delete_task(task_id)
    await callback.message.edit_text("🗑 Задача удалена!")
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_start")
async def back_to_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "back_to_priority")
async def back_to_priority(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.waiting_for_priority)
    await callback.message.delete()
    await callback.message.answer(
        "⚡ Выберите приоритет:",
        reply_markup=get_priority_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_category")
async def back_to_category(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.waiting_for_category)
    categories = db.get_user_categories(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        "📁 Выберите категорию:",
        reply_markup=get_category_keyboard(categories)
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_recurring")
async def back_to_recurring(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TaskStates.waiting_for_recurring)
    await callback.message.delete()
    await callback.message.answer(
        "🔄 Это повторяющаяся задача?",
        reply_markup=get_recurring_keyboard()
    )
    await callback.answer()

@dp.message(F.text == "ℹ️ Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    now = get_minsk_time()
    help_text = (
        "📚 **Справка по командам:**\n\n"
        "**Основные функции:**\n"
        "• `➕ Новая задача` - создать задачу с категорией, тегами и повторением\n"
        "• `📋 Мои задачи` - просмотр с фильтрацией\n"
        "• `📊 Статистика` - графики выполнения\n"
        "• `📅 Сегодня/Неделя` - задачи на период\n"
        "• `📁 Категории` - управление категориями\n"
        "• `🏷️ Теги` - управление тегами\n\n"
        "**Новые возможности:**\n"
        "• 🔄 **Повторяющиеся задачи** (день/неделя/месяц)\n"
        "• 🏷️ **Множественные теги**\n"
        "• ⏰ **Отложить** напоминание\n"
        "• 📊 **Статистика** с графиками\n"
        "• 📆 **Фильтрация по датам**\n\n"
        f"📍 **Часовой пояс:** {TIMEZONE}\n"
        f"🕐 **Текущее время:** {now.strftime('%d.%m.%Y %H:%M')}"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

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
    while True:
        try:
            chat_id, text = await queue.get()
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
        finally:
            queue.task_done()

async def main():
    logger.info("🚀 Запуск бота с новыми функциями...")
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
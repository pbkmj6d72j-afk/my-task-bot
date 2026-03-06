import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
import pytz

from database import Database
from scheduler import ReminderScheduler

# Загрузка переменных окружения
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Minsk")

if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден!")
    sys.exit(1)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db = Database(timezone=TIMEZONE)
tz = pytz.timezone(TIMEZONE)

# Глобальная очередь для сообщений
message_queue = None

def get_minsk_time():
    """Возвращает текущее минское время"""
    return datetime.now(tz)

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

# --- Клавиатуры ---
def get_main_keyboard():
    """Основная клавиатура"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="➕ Новая задача")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📅 Сегодня/Неделя")],
            [KeyboardButton(text="📁 Категории"), KeyboardButton(text="ℹ️ Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_priority_keyboard():
    """Клавиатура выбора приоритета"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Высокий", callback_data="priority:high")],
        [InlineKeyboardButton(text="🟡 Средний", callback_data="priority:medium")],
        [InlineKeyboardButton(text="🔵 Низкий", callback_data="priority:low")]
    ])

def get_category_keyboard(categories):
    """Клавиатура выбора категории"""
    buttons = []
    for cat in categories:
        buttons.append([InlineKeyboardButton(text=cat, callback_data=f"category:{cat}")])
    buttons.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="category:new")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_today_week_keyboard():
    """Клавиатура выбора периода"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Сегодня", callback_data="period:today")],
        [InlineKeyboardButton(text="📆 Завтра", callback_data="period:tomorrow")],
        [InlineKeyboardButton(text="📅 Эта неделя", callback_data="period:week")]
    ])

def get_task_actions_keyboard(task_id):
    """Клавиатура действий с задачей"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete:{task_id}")],
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"edit:{task_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete:{task_id}")]
    ])

def get_edit_options_keyboard(task_id):
    """Клавиатура выбора что редактировать"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Текст", callback_data=f"edit_text:{task_id}")],
        [InlineKeyboardButton(text="📅 Дедлайн", callback_data=f"edit_deadline:{task_id}")],
        [InlineKeyboardButton(text="📁 Категория", callback_data=f"edit_category:{task_id}")],
        [InlineKeyboardButton(text="⚡ Приоритет", callback_data=f"edit_priority:{task_id}")]
    ])

def get_calendar_keyboard():
    """Клавиатура выбора даты"""
    now = get_minsk_time()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📅 Сегодня ({now.strftime('%d.%m.%Y')})", callback_data="date:today")],
        [InlineKeyboardButton(text=f"📅 Завтра ({(now+timedelta(days=1)).strftime('%d.%m.%Y')})", callback_data="date:tomorrow")],
        [InlineKeyboardButton(text=f"📅 Послезавтра ({(now+timedelta(days=2)).strftime('%d.%m.%Y')})", callback_data="date:after_tomorrow")],
        [InlineKeyboardButton(text="✏️ Ввести вручную", callback_data="date:custom")]
    ])

# --- Обработчики команд ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Обработка команды /start"""
    user = message.from_user
    db.add_user(user.id, user.username, user.first_name, user.last_name)
    now = get_minsk_time()
    await message.answer(
        f"👋 **Привет, {user.first_name}!**\n\n"
        f"📍 Часовой пояс: {TIMEZONE}\n"
        f"🕐 Текущее время: {now.strftime('%d.%m.%Y %H:%M')}",
        reply_markup=get_main_keyboard()
    )

@dp.message(F.text == "➕ Новая задача")
@dp.message(Command("add"))
async def cmd_add(message: types.Message, state: FSMContext):
    """Начало создания задачи"""
    await state.set_state(TaskStates.waiting_for_task_text)
    await message.answer(
        "📝 Введите текст задачи:",
        reply_markup=types.ReplyKeyboardRemove()
    )

@dp.message(TaskStates.waiting_for_task_text)
async def process_task_text(message: types.Message, state: FSMContext):
    """Обработка текста задачи"""
    if len(message.text) < 3:
        await message.answer("❌ Слишком короткий текст. Попробуйте снова:")
        return
    
    await state.update_data(task_text=message.text)
    categories = db.get_user_categories(message.from_user.id)
    await state.set_state(TaskStates.waiting_for_category)
    await message.answer(
        "📁 Выберите категорию:",
        reply_markup=get_category_keyboard(categories)
    )

@dp.callback_query(TaskStates.waiting_for_category, F.data.startswith("category:"))
async def process_category(callback: types.CallbackQuery, state: FSMContext):
    """Обработка выбора категории"""
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
    """Обработка новой категории"""
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
    """Обработка выбора приоритета"""
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
    """Обработка выбора даты из календаря"""
    choice = callback.data.split(":")[1]
    now = get_minsk_time()
    
    if choice == "today":
        await state.update_data(selected_date=now.strftime("%Y-%m-%d"))
        await callback.message.answer("🕐 Введите время (ЧЧ:ММ):")
    elif choice == "tomorrow":
        tomorrow = now + timedelta(days=1)
        await state.update_data(selected_date=tomorrow.strftime("%Y-%m-%d"))
        await callback.message.answer("🕐 Введите время (ЧЧ:ММ):")
    elif choice == "after_tomorrow":
        after = now + timedelta(days=2)
        await state.update_data(selected_date=after.strftime("%Y-%m-%d"))
        await callback.message.answer("🕐 Введите время (ЧЧ:ММ):")
    elif choice == "custom":
        await callback.message.answer("Введите дату в формате ДД.ММ.ГГГГ:")
    
    await callback.answer()

@dp.message(TaskStates.waiting_for_deadline)
async def process_deadline_input(message: types.Message, state: FSMContext):
    """Обработка ввода даты/времени"""
    data = await state.get_data()
    
    # Если даты еще нет - ввод даты
    if 'selected_date' not in data:
        try:
            date_obj = datetime.strptime(message.text, "%d.%m.%Y")
            await state.update_data(selected_date=date_obj.strftime("%Y-%m-%d"))
            await message.answer("🕐 Введите время (ЧЧ:ММ):")
        except ValueError:
            await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ")
    
    # Если дата есть - ввод времени
    else:
        try:
            time_obj = datetime.strptime(message.text, "%H:%M").time()
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
            
            # Создание задачи
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
@dp.message(Command("tasks"))
async def cmd_tasks(message: types.Message):
    """Просмотр активных задач"""
    tasks = db.get_user_tasks(message.from_user.id, status='active')
    
    if not tasks:
        await message.answer("📭 Нет активных задач.")
        return
    
    for task in tasks:
        text = (
            f"📌 **{task['task_text']}**\n"
            f"📁 {task['category']} | ⚡ {task['priority']}\n"
            f"📅 {task['deadline_obj'].strftime('%d.%m.%Y %H:%M')}"
        )
        await message.answer(text, reply_markup=get_task_actions_keyboard(task['id']))

@dp.message(F.text == "📊 Статистика")
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Просмотр статистики"""
    stats = db.get_stats(message.from_user.id)
    
    # Формируем текст статистики
    cat_text = ""
    for cat, count in stats['categories'].items():
        bar = "█" * min(count, 10)
        cat_text += f"  {cat}: {bar} {count}\n"
    
    pri_text = ""
    for pri, count in stats['priorities'].items():
        bar = "█" * min(count, 10)
        pri_text += f"  {pri}: {bar} {count}\n"
    
    await message.answer(
        f"📊 **Статистика**\n\n"
        f"📝 Всего задач: {stats['total']}\n"
        f"✅ Выполнено: {stats['completed']}\n"
        f"⏳ В работе: {stats['active']}\n"
        f"🎯 Прогресс: {stats['completion_rate']:.1f}%\n\n"
        f"📁 **По категориям:**\n{cat_text}\n"
        f"⚡ **По приоритетам:**\n{pri_text}"
    )

@dp.message(F.text == "📅 Сегодня/Неделя")
async def cmd_period(message: types.Message):
    """Меню выбора периода"""
    await message.answer(
        "Выберите период:",
        reply_markup=get_today_week_keyboard()
    )

@dp.callback_query(F.data.startswith("period:"))
async def show_period(callback: types.CallbackQuery):
    """Показ задач за выбранный период"""
    period = callback.data.split(":")[1]
    now = get_minsk_time()
    
    # Определяем начало и конец периода
    if period == "today":
        start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
        end = start + timedelta(days=1)
        title = f"📅 Задачи на сегодня ({now.strftime('%d.%m.%Y')})"
    elif period == "tomorrow":
        tomorrow = now + timedelta(days=1)
        start = tz.localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0))
        end = start + timedelta(days=1)
        title = f"📆 Задачи на завтра ({tomorrow.strftime('%d.%m.%Y')})"
    else:  # week
        start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
        end = start + timedelta(days=7)
        title = f"📅 Задачи на неделю ({now.strftime('%d.%m')} - {(now+timedelta(days=6)).strftime('%d.%m.%Y')})"
    
    # Фильтруем задачи
    tasks = db.get_user_tasks(callback.from_user.id, status='active')
    filtered = [t for t in tasks if start <= t['deadline_obj'] <= end]
    
    if not filtered:
        await callback.message.edit_text(f"{title}\n\n🎉 Нет задач!")
    else:
        text = f"{title}\n\n"
        for t in filtered[:10]:  # Показываем первые 10
            text += f"• **{t['task_text'][:30]}** - {t['deadline_obj'].strftime('%d.%m %H:%M')}\n"
        await callback.message.edit_text(text)
    
    await callback.answer()

@dp.message(F.text == "📁 Категории")
async def cmd_categories(message: types.Message):
    """Просмотр категорий"""
    categories = db.get_user_categories(message.from_user.id)
    text = "📁 **Ваши категории:**\n\n"
    for cat in categories:
        count = len(db.get_user_tasks(message.from_user.id, category=cat))
        text += f"• {cat} ({count} задач)\n"
    await message.answer(text)

@dp.callback_query(F.data.startswith("complete:"))
async def complete_task(callback: types.CallbackQuery):
    """Отметка задачи как выполненной"""
    task_id = int(callback.data.split(":")[1])
    db.complete_task(task_id)
    await callback.message.edit_text("✅ Задача выполнена! Отличная работа! 🎉")
    await callback.answer()

@dp.callback_query(F.data.startswith("delete:"))
async def delete_task(callback: types.CallbackQuery):
    """Удаление задачи"""
    task_id = int(callback.data.split(":")[1])
    db.delete_task(task_id)
    await callback.message.edit_text("🗑 Задача удалена!")
    await callback.answer()

@dp.callback_query(F.data.startswith("edit:"))
async def edit_task(callback: types.CallbackQuery):
    """Меню редактирования задачи"""
    task_id = int(callback.data.split(":")[1])
    await callback.message.edit_text(
        "Что хотите изменить?",
        reply_markup=get_edit_options_keyboard(task_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("edit_text:"))
async def edit_task_text(callback: types.CallbackQuery, state: FSMContext):
    """Редактирование текста задачи"""
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(TaskStates.waiting_for_edit_text)
    await callback.message.answer("📝 Введите новый текст задачи:")
    await callback.answer()

@dp.message(TaskStates.waiting_for_edit_text)
async def process_edit_text(message: types.Message, state: FSMContext):
    """Обработка нового текста"""
    data = await state.get_data()
    db.update_task(data['edit_task_id'], task_text=message.text)
    await state.clear()
    await message.answer("✅ Текст задачи обновлен!", reply_markup=get_main_keyboard())

@dp.callback_query(F.data.startswith("edit_deadline:"))
async def edit_task_deadline(callback: types.CallbackQuery, state: FSMContext):
    """Редактирование дедлайна"""
    task_id = int(callback.data.split(":")[1])
    await state.update_data(edit_task_id=task_id)
    await state.set_state(TaskStates.waiting_for_edit_deadline)
    await callback.message.answer("📅 Введите новый дедлайн (ДД.ММ.ГГГГ ЧЧ:ММ):")
    await callback.answer()

@dp.message(TaskStates.waiting_for_edit_deadline)
async def process_edit_deadline(message: types.Message, state: FSMContext):
    """Обработка нового дедлайна"""
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
    """Редактирование категории"""
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
    """Обработка новой категории"""
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
    """Редактирование приоритета"""
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
    """Обработка нового приоритета"""
    priority_map = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
    priority = priority_map[callback.data.split(":")[1]]
    data = await state.get_data()
    db.update_task(data['edit_task_id'], priority=priority)
    await state.clear()
    await callback.message.answer("✅ Приоритет обновлен!", reply_markup=get_main_keyboard())
    await callback.answer()

@dp.message(F.text == "ℹ️ Помощь")
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Справка по командам"""
    now = get_minsk_time()
    help_text = (
        "📚 **Справка по командам:**\n\n"
        "**Основные функции:**\n"
        "• `➕ Новая задача` - создать задачу\n"
        "• `📋 Мои задачи` - просмотр задач\n"
        "• `📊 Статистика` - статистика выполнения\n"
        "• `📅 Сегодня/Неделя` - задачи на период\n"
        "• `📁 Категории` - список категорий\n\n"
        "**Управление задачами:**\n"
        "• ✅ Отметить выполненной\n"
        "• ✏️ Редактировать (текст, дату, категорию, приоритет)\n"
        "• 🗑 Удалить\n\n"
        f"📍 **Часовой пояс:** {TIMEZONE}\n"
        f"🕐 **Текущее время:** {now.strftime('%d.%m.%Y %H:%M')}"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@dp.message(F.text == "❌ Удалить задачу")
async def cmd_delete_prompt(message: types.Message):
    """Меню удаления задачи (для совместимости)"""
    tasks = db.get_user_tasks(message.from_user.id, status='active')
    if not tasks:
        await message.answer("📭 Нет задач для удаления.")
        return
    
    for task in tasks[:5]:
        text = f"🗑 **{task['task_text'][:30]}**"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Удалить", callback_data=f"delete:{task['id']}")]
        ])
        await message.answer(text, reply_markup=keyboard)

@dp.message()
async def handle_unknown(message: types.Message):
    """Обработка неизвестных сообщений"""
    await message.answer(
        "Я не понимаю эту команду. Используйте кнопки меню или /help",
        reply_markup=get_main_keyboard()
    )

@dp.error()
async def error_handler(event: types.ErrorEvent):
    """Глобальный обработчик ошибок"""
    logger.error(f"❌ Ошибка: {event.exception}")

# --- Воркер для отправки сообщений ---
async def message_sender_worker(queue):
    """Отправка сообщений из очереди"""
    while True:
        try:
            chat_id, text = await queue.get()
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
        finally:
            queue.task_done()

# --- Главная функция ---
async def main():
    """Запуск бота"""
    global message_queue
    message_queue = asyncio.Queue()
    asyncio.create_task(message_sender_worker(message_queue))
    
    scheduler = ReminderScheduler(db, bot, TIMEZONE, message_queue)
    scheduler.start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
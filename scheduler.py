import logging
import sys
import traceback
import asyncio
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scheduler.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class ReminderScheduler:
    def __init__(self, db, bot, timezone='Europe/Moscow', message_queue=None):
        """
        Инициализация планировщика
        
        Args:
            db: объект базы данных
            bot: объект бота
            timezone: часовой пояс (например, 'Europe/Minsk')
            message_queue: очередь для отправки сообщений
        """
        self.db = db
        self.bot = bot
        self.timezone_str = timezone
        self.timezone = pytz.timezone(timezone)
        self.message_queue = message_queue
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        
        # Для отслеживания
        self.check_count = 0
        
        logger.info("="*70)
        logger.info("🆕 ПЛАНИРОВЩИК ИНИЦИАЛИЗИРОВАН")
        logger.info(f"📅 Часовой пояс: {timezone}")
        logger.info(f"📨 Очередь сообщений: {'✅ Есть' if message_queue else '❌ НЕТ'}")
        logger.info(f"🤖 Бот передан: {'✅ Да' if bot else '❌ Нет'}")
        logger.info("="*70)
    
    def _make_aware(self, dt):
        """
        Преобразует наивное datetime в timezone-aware
        
        Args:
            dt: datetime объект (может быть наивным или aware)
        
        Returns:
            timezone-aware datetime в часовом поясе self.timezone
        """
        if dt.tzinfo is None:
            # Если время без часового пояса - добавляем наш часовой пояс
            return self.timezone.localize(dt)
        else:
            # Если время уже с часовым поясом - конвертируем в наш
            return dt.astimezone(self.timezone)
    
    def start(self):
        """Запуск планировщика"""
        try:
            logger.info("🚀 ЗАПУСК ПЛАНИРОВЩИКА")
            
            # Основная задача проверки напоминаний (каждую минуту)
            self.scheduler.add_job(
                self.check_reminders,
                IntervalTrigger(minutes=1),
                id='check_reminders',
                replace_existing=True,
                max_instances=1
            )
            logger.info("✅ Задача check_reminders добавлена (интервал: 1 минута)")
            
            # Запускаем планировщик
            self.scheduler.start()
            logger.info("✅ ПЛАНИРОВЩИК ЗАПУЩЕН")
            logger.info(f"📋 Запланировано задач: {len(self.scheduler.get_jobs())}")
            
        except Exception as e:
            logger.error(f"❌ ОШИБКА ЗАПУСКА: {e}")
            logger.error(traceback.format_exc())
    
    async def check_reminders(self):
        """Проверка реальных задач в БД"""
        self.check_count += 1
        try:
            # Текущее время с часовым поясом
            now = datetime.now(self.timezone)
            logger.info("="*80)
            logger.info(f"🔍 ПРОВЕРКА НАПОМИНАНИЙ #{self.check_count} в {now.strftime('%d.%m.%Y %H:%M:%S %z')}")
            
            # Получаем ВСЕ активные задачи
            all_tasks = self.db.get_all_active_tasks()
            logger.info(f"📋 Всего активных задач: {len(all_tasks)}")
            
            for task in all_tasks:
                try:
                    task_id = task['id']
                    user_id = task['user_id']
                    
                    # Получаем строку с дедлайном из БД
                    deadline_str = task['deadline']
                    
                    # Преобразуем строку в наивный datetime-объект
                    deadline_naive = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
                    
                    # Добавляем часовой пояс (превращаем в aware)
                    deadline = self._make_aware(deadline_naive)
                    
                    # Вычисляем разницу во времени (оба объекта - aware)
                    time_left = deadline - now
                    minutes_left = int(time_left.total_seconds() / 60)
                    hours_left = int(time_left.total_seconds() / 3600)
                    days_left = time_left.days
                    
                    # Получаем статусы напоминаний
                    reminder_3d = task.get('reminder_3d', False)
                    reminder_24h = task.get('reminder_24h', False)
                    reminder_1h = task.get('reminder_1h', False)
                    reminder_5m = task.get('reminder_5m', False)
                    
                    logger.info(f"📋 Задача #{task_id} (пользователь {user_id}):")
                    logger.info(f"   📝 Текст: {task['task_text'][:50]}...")
                    logger.info(f"   📅 Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}")
                    logger.info(f"   ⏳ До дедлайна: {days_left} д {hours_left} ч {minutes_left} мин")
                    logger.info(f"   🏷️ Статусы: 3д:{reminder_3d} 24ч:{reminder_24h} 1ч:{reminder_1h} 5м:{reminder_5m}")
                    
                    # Проверяем напоминание за 5 минут
                    if not reminder_5m and 0 < minutes_left <= 5:
                        logger.info(f"   ✅ НУЖНО: напоминание за 5 минут")
                        await self._send_reminder_internal(task, '5m')
                        self.db.mark_reminder_sent(task_id, '5m')
                    
                    # Проверяем напоминание за 1 час (только если еще не отправлено и время подходит)
                    elif not reminder_1h and 0 < hours_left <= 1 and minutes_left <= 60:
                        logger.info(f"   ✅ НУЖНО: напоминание за 1 час")
                        await self._send_reminder_internal(task, '1h')
                        self.db.mark_reminder_sent(task_id, '1h')
                    
                    # Проверяем напоминание за 24 часа
                    elif not reminder_24h and 0 < days_left <= 1 and hours_left <= 24:
                        logger.info(f"   ✅ НУЖНО: напоминание за 24 часа")
                        await self._send_reminder_internal(task, '24h')
                        self.db.mark_reminder_sent(task_id, '24h')
                    
                    # Проверяем напоминание за 3 дня
                    elif not reminder_3d and 1 < days_left <= 3:
                        logger.info(f"   ✅ НУЖНО: напоминание за 3 дня")
                        await self._send_reminder_internal(task, '3d')
                        self.db.mark_reminder_sent(task_id, '3d')
                    
                    # Если дедлайн уже прошел
                    elif time_left.total_seconds() < 0:
                        logger.info(f"   ⏰ Дедлайн просрочен на {abs(time_left.total_seconds() / 60):.0f} минут")
                        # Здесь можно добавить логику для просроченных задач
                    
                    else:
                        logger.info(f"   ⏺️ Ожидание следующей проверки")
                        
                except Exception as task_error:
                    logger.error(f"❌ Ошибка при обработке задачи {task.get('id', 'unknown')}: {task_error}")
                    continue
            
            logger.info(f"✅ ПРОВЕРКА #{self.check_count} ЗАВЕРШЕНА")
            
        except Exception as e:
            logger.error(f"❌ ОШИБКА В check_reminders: {e}")
            logger.error(traceback.format_exc())
        logger.info("="*80)
    
    async def _send_reminder_internal(self, task: dict, reminder_type: str):
        """
        Внутренний метод отправки напоминаний
        
        Args:
            task: словарь с данными задачи
            reminder_type: тип напоминания ('3d', '24h', '1h', '5m')
        """
        try:
            logger.info(f"📨 ОТПРАВКА НАПОМИНАНИЯ: задача {task['id']}, тип {reminder_type}")
            
            # Парсим дедлайн
            deadline_naive = datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S')
            deadline = self._make_aware(deadline_naive)
            now = datetime.now(self.timezone)
            time_left = deadline - now
            
            # Формируем текст в зависимости от интервала
            if reminder_type == '3d':
                days = time_left.days
                if days % 10 == 1 and days % 100 != 11:
                    time_text = f"{days} день"
                elif 2 <= days % 10 <= 4 and (days % 100 < 10 or days % 100 >= 20):
                    time_text = f"{days} дня"
                else:
                    time_text = f"{days} дней"
                    
            elif reminder_type == '24h':
                time_text = "24 часа"
                
            elif reminder_type == '1h':
                time_text = "1 час"
                
            elif reminder_type == '5m':
                minutes = int(time_left.total_seconds() / 60)
                if minutes % 10 == 1 and minutes % 100 != 11:
                    time_text = f"{minutes} минута"
                elif 2 <= minutes % 10 <= 4 and (minutes % 100 < 10 or minutes % 100 >= 20):
                    time_text = f"{minutes} минуты"
                else:
                    time_text = f"{minutes} минут"
                    
            else:
                time_text = "тест"
            
            # Формируем сообщение
            message = (
                f"⏰ **НАПОМИНАНИЕ!**\n\n"
                f"📌 **Задача:** {task['task_text']}\n"
                f"⏳ **Осталось:** {time_text}\n"
                f"📅 **Дедлайн:** {deadline.strftime('%d.%m.%Y %H:%M')}"
            )
            
            logger.info(f"📝 Текст сообщения сформирован ({len(message)} символов)")
            logger.info(f"   👤 Пользователь: {task['user_id']}")
            logger.info(f"   ⏱️ До дедлайна: {time_left.total_seconds():.0f} секунд")
            
            # Отправка через очередь или напрямую
            if self.message_queue:
                logger.info(f"   ➡️ Добавляю в очередь...")
                await self.message_queue.put((task['user_id'], message))
                logger.info(f"   ✅ Добавлено в очередь, размер: {self.message_queue.qsize()}")
            else:
                logger.info(f"   ➡️ Отправляю напрямую...")
                await self.bot.send_message(chat_id=task['user_id'], text=message)
                logger.info(f"   ✅ Отправлено напрямую")
                
            logger.info(f"✅ НАПОМИНАНИЕ ОТПРАВЛЕНО: задача {task['id']}")
                
        except Exception as e:
            logger.error(f"❌ ОШИБКА ПРИ ОТПРАВКЕ НАПОМИНАНИЯ: {e}")
            logger.error(traceback.format_exc())
    
    def stop(self):
        """Остановка планировщика"""
        try:
            if hasattr(self, 'scheduler') and self.scheduler.running:
                self.scheduler.shutdown()
                logger.info("🛑 Планировщик остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке: {e}")
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
            timezone: часовой пояс
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
        
        logger.info("="*60)
        logger.info("🆕 ПЛАНИРОВЩИК ИНИЦИАЛИЗИРОВАН")
        logger.info(f"📅 Часовой пояс: {timezone}")
        logger.info(f"📨 Очередь сообщений: {'✅ Есть' if message_queue else '❌ НЕТ'}")
        logger.info(f"🤖 Бот передан: {'✅ Да' if bot else '❌ Нет'}")
        logger.info("="*60)
    
    def _make_aware(self, dt):
        """
        Преобразует наивное datetime в timezone-aware
        
        Args:
            dt: datetime объект (может быть наивным или aware)
        
        Returns:
            timezone-aware datetime
        """
        if dt.tzinfo is None:
            return self.timezone.localize(dt)
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
            now = datetime.now(self.timezone)
            logger.info("="*70)
            logger.info(f"🔍 ПРОВЕРКА НАПОМИНАНИЙ #{self.check_count} в {now.strftime('%H:%M:%S')}")
            
            # Получаем ВСЕ активные задачи
            all_tasks = self.db.get_all_active_tasks()
            
            logger.info(f"📋 Всего активных задач: {len(all_tasks)}")
            
            for task in all_tasks:
                task_id = task['id']
                deadline_naive = datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S')
                deadline = self._make_aware(deadline_naive)
                
                # Вычисляем, сколько времени осталось
                time_left = deadline - now
                minutes_left = int(time_left.total_seconds() / 60)
                hours_left = int(time_left.total_seconds() / 3600)
                days_left = time_left.days
                
                logger.info(f"📋 Задача #{task_id}: осталось {days_left} д {hours_left} ч {minutes_left} мин | "
                           f"напоминания: 3д:{task.get('reminder_3d',0)} 24ч:{task.get('reminder_24h',0)} "
                           f"1ч:{task.get('reminder_1h',0)} 5м:{task.get('reminder_5m',0)}")
                
                # Проверяем напоминание за 5 минут
                if not task.get('reminder_5m', False) and 0 < minutes_left <= 5:
                    logger.info(f"   ✅ НУЖНО: напоминание за 5 минут")
                    await self._send_reminder_internal(task, '5m')
                    self.db.mark_reminder_sent(task_id, '5m')
                
                # Проверяем напоминание за 1 час (только если еще не отправлено и время подходит)
                elif not task.get('reminder_1h', False) and 0 < hours_left <= 1 and minutes_left <= 60:
                    logger.info(f"   ✅ НУЖНО: напоминание за 1 час")
                    await self._send_reminder_internal(task, '1h')
                    self.db.mark_reminder_sent(task_id, '1h')
                
                # Проверяем напоминание за 24 часа
                elif not task.get('reminder_24h', False) and 0 < days_left <= 1 and hours_left <= 24:
                    logger.info(f"   ✅ НУЖНО: напоминание за 24 часа")
                    await self._send_reminder_internal(task, '24h')
                    self.db.mark_reminder_sent(task_id, '24h')
                
                # Проверяем напоминание за 3 дня
                elif not task.get('reminder_3d', False) and 1 < days_left <= 3:
                    logger.info(f"   ✅ НУЖНО: напоминание за 3 дня")
                    await self._send_reminder_internal(task, '3d')
                    self.db.mark_reminder_sent(task_id, '3d')
                
                else:
                    # Логируем, почему ничего не отправляем
                    if task.get('reminder_5m', False):
                        logger.info(f"   ⏺️ Напоминание за 5 минут уже отправлено")
                    elif task.get('reminder_1h', False):
                        logger.info(f"   ⏺️ Напоминание за 1 час уже отправлено")
                    elif task.get('reminder_24h', False):
                        logger.info(f"   ⏺️ Напоминание за 24 часа уже отправлено")
                    elif task.get('reminder_3d', False):
                        logger.info(f"   ⏺️ Напоминание за 3 дня уже отправлено")
                    elif days_left > 3:
                        logger.info(f"   ⏺️ Еще рано (до дедлайна >3 дней)")
                    elif days_left <= 0:
                        logger.info(f"   ⏺️ Дедлайн уже прошел, задача будет отмечена как просроченная")
                        # Здесь можно добавить логику для просроченных задач
                    else:
                        logger.info(f"   ⏺️ Не подходит под условия отправки")
            
            logger.info(f"✅ ПРОВЕРКА #{self.check_count} ЗАВЕРШЕНА")
            
        except Exception as e:
            logger.error(f"❌ ОШИБКА В check_reminders: {e}")
            logger.error(traceback.format_exc())
        logger.info("="*70)
    
    async def _send_reminder_internal(self, task: dict, reminder_type: str):
        """Внутренний метод отправки напоминаний"""
        try:
            logger.info(f"📨 _send_reminder_internal: задача {task['id']}, тип {reminder_type}")
            
            # Парсим дедлайн (он из БД всегда без timezone)
            deadline_naive = datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S')
            # Добавляем timezone
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
            logger.info(f"   До дедлайна осталось: {time_left.total_seconds():.0f} секунд")
            
            # Отправка через очередь или напрямую
            if self.message_queue:
                logger.info(f"   ➡️ Добавляю в очередь для пользователя {task['user_id']}")
                await self.message_queue.put((task['user_id'], message))
                logger.info(f"   ✅ Добавлено, размер очереди: {self.message_queue.qsize()}")
            else:
                logger.info(f"   ➡️ Отправляю напрямую пользователю {task['user_id']}")
                await self.bot.send_message(chat_id=task['user_id'], text=message)
                logger.info(f"   ✅ Отправлено напрямую")
                
        except Exception as e:
            logger.error(f"❌ ОШИБКА В _send_reminder_internal: {e}")
            logger.error(traceback.format_exc())
    
    def stop(self):
        """Остановка планировщика"""
        try:
            if hasattr(self, 'scheduler') and self.scheduler.running:
                self.scheduler.shutdown()
                logger.info("🛑 Планировщик остановлен")
        except Exception as e:
            logger.error(f"Ошибка при остановке: {e}")
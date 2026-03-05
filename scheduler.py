import logging
import sys
import traceback
import asyncio
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('scheduler.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

class ReminderScheduler:
    def __init__(self, db, bot, timezone='Europe/Minsk', message_queue=None):
        self.db = db
        self.bot = bot
        self.timezone_str = timezone
        self.timezone = pytz.timezone(timezone)
        self.message_queue = message_queue
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.check_count = 0
        
        logger.info("="*60)
        logger.info("🆕 ПЛАНИРОВЩИК ИНИЦИАЛИЗИРОВАН")
        logger.info(f"📅 Часовой пояс: {timezone}")
        logger.info("="*60)
    
    def _make_aware(self, dt):
        if dt.tzinfo is None:
            return self.timezone.localize(dt)
        return dt.astimezone(self.timezone)
    
    def start(self):
        try:
            self.scheduler.add_job(
                self.check_reminders,
                IntervalTrigger(minutes=1),
                id='check_reminders',
                replace_existing=True,
                max_instances=1
            )
            self.scheduler.start()
            logger.info("✅ ПЛАНИРОВЩИК ЗАПУЩЕН")
        except Exception as e:
            logger.error(f"❌ ОШИБКА ЗАПУСКА: {e}")
    
    async def check_reminders(self):
        self.check_count += 1
        try:
            now = datetime.now(self.timezone)
            logger.info(f"🔍 ПРОВЕРКА #{self.check_count} в {now.strftime('%H:%M:%S')}")
            
            all_tasks = self.db.get_all_active_tasks()
            
            for task in all_tasks:
                try:
                    task_id = task['id']
                    user_id = task['user_id']
                    deadline_naive = datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S')
                    deadline = self._make_aware(deadline_naive)
                    
                    time_left = deadline - now
                    minutes_left = int(time_left.total_seconds() / 60)
                    hours_left = int(time_left.total_seconds() / 3600)
                    days_left = time_left.days
                    
                    # Проверяем напоминания
                    if not task.get('reminder_5m', False) and 0 < minutes_left <= 5:
                        await self._send_reminder(task, '5m')
                        self.db.mark_reminder_sent(task_id, '5m')
                    
                    elif not task.get('reminder_1h', False) and 0 < hours_left <= 1:
                        await self._send_reminder(task, '1h')
                        self.db.mark_reminder_sent(task_id, '1h')
                    
                    elif not task.get('reminder_24h', False) and 0 < days_left <= 1:
                        await self._send_reminder(task, '24h')
                        self.db.mark_reminder_sent(task_id, '24h')
                    
                    elif not task.get('reminder_3d', False) and 1 < days_left <= 3:
                        await self._send_reminder(task, '3d')
                        self.db.mark_reminder_sent(task_id, '3d')
                        
                except Exception as e:
                    logger.error(f"Ошибка обработки задачи {task.get('id')}: {e}")
                    
        except Exception as e:
            logger.error(f"Ошибка в check_reminders: {e}")
    
    async def _send_reminder(self, task: dict, reminder_type: str):
        try:
            deadline = datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S')
            deadline = self._make_aware(deadline)
            now = datetime.now(self.timezone)
            time_left = deadline - now
            
            if reminder_type == '3d':
                days = time_left.days
                time_text = f"{days} дня" if days % 10 == 1 else f"{days} дней"
            elif reminder_type == '24h':
                time_text = "24 часа"
            elif reminder_type == '1h':
                time_text = "1 час"
            else:
                minutes = int(time_left.total_seconds() / 60)
                time_text = f"{minutes} минут"
            
            # Добавляем теги в напоминание
            tags_text = ""
            if task.get('tags'):
                tags = eval(task['tags']) if isinstance(task['tags'], str) else task['tags']
                if tags:
                    tags_text = f"\n🏷️ #{', #'.join(tags)}"
            
            message = (
                f"⏰ **НАПОМИНАНИЕ!**\n\n"
                f"📌 **Задача:** {task['task_text']}{tags_text}\n"
                f"⏳ **Осталось:** {time_text}\n"
                f"📅 **Дедлайн:** {deadline.strftime('%d.%m.%Y %H:%M')}"
            )
            
            if self.message_queue:
                await self.message_queue.put((task['user_id'], message))
            else:
                await self.bot.send_message(chat_id=task['user_id'], text=message)
                
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")
    
    def stop(self):
        if hasattr(self, 'scheduler') and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("🛑 Планировщик остановлен")
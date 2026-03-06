import logging
import sys
import asyncio
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ReminderScheduler:
    def __init__(self, db, bot, timezone='Europe/Minsk', message_queue=None):
        self.db = db
        self.bot = bot
        self.timezone = pytz.timezone(timezone)
        self.message_queue = message_queue
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.check_count = 0

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
                replace_existing=True
            )
            self.scheduler.start()
            logger.info("✅ Планировщик запущен")
        except Exception as e:
            logger.error(f"❌ Ошибка запуска: {e}")

    async def check_reminders(self):
        self.check_count += 1
        try:
            now = datetime.now(self.timezone)
            all_tasks = self.db.get_all_active_tasks()

            for task in all_tasks:
                try:
                    deadline = self._make_aware(datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S'))
                    time_left = deadline - now
                    minutes_left = int(time_left.total_seconds() / 60)
                    hours_left = int(time_left.total_seconds() / 3600)
                    days_left = time_left.days

                    if not task['reminder_5m'] and 0 < minutes_left <= 5:
                        await self._send_reminder(task, '5m', time_left)
                        self.db.mark_reminder_sent(task['id'], '5m')
                    elif not task['reminder_1h'] and 0 < hours_left <= 1:
                        await self._send_reminder(task, '1h', time_left)
                        self.db.mark_reminder_sent(task['id'], '1h')
                    elif not task['reminder_24h'] and 0 < days_left <= 1:
                        await self._send_reminder(task, '24h', time_left)
                        self.db.mark_reminder_sent(task['id'], '24h')
                    elif not task['reminder_3d'] and 1 < days_left <= 3:
                        await self._send_reminder(task, '3d', time_left)
                        self.db.mark_reminder_sent(task['id'], '3d')
                except Exception as e:
                    logger.error(f"Ошибка задачи {task.get('id')}: {e}")
        except Exception as e:
            logger.error(f"Ошибка проверки: {e}")

    async def _send_reminder(self, task, rtype, time_left):
        try:
            deadline = self._make_aware(datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S'))
            
            # Формируем текст
            if rtype == '3d':
                time_text = f"{time_left.days} дня"
            elif rtype == '24h':
                time_text = "24 часа"
            elif rtype == '1h':
                time_text = "1 час"
            else:
                minutes = int(time_left.total_seconds() / 60)
                time_text = f"{minutes} минут"
            
            message = (
                f"⏰ **НАПОМИНАНИЕ!**\n\n"
                f"📌 **Задача:** {task['task_text']}\n"
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
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("🛑 Планировщик остановлен")
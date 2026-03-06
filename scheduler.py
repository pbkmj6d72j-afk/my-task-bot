import logging
import sys
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
    def __init__(self, db, bot, timezone='Europe/Minsk', message_queue=None):
        self.db = db
        self.bot = bot
        self.timezone = pytz.timezone(timezone)
        self.message_queue = message_queue
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.check_count = 0
        logger.info("="*50)
        logger.info("🆕 ПЛАНИРОВЩИК ИНИЦИАЛИЗИРОВАН")
        logger.info(f"📅 Часовой пояс: {timezone}")
        logger.info(f"📨 Очередь сообщений: {'✅ Есть' if message_queue else '❌ НЕТ'}")
        logger.info("="*50)

    def _make_aware(self, dt):
        if dt.tzinfo is None:
            return self.timezone.localize(dt)
        return dt.astimezone(self.timezone)

    def start(self):
        """Запуск планировщика"""
        try:
            self.scheduler.add_job(
                self.check_reminders,
                IntervalTrigger(minutes=1),
                id='check_reminders',
                replace_existing=True
            )
            self.scheduler.start()
            logger.info("✅ ПЛАНИРОВЩИК УСПЕШНО ЗАПУЩЕН")
            jobs = self.scheduler.get_jobs()
            logger.info(f"📋 Запланировано задач: {len(jobs)}")
            return True
        except Exception as e:
            logger.error(f"❌ ОШИБКА ЗАПУСКА: {e}")
            return False

    async def check_reminders(self):
        """Проверка напоминаний"""
        self.check_count += 1
        try:
            now = datetime.now(self.timezone)
            logger.info("="*60)
            logger.info(f"🔍 ПРОВЕРКА НАПОМИНАНИЙ #{self.check_count} в {now.strftime('%H:%M:%S')}")

            # Получаем все активные задачи
            all_tasks = self.db.get_all_active_tasks()
            logger.info(f"📋 Получено задач из БД: {len(all_tasks)}")

            if not all_tasks:
                logger.info("📭 Нет активных задач для проверки")
                return

            for task in all_tasks:
                try:
                    task_id = task['id']
                    user_id = task['user_id']
                    task_text = task['task_text'][:30]
                    
                    # Парсим дедлайн
                    deadline = self._make_aware(datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S'))
                    
                    # Вычисляем разницу
                    time_left = deadline - now
                    minutes_left = int(time_left.total_seconds() / 60)
                    hours_left = int(time_left.total_seconds() / 3600)
                    days_left = time_left.days
                    
                    # Получаем статусы напоминаний
                    reminder_5m = task.get('reminder_5m', False)
                    reminder_1h = task.get('reminder_1h', False)
                    reminder_24h = task.get('reminder_24h', False)
                    reminder_3d = task.get('reminder_3d', False)
                    
                    logger.info(f"📋 Задача #{task_id}: '{task_text}'")
                    logger.info(f"   📅 Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}")
                    logger.info(f"   ⏳ Осталось: {days_left}д {hours_left}ч {minutes_left}м")
                    logger.info(f"   🔄 Повторение: {task.get('recurring_type', 'нет')}")
                    logger.info(f"   🏷️ Статусы: 5м:{reminder_5m} 1ч:{reminder_1h} 24ч:{reminder_24h} 3д:{reminder_3d}")

                    # Проверяем напоминание за 5 минут
                    if not reminder_5m and 0 < minutes_left <= 5:
                        logger.info(f"   ✅ НУЖНО ОТПРАВИТЬ: напоминание за 5 минут")
                        await self._send_reminder(task, '5 минут')
                        self.db.mark_reminder_sent(task_id, '5m')
                    
                    # Проверяем напоминание за 1 час
                    elif not reminder_1h and 0 < hours_left <= 1:
                        logger.info(f"   ✅ НУЖНО ОТПРАВИТЬ: напоминание за 1 час")
                        await self._send_reminder(task, '1 час')
                        self.db.mark_reminder_sent(task_id, '1h')
                    
                    # Проверяем напоминание за 24 часа
                    elif not reminder_24h and 0 < days_left <= 1:
                        logger.info(f"   ✅ НУЖНО ОТПРАВИТЬ: напоминание за 24 часа")
                        await self._send_reminder(task, '24 часа')
                        self.db.mark_reminder_sent(task_id, '24h')
                    
                    # Проверяем напоминание за 3 дня
                    elif not reminder_3d and 1 < days_left <= 3:
                        logger.info(f"   ✅ НУЖНО ОТПРАВИТЬ: напоминание за 3 дня")
                        await self._send_reminder(task, '3 дня')
                        self.db.mark_reminder_sent(task_id, '3d')
                    
                    else:
                        logger.info(f"   ⏺️ Напоминание не требуется")

                except Exception as e:
                    logger.error(f"❌ Ошибка при обработке задачи {task.get('id', 'unknown')}: {e}")
                    continue

            logger.info(f"✅ ПРОВЕРКА #{self.check_count} ЗАВЕРШЕНА")
            logger.info("="*60)

        except Exception as e:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")

    async def _send_reminder(self, task, time_text):
        """Отправка напоминания"""
        try:
            deadline = self._make_aware(datetime.strptime(task['deadline'], '%Y-%m-%d %H:%M:%S'))
            
            # Добавляем информацию о повторении
            recurring_text = ""
            if task.get('recurring_type'):
                recurring_text = f"\n🔄 Повторяется: кажд{'' if task['recurring_interval']==1 else 'ые'} {task['recurring_interval']} {task['recurring_type']}"
            
            message = (
                f"⏰ **НАПОМИНАНИЕ!**\n\n"
                f"📌 **Задача:** {task['task_text']}\n"
                f"⏳ **Осталось:** {time_text}\n"
                f"📅 **Дедлайн:** {deadline.strftime('%d.%m.%Y %H:%M')}{recurring_text}"
            )

            logger.info(f"📨 Отправка напоминания пользователю {task['user_id']}")

            if self.message_queue:
                await self.message_queue.put((task['user_id'], message))
                logger.info(f"✅ Сообщение добавлено в очередь (размер: {self.message_queue.qsize()})")
            else:
                await self.bot.send_message(chat_id=task['user_id'], text=message)
                logger.info(f"✅ Сообщение отправлено напрямую")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки напоминания: {e}")

    def stop(self):
        """Остановка планировщика"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown()
                logger.info("🛑 Планировщик остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке: {e}")
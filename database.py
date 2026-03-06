import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pytz
import logging
import json

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="tasks.db", timezone='Europe/Minsk'):
        self.db_path = db_path
        self.timezone = pytz.timezone(timezone)
        self.init_db()

    def init_db(self):
        """Создание таблиц с поддержкой подзадач и множественных напоминаний"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблица пользователей
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица задач (основные)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_text TEXT NOT NULL,
                    deadline TIMESTAMP NOT NULL,
                    category TEXT DEFAULT 'Без категории',
                    priority TEXT DEFAULT 'Средний',
                    status TEXT DEFAULT 'active',
                    recurring_type TEXT DEFAULT NULL,
                    recurring_interval INTEGER DEFAULT 1,
                    parent_task_id INTEGER DEFAULT NULL,
                    has_subtasks BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reminder_3d BOOLEAN DEFAULT 0,
                    reminder_24h BOOLEAN DEFAULT 0,
                    reminder_1h BOOLEAN DEFAULT 0,
                    reminder_5m BOOLEAN DEFAULT 0,
                    reminder_custom TEXT DEFAULT NULL,
                    reminder_sent BOOLEAN DEFAULT 0
                )
            """)

            # Таблица подзадач
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subtasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    subtask_text TEXT NOT NULL,
                    completed BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
            """)

            # Таблица множественных напоминаний
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    reminder_time TIMESTAMP NOT NULL,
                    reminder_text TEXT DEFAULT NULL,
                    sent BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
            """)

            # Таблица категорий
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category_name TEXT NOT NULL,
                    color TEXT DEFAULT '#3498db',
                    UNIQUE(user_id, category_name)
                )
            """)

    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Добавление нового пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, username, first_name, last_name))

            # Добавляем стандартные категории с цветами
            default_categories = [
                ("Без категории", "#95a5a6"),
                ("Работа", "#e74c3c"),
                ("Личное", "#2ecc71"),
                ("Учеба", "#f39c12"),
                ("Здоровье", "#e91e63"),
                ("Финансы", "#3498db")
            ]
            
            for category, color in default_categories:
                try:
                    conn.execute("""
                        INSERT INTO categories (user_id, category_name, color)
                        VALUES (?, ?, ?)
                    """, (user_id, category, color))
                except sqlite3.IntegrityError:
                    pass

    def get_user_categories(self, user_id: int) -> List[Dict]:
        """Получение всех категорий пользователя с цветами"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM categories 
                WHERE user_id = ? ORDER BY category_name
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_category(self, user_id: int, category_name: str, color: str = "#3498db"):
        """Добавление новой категории с цветом"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO categories (user_id, category_name, color)
                    VALUES (?, ?, ?)
                """, (user_id, category_name, color))
                return True
            except sqlite3.IntegrityError:
                return False

    def _format_deadline_for_db(self, dt: datetime) -> str:
        """Конвертация datetime в строку для БД (UTC)"""
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(pytz.UTC)
        else:
            local_dt = self.timezone.localize(dt)
            dt_utc = local_dt.astimezone(pytz.UTC)
        return dt_utc.strftime('%Y-%m-%d %H:%M:%S')

    def _parse_deadline_from_db(self, deadline_str: str) -> datetime:
        """Конвертация строки из БД в datetime (локальное время)"""
        naive_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
        utc_dt = pytz.UTC.localize(naive_dt)
        return utc_dt.astimezone(self.timezone)

    def _calculate_next_deadline(self, current_deadline: datetime, recurring_type: str, interval: int = 1) -> datetime:
        """Вычисление следующего дедлайна для повторяющейся задачи"""
        if recurring_type == "day":
            return current_deadline + timedelta(days=interval)
        elif recurring_type == "week":
            return current_deadline + timedelta(weeks=interval)
        elif recurring_type == "month":
            # Простая аппроксимация месяца как 30 дней
            return current_deadline + timedelta(days=30 * interval)
        elif recurring_type == "year":
            return current_deadline + timedelta(days=365 * interval)
        else:
            return current_deadline

    def add_task(self, user_id: int, task_text: str, deadline_input,
                 category: str = "Без категории", priority: str = "Средний",
                 recurring_type: str = None, recurring_interval: int = 1) -> int:
        """Добавление новой задачи с поддержкой повторения"""
        
        # Парсинг даты
        if isinstance(deadline_input, str):
            try:
                dt = datetime.strptime(deadline_input, '%d.%m.%Y %H:%M')
            except ValueError:
                try:
                    dt = datetime.strptime(deadline_input, '%Y-%m-%d %H:%M:%S')
                except ValueError as e:
                    raise ValueError(f"Неверный формат даты: {deadline_input}") from e
        elif isinstance(deadline_input, datetime):
            dt = deadline_input
        else:
            raise TypeError(f"Неподдерживаемый тип: {type(deadline_input)}")

        deadline_str = self._format_deadline_for_db(dt)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tasks (user_id, task_text, deadline, category, priority, recurring_type, recurring_interval)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (user_id, task_text, deadline_str, category, priority, recurring_type, recurring_interval))
            task_id = cursor.fetchone()[0]
            logger.info(f"✅ Задача {task_id} создана. Повторение: {recurring_type if recurring_type else 'нет'}")
            return task_id

    def add_subtask(self, task_id: int, subtask_text: str) -> int:
        """Добавление подзадачи"""
        with sqlite3.connect(self.db_path) as conn:
            # Отмечаем, что у задачи есть подзадачи
            conn.execute("UPDATE tasks SET has_subtasks = 1 WHERE id = ?", (task_id,))
            
            cursor = conn.execute("""
                INSERT INTO subtasks (task_id, subtask_text)
                VALUES (?, ?)
                RETURNING id
            """, (task_id, subtask_text))
            subtask_id = cursor.fetchone()[0]
            logger.info(f"✅ Подзадача {subtask_id} добавлена к задаче {task_id}")
            return subtask_id

    def get_subtasks(self, task_id: int) -> List[Dict]:
        """Получение всех подзадач для задачи"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM subtasks 
                WHERE task_id = ?
                ORDER BY created_at
            """, (task_id,))
            return [dict(row) for row in cursor.fetchall()]

    def complete_subtask(self, subtask_id: int):
        """Отметить подзадачу как выполненную"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE subtasks SET completed = 1 WHERE id = ?", (subtask_id,))
            logger.info(f"✅ Подзадача {subtask_id} выполнена")

    def add_reminder(self, task_id: int, reminder_time: datetime, reminder_text: str = None):
        """Добавление дополнительного напоминания"""
        reminder_str = self._format_deadline_for_db(reminder_time)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO reminders (task_id, reminder_time, reminder_text)
                VALUES (?, ?, ?)
                RETURNING id
            """, (task_id, reminder_str, reminder_text))
            reminder_id = cursor.fetchone()[0]
            logger.info(f"⏰ Напоминание {reminder_id} добавлено к задаче {task_id} на {reminder_time}")
            return reminder_id

    def get_pending_reminders(self) -> List[Dict]:
        """Получение всех неотправленных напоминаний"""
        now = datetime.now(pytz.UTC)
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT r.*, t.user_id, t.task_text 
                FROM reminders r
                JOIN tasks t ON r.task_id = t.id
                WHERE r.sent = 0 AND r.reminder_time <= ?
            """, (now_str,))
            return [dict(row) for row in cursor.fetchall()]

    def mark_reminder_sent(self, reminder_id: int):
        """Отметить напоминание как отправленное"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))

    def get_user_tasks(self, user_id: int, status: str = None, category: str = None) -> List[Dict]:
        """Получение задач пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            query = "SELECT * FROM tasks WHERE user_id = ?"
            params = [user_id]

            if status:
                query += " AND status = ?"
                params.append(status)

            if category and category != "Все категории":
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY deadline"

            cursor = conn.execute(query, params)

            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                tasks.append(task)
            return tasks

    def get_all_active_tasks(self) -> List[Dict]:
        """Получение всех активных задач (для планировщика)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tasks 
                WHERE status = 'active'
                ORDER BY deadline
            """)
            rows = cursor.fetchall()
            logger.info(f"📊 get_all_active_tasks: найдено {len(rows)} активных задач")
            
            tasks = []
            for row in rows:
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                tasks.append(task)
            return tasks

    def get_overdue_tasks(self, user_id: int) -> List[Dict]:
        """Получение просроченных задач"""
        now = datetime.now(self.timezone)
        now_str = self._format_deadline_for_db(now)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tasks 
                WHERE user_id = ? AND status = 'active' AND deadline <= ?
                ORDER BY deadline
            """, (user_id, now_str))
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                tasks.append(task)
            return tasks

    def get_tasks_by_month(self, user_id: int, year: int, month: int) -> List[Dict]:
        """Получение задач за конкретный месяц"""
        start = datetime(year, month, 1, 0, 0, 0)
        if month == 12:
            end = datetime(year + 1, 1, 1, 0, 0, 0)
        else:
            end = datetime(year, month + 1, 1, 0, 0, 0)
        
        start_str = self._format_deadline_for_db(tz.localize(start))
        end_str = self._format_deadline_for_db(tz.localize(end))
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tasks 
                WHERE user_id = ? AND deadline >= ? AND deadline < ? AND status = 'active'
                ORDER BY deadline
            """, (user_id, start_str, end_str))
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                tasks.append(task)
            return tasks

    def get_task(self, task_id: int) -> Optional[Dict]:
        """Получение конкретной задачи"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                return task
            return None

    def complete_task(self, task_id: int) -> bool:
        """Отметить задачу как выполненную и создать повторяющуюся"""
        task = self.get_task(task_id)
        if not task:
            logger.error(f"❌ Задача {task_id} не найдена")
            return False

        with sqlite3.connect(self.db_path) as conn:
            # Отмечаем текущую задачу как выполненную
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
            logger.info(f"✅ Задача {task_id} отмечена как выполненная")

            # Если задача повторяющаяся, создаём новую
            if task['recurring_type']:
                next_deadline = self._calculate_next_deadline(
                    task['deadline_obj'],
                    task['recurring_type'],
                    task['recurring_interval']
                )
                
                cursor = conn.execute("""
                    INSERT INTO tasks (
                        user_id, task_text, deadline, category, priority, 
                        recurring_type, recurring_interval, parent_task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING id
                """, (
                    task['user_id'], task['task_text'],
                    self._format_deadline_for_db(next_deadline),
                    task['category'], task['priority'],
                    task['recurring_type'], task['recurring_interval'],
                    task_id
                ))
                new_task_id = cursor.fetchone()[0]
                logger.info(f"🔄 Создана новая повторяющаяся задача {new_task_id} на {next_deadline.strftime('%d.%m.%Y %H:%M')}")
            
            return True

    def delete_task(self, task_id: int):
        """Удалить задачу (подзадачи удалятся автоматически по FOREIGN KEY)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            logger.info(f"🗑 Задача {task_id} удалена")

    def update_task(self, task_id: int, **kwargs):
        """Обновление задачи"""
        allowed_fields = ['task_text', 'deadline', 'category', 'priority', 'recurring_type', 'recurring_interval']
        updates = []
        values = []
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                if key == 'deadline' and isinstance(value, datetime):
                    value = self._format_deadline_for_db(value)
                updates.append(f"{key} = ?")
                values.append(value)
                
        if not updates:
            return False
            
        values.append(task_id)
        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(query, values)
            logger.info(f"✏️ Задача {task_id} обновлена: {kwargs}")
            return True

    def mark_reminder_sent(self, task_id: int, reminder_type: str):
        """Отметить стандартное напоминание как отправленное"""
        field_map = {'3d': 'reminder_3d', '24h': 'reminder_24h', '1h': 'reminder_1h', '5m': 'reminder_5m'}
        field = field_map.get(reminder_type)
        if field:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(f"UPDATE tasks SET {field} = 1 WHERE id = ?", (task_id,))
                logger.info(f"📝 Напоминание {reminder_type} для задачи {task_id} отмечено")

    def get_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", (user_id,)).fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'active'", (user_id,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'", (user_id,)).fetchone()[0]

            # Просроченные
            now = datetime.now(self.timezone)
            now_str = self._format_deadline_for_db(now)
            overdue = conn.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE user_id = ? AND status = 'active' AND deadline <= ?
            """, (user_id, now_str)).fetchone()[0]

            cat_cursor = conn.execute("""
                SELECT category, COUNT(*) as count FROM tasks
                WHERE user_id = ? AND status = 'active'
                GROUP BY category
            """, (user_id,))
            categories = {row[0]: row[1] for row in cat_cursor.fetchall()}

            pri_cursor = conn.execute("""
                SELECT priority, COUNT(*) as count FROM tasks
                WHERE user_id = ? AND status = 'active'
                GROUP BY priority
            """, (user_id,))
            priorities = {row[0]: row[1] for row in pri_cursor.fetchall()}

            recurring = conn.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE user_id = ? AND status = 'active' AND recurring_type IS NOT NULL
            """, (user_id,)).fetchone()[0]

            return {
                'total': total,
                'active': active,
                'completed': completed,
                'overdue': overdue,
                'recurring': recurring,
                'completion_rate': (completed / total * 100) if total > 0 else 0,
                'categories': categories,
                'priorities': priorities
            }
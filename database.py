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
        """Создание таблиц с поддержкой повторяющихся задач"""
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

            # Таблица задач с полями для повторения
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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reminder_3d BOOLEAN DEFAULT 0,
                    reminder_24h BOOLEAN DEFAULT 0,
                    reminder_1h BOOLEAN DEFAULT 0,
                    reminder_5m BOOLEAN DEFAULT 0
                )
            """)

            # Таблица категорий
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category_name TEXT NOT NULL,
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

            # Добавляем стандартные категории
            default_categories = ["Без категории", "Работа", "Личное", "Учеба", "Здоровье", "Финансы"]
            for category in default_categories:
                try:
                    conn.execute("""
                        INSERT INTO categories (user_id, category_name)
                        VALUES (?, ?)
                    """, (user_id, category))
                except sqlite3.IntegrityError:
                    pass

    def get_user_categories(self, user_id: int) -> List[str]:
        """Получение всех категорий пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT category_name FROM categories 
                WHERE user_id = ? ORDER BY category_name
            """, (user_id,))
            return [row[0] for row in cursor.fetchall()]

    def add_category(self, user_id: int, category_name: str):
        """Добавление новой категории"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO categories (user_id, category_name)
                    VALUES (?, ?)
                """, (user_id, category_name))
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
        """Удалить задачу"""
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
        """Отметить, что напоминание отправлено"""
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

            # Статистика по повторяющимся задачам
            recurring = conn.execute("""
                SELECT COUNT(*) FROM tasks 
                WHERE user_id = ? AND status = 'active' AND recurring_type IS NOT NULL
            """, (user_id,)).fetchone()[0]

            return {
                'total': total,
                'active': active,
                'completed': completed,
                'recurring': recurring,
                'completion_rate': (completed / total * 100) if total > 0 else 0,
                'categories': categories,
                'priorities': priorities
            }
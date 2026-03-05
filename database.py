import sqlite3
from datetime import datetime
from typing import List, Dict, Optional
import pytz

class Database:
    def __init__(self, db_path="tasks.db", timezone='Europe/Minsk'):
        self.db_path = db_path
        self.timezone = pytz.timezone(timezone)
        self.init_db()
    
    def init_db(self):
        """Создание таблиц при первом запуске"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_text TEXT NOT NULL,
                    deadline TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reminder_3d BOOLEAN DEFAULT 0,
                    reminder_24h BOOLEAN DEFAULT 0,
                    reminder_1h BOOLEAN DEFAULT 0,
                    reminder_5m BOOLEAN DEFAULT 0
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    
    def _format_deadline_for_db(self, dt: datetime) -> str:
        """
        Преобразует datetime в строку для сохранения в БД
        Всегда сохраняет в UTC, чтобы избежать проблем с часовыми поясами
        """
        if dt.tzinfo is not None:
            # Если время с часовым поясом, конвертируем в UTC
            dt_utc = dt.astimezone(pytz.UTC)
        else:
            # Если время без пояса, считаем что оно в локальном поясе и конвертируем
            local_dt = self.timezone.localize(dt)
            dt_utc = local_dt.astimezone(pytz.UTC)
        
        # Возвращаем строку в формате ISO
        return dt_utc.strftime('%Y-%m-%d %H:%M:%S')
    
    def _parse_deadline_from_db(self, deadline_str: str) -> datetime:
        """
        Преобразует строку из БД в datetime с правильным часовым поясом
        """
        # Парсим строку в naive datetime
        naive_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
        
        # Так как в БД хранится UTC, добавляем UTC timezone
        utc_dt = pytz.UTC.localize(naive_dt)
        
        # Конвертируем в локальный часовой пояс
        local_dt = utc_dt.astimezone(self.timezone)
        
        return local_dt
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Добавление или обновление пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, username, first_name, last_name))
    
    def add_task(self, user_id: int, task_text: str, deadline_input) -> int:
        """
        Добавление новой задачи
        
        Args:
            user_id: ID пользователя
            task_text: текст задачи
            deadline_input: может быть строкой или datetime объектом
        """
        # Преобразуем входные данные в datetime объект
        if isinstance(deadline_input, str):
            # Если пришла строка, парсим её
            try:
                # Пробуем разные форматы
                for fmt in ['%Y-%m-%d %H:%M:%S', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M']:
                    try:
                        dt = datetime.strptime(deadline_input, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    raise ValueError(f"Не удалось распарсить дату: {deadline_input}")
            except Exception as e:
                raise ValueError(f"Ошибка парсинга даты: {e}")
        elif isinstance(deadline_input, datetime):
            dt = deadline_input
        else:
            raise TypeError(f"Неподдерживаемый тип для deadline: {type(deadline_input)}")
        
        # Форматируем для сохранения в БД
        deadline_str = self._format_deadline_for_db(dt)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tasks (user_id, task_text, deadline)
                VALUES (?, ?, ?)
                RETURNING id
            """, (user_id, task_text, deadline_str))
            task_id = cursor.fetchone()[0]
            return task_id
    
    def get_user_tasks(self, user_id: int, status: str = None) -> List[Dict]:
        """Получение задач пользователя с правильным отображением даты"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if status:
                cursor = conn.execute("""
                    SELECT * FROM tasks 
                    WHERE user_id = ? AND status = ?
                    ORDER BY deadline
                """, (user_id, status))
            else:
                cursor = conn.execute("""
                    SELECT * FROM tasks 
                    WHERE user_id = ?
                    ORDER BY 
                        CASE status 
                            WHEN 'active' THEN 1
                            WHEN 'completed' THEN 2
                        END,
                        deadline
                """, (user_id,))
            
            rows = cursor.fetchall()
            tasks = []
            for row in rows:
                task = dict(row)
                # Преобразуем дату из БД в локальный часовой пояс
                if 'deadline' in task:
                    deadline_dt = self._parse_deadline_from_db(task['deadline'])
                    # Сохраняем и строковое представление для отображения
                    task['deadline_display'] = deadline_dt.strftime('%d.%m.%Y %H:%M')
                    task['deadline_obj'] = deadline_dt
                tasks.append(task)
            
            return tasks
    
    def get_all_active_tasks(self) -> List[Dict]:
        """Получение ВСЕХ активных задач (для планировщика)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tasks 
                WHERE status = 'active'
                ORDER BY deadline
            """)
            
            rows = cursor.fetchall()
            tasks = []
            for row in rows:
                task = dict(row)
                # Для планировщика нам нужен datetime объект
                if 'deadline' in task:
                    task['deadline_dt'] = self._parse_deadline_from_db(task['deadline'])
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
                if 'deadline' in task:
                    task['deadline_dt'] = self._parse_deadline_from_db(task['deadline'])
                return task
            return None
    
    def complete_task(self, task_id: int):
        """Отметить задачу как выполненную"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE tasks 
                SET status = 'completed' 
                WHERE id = ?
            """, (task_id,))
    
    def delete_task(self, task_id: int):
        """Удалить задачу"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    
    def mark_reminder_sent(self, task_id: int, reminder_type: str):
        """Отметить, что напоминание отправлено"""
        field_map = {
            '3d': 'reminder_3d',
            '24h': 'reminder_24h',
            '1h': 'reminder_1h',
            '5m': 'reminder_5m'
        }
        field = field_map.get(reminder_type)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE tasks SET {field} = 1 WHERE id = ?", (task_id,))
    
    def get_tasks_for_reminder(self, reminder_type: str, current_time: str) -> List[Dict]:
        """
        Получение задач для напоминаний (для обратной совместимости)
        """
        field_map = {
            '3d': 'reminder_3d',
            '24h': 'reminder_24h',
            '1h': 'reminder_1h',
            '5m': 'reminder_5m'
        }
        field = field_map.get(reminder_type)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(f"""
                SELECT * FROM tasks 
                WHERE status = 'active' 
                AND {field} = 0
                AND datetime(deadline) <= datetime(?)
            """, (current_time,))
            
            return [dict(row) for row in cursor.fetchall()]
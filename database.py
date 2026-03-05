import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pytz
import json

class Database:
    def __init__(self, db_path="tasks.db", timezone='Europe/Minsk'):
        self.db_path = db_path
        self.timezone = pytz.timezone(timezone)
        self.init_db()
    
    def init_db(self):
        """Создание таблиц с новыми полями"""
        with sqlite3.connect(self.db_path) as conn:
            # Таблица задач с новыми полями
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_text TEXT NOT NULL,
                    deadline TIMESTAMP NOT NULL,
                    category TEXT DEFAULT 'Без категории',
                    priority TEXT DEFAULT 'Средний',
                    tags TEXT DEFAULT '[]',
                    recurring_type TEXT DEFAULT NULL,
                    recurring_interval INTEGER DEFAULT NULL,
                    parent_task_id INTEGER DEFAULT NULL,
                    status TEXT DEFAULT 'active',
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
                    color TEXT DEFAULT '#3498db',
                    UNIQUE(user_id, category_name)
                )
            """)
            
            # Таблица тегов
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    tag_name TEXT NOT NULL,
                    color TEXT DEFAULT '#95a5a6',
                    UNIQUE(user_id, tag_name)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    settings TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Добавление или обновление пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, username, first_name, last_name))
            
            # Добавляем стандартные категории
            default_categories = ["📚 Работа", "🏠 Личное", "🎓 Учеба", "❤️ Здоровье", "💰 Финансы"]
            default_colors = ["#e74c3c", "#2ecc71", "#f39c12", "#e91e63", "#3498db"]
            
            for i, category in enumerate(default_categories):
                try:
                    conn.execute("""
                        INSERT INTO categories (user_id, category_name, color)
                        VALUES (?, ?, ?)
                    """, (user_id, category, default_colors[i]))
                except sqlite3.IntegrityError:
                    pass
    
    def add_category(self, user_id: int, category_name: str, color: str = "#3498db"):
        """Добавление новой категории"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO categories (user_id, category_name, color)
                    VALUES (?, ?, ?)
                """, (user_id, category_name, color))
                return True
            except sqlite3.IntegrityError:
                return False
    
    def get_user_categories(self, user_id: int) -> List[Dict]:
        """Получение всех категорий пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM categories 
                WHERE user_id = ?
                ORDER BY category_name
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def add_tag(self, user_id: int, tag_name: str, color: str = "#95a5a6"):
        """Добавление нового тега"""
        with sqlite3.connect(self.db_path) as conn:
            try:
                conn.execute("""
                    INSERT INTO tags (user_id, tag_name, color)
                    VALUES (?, ?, ?)
                """, (user_id, tag_name, color))
                return True
            except sqlite3.IntegrityError:
                return False
    
    def get_user_tags(self, user_id: int) -> List[Dict]:
        """Получение всех тегов пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tags 
                WHERE user_id = ?
                ORDER BY tag_name
            """, (user_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def _format_deadline_for_db(self, dt: datetime) -> str:
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(pytz.UTC)
        else:
            local_dt = self.timezone.localize(dt)
            dt_utc = local_dt.astimezone(pytz.UTC)
        return dt_utc.strftime('%Y-%m-%d %H:%M:%S')
    
    def _parse_deadline_from_db(self, deadline_str: str) -> datetime:
        naive_dt = datetime.strptime(deadline_str, '%Y-%m-%d %H:%M:%S')
        utc_dt = pytz.UTC.localize(naive_dt)
        return utc_dt.astimezone(self.timezone)
    
    def add_task(self, user_id: int, task_text: str, deadline_input, 
                 category: str = "Без категории", priority: str = "Средний",
                 tags: List[str] = None, recurring_type: str = None,
                 recurring_interval: int = None) -> int:
        """Добавление новой задачи с расширенными параметрами"""
        
        if isinstance(deadline_input, str):
            try:
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
            raise TypeError(f"Неподдерживаемый тип: {type(deadline_input)}")
        
        deadline_str = self._format_deadline_for_db(dt)
        tags_json = json.dumps(tags or [])
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tasks (
                    user_id, task_text, deadline, category, priority, 
                    tags, recurring_type, recurring_interval
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
            """, (user_id, task_text, deadline_str, category, priority, 
                  tags_json, recurring_type, recurring_interval))
            task_id = cursor.fetchone()[0]
            
            # Добавляем новые теги в общий список
            if tags:
                for tag in tags:
                    try:
                        conn.execute("""
                            INSERT INTO tags (user_id, tag_name)
                            VALUES (?, ?)
                        """, (user_id, tag))
                    except sqlite3.IntegrityError:
                        pass
            
            return task_id
    
    def get_user_tasks(self, user_id: int, status: str = None, 
                       category: str = None, tag: str = None,
                       from_date: datetime = None, to_date: datetime = None) -> List[Dict]:
        """Получение задач с расширенной фильтрацией"""
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
            
            if tag:
                query += " AND tags LIKE ?"
                params.append(f'%"{tag}"%')
            
            if from_date:
                from_str = self._format_deadline_for_db(from_date)
                query += " AND deadline >= ?"
                params.append(from_str)
            
            if to_date:
                to_str = self._format_deadline_for_db(to_date)
                query += " AND deadline <= ?"
                params.append(to_str)
            
            query += """ ORDER BY 
                CASE priority 
                    WHEN 'Высокий' THEN 1 
                    WHEN 'Средний' THEN 2 
                    WHEN 'Низкий' THEN 3 
                    ELSE 4 
                END, 
                deadline"""
            
            cursor = conn.execute(query, params)
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['deadline_obj'] = self._parse_deadline_from_db(task['deadline'])
                task['tags'] = json.loads(task['tags'])
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
                task['tags'] = json.loads(task['tags'])
                return task
            return None
    
    def update_task(self, task_id: int, **kwargs) -> bool:
        """Обновление задачи"""
        allowed_fields = ['task_text', 'deadline', 'category', 'priority', 
                         'tags', 'recurring_type', 'recurring_interval', 'status']
        updates = []
        values = []
        
        for key, value in kwargs.items():
            if key in allowed_fields:
                if key == 'deadline' and isinstance(value, datetime):
                    value = self._format_deadline_for_db(value)
                elif key == 'tags' and isinstance(value, list):
                    value = json.dumps(value)
                updates.append(f"{key} = ?")
                values.append(value)
        
        if not updates:
            return False
        
        values.append(task_id)
        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(query, values)
            return True
    
    def complete_task(self, task_id: int):
        """Отметить задачу как выполненную и создать повторяющуюся"""
        task = self.get_task(task_id)
        if not task:
            return False
        
        with sqlite3.connect(self.db_path) as conn:
            # Отмечаем текущую задачу как выполненную
            conn.execute("UPDATE tasks SET status = 'completed' WHERE id = ?", (task_id,))
            
            # Если задача повторяющаяся, создаем новую
            if task['recurring_type']:
                next_deadline = self._calculate_next_deadline(
                    task['deadline_obj'], 
                    task['recurring_type'],
                    task['recurring_interval']
                )
                
                conn.execute("""
                    INSERT INTO tasks (
                        user_id, task_text, deadline, category, priority, 
                        tags, recurring_type, recurring_interval, parent_task_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task['user_id'], task['task_text'],
                    self._format_deadline_for_db(next_deadline),
                    task['category'], task['priority'],
                    json.dumps(task['tags']),
                    task['recurring_type'], task['recurring_interval'],
                    task['parent_task_id'] or task_id
                ))
            
            return True
    
    def _calculate_next_deadline(self, current: datetime, rtype: str, interval: int) -> datetime:
        """Вычисление следующего дедлайна для повторяющейся задачи"""
        if rtype == 'day':
            return current + timedelta(days=interval)
        elif rtype == 'week':
            return current + timedelta(weeks=interval)
        elif rtype == 'month':
            # Простое приближение для месяца
            return current + timedelta(days=30 * interval)
        return current
    
    def get_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            # Общее количество
            total = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ?", 
                                (user_id,)).fetchone()[0]
            
            # По статусам
            active = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'active'",
                                 (user_id,)).fetchone()[0]
            completed = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND status = 'completed'",
                                    (user_id,)).fetchone()[0]
            
            # По категориям
            cat_cursor = conn.execute("""
                SELECT category, COUNT(*) as count 
                FROM tasks WHERE user_id = ? AND status = 'active'
                GROUP BY category
            """, (user_id,))
            categories = {row[0]: row[1] for row in cat_cursor.fetchall()}
            
            # По приоритетам
            pri_cursor = conn.execute("""
                SELECT priority, COUNT(*) as count 
                FROM tasks WHERE user_id = ? AND status = 'active'
                GROUP BY priority
            """, (user_id,))
            priorities = {row[0]: row[1] for row in pri_cursor.fetchall()}
            
            return {
                'total': total,
                'active': active,
                'completed': completed,
                'completion_rate': (completed / total * 100) if total > 0 else 0,
                'categories': categories,
                'priorities': priorities
            }
    
    def snooze_task(self, task_id: int, minutes: int):
        """Отложить задачу на указанное количество минут"""
        task = self.get_task(task_id)
        if not task:
            return False
        
        new_deadline = task['deadline_obj'] + timedelta(minutes=minutes)
        return self.update_task(task_id, deadline=new_deadline)
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

class Database:
    def __init__(self, db_path="tasks.db"):
        self.db_path = db_path
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
    
    def add_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Добавление или обновление пользователя"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (user_id, username, first_name, last_name))
    
    def add_task(self, user_id: int, task_text: str, deadline: str) -> int:
        """Добавление новой задачи"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO tasks (user_id, task_text, deadline)
                VALUES (?, ?, ?)
                RETURNING id
            """, (user_id, task_text, deadline))
            task_id = cursor.fetchone()[0]
            return task_id
    
    def get_user_tasks(self, user_id: int, status: str = None) -> List[Dict]:
        """Получение задач пользователя"""
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
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_active_tasks(self) -> List[Dict]:
        """Получение ВСЕХ активных задач (для планировщика)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM tasks 
                WHERE status = 'active'
                ORDER BY deadline
            """)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_task(self, task_id: int) -> Optional[Dict]:
        """Получение конкретной задачи"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
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
        Получение задач для напоминаний (оставлено для обратной совместимости)
        reminder_type: '3d', '24h', '1h', '5m'
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
# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import requests
import sqlite3
import uuid


class TaskManager:
    def __init__(self, db_path='tasks.db'):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                analysis_id TEXT,
                output_dir TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def create_task(self, analysis_id, output_dir):
        task_id = str(uuid.uuid4())
        conn = self._get_connection()
        conn.execute('''
            INSERT INTO tasks (task_id, status, analysis_id, output_dir)
            VALUES (?, ?, ?, ?)
        ''', (task_id, 'running', analysis_id, output_dir))
        conn.commit()
        conn.close()
        return task_id

    def update_task_status(self, task_id, status):
        conn = self._get_connection()
        conn.execute('''
            UPDATE tasks SET status = ? WHERE task_id = ?
        ''', (status, task_id))
        conn.commit()
        conn.close()


def create_task(url, server_id: str, server_status: str, tool_name: str):
    data = {
        'server_id': server_id,
        'server_status': server_status,
        'tool_name': tool_name
    }
    response = requests.post(url, data=data)
    return response


def update_task(url, server_id: str, server_status: str, server_file_path: str, tool_result: str):
    data = {
        'server_id': server_id,
        'server_status': server_status,
        'server_file_path': server_file_path,
        'tool_result': tool_result,
    }
    response = requests.post(url, data=data)
    return response

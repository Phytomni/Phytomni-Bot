# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2025. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
import asyncio
import sqlite3
import uuid
from random import uniform
from typing import List

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR


class TaskManager:
    def __init__(self, db_path='server_tasks.db'):
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

    def create_task(self):
        task_id = str(uuid.uuid4())
        conn = self._get_connection()
        conn.execute('''
            INSERT INTO tasks (task_id, status, analysis_id, output_dir)
            VALUES (?, ?, ?, ?)
        ''', (task_id, 'running', 'unupdated', 'unupdated'))
        conn.commit()
        conn.close()
        return task_id

    def update_task(self, task_id, status, analysis_id, output_dir):
        conn = self._get_connection()
        conn.execute('''
            UPDATE tasks
            SET status = ?, analysis_id = ?, output_dir = ? WHERE task_id = ?
        ''', (status, task_id, analysis_id, output_dir))
        conn.commit()
        conn.close()


async def create_task(url,
                      server_id: str,
                      server_status: str,
                      tool_name: str,
                      timeout: int = 60,
                      retriable_codes: List[int] = [429, 500, 502, 503, 504],
                      max_retries: int = 5):
    data = {
        'server_id': server_id,
        'server_status': server_status,
        'tool_name': tool_name
    }
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url,
                    data=data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to rerank: {str(e)}"
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e


async def update_task(url,
                      server_id: str,
                      server_status: str,
                      server_file_path: str,
                      tool_result: str,
                      timeout: int = 60,
                      retriable_codes: List[int] = [429, 500, 502, 503, 504],
                      max_retries: int = 5):
    data = {
        'server_id': server_id,
        'server_status': server_status,
        'server_file_path': server_file_path,
        'tool_result': tool_result,
    }
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    url,
                    data=data,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()

            except HTTPStatusError as e:
                if (
                    hasattr(e, 'response') and
                    e.response is not None and
                    e.response.status_code in retriable_codes and
                    attempt < max_retries
                ):
                    wait_time = (2 ** attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Failed to rerank: {str(e)}"
                )) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5 ** attempt)
                    continue
                raise McpError(ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Network error: {str(e)}"
                )) from e

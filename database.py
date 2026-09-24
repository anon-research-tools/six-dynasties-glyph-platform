import sqlite3
import os
import pandas as pd
import logging
from contextlib import contextmanager
import threading
from datetime import datetime

# 数据库路径：相對當前模塊（跨平台），可用 SIX_DYN_STUDENTS_DB 環境變數覆蓋
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get('SIX_DYN_STUDENTS_DB') or \
    os.path.join(_MODULE_DIR, '網頁部署', 'students.db')

# 注意：不再使用全局 db_lock 串行化所有操作。
# WAL 模式下 SQLite 自己處理讀寫併發；業務層串行化反而讓讀請求互相阻塞。

import time as _time_mod
from functools import wraps as _wraps

def retry_on_locked(max_attempts=3, base_delay=0.1):
    """裝飾器：捕獲 'database is locked/busy' 錯誤並重試。
    busy_timeout=5000 通常已足夠，這是極端場景的兜底。
    """
    def decorator(fn):
        @_wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if 'locked' not in msg and 'busy' not in msg:
                        raise
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = base_delay * (5 ** attempt)  # 0.1s, 0.5s, 2.5s
                        logger.warning(f"[retry] {fn.__name__} attempt {attempt+1} locked, retrying in {delay}s")
                        _time_mod.sleep(delay)
            logger.error(f"[retry] {fn.__name__} exhausted {max_attempts} attempts: {last_exc}")
            raise last_exc
        return wrapper
    return decorator

# 设置日志
logger = logging.getLogger(__name__)

def get_db_path():
    """获取数据库文件路径"""
    return DB_PATH

@contextmanager
def get_db_connection():
    """获取数据库连接的上下文管理器。
    WAL + busy_timeout 配置让多人并发写入稳定，不再需要业务层加锁。
    """
    conn = None
    try:
        # timeout= 会被后面的 PRAGMA busy_timeout 覆盖，不再指定
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL：多读单写并行
        conn.execute('PRAGMA journal_mode=WAL')
        # 写冲突时等待 5 秒（单次写入仅 10-30ms，足够让步）
        conn.execute('PRAGMA busy_timeout=5000')
        # 性能优化
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-20000')    # 20MB
        conn.execute('PRAGMA temp_store=MEMORY')
        yield conn
    except Exception as e:
        logger.error(f"数据库连接错误: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def init_database():
    """初始化数据库"""
    try:
        with get_db_connection() as conn:
            # 创建学生标注数据表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS student_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    char_id TEXT NOT NULL,
                    Situation TEXT DEFAULT '',
                    where_field TEXT DEFAULT '',  -- 使用where_field避免与SQL关键字冲突
                    operation TEXT DEFAULT '',
                    remark TEXT DEFAULT '',       -- 备注字段
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, char_id)
                )
            ''')
            
            # 创建文件大小监控表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS file_monitoring (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    record_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 任務分配表（按學生記錄每個 keyword 的狀態）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS task_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    frequency INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    completed_by TEXT DEFAULT '',
                    previous_owner TEXT DEFAULT '',
                    UNIQUE(student_id, keyword)
                )
            ''')

            # 任務池（待分配 keyword 的集合）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS task_pool (
                    keyword TEXT PRIMARY KEY,
                    frequency INTEGER DEFAULT 0,
                    previous_owner TEXT DEFAULT '',
                    previous_student_id TEXT DEFAULT ''
                )
            ''')

            # 行為日誌（登錄/心跳/寫入等事件）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 賬戶設置（啟用狀態、顯示名、備註）
            conn.execute('''
                CREATE TABLE IF NOT EXISTS account_settings (
                    student_id TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    display_name TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 默認字圖頁的「選中寫卷分類」。
            # 按賬號與字頭保存，避免不同學生或不同字頭的精選分組互相污染。
            conn.execute('''
                CREATE TABLE IF NOT EXISTS manuscript_collections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    name TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(student_id, keyword, name)
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS manuscript_collection_items (
                    collection_id INTEGER NOT NULL,
                    manuscript_id TEXT NOT NULL,
                    position INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(collection_id, manuscript_id),
                    FOREIGN KEY(collection_id) REFERENCES manuscript_collections(id) ON DELETE CASCADE
                )
            ''')

            # 索引（既覆蓋現有 DB，也讓冷重建後性能不退化）
            conn.execute('CREATE INDEX IF NOT EXISTS idx_student_char ON student_annotations(student_id, char_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_file_monitoring ON file_monitoring(file_path, record_time)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_task_student ON task_assignments(student_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_task_keyword ON task_assignments(keyword)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_task_status ON task_assignments(student_id, status)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_activity_student ON activity_log(student_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_activity_time ON activity_log(created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pool_freq ON task_pool(frequency DESC)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_manuscript_collections_lookup ON manuscript_collections(student_id, keyword)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_manuscript_collection_items_collection ON manuscript_collection_items(collection_id, position)')

            conn.commit()
            logger.info("数据库初始化完成")

            # 啟動時清理 90 天前的活動日誌，防止無限增長
            try:
                cur = conn.execute(
                    "DELETE FROM activity_log WHERE created_at < datetime('now', '-90 days')"
                )
                pruned = cur.rowcount
                conn.commit()
                if pruned > 0:
                    logger.info(f"activity_log 清理 {pruned} 條超過 90 天的記錄")
            except Exception as _e:
                logger.warning(f"activity_log 清理失敗: {_e}")
    except Exception as e:
        logger.error(f"数据库初始化失败: {str(e)}")
        raise

@retry_on_locked()
def add_student_annotation(student_id, char_id, situation='', where_field='', operation='', remark=''):
    """添加或更新学生标注数据"""
    try:
        with get_db_connection() as conn:
            # 使用 INSERT OR REPLACE 确保数据的一致性
            conn.execute('''
                INSERT OR REPLACE INTO student_annotations
                (student_id, char_id, Situation, where_field, operation, remark, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', (student_id, char_id, situation, where_field, operation, remark))
            conn.commit()
            logger.info(f"学生 {student_id} 标注字符 {char_id} 已保存到数据库")
            return True
    except sqlite3.OperationalError:
        raise  # 交給 @retry_on_locked
    except Exception as e:
        logger.error(f"保存学生标注数据失败: {str(e)}")
        return False

def get_student_annotations(student_id):
    """获取学生的所有标注数据"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT char_id, Situation, where_field as "where", operation, remark as 備註
                FROM student_annotations 
                WHERE student_id = ?
            ''', (student_id,))
            rows = cursor.fetchall()
            # 转换为列表格式，与原有CSV处理逻辑保持一致
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"获取学生标注数据失败: {str(e)}")
        return []


def _clean_manuscript_ids(manuscript_ids):
    clean = []
    seen = set()
    for manuscript_id in manuscript_ids or []:
        mid = str(manuscript_id or '').strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        clean.append(mid)
    return clean


@retry_on_locked()
def save_manuscript_collection(student_id, keyword, name, manuscript_ids, note=''):
    """保存或覆蓋一個字頭下的寫卷分類。"""
    student_id = str(student_id or '').strip()
    keyword = str(keyword or '').strip()
    name = str(name or '').strip()
    note = str(note or '').strip()
    manuscript_ids = _clean_manuscript_ids(manuscript_ids)
    if not student_id or not keyword or not name or not manuscript_ids:
        return None

    try:
        with get_db_connection() as conn:
            row = conn.execute(
                '''
                SELECT id FROM manuscript_collections
                WHERE student_id = ? AND keyword = ? AND name = ?
                ''',
                (student_id, keyword, name)
            ).fetchone()
            if row:
                collection_id = row['id']
                conn.execute(
                    '''
                    UPDATE manuscript_collections
                    SET note = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    ''',
                    (note, collection_id)
                )
            else:
                cursor = conn.execute(
                    '''
                    INSERT INTO manuscript_collections (student_id, keyword, name, note)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (student_id, keyword, name, note)
                )
                collection_id = cursor.lastrowid

            conn.execute(
                'DELETE FROM manuscript_collection_items WHERE collection_id = ?',
                (collection_id,)
            )
            conn.executemany(
                '''
                INSERT INTO manuscript_collection_items
                (collection_id, manuscript_id, position)
                VALUES (?, ?, ?)
                ''',
                [(collection_id, mid, idx) for idx, mid in enumerate(manuscript_ids)]
            )
            conn.commit()
            return {
                'id': collection_id,
                'student_id': student_id,
                'keyword': keyword,
                'name': name,
                'note': note,
                'count': len(manuscript_ids),
                'manuscript_ids': manuscript_ids
            }
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"保存寫卷分類失敗: {str(e)}")
        return None


def get_manuscript_collections(student_id, keyword):
    """列出當前賬號在某字頭下保存的寫卷分類。"""
    student_id = str(student_id or '').strip()
    keyword = str(keyword or '').strip()
    if not student_id or not keyword:
        return []

    try:
        with get_db_connection() as conn:
            rows = conn.execute(
                '''
                SELECT c.id, c.student_id, c.keyword, c.name, c.note,
                       datetime(c.created_at, '+8 hours') AS created_at,
                       datetime(c.updated_at, '+8 hours') AS updated_at,
                       COUNT(i.manuscript_id) AS count
                FROM manuscript_collections c
                LEFT JOIN manuscript_collection_items i ON i.collection_id = c.id
                WHERE c.student_id = ? AND c.keyword = ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC, c.id DESC
                ''',
                (student_id, keyword)
            ).fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"讀取寫卷分類列表失敗: {str(e)}")
        return []


def get_manuscript_collection(student_id, keyword, collection_id):
    """讀取某個寫卷分類及其寫卷號。"""
    student_id = str(student_id or '').strip()
    keyword = str(keyword or '').strip()
    try:
        collection_id = int(collection_id)
    except (TypeError, ValueError):
        return None
    if not student_id or not keyword:
        return None

    try:
        with get_db_connection() as conn:
            row = conn.execute(
                '''
                SELECT id, student_id, keyword, name, note,
                       datetime(created_at, '+8 hours') AS created_at,
                       datetime(updated_at, '+8 hours') AS updated_at
                FROM manuscript_collections
                WHERE id = ? AND student_id = ? AND keyword = ?
                ''',
                (collection_id, student_id, keyword)
            ).fetchone()
            if not row:
                return None

            item_rows = conn.execute(
                '''
                SELECT manuscript_id
                FROM manuscript_collection_items
                WHERE collection_id = ?
                ORDER BY position ASC, manuscript_id ASC
                ''',
                (collection_id,)
            ).fetchall()

            result = dict(row)
            result['manuscript_ids'] = [item['manuscript_id'] for item in item_rows]
            result['count'] = len(result['manuscript_ids'])
            return result
    except Exception as e:
        logger.error(f"讀取寫卷分類失敗: {str(e)}")
        return None


@retry_on_locked()
def delete_manuscript_collection(student_id, keyword, collection_id):
    """刪除當前賬號的一個寫卷分類。"""
    student_id = str(student_id or '').strip()
    keyword = str(keyword or '').strip()
    try:
        collection_id = int(collection_id)
    except (TypeError, ValueError):
        return False
    if not student_id or not keyword:
        return False

    try:
        with get_db_connection() as conn:
            row = conn.execute(
                '''
                SELECT id FROM manuscript_collections
                WHERE id = ? AND student_id = ? AND keyword = ?
                ''',
                (collection_id, student_id, keyword)
            ).fetchone()
            if not row:
                return False
            conn.execute(
                'DELETE FROM manuscript_collection_items WHERE collection_id = ?',
                (collection_id,)
            )
            conn.execute(
                'DELETE FROM manuscript_collections WHERE id = ?',
                (collection_id,)
            )
            conn.commit()
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"刪除寫卷分類失敗: {str(e)}")
        return False

def get_student_annotations_by_char_ids(student_id, char_ids):
    """获取学生对特定字符列表的标注数据"""
    if not char_ids:
        return []

    try:
        with get_db_connection() as conn:
            # 动态构建 IN 查询
            placeholders = ','.join(['?'] * len(char_ids))
            sql = f'''
                SELECT char_id, Situation, where_field as "where", operation, remark as 備註
                FROM student_annotations
                WHERE student_id = ? AND char_id IN ({placeholders})
            '''
            params = [student_id] + list(char_ids)
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"获取学生标注数据失败: {str(e)}")
        return []

def get_all_annotations_by_char_ids(char_ids):
    """獲取所有賬號對特定字符的標注數據（用於顯示前人成果）"""
    if not char_ids:
        return {}

    try:
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(char_ids))
            sql = f'''
                SELECT student_id, char_id, Situation, where_field as "where",
                       operation, remark as 備註, created_at, updated_at
                FROM student_annotations
                WHERE char_id IN ({placeholders})
            '''
            cursor = conn.execute(sql, list(char_ids))
            rows = cursor.fetchall()

            # 按 char_id 分組，每個 char_id 下按 student_id 分組
            result = {}
            for row in rows:
                cid = row['char_id']
                sid = row['student_id']
                if cid not in result:
                    result[cid] = {}
                result[cid][sid] = {
                    'Situation': row['Situation'] or '',
                    'where': row['where'] or '',
                    'operation': row['operation'] or '',
                    '備註': row['備註'] or '',
                    'created_at': row['created_at'] or '',
                    'updated_at': row['updated_at'] or ''
                }
            return result
    except Exception as e:
        logger.error(f"獲取所有標注數據失敗: {str(e)}")
        return {}

def get_task_assignments_for_keywords(keywords):
    """按字頭查任務分配，用於把舊學生標注放回字頭上下文。"""
    clean_keywords = []
    seen = set()
    for keyword in keywords or []:
        kw = str(keyword or '').strip()
        if kw and kw not in seen:
            clean_keywords.append(kw)
            seen.add(kw)
    if not clean_keywords:
        return {}

    try:
        with get_db_connection() as conn:
            placeholders = ','.join(['?'] * len(clean_keywords))
            cursor = conn.execute(f'''
                SELECT student_id, keyword, status, assigned_at, completed_at,
                       completed_by, previous_owner
                FROM task_assignments
                WHERE keyword IN ({placeholders})
            ''', clean_keywords)
            result = {}
            for row in cursor.fetchall():
                sid = row['student_id']
                result.setdefault(sid, []).append(dict(row))
            return result
    except Exception as e:
        logger.error(f"按字頭查任務分配失敗: {str(e)}")
        return {}

def get_student_annotation(student_id, char_id):
    """获取学生对特定字符的标注数据"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT Situation, where_field as "where", operation, remark as 備註
                FROM student_annotations 
                WHERE student_id = ? AND char_id = ?
            ''', (student_id, char_id))
            row = cursor.fetchone()
            return dict(row) if row else {}
    except Exception as e:
        logger.error(f"获取学生标注数据失败: {str(e)}")
        return {}

@retry_on_locked()
def delete_student_annotation(student_id, char_id):
    """删除学生的特定标注数据"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                DELETE FROM student_annotations
                WHERE student_id = ? AND char_id = ?
            ''', (student_id, char_id))
            conn.commit()
            logger.info(f"学生 {student_id} 的字符 {char_id} 标注数据已删除")
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"删除学生标注数据失败: {str(e)}")
        return False

@retry_on_locked()
def record_file_size(file_path, file_size):
    """记录文件大小用于监控"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO file_monitoring (file_path, file_size)
                VALUES (?, ?)
            ''', (file_path, file_size))
            conn.commit()
            logger.info(f"文件大小记录已保存: {file_path} - {file_size} bytes")
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"记录文件大小失败: {str(e)}")

def get_file_size_history(file_path, limit=100):
    """获取文件大小历史记录"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT file_size, record_time
                FROM file_monitoring 
                WHERE file_path = ?
                ORDER BY record_time DESC
                LIMIT ?
            ''', (file_path, limit))
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"获取文件大小历史记录失败: {str(e)}")
        return []

def check_file_size_anomaly(file_path, current_size):
    """检查文件大小是否异常"""
    try:
        history = get_file_size_history(file_path, 10)  # 获取最近10条记录
        if len(history) < 2:
            return False  # 数据不足，无法判断
        
        # 计算历史平均大小
        avg_size = sum(record['file_size'] for record in history) / len(history)
        
        # 如果当前大小小于历史平均大小的50%，则认为异常
        if current_size < avg_size * 0.5:
            logger.warning(f"文件大小异常: {file_path} 当前大小 {current_size} 小于历史平均值 {avg_size} 的50%")
            return True
        return False
    except Exception as e:
        logger.error(f"检查文件大小异常失败: {str(e)}")
        return False

def migrate_from_csv_to_db():
    """从CSV文件迁移数据到数据库"""
    # 这个函数将在后续实现，用于将现有的CSV数据迁移到数据库
    pass

def export_student_data_to_csv(student_id, csv_file_path):
    """将学生的标注数据导出到CSV文件"""
    try:
        annotations = get_student_annotations(student_id)
        if not annotations:
            # 如果没有数据，创建空的CSV文件
            columns = ['char_id', 'Situation', 'where', 'operation', '備註']
            df = pd.DataFrame(columns=columns)
            df.to_csv(csv_file_path, index=False, encoding='utf-8-sig')
            return True
            
        # 转换为DataFrame并保存
        # 注：get_student_annotations 返回 list[dict]，直接遍历
        data = []
        for annotation in annotations:
            row = {
                'char_id': annotation.get('char_id', ''),
                'Situation': annotation.get('Situation', ''),
                'where': annotation.get('where', ''),
                'operation': annotation.get('operation', ''),
                '備註': annotation.get('備註', '')
            }
            data.append(row)
            
        df = pd.DataFrame(data)
        df.to_csv(csv_file_path, index=False, encoding='utf-8-sig')
        logger.info(f"学生 {student_id} 数据已导出到 {csv_file_path}")
        return True
    except Exception as e:
        logger.error(f"导出学生数据到CSV失败: {str(e)}")
        return False

# ======== 任務分配相關函數 ========

def get_student_tasks(student_id):
    """獲取學生的所有任務分配，按狀態和頻率排序（未完成在前）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT keyword, frequency, status, assigned_at, completed_at,
                       completed_by, previous_owner
                FROM task_assignments
                WHERE student_id = ?
                ORDER BY
                    CASE WHEN status = 'pending' THEN 0 ELSE 1 END,
                    frequency DESC
            ''', (student_id,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"獲取學生任務失敗: {str(e)}")
        return []

def get_student_task_stats(student_id):
    """獲取學生任務統計"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
                FROM task_assignments
                WHERE student_id = ?
            ''', (student_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {'total': 0, 'completed': 0, 'pending': 0}
    except Exception as e:
        logger.error(f"獲取學生任務統計失敗: {str(e)}")
        return {'total': 0, 'completed': 0, 'pending': 0}

def is_keyword_assigned(student_id, keyword):
    """檢查某個關鍵字是否分配給了該學生"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT COUNT(*) FROM task_assignments
                WHERE student_id = ? AND keyword = ?
            ''', (student_id, keyword))
            return cursor.fetchone()[0] > 0
    except Exception as e:
        logger.error(f"檢查任務分配失敗: {str(e)}")
        return False

@retry_on_locked()
def mark_task_completed(student_id, keyword):
    """標記任務為已完成"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE task_assignments
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP, completed_by = ?
                WHERE student_id = ? AND keyword = ?
            ''', (student_id, student_id, keyword))
            conn.commit()
            logger.info(f"任務標記完成: {student_id} - {keyword}")
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"標記任務完成失敗: {str(e)}")
        return False

@retry_on_locked()
def reset_task_status(student_id, keyword):
    """將已完成的任務重置為未完成"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE task_assignments
                SET status = 'pending', completed_at = NULL, completed_by = ''
                WHERE student_id = ? AND keyword = ?
            ''', (student_id, keyword))
            conn.commit()
            logger.info(f"任務重置: {student_id} - {keyword}")
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"重置任務失敗: {str(e)}")
        return False

def get_all_task_stats():
    """獲取所有賬號的任務統計（管理員用）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT student_id,
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
                FROM task_assignments
                GROUP BY student_id
                ORDER BY student_id
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"獲取所有任務統計失敗: {str(e)}")
        return []


# ======== 活動記錄函數 ========

@retry_on_locked()
def log_activity(student_id, action, detail=''):
    """記錄學生活動"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO activity_log (student_id, action, detail)
                VALUES (?, ?, ?)
            ''', (student_id, action, detail))
            conn.commit()
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"記錄活動失敗: {str(e)}")

def get_admin_overview():
    """獲取管理員概覽數據。附帶近 24 小時活動次數,超閾值的賬號 admin 面板會標紅警示爬蟲風險。"""
    try:
        with get_db_connection() as conn:
            # 每個賬號的任務統計 + 最近活動時間 + 近24h 活動計數
            cursor = conn.execute('''
                SELECT
                    t.student_id,
                    s.display_name,
                    s.enabled,
                    s.note,
                    COUNT(*) as total,
                    SUM(CASE WHEN t.status = 'completed' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN t.status = 'pending' THEN 1 ELSE 0 END) as pending,
                    SUM(t.frequency) as total_freq,
                    SUM(CASE WHEN t.status = 'completed' THEN t.frequency ELSE 0 END) as completed_freq,
                    SUM(CASE WHEN t.status = 'pending' THEN t.frequency ELSE 0 END) as pending_freq,
                    (SELECT datetime(MAX(a.created_at), '+8 hours') FROM activity_log a WHERE a.student_id = t.student_id) as last_active,
                    (SELECT COUNT(*) FROM activity_log a WHERE a.student_id = t.student_id AND a.created_at > datetime('now', '-24 hours')) as activity_24h
                FROM task_assignments t
                LEFT JOIN account_settings s ON t.student_id = s.student_id
                GROUP BY t.student_id
                ORDER BY t.student_id
            ''')
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"獲取管理員概覽失敗: {str(e)}")
        return []

def get_student_activity(student_id, limit=50):
    """獲取學生最近活動記錄（轉換為北京時間）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT action, detail,
                       datetime(created_at, '+8 hours') as created_at
                FROM activity_log
                WHERE student_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            ''', (student_id, limit))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"獲取學生活動失敗: {str(e)}")
        return []

@retry_on_locked()
def toggle_account(student_id, enabled):
    """啟用/禁用賬號"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE account_settings SET enabled = ?, updated_at = CURRENT_TIMESTAMP
                WHERE student_id = ?
            ''', (1 if enabled else 0, student_id))
            conn.commit()
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"切換賬號狀態失敗: {str(e)}")
        return False

ADMIN_ID = 'student_01'

def is_account_enabled(student_id):
    """檢查賬號是否啟用"""
    if student_id == ADMIN_ID:
        return True
    try:
        with get_db_connection() as conn:
            cursor = conn.execute(
                'SELECT enabled FROM account_settings WHERE student_id = ?', (student_id,))
            row = cursor.fetchone()
            return bool(row['enabled']) if row else True
    except Exception as e:
        logger.error(f"檢查賬號狀態失敗: {str(e)}")
        return True

@retry_on_locked()
def update_account_note(student_id, note):
    """更新賬號備註"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE account_settings SET note = ?, updated_at = CURRENT_TIMESTAMP
                WHERE student_id = ?
            ''', (note, student_id))
            conn.commit()
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"更新賬號備註失敗: {str(e)}")
        return False

@retry_on_locked()
def update_account_display_name(student_id, display_name):
    """更新賬號顯示名稱"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                UPDATE account_settings SET display_name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE student_id = ?
            ''', (display_name, student_id))
            conn.commit()
            return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"更新顯示名稱失敗: {str(e)}")
        return False


# ======== 任務池管理 ========

def get_pool_stats():
    """獲取任務池概覽"""
    try:
        with get_db_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM task_pool").fetchone()[0]
            total_freq = conn.execute("SELECT SUM(frequency) FROM task_pool").fetchone()[0] or 0
            has_prev = conn.execute("SELECT COUNT(*) FROM task_pool WHERE previous_owner != ''").fetchone()[0]
            return {'total': total, 'total_freq': total_freq, 'has_previous_work': has_prev}
    except Exception as e:
        logger.error(f"獲取池子統計失敗: {str(e)}")
        return {'total': 0, 'total_freq': 0, 'has_previous_work': 0}

def get_pool_keywords(limit=50, offset=0):
    """獲取池子中的關鍵字列表"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute("""
                SELECT keyword, frequency, previous_owner, previous_student_id
                FROM task_pool ORDER BY frequency DESC LIMIT ? OFFSET ?
            """, (limit, offset))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"獲取池子列表失敗: {str(e)}")
        return []

@retry_on_locked()
def assign_from_pool(student_id, count):
    """從池子中分配任務給學生，同時複製前人標注。
    用 BEGIN IMMEDIATE 序列化：同時兩個 admin 點分配不會搶到重複 keyword。"""
    try:
        with get_db_connection() as conn:
            # 提升為寫鎖，後續 SELECT/INSERT/DELETE 共享同一事務
            conn.execute('BEGIN IMMEDIATE')
            items = conn.execute("""
                SELECT keyword, frequency, previous_owner, previous_student_id
                FROM task_pool ORDER BY frequency DESC LIMIT ?
            """, (count,)).fetchall()

            if not items:
                conn.commit()
                return {'assigned': 0, 'copied_annotations': 0}

            assigned = 0
            copied = 0

            for item in items:
                kw = item['keyword']
                prev_sid = item['previous_student_id']

                # 創建任務分配
                conn.execute("""
                    INSERT OR IGNORE INTO task_assignments
                    (student_id, keyword, frequency, status, previous_owner)
                    VALUES (?, ?, ?, 'pending', ?)
                """, (student_id, kw, item['frequency'], item['previous_owner'] or ''))
                assigned += 1

                # 複製前人標注（只複製與該關鍵字相關的 char_id）
                if prev_sid and prev_sid != student_id:
                    # 從 characters.db 查出該關鍵字對應的 char_ids
                    # 必須走 get_char_db_connection 才能享受 WAL + busy_timeout
                    from char_database import get_char_db_connection
                    try:
                        with get_char_db_connection() as _cconn:
                            related_ids = [r[0] for r in _cconn.execute(
                                "SELECT char_id FROM characters WHERE ocr_txt=? OR cmp_txt=? OR txt=?",
                                (kw, kw, kw)).fetchall()]
                    except Exception as e:
                        logger.warning(f"查詢 {kw} 相關 char_id 失敗: {e}")
                        related_ids = []

                    if related_ids:
                        placeholders = ','.join(['?'] * len(related_ids))
                        prev_marks = conn.execute(f"""
                            SELECT char_id, Situation, where_field, operation, remark
                            FROM student_annotations
                            WHERE student_id = ? AND char_id IN ({placeholders})
                        """, [prev_sid] + related_ids).fetchall()

                        for mark in prev_marks:
                            has_content = any([mark['Situation'], mark['where_field'],
                                              mark['operation'], mark['remark']])
                            if has_content:
                                try:
                                    conn.execute("""
                                        INSERT OR IGNORE INTO student_annotations
                                        (student_id, char_id, Situation, where_field, operation, remark)
                                        VALUES (?, ?, ?, ?, ?, ?)
                                    """, (student_id, mark['char_id'], mark['Situation'],
                                          mark['where_field'], mark['operation'], mark['remark']))
                                    copied += 1
                                except Exception:
                                    pass

                # 從池子移除
                conn.execute("DELETE FROM task_pool WHERE keyword = ?", (kw,))

            conn.commit()
            logger.info(f"分配任務: {student_id} 獲得 {assigned} 個字, 複製 {copied} 條標注")
            return {'assigned': assigned, 'copied_annotations': copied}
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"分配任務失敗: {str(e)}")
        return {'assigned': 0, 'copied_annotations': 0, 'error': str(e)}

@retry_on_locked()
def return_to_pool(student_id, keyword):
    """將任務退回池子。
    同時清掉 characters.default_keyword 中該 keyword 的全局選擇，
    下一個接手的學生才不會看到前任的"後端默認"遺留。"""
    try:
        with get_db_connection() as conn:
            task = conn.execute("""
                SELECT keyword, frequency, previous_owner
                FROM task_assignments
                WHERE student_id = ? AND keyword = ? AND status = 'pending'
            """, (student_id, keyword)).fetchone()

            if not task:
                return False

            conn.execute("""
                INSERT OR IGNORE INTO task_pool (keyword, frequency, previous_owner, previous_student_id)
                VALUES (?, ?, ?, ?)
            """, (task['keyword'], task['frequency'], task['previous_owner'] or '', student_id))

            conn.execute("DELETE FROM task_assignments WHERE student_id = ? AND keyword = ?",
                         (student_id, keyword))
            conn.commit()

        # 清 characters.default_keyword（在不同的 DB 連接裡做）
        try:
            from char_database import clear_default_keyword as _clear_ck
            cleared = _clear_ck(keyword)
            if cleared:
                logger.info(f"return_to_pool: 清除 {keyword} 的 default_keyword {cleared} 條")
        except Exception as _e:
            logger.warning(f"return_to_pool 清 default_keyword 失敗: {_e}")

        return True
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"退回任務失敗: {str(e)}")
        return False


@retry_on_locked()
def record_heartbeat(student_id, page=''):
    """記錄心跳（每60秒一次，僅頁面聚焦時）"""
    try:
        with get_db_connection() as conn:
            conn.execute('''
                INSERT INTO activity_log (student_id, action, detail)
                VALUES (?, 'heartbeat', ?)
            ''', (student_id, page))
            conn.commit()
    except sqlite3.OperationalError:
        raise
    except Exception as e:
        logger.error(f"記錄心跳失敗: {str(e)}")

def get_all_active_times(days=30):
    """批量獲取所有學生的活躍時間（分鐘）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT student_id, created_at FROM activity_log
                WHERE action = 'heartbeat'
                AND created_at >= datetime('now', ?)
                ORDER BY student_id, created_at
            ''', (f'-{days} days',))
            rows = cursor.fetchall()

            from datetime import datetime as _dt
            from collections import defaultdict
            by_student = defaultdict(list)
            for row in rows:
                by_student[row['student_id']].append(row['created_at'])

            result = {}
            for sid, timestamps in by_student.items():
                total = 0
                prev = None
                for ts in timestamps:
                    try:
                        curr = _dt.strptime(ts, '%Y-%m-%d %H:%M:%S')
                    except (ValueError, TypeError):
                        continue
                    if prev and (curr - prev).total_seconds() <= 180:
                        total += (curr - prev).total_seconds() / 60
                    else:
                        total += 1
                    prev = curr
                result[sid] = round(total)
            return result
    except Exception as e:
        logger.error(f"批量計算活躍時間失敗: {str(e)}")
        return {}

def get_student_sessions(student_id, days=30):
    """獲取學生的會話列表，含行為質量分析（北京時間）"""
    try:
        with get_db_connection() as conn:
            cursor = conn.execute('''
                SELECT action, detail,
                       datetime(created_at, '+8 hours') as created_at
                FROM activity_log
                WHERE student_id = ?
                AND created_at >= datetime('now', ?)
                ORDER BY created_at
            ''', (student_id, f'-{days} days'))
            rows = cursor.fetchall()
            if not rows:
                return []

            import re
            from datetime import datetime as _dt
            sessions = []
            s_start = s_end = None
            s_ops = 0
            s_scroll_slow = 0
            s_scroll_fast = 0
            s_btn_clicks = 0
            prev = None

            def _parse_detail(detail):
                slow = fast = btn = 0
                m = re.search(r'慢(\d+)/快(\d+)', detail or '')
                if m:
                    slow, fast = int(m.group(1)), int(m.group(2))
                m = re.search(r'按鈕:(\d+)次', detail or '')
                if m:
                    btn = int(m.group(1))
                return slow, fast, btn

            def _save_session():
                if not s_start:
                    return
                dur = max(1, round((s_end - s_start).total_seconds() / 60))
                total_scroll = s_scroll_slow + s_scroll_fast
                quality = ''
                if total_scroll > 0:
                    slow_ratio = s_scroll_slow / total_scroll
                    if slow_ratio >= 0.6:
                        quality = 'careful'
                    elif slow_ratio >= 0.3:
                        quality = 'normal'
                    else:
                        quality = 'rushed'
                sessions.append({
                    'start': s_start.strftime('%m-%d %H:%M'),
                    'duration': dur,
                    'actions': s_ops,
                    'scroll_slow': s_scroll_slow,
                    'scroll_fast': s_scroll_fast,
                    'btn_clicks': s_btn_clicks,
                    'quality': quality
                })

            for row in rows:
                try:
                    curr = _dt.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    continue
                is_op = row['action'] != 'heartbeat'
                slow, fast, btn = _parse_detail(row['detail'])

                if prev and (curr - prev).total_seconds() > 180:
                    _save_session()
                    s_start = s_end = curr
                    s_ops = 1 if is_op else 0
                    s_scroll_slow = slow
                    s_scroll_fast = fast
                    s_btn_clicks = btn
                else:
                    if not s_start:
                        s_start = curr
                    s_end = curr
                    if is_op:
                        s_ops += 1
                    s_scroll_slow += slow
                    s_scroll_fast += fast
                    s_btn_clicks += btn
                prev = curr

            _save_session()
            return sessions[-20:]
    except Exception as e:
        logger.error(f"獲取學生會話失敗: {str(e)}")
        return []


# 初始化数据库
if __name__ == '__main__':
    init_database()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
字符數據庫模塊
用於管理 98 萬筆字符數據的 SQLite 數據庫操作
"""

import sqlite3
import os
import logging
from contextlib import contextmanager
import threading

# 數據庫路徑：相對當前模塊位置（跨平台部署友善）
# 可用環境變數 SIX_DYN_CHAR_DB 覆蓋（例如把 DB 放在單獨磁盤）
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
CHAR_DB_PATH = os.environ.get('SIX_DYN_CHAR_DB') or \
    os.path.join(_MODULE_DIR, "data", "characters.db")

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

# 設置日志
logger = logging.getLogger(__name__)

# ---- COUNT(*) 結果緩存 ----
# characters.db 只有 default_keyword 字段會被寫入，其它字段穩定；
# 搜索的 WHERE 不涉及 default_keyword，故 COUNT 在進程生命週期內可安全緩存。
_count_cache = {}
_count_cache_lock = threading.Lock()
_COUNT_CACHE_MAX = 512

def _get_or_compute_count(cache_key, conn, count_sql, params):
    """讀取 COUNT 緩存；未命中則執行 SQL 並寫回。簡單 FIFO 淘汰。"""
    with _count_cache_lock:
        if cache_key in _count_cache:
            return _count_cache[cache_key]
    # 緩存未命中，在鎖外執行 SQL
    result = conn.execute(count_sql, params).fetchone()[0]
    with _count_cache_lock:
        if len(_count_cache) >= _COUNT_CACHE_MAX:
            _count_cache.pop(next(iter(_count_cache)), None)
        _count_cache[cache_key] = result
    return result

def invalidate_count_cache():
    """當有結構性寫入（如導入數據）時手動清空。"""
    with _count_cache_lock:
        n = len(_count_cache)
        _count_cache.clear()
    return n

# 基本搜索的文本字段列表（與 search_chars_paginated 共享，保持一致）
BASIC_SEARCH_FIELDS = ['ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives',
                       'other_info', 'ocr_txt正', 'ocr_col正']

# 白名單：所有合法的 characters 表列名，用於防止 SQL 注入。
# sort_column / search_column / targets 都只能從這個集合取值。
# 'other_info' 是邏輯別名，走特殊排序路徑（code/priority/page 三列複合）。
_ALLOWED_COLUMNS = frozenset([
    'char_id', 'img_name', 'ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives',
    'column_no', 'char_no', 'cid', 'other_info', 'ocr_txt正', 'ocr_col正',
    'x', 'y', 'w', 'h', 'h_atten', 'r_atten', 'default_keyword', 'img_url',
    'other_info_code', 'other_info_page', 'h_atten_priority',
])

def _safe_column(name, default=None):
    """只允許白名單內的列名；否則返回 default。防止 f-string 拼接 SQL 被注入。"""
    if isinstance(name, str) and name in _ALLOWED_COLUMNS:
        return name
    return default

def _safe_direction(direction, default='ASC'):
    """只允許 ASC / DESC。"""
    if isinstance(direction, str) and direction.upper() in ('ASC', 'DESC'):
        return direction.upper()
    return default

def _safe_columns_list(names):
    """過濾列名列表，移除不在白名單內的。用於 advanced search targets。"""
    if not names:
        return []
    return [n for n in names if isinstance(n, str) and n in _ALLOWED_COLUMNS]

def warm_basic_search_count(query, conn=None):
    """預熱基本搜索（無 search_column）的 COUNT 緩存。
    供 dashboard 後台預熱線程使用。"""
    if not query or not query.strip():
        return 0
    query = query.strip()
    field_conditions = [f'{field} LIKE ?' for field in BASIC_SEARCH_FIELDS]
    where_clause = f'({" OR ".join(field_conditions)})'
    params = [f'%{query}%'] * len(BASIC_SEARCH_FIELDS)
    count_sql = f'SELECT COUNT(*) FROM characters WHERE {where_clause}'
    cache_key = ('scp', 'basic', '', 'and', query, ())

    if conn is None:
        with get_char_db_connection() as conn:
            return _get_or_compute_count(cache_key, conn, count_sql, params)
    return _get_or_compute_count(cache_key, conn, count_sql, params)

def warm_basic_search_counts_batch(queries):
    """為一批 query 複用同一個連接預熱 COUNT 緩存。返回已預熱的數量。"""
    warmed = 0
    try:
        with get_char_db_connection() as conn:
            for q in queries:
                if not q:
                    continue
                try:
                    warm_basic_search_count(q, conn=conn)
                    warmed += 1
                except Exception as e:
                    logger.warning(f"預熱 query={q!r} 失敗: {e}")
    except Exception as e:
        logger.error(f"預熱批次失敗: {e}")
    return warmed

def get_char_db_path():
    """獲取字符數據庫文件路徑"""
    return CHAR_DB_PATH

@contextmanager
def get_char_db_connection():
    """獲取字符數據庫連接的上下文管理器。
    WAL 模式允許多讀 + 單寫併行；busy_timeout 在寫衝突時自動等待，不用業務層額外加鎖。
    """
    conn = None
    try:
        # timeout= 會被後面的 PRAGMA busy_timeout 覆蓋，不再指定
        conn = sqlite3.connect(CHAR_DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL 模式（讀寫不互斥）
        conn.execute('PRAGMA journal_mode=WAL')
        # 寫衝突時等待 5 秒（遠大於單次寫入耗時 10-30ms），避免 "database is locked" 報錯
        conn.execute('PRAGMA busy_timeout=5000')
        # 性能優化
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=-50000')     # 50MB
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.execute('PRAGMA mmap_size=268435456')   # 256MB mmap I/O
        yield conn
    except Exception as e:
        logger.error(f"字符數據庫連接錯誤: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def init_char_database():
    """初始化字符數據庫"""
    try:
        # 確保目錄存在
        os.makedirs(os.path.dirname(CHAR_DB_PATH), exist_ok=True)
        
        with get_char_db_connection() as conn:
            # 創建字符數據表
            conn.execute('''
                CREATE TABLE IF NOT EXISTS characters (
                    char_id TEXT PRIMARY KEY,
                    img_name TEXT,
                    ocr_txt TEXT,
                    ocr_col TEXT,
                    cmp_txt TEXT,
                    txt TEXT,
                    alternatives TEXT,
                    column_no INTEGER,
                    char_no INTEGER,
                    cid INTEGER,
                    other_info TEXT,
                    ocr_txt正 TEXT,
                    ocr_col正 TEXT,
                    x INTEGER,
                    y INTEGER,
                    w INTEGER,
                    h INTEGER,
                    h_atten TEXT,
                    r_atten TEXT,
                    default_keyword TEXT,
                    img_url TEXT
                )
            ''')
            
            # 創建索引以提高查詢性能
            indexes = [
                'CREATE INDEX IF NOT EXISTS idx_img_name ON characters(img_name)',
                'CREATE INDEX IF NOT EXISTS idx_ocr_txt ON characters(ocr_txt)',
                'CREATE INDEX IF NOT EXISTS idx_ocr_col ON characters(ocr_col)',
                'CREATE INDEX IF NOT EXISTS idx_cmp_txt ON characters(cmp_txt)',
                'CREATE INDEX IF NOT EXISTS idx_txt ON characters(txt)',
                'CREATE INDEX IF NOT EXISTS idx_alternatives ON characters(alternatives)',
                'CREATE INDEX IF NOT EXISTS idx_other_info ON characters(other_info)',
                'CREATE INDEX IF NOT EXISTS idx_ocr_txt正 ON characters(ocr_txt正)',
                'CREATE INDEX IF NOT EXISTS idx_ocr_col正 ON characters(ocr_col正)',
                'CREATE INDEX IF NOT EXISTS idx_h_atten ON characters(h_atten)',
                'CREATE INDEX IF NOT EXISTS idx_r_atten ON characters(r_atten)',
                'CREATE INDEX IF NOT EXISTS idx_default_keyword ON characters(default_keyword)',
                # 複合索引用於排序優化
                'CREATE INDEX IF NOT EXISTS idx_other_info_atten ON characters(other_info, h_atten, r_atten)',
            ]
            
            for index_sql in indexes:
                conn.execute(index_sql)
            
            conn.commit()
            logger.info("字符數據庫初始化完成")
    except Exception as e:
        logger.error(f"字符數據庫初始化失敗: {str(e)}")
        raise

def get_char_by_id(char_id):
    """根據 char_id 獲取字符數據"""
    try:
        with get_char_db_connection() as conn:
            cursor = conn.execute('SELECT * FROM characters WHERE char_id = ?', (char_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"獲取字符數據失敗 (char_id={char_id}): {str(e)}")
        return None

def get_all_chars_count():
    """獲取所有字符的總數"""
    try:
        with get_char_db_connection() as conn:
            cursor = conn.execute('SELECT COUNT(*) FROM characters')
            return cursor.fetchone()[0]
    except Exception as e:
        logger.error(f"獲取字符總數失敗: {str(e)}")
        return 0

def search_chars_in_db(query, search_column=None, search_type='basic', search_logic='and', targets=None, 
                       sort_column=None, sort_direction=None, limit=None, offset=None):
    """
    在數據庫中搜索字符
    
    Args:
        query: 搜索關鍵詞
        search_column: 指定搜索列（基本搜索）
        search_type: 'basic' 或 'advanced'
        search_logic: 'and' 或 'or'（高級搜索）
        targets: 目標字段列表（高級搜索）
        sort_column: 排序列
        sort_direction: 'asc' 或 'desc'
        limit: 限制返回數量
        offset: 偏移量
    
    Returns:
        list: 搜索結果列表
    """
    try:
        with get_char_db_connection() as conn:
            # 構建 WHERE 子句
            where_clauses = []
            params = []

            # 白名單清洗用戶輸入的列名，防 SQL 注入
            safe_search_column = _safe_column(search_column) if search_column else None
            safe_targets = _safe_columns_list(targets)

            if search_type == 'basic':
                if safe_search_column:
                    where_clauses.append(f'{safe_search_column} LIKE ?')
                    params.append(f'%{query}%')
                else:
                    field_conditions = [f'{field} LIKE ?' for field in BASIC_SEARCH_FIELDS]
                    where_clauses.append(f'({" OR ".join(field_conditions)})')
                    params.extend([f'%{query}%'] * len(BASIC_SEARCH_FIELDS))

            elif search_type == 'advanced' and safe_targets:
                field_conditions = [f'{target} LIKE ?' for target in safe_targets]
                if search_logic == 'and':
                    where_clauses.append(f'({" AND ".join(field_conditions)})')
                else:  # or
                    where_clauses.append(f'({" OR ".join(field_conditions)})')
                params.extend([f'%{query}%'] * len(safe_targets))

            # 構建 SQL 查詢
            sql = 'SELECT * FROM characters'
            if where_clauses:
                sql += ' WHERE ' + ' AND '.join(where_clauses)

            # 排序：列名走白名單，方向只接受 ASC/DESC
            safe_sort = _safe_column(sort_column) if sort_column else None
            if safe_sort and sort_direction:
                dir_upper = _safe_direction(sort_direction)
                if safe_sort == 'other_info':
                    attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                    sql += (f' ORDER BY other_info_code {dir_upper}, '
                            f'h_atten_priority {attn_dir}, other_info_page {dir_upper}')
                else:
                    sql += f' ORDER BY {safe_sort} {dir_upper}'

            # 分頁：limit/offset 強制轉 int，避免字串注入
            if limit is not None:
                sql += f' LIMIT {int(limit)}'
            if offset is not None:
                sql += f' OFFSET {int(offset)}'

            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"搜索字符失敗: {str(e)}")
        return []


def search_chars_paginated(query, search_column=None, search_type='basic', search_logic='and', targets=None,
                           sort_column=None, sort_direction=None, page=1, per_page=500, manuscript=None,
                           query_variants=None):
    """
    在數據庫中搜索字符（帶總數與分頁）

    Returns:
        (rows, total_count)
    """
    try:
        with get_char_db_connection() as conn:
            where_clauses = []
            params = []

            # 白名單清洗輸入列名，防注入
            safe_search_column = _safe_column(search_column) if search_column else None
            safe_targets = _safe_columns_list(targets)

            query_terms = []
            seen_terms = set()
            for raw_term in [query] + list(query_variants or []):
                term = str(raw_term or '').strip()
                if term and term not in seen_terms:
                    query_terms.append(term)
                    seen_terms.add(term)
            if not query_terms:
                query_terms = [query]

            if search_type == 'basic':
                if safe_search_column:
                    where_clauses.append(
                        '(' + ' OR '.join([f'{safe_search_column} LIKE ?' for _ in query_terms]) + ')'
                    )
                    params.extend([f'%{term}%' for term in query_terms])
                else:
                    term_conditions = []
                    for term in query_terms:
                        field_conditions = [f'{field} LIKE ?' for field in BASIC_SEARCH_FIELDS]
                        term_conditions.append(f'({" OR ".join(field_conditions)})')
                        params.extend([f'%{term}%'] * len(BASIC_SEARCH_FIELDS))
                    where_clauses.append(f'({" OR ".join(term_conditions)})')
            elif search_type == 'advanced' and safe_targets:
                term_conditions = []
                for term in query_terms:
                    field_conditions = [f'{target} LIKE ?' for target in safe_targets]
                    if search_logic == 'and':
                        term_conditions.append(f'({" AND ".join(field_conditions)})')
                    else:
                        term_conditions.append(f'({" OR ".join(field_conditions)})')
                    params.extend([f'%{term}%'] * len(safe_targets))
                where_clauses.append(f'({" OR ".join(term_conditions)})')

            # 按寫卷過濾（可選）—— 限定 other_info_code = 某寫卷號
            if manuscript:
                where_clauses.append('other_info_code = ?')
                params.append(manuscript)

            base_sql = 'FROM characters'
            if where_clauses:
                base_sql += ' WHERE ' + ' AND '.join(where_clauses)

            count_sql = f'SELECT COUNT(*) {base_sql}'
            # cache key 使用清洗後的列名，避免同 query 不同注入串產生大量假緩存
            query_cache_key = query if query_terms == [query] else tuple(query_terms)
            cache_key = ('scp', search_type, safe_search_column or '', search_logic,
                         query_cache_key, tuple(safe_targets), manuscript or '')
            total_count = _get_or_compute_count(cache_key, conn, count_sql, params)

            sql = f'SELECT * {base_sql}'
            safe_sort = _safe_column(sort_column) if sort_column else None
            if safe_sort and sort_direction:
                dir_upper = _safe_direction(sort_direction)
                if safe_sort == 'other_info':
                    attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                    sql += (f' ORDER BY other_info_code {dir_upper}, '
                            f'h_atten_priority {attn_dir}, other_info_page {dir_upper}')
                else:
                    sql += f' ORDER BY {safe_sort} {dir_upper}'

            # per_page / offset 強制轉 int
            try:
                per_page_int = int(per_page)
            except (TypeError, ValueError):
                per_page_int = 100
            if per_page_int <= 0:
                per_page_int = total_count or 1
            try:
                page_int = max(1, int(page))
            except (TypeError, ValueError):
                page_int = 1
            offset = (page_int - 1) * per_page_int
            sql += f' LIMIT {per_page_int} OFFSET {offset}'

            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows], total_count
    except Exception as e:
        logger.error(f"搜索字符失敗: {str(e)}")
        return [], 0

def get_paginated_chars(page=1, per_page=500, sort_column=None, sort_direction=None, where_clause=None, params=None):
    """
    獲取分頁的字符數據

    Args:
        page: 頁碼（從 1 開始）
        per_page: 每頁數量
        sort_column: 排序列
        sort_direction: 'asc' 或 'desc'
        where_clause: WHERE 子句（不包含 WHERE 關鍵字）
        params: WHERE 子句的參數

    Returns:
        tuple: (數據列表, 總頁數, 總數量)
    """
    try:
        with get_char_db_connection() as conn:
            # 獲取總數
            count_sql = 'SELECT COUNT(*) FROM characters'
            if where_clause:
                count_sql += f' WHERE {where_clause}'

            cursor = conn.execute(count_sql, params or [])
            total_count = cursor.fetchone()[0]

            # 計算總頁數
            total_pages = (total_count + per_page - 1) // per_page if per_page > 0 else 1

            # 對於other_info排序，需要特殊處理（Python層面排序）
            # 為了避免加載全部數據，我們只加載足夠當前頁面的數據
            if sort_column == 'other_info':
                # 動態計算需要加載的數據量：確保能覆蓋當前頁
                # 加載 (page * per_page * 2) 條數據，最少2000條，最多10000條
                rows_needed = max(2000, min(10000, page * per_page * 2))
                max_rows_for_sort = min(rows_needed, total_count)

                sql = 'SELECT * FROM characters'
                if where_clause:
                    sql += f' WHERE {where_clause}'
                sql += f' LIMIT {max_rows_for_sort}'

                cursor = conn.execute(sql, params or [])
                rows = [dict(row) for row in cursor.fetchall()]

                # 返回數據，告訴調用者需要在Python層面排序
                return rows, total_pages, total_count
            else:
                # 構建查詢
                sql = 'SELECT * FROM characters'
                if where_clause:
                    sql += f' WHERE {where_clause}'

                # 排序：列名白名單，方向只接受 ASC/DESC
                safe_sort = _safe_column(sort_column) if sort_column else None
                if safe_sort and sort_direction:
                    dir_upper = _safe_direction(sort_direction)
                    if safe_sort == 'other_info':
                        attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                        sql += (f' ORDER BY other_info_code {dir_upper}, '
                                f'h_atten_priority {attn_dir}, other_info_page {dir_upper}')
                    else:
                        sql += f' ORDER BY {safe_sort} {dir_upper}'

                # 分頁：強制 int
                per_page_int = int(per_page) if per_page else 100
                page_int = max(1, int(page))
                offset = (page_int - 1) * per_page_int
                sql += f' LIMIT {per_page_int} OFFSET {offset}'

                cursor = conn.execute(sql, params or [])
                rows = cursor.fetchall()

                return [dict(row) for row in rows], total_pages, total_count
    except Exception as e:
        logger.error(f"獲取分頁數據失敗: {str(e)}")
        return [], 1, 0

@retry_on_locked()
def update_default_keyword(char_ids, keyword):
    """
    更新字符的 default_keyword

    Args:
        char_ids: 字符 ID 列表
        keyword: 關鍵詞

    Returns:
        int: 更新的行數
    """
    try:
        with get_char_db_connection() as conn:
            placeholders = ','.join(['?'] * len(char_ids))
            sql = f'UPDATE characters SET default_keyword = ? WHERE char_id IN ({placeholders})'
            cursor = conn.execute(sql, [keyword] + char_ids)
            conn.commit()
            return cursor.rowcount
    except sqlite3.OperationalError:
        # 由 @retry_on_locked 處理 locked/busy 錯誤，這裡必須讓它拋出
        raise
    except Exception as e:
        logger.error(f"更新 default_keyword 失敗: {str(e)}")
        return 0

@retry_on_locked()
def replace_default_entries(keyword, char_ids, manuscript_ids=None):
    """原子操作：先清掉同 keyword 同寫卷的舊默認行，再把新 char_ids 設為該 keyword。
    取代之前 clear + update 兩步分離的用法（中間崩潰會留空狀態）。
    寫卷限定優先使用結構化欄位 other_info_code；若舊資料缺失，才回退到 other_info 前綴。

    Args:
        keyword: 要寫入的關鍵詞
        char_ids: 本次要設為默認的字符 ID 列表
        manuscript_ids: 限定清理的寫卷 ID 列表（可選）

    Returns:
        int: 新寫入的行數（update 階段的 rowcount）
    """
    if not char_ids:
        return 0
    try:
        with get_char_db_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # 步驟 1：清理舊默認
            if manuscript_ids:
                conditions = ["other_info_code = ?" for _ in manuscript_ids]
                fallback_conditions = ["(other_info_code IS NULL OR other_info_code = '') AND other_info LIKE ?" for _ in manuscript_ids]
                mid_params = [f'{mid}%' for mid in manuscript_ids]
                sql_clear = (
                    "UPDATE characters SET default_keyword = '' "
                    f"WHERE default_keyword = ? AND "
                    f"(({' OR '.join(conditions)}) OR ({' OR '.join(fallback_conditions)}))"
                )
                conn.execute(sql_clear, [keyword] + list(manuscript_ids) + mid_params)
            else:
                conn.execute(
                    "UPDATE characters SET default_keyword = '' WHERE default_keyword = ?",
                    (keyword,))
            # 步驟 2：寫新默認
            placeholders = ','.join(['?'] * len(char_ids))
            cursor = conn.execute(
                f'UPDATE characters SET default_keyword = ? WHERE char_id IN ({placeholders})',
                [keyword] + list(char_ids))
            conn.commit()
            return cursor.rowcount
    except sqlite3.OperationalError:
        raise  # 讓 @retry_on_locked 處理
    except Exception as e:
        logger.error(f"replace_default_entries 失敗: {str(e)}")
        return 0


@retry_on_locked()
def clear_default_keyword(keyword, manuscript_ids=None):
    """
    清除指定關鍵詞的 default_keyword
    
    Args:
        keyword: 要清除的關鍵詞
        manuscript_ids: 寫卷 ID 列表（可選）
    
    Returns:
        int: 更新的行數
    """
    try:
        with get_char_db_connection() as conn:
            if manuscript_ids:
                # 按寫卷清除：優先使用結構化 other_info_code，舊資料缺失時才回退 other_info 前綴。
                conditions = ["other_info_code = ?" for _ in manuscript_ids]
                fallback_conditions = ["(other_info_code IS NULL OR other_info_code = '') AND other_info LIKE ?" for _ in manuscript_ids]
                mid_params = [f'{mid}%' for mid in manuscript_ids]
                sql = (
                    "UPDATE characters SET default_keyword = '' "
                    f"WHERE default_keyword = ? AND "
                    f"(({' OR '.join(conditions)}) OR ({' OR '.join(fallback_conditions)}))"
                )
                cursor = conn.execute(sql, [keyword] + list(manuscript_ids) + mid_params)
            else:
                # 清除所有
                sql = "UPDATE characters SET default_keyword = '' WHERE default_keyword = ?"
                cursor = conn.execute(sql, (keyword,))
            
            conn.commit()
            return cursor.rowcount
    except sqlite3.OperationalError:
        raise  # 讓 @retry_on_locked 處理
    except Exception as e:
        logger.error(f"清除 default_keyword 失敗: {str(e)}")
        return 0

def get_chars_by_img_name(img_name):
    """根據圖片名稱獲取字符數據"""
    try:
        with get_char_db_connection() as conn:
            cursor = conn.execute('SELECT * FROM characters WHERE img_name = ?', (img_name,))
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"根據圖片名稱獲取字符失敗: {str(e)}")
        return []

def _build_source_filter(conn, source_char_ids):
    """把來源字圖 ID 以臨時表加入連接，返回 SQL 片段與參數占位。
    返回值可為 None（不需要來源過濾）或 'char_id IN (...)' 片段。"""
    if source_char_ids is None:
        return None
    source_ids = list(dict.fromkeys([str(v) for v in source_char_ids if v]))
    if not source_ids:
        return "_NO_SOURCE_"

    conn.execute('CREATE TEMP TABLE IF NOT EXISTS tmp_default_source_char_ids (char_id TEXT PRIMARY KEY)')
    conn.execute('DELETE FROM tmp_default_source_char_ids')
    conn.executemany(
        'INSERT INTO tmp_default_source_char_ids(char_id) VALUES (?)',
        [(c,) for c in source_ids]
    )
    return 'char_id IN (SELECT char_id FROM tmp_default_source_char_ids)'


def get_default_chars_paginated(page=1, per_page=500, sort_column=None, sort_direction=None,
                               source_char_ids=None, include_default_in_source=False):
    """
    獲取所有有默認關鍵詞的字圖（分頁）

    Args:
        page: 頁碼
        per_page: 每頁數量
        sort_column: 排序列
        sort_direction: 排序方向

    Returns:
        tuple: (數據列表, 總頁數, 總數量)
    """
    if source_char_ids is None:
        return get_paginated_chars(
            page=page,
            per_page=per_page,
            sort_column=sort_column or 'default_keyword',
            sort_direction=sort_direction or 'asc',
            where_clause="default_keyword IS NOT NULL AND default_keyword != ''"
        )

    try:
        with get_char_db_connection() as conn:
            source_filter = _build_source_filter(conn, source_char_ids)
            if source_filter == '_NO_SOURCE_':
                if include_default_in_source:
                    source_filter = None
                else:
                    return [], 1, 0

            if include_default_in_source:
                where_clause = f'WHERE (default_keyword IS NOT NULL AND default_keyword != "") OR {source_filter}'
            else:
                where_clause = f'WHERE {source_filter}'

            # 獲取總數
            count_sql = f'SELECT COUNT(*) FROM characters {where_clause}'
            total_count = conn.execute(count_sql).fetchone()[0]

            # 反算總頁
            try:
                per_page_int = int(per_page)
            except (TypeError, ValueError):
                per_page_int = 100
            if per_page_int <= 0:
                per_page_int = total_count or 1
            total_pages = (total_count + per_page_int - 1) // per_page_int if per_page_int > 0 else 1

            sql = f'SELECT * FROM characters {where_clause}'
            safe_sort = _safe_column(sort_column) if sort_column else None
            if safe_sort and sort_direction:
                dir_upper = _safe_direction(sort_direction)
                if safe_sort == 'other_info':
                    attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                    sql += (f' ORDER BY other_info_code {dir_upper}, '
                            f'h_atten_priority {attn_dir}, other_info_page {dir_upper}')
                else:
                    sql += f' ORDER BY {safe_sort} {dir_upper}'

            try:
                page_int = max(1, int(page))
            except (TypeError, ValueError):
                page_int = 1
            offset = (page_int - 1) * per_page_int
            sql += f' LIMIT {per_page_int} OFFSET {offset}'
            rows = conn.execute(sql).fetchall()

            return [dict(row) for row in rows], total_pages, total_count
    except Exception as e:
        logger.error(f"獲取默認字圖分頁失敗: {str(e)}")
        return [], 1, 0

def search_default_chars_paginated(query, search_column=None, search_type='basic', search_logic='and', targets=None,
                                  sort_column=None, sort_direction=None, page=1, per_page=500,
                                  source_char_ids=None, include_default_in_source=False):
    """
    在默認字圖中搜索字符（帶總數與分頁）
    只搜索有 default_keyword 標記的數據

    Returns:
        (rows, total_count)
    """
    try:
        with get_char_db_connection() as conn:
            where_clauses = []
            params = []
            safe_search_column = _safe_column(search_column)
            safe_targets = _safe_columns_list(targets)

            source_filter = _build_source_filter(conn, source_char_ids)
            if source_filter == '_NO_SOURCE_':
                if include_default_in_source:
                    source_filter = None
                else:
                    return [], 0

            # 必須有 default_keyword；若使用來源字圖，依來源模式決定 OR/AND。
            if source_filter:
                if include_default_in_source:
                    where_clauses.append(f'((default_keyword IS NOT NULL AND default_keyword != "") OR {source_filter})')
                else:
                    where_clauses.append(source_filter)
            else:
                    where_clauses.append("(default_keyword IS NOT NULL AND default_keyword != '')")

            if search_type == 'basic':
                if safe_search_column:
                    where_clauses.append(f'{safe_search_column} LIKE ?')
                    params.append(f'%{query}%')
                else:
                    text_fields = ['ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives', 'other_info', 'ocr_txt正', 'ocr_col正', 'default_keyword']
                    field_conditions = [f'{field} LIKE ?' for field in text_fields]
                    where_clauses.append(f'({" OR ".join(field_conditions)})')
                    params.extend([f'%{query}%'] * len(text_fields))
            elif search_type == 'advanced' and safe_targets:
                field_conditions = [f'{target} LIKE ?' for target in safe_targets]
                if search_logic == 'and':
                    where_clauses.append(f'({" AND ".join(field_conditions)})')
                else:
                    where_clauses.append(f'({" OR ".join(field_conditions)})')
                params.extend([f'%{query}%'] * len(safe_targets))

            base_sql = 'FROM characters'
            if where_clauses:
                base_sql += ' WHERE ' + ' AND '.join(where_clauses)

            count_sql = f'SELECT COUNT(*) {base_sql}'
            total_count = conn.execute(count_sql, params).fetchone()[0]

            sql = f'SELECT * {base_sql}'
            safe_sort = _safe_column(sort_column) if sort_column else None
            if safe_sort and sort_direction:
                dir_upper = _safe_direction(sort_direction)
                if safe_sort == 'other_info':
                    # 使用結構化字段 + 複合索引 idx_sort_composite，等價 sort_data_in_python
                    # 排序鍵 (code, -attention, page)。attention 方向與主方向相反。
                    attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                    sql += (f' ORDER BY other_info_code {dir_upper}, '
                            f'h_atten_priority {attn_dir}, other_info_page {dir_upper}')
                else:
                    sql += f' ORDER BY {safe_sort} {dir_upper}'

            try:
                page = max(1, int(page))
            except (TypeError, ValueError):
                page = 1
            try:
                per_page = int(per_page)
            except (TypeError, ValueError):
                per_page = 500
            if per_page <= 0:
                per_page = total_count or 1
            offset = (page - 1) * per_page
            sql += f' LIMIT {per_page} OFFSET {offset}'

            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows], total_count
    except Exception as e:
        logger.error(f"搜索默認字圖失敗: {str(e)}")
        return [], 0

# 初始化數據庫
if __name__ == '__main__':
    init_char_database()
    print(f"字符數據庫已初始化: {CHAR_DB_PATH}")

import csv
import os
import threading
import time
import pickle
import cv2
from flask import Flask, render_template, url_for, request, jsonify, send_file, current_app, session, redirect, Response
import math
import tempfile
import subprocess
import re
from werkzeug.utils import secure_filename
import base64
import numpy as np
import logging
from logging.handlers import RotatingFileHandler
import io
import pandas as pd
from datetime import datetime, timedelta
import argparse
import json
import threading
import zipfile
from functools import wraps
import sqlite3
import hmac as _hmac
import hashlib as _hashlib
import unicodedata
from collections import Counter, defaultdict
from xml.sax.saxutils import escape as xml_escape
import database as _database

try:
    import opencc as _opencc
    _OPENCC_S2T = _opencc.OpenCC('s2t')
    _OPENCC_T2S = _opencc.OpenCC('t2s')
except Exception as _opencc_exc:
    _OPENCC_S2T = None
    _OPENCC_T2S = None
    logging.getLogger(__name__).warning(f"OpenCC 不可用，簡繁搜索擴展停用: {_opencc_exc}")

# ======== AI 筛选来源配置 ========
AI_PICK_DB_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE_TO_DB = {
    'manual': None,  # 只要有 default_keyword
    'gemini_pro': [
        'ai_picks.db',
    ],
    'gemini_fresh': [
        'ai_picks_flash.db',
    ],
    'all': [
        'ai_picks.db',
        'ai_picks_flash.db',
    ],
    # 全局候选模式：不限制为已筛选 char_id，只把筛选来源叠加为标记。
    'global_all': [
        'ai_picks.db',
        'ai_picks_flash.db',
    ],
    # 單字候選模式：使用舊 Gemini pending 口徑找 n=1 漏覆蓋候選，仍走 /defaults 原頁面。
    'single_candidate': None,
}
DEFAULT_DEFAULT_SOURCE = 'manual'
_DEFAULTS_SEARCH_TARGETS = ('char_id', 'cmp_txt', 'txt', 'other_info', 'ocr_txt正', 'ocr_col正')
AI_AUDIT_AUTH_FIELDS = ('txt', 'cmp_txt')
AI_AUDIT_OCR_FIELDS = ('ocr_txt', 'ocr_col', 'ocr_txt正', 'ocr_col正')
AI_AUDIT_WEAK_FIELDS = ('alternatives',)
AI_AUDIT_STRONG_FIELDS = AI_AUDIT_AUTH_FIELDS + AI_AUDIT_OCR_FIELDS
AI_AUDIT_SEARCH_FIELDS = AI_AUDIT_STRONG_FIELDS + AI_AUDIT_WEAK_FIELDS
PROGRESS_XLSX_PATH = os.path.join(AI_PICK_DB_DIR, '賬戶密碼和進度', '篩選字圖進度表2026年04月15日15.xlsx')
MANUSCRIPT_CATALOG_XLSX_PATH = os.environ.get("SIX_DYN_CATALOG_XLSX") or ""
_AI_SOURCE_CACHE = {}
_AI_SOURCE_CACHE_LOCK = threading.Lock()
_KEYWORD_VARIANT_CACHE = None
_KEYWORD_VARIANT_CACHE_LOCK = threading.Lock()
_VARIANT_TABLE_CACHE = None
_VARIANT_TABLE_CACHE_LOCK = threading.Lock()
_PROGRESS_ORDER_CACHE = None
_PROGRESS_ORDER_CACHE_LOCK = threading.Lock()
_MANUSCRIPT_CATALOG_CACHE = None
_MANUSCRIPT_CATALOG_CACHE_LOCK = threading.Lock()
_SINGLE_CANDIDATE_CACHE = None
_SINGLE_CANDIDATE_CACHE_LOCK = threading.Lock()
_SINGLE_CANDIDATE_BACKUP_LOCK = threading.Lock()
_SINGLE_CANDIDATE_LAST_BACKUP_TS = 0

# ======== 導入數據庫模塊 ========
from database import (
    get_student_annotations, add_student_annotation,
    get_student_annotation, delete_student_annotation,
    record_file_size, check_file_size_anomaly,
    get_student_annotations_by_char_ids, get_all_annotations_by_char_ids,
    get_task_assignments_for_keywords,
    get_student_tasks, get_student_task_stats, is_keyword_assigned,
    mark_task_completed, reset_task_status, log_activity, get_admin_overview,
    get_student_activity, toggle_account, is_account_enabled,
    update_account_note, update_account_display_name,
    record_heartbeat, get_all_active_times, get_student_sessions,
    get_pool_stats, get_pool_keywords, assign_from_pool, return_to_pool,
    save_manuscript_collection, get_manuscript_collections,
    get_manuscript_collection, delete_manuscript_collection
)

# ======== 導入字符數據庫模塊 ========
from char_database import (
    init_char_database, get_char_by_id, get_all_chars_count,
    search_chars_in_db, get_paginated_chars, update_default_keyword,
    clear_default_keyword, get_chars_by_img_name, search_chars_paginated,
    get_default_chars_paginated, search_default_chars_paginated,
    warm_basic_search_counts_batch, invalidate_count_cache,
    replace_default_entries, CHAR_DB_PATH
)

# ======== 導入數據處理輔助模塊 ========
from data_helpers import merge_student_marks, sort_data_in_python, build_sort_sql, extract_code_from_other_info

# ======== 導入詞典模塊（敦煌俗字典 / 教育部異體字字典） ========
try:
    import dict_database
except Exception as _dict_exc:
    dict_database = None
    logging.getLogger(__name__).warning(f"dict_database 不可用: {_dict_exc}")

# ======== 自訂路徑區（請根據你本地環境修改） ========
# 路徑配置：優先讀環境變數（雲部署用），否則用相對路徑（本地開發用）。
# 大圖片目錄（~GB 級）通常不跟代碼倉庫一起走，所以用 SIX_DYN_* 環境變數指向。
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_DIR = os.path.join(_PROJECT_DIR, '網頁部署')
SINGLE_CANDIDATE_CLEANER_DB_PATH = os.path.join(_PROJECT_DIR, 'single_candidate_cleaner.db')
SINGLE_CANDIDATE_CLEANER_BACKUP_DIR = os.path.join(_PROJECT_DIR, 'backups', 'single_candidate_cleaner')
SINGLE_CANDIDATE_THUMB_DIR = os.path.join(_PROJECT_DIR, 'cache', 'single_candidate_thumbs_v1')

HTML_TEMPLATE_PATH = os.path.join(_PROJECT_DIR, "templates")
LOCAL_STATIC_DIR = os.path.join(_PROJECT_DIR, 'static')

# 切割後的字圖目錄（查詢頁顯示用）
IMAGE_FOLDER_PATH = os.path.abspath(
    os.environ.get('SIX_DYN_IMAGE_FOLDER')
    or ""
)

# 原始整頁掃描圖目錄（點擊原圖時用）
ORIGINAL_IMAGE_FOLDER = os.environ.get('SIX_DYN_ORIGINAL_FOLDER') or \
    ""

# DB 與緩存檔（隨代碼部署，走相對路徑）
CSV_PATH = os.path.join(_DB_DIR, '5全部98萬字圖+篩線上處理+超過3個字符的单元格改为空+判斷不一致.csv')
DEFAULT_DB_PATH = os.path.join(_DB_DIR, 'default_keyword.db')
INDEX_CACHE_FILE = os.path.join(_DB_DIR, 'image_index.pkl')
VARIANT_TABLE_PATH = os.path.join(_PROJECT_DIR, '字典', 'variant.txt')
CHAR_DATA_CACHE_FILE = os.path.join(_DB_DIR, 'char_data.pkl')
HTML_FILE = "workspace.html"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.json')

# ======== 學生管理配置（自動生成 student_01 ~ student_50） ========
STUDENTS_CONFIG = {f'student_{i:02d}': f'學生{i:02d}' for i in range(1, 51)
}

# ======== 初始化 Flask 應用 ========
# 注意：不再把圖片目錄設為 static_folder，改用受保護的路由提供圖片
# LOCAL_STATIC_DIR 已在上方路徑配置區定義
app = Flask(__name__,
            template_folder=HTML_TEMPLATE_PATH,
            static_folder=LOCAL_STATIC_DIR,
            static_url_path='/assets')
# 模板自動重載：開發打開，生產關閉（省 render_template 每次都比 mtime 的開銷）。
# 設環境變數 FLASK_DEBUG=1 開啟開發模式。
_DEV_MODE = bool(os.environ.get('FLASK_DEBUG', '').strip())
app.config['TEMPLATES_AUTO_RELOAD'] = _DEV_MODE
app.jinja_env.auto_reload = _DEV_MODE

# ======== 管理員賬號 ========
ADMIN_ID = 'student_01'

# 设置密钥用于session（從文件讀取或自動生成）
_secret_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.flask_secret')
if os.path.exists(_secret_file):
    with open(_secret_file, 'r') as f:
        app.secret_key = f.read().strip()
else:
    import secrets
    _key = secrets.token_hex(32)
    with open(_secret_file, 'w') as f:
        f.write(_key)
    app.secret_key = _key

# Session 過期與安全設置
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=3)
app.config['SESSION_COOKIE_HTTPONLY'] = True    # JS 不能讀（防 XSS 竊 cookie）
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'   # 跨站 POST 攔一半（防基礎 CSRF）
# SECURE 只在 HTTPS 下生效；用環境變數控制，本機 HTTP 開發不受影響。
# 上線後 nginx 前置 HTTPS 時，設 FLASK_HTTPS_ONLY=1
app.config['SESSION_COOKIE_SECURE'] = bool(os.environ.get('FLASK_HTTPS_ONLY', '').strip())

# ======== 任務 Token 工具（不暴露關鍵字於 URL） ========
def _task_token(student_id, keyword):
    """為 (student, keyword) 生成短哈希 token，不可由學生反推。"""
    msg = f"{student_id}\x00{keyword}".encode('utf-8')
    digest = _hmac.new(app.secret_key.encode('utf-8') if isinstance(app.secret_key, str) else app.secret_key,
                       msg, _hashlib.sha256).hexdigest()
    return digest[:12]

def _resolve_task_token(student_id, token):
    """在該學生已分配的任務中找到與 token 匹配的 keyword；不存在則返回 None。"""
    try:
        tasks = get_student_tasks(student_id)
    except Exception:
        return None
    for t in tasks:
        kw = t.get('keyword', '')
        if kw and _task_token(student_id, kw) == token:
            return kw
    return None

# 优化配置
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # 静态文件缓存1年
app.config['JSON_AS_ASCII'] = False  # 支持中文JSON

# 创建日志目录
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# 配置日志
import logging
from logging.handlers import RotatingFileHandler

# 创建日志处理器
file_handler = RotatingFileHandler(
    os.path.join(log_dir, 'search.log'),
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

# 配置根日志记录器
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

# 禁用 Werkzeug 的默认日志
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# 创建应用日志记录器
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 添加 min 函數到 Jinja2 全局變量
app.jinja_env.globals.update(min=min)

# 圖片 URL 轉換：/static/xxx → /img/xxx
@app.template_filter('img_url')
def img_url_filter(url):
    if url and url.startswith('/static/'):
        return '/img/' + url[8:]
    return url

# ======== 登入驗證裝飾器（提前定義，供後續路由使用）========
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

# 安全頭
@app.after_request
def after_request(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

# Gzip 壓縮（使用 Flask-Compress 如果可用）
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# ======== 圖片索引相關函數 ========
def build_image_index(image_folder):
    index = {}
    for root, _, files in os.walk(image_folder):
        for file in files:
            name, ext = os.path.splitext(file)
            if ext.lower() in ['.jpg', '.png', '.jpeg']:
                index[name.strip()] = os.path.join(root, file)
    return index

def load_image_index(cache_file, image_folder):
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        except Exception:
            pass
    index = build_image_index(image_folder)
    with open(cache_file, 'wb') as f:
        pickle.dump(index, f)
    return index

# ======== 文件大小監控函數 ========
def monitor_file_sizes():
    """監控重要文件的大小變化"""
    try:
        # 監控學生CSV文件
        for student_id in STUDENTS_CONFIG.keys():
            file_path = get_student_file_path(student_id)
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                record_file_size(file_path, file_size)
                
                # 检查是否异常
                if check_file_size_anomaly(file_path, file_size):
                    logger.warning(f"檢測到文件大小異常: {file_path}")
                    
        # 監控主CSV文件
        if os.path.exists(CSV_PATH):
            file_size = os.path.getsize(CSV_PATH)
            record_file_size(CSV_PATH, file_size)
            
            # 检查是否异常
            if check_file_size_anomaly(CSV_PATH, file_size):
                logger.warning(f"檢測到主CSV文件大小異常: {CSV_PATH}")
                
    except Exception as e:
        logger.error(f"文件大小監控失敗: {str(e)}")

# ======== 數據加載相關函數（已改用數據庫，不再需要 CSV 緩存）========
# 注意：char_data 相關函數已移除，現在直接從數據庫查詢

# ======== 全局變量 ========
image_index = None
data_load_lock = threading.Lock()

def ensure_data_loaded():
    """確保圖片索引已經被線程安全地加載到內存中。"""
    global image_index
    if image_index is None:
        with data_load_lock:
            if image_index is None:
                logger.info("[數據加載] 偵測到 image_index 未加載，開始加載...")
                image_index = load_image_index(INDEX_CACHE_FILE, ORIGINAL_IMAGE_FOLDER)
                logger.info("[數據加載] image_index 加載完成。")
temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_images')
if not os.path.exists(temp_dir):
    os.makedirs(temp_dir)

# 創建下載數據的臨時目錄
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# 保存下載數據的文件
DOWNLOAD_FILE = os.path.join(DOWNLOAD_DIR, '下載的精選字圖.csv')

# ======== 合併學生標記（含前人成果） ========
def _clean_mark_value(value):
    value = '' if value is None else str(value).strip()
    if value.lower() in ('nan', 'none', 'null'):
        return ''
    return value


def _summarize_student_mark(mark):
    labels = []
    situation = _clean_mark_value(mark.get('Situation'))
    where = _clean_mark_value(mark.get('where'))
    operation = _clean_mark_value(mark.get('operation'))
    remark = _clean_mark_value(mark.get('備註'))

    if situation == 'Bad':
        labels.append('殘損')
    elif situation:
        labels.append(situation)

    if where.startswith('Mis/'):
        parts = where.split('/')
        if len(parts) >= 3:
            labels.append(f'誤入 {parts[1]}->{parts[2]}')
        else:
            labels.append('誤入')
    elif where:
        labels.append(where)

    number_value = operation.split('/')[-1] if '/' in operation else operation
    if number_value in ('1', '2', '3', '4'):
        labels.append(f'字形 {number_value}')
    elif operation:
        labels.append(operation)

    if remark:
        labels.append(f'備註: {remark[:24]}')
    return ' / '.join(labels)


def _build_keyword_student_contexts(context_keyword):
    """返回做過當前字頭任務的學生賬號，用於區分上下文標注與同字圖歷史標注。"""
    keyword = normalize_search_text(context_keyword)
    if not keyword:
        return {}
    try:
        variants = sorted(get_keyword_search_variants(keyword))
        assignments = get_task_assignments_for_keywords(variants)
    except Exception as e:
        logger.warning(f"[student-mark-context] 讀取字頭任務關聯失敗 keyword={keyword}: {e}")
        return {}

    contexts = {}
    for student_id, rows in assignments.items():
        clean_rows = []
        for row in rows:
            kw = normalize_search_text(row.get('keyword', ''))
            if not kw:
                continue
            clean_rows.append({
                'keyword': kw,
                'status': row.get('status') or '',
                'previous_owner': row.get('previous_owner') or ''
            })
        if clean_rows:
            contexts[student_id] = clean_rows
    return contexts


def _attach_keyword_context_to_marks(data_list, context_keyword):
    if not data_list:
        return data_list

    student_contexts = _build_keyword_student_contexts(context_keyword)
    has_context = bool(student_contexts)

    for row in data_list:
        context_marks = []
        historical_marks = []
        for mark in row.get('other_marks') or []:
            summary = _summarize_student_mark(mark)
            if not summary:
                continue
            item = dict(mark)
            item['summary'] = summary
            contexts = student_contexts.get(item.get('student_id'), [])
            if contexts:
                item['context_keywords'] = [ctx['keyword'] for ctx in contexts]
                item['context_statuses'] = [ctx['status'] for ctx in contexts if ctx.get('status')]
                context_marks.append(item)
            elif has_context:
                historical_marks.append(item)

        row['context_student_marks'] = sorted(
            context_marks,
            key=lambda x: (x.get('student_id') or '', x.get('summary') or '')
        )
        row['historical_student_marks'] = sorted(
            historical_marks,
            key=lambda x: (x.get('student_id') or '', x.get('summary') or '')
        )
    return data_list


def merge_marks_with_history(data_list, student_id, context_keyword=None):
    """合併當前學生標記和所有其他學生的前人成果"""
    if not data_list:
        return merge_student_marks(data_list, {})

    char_ids = [row['char_id'] for row in data_list if row.get('char_id')]
    if not char_ids:
        return merge_student_marks(data_list, {})

    student_marks_dict = {}
    all_marks_dict = {}

    if student_id:
        try:
            marks = get_student_annotations_by_char_ids(student_id, char_ids)
            student_marks_dict = {m['char_id']: m for m in marks} if marks else {}
            # 獲取所有人的標注（含前人成果）
            all_marks_dict = get_all_annotations_by_char_ids(char_ids)
        except Exception as e:
            logger.error(f"合併標記數據時出錯: {str(e)}")

    merged = merge_student_marks(data_list, student_marks_dict, all_marks_dict, student_id)
    if context_keyword:
        merged = _attach_keyword_context_to_marks(merged, context_keyword)
    return merged


# ======== 默认关键词写回 DB ========
def save_default_entries(char_ids, keyword, manuscript_ids=None):
    """按卷限定：先清掉同卷同keyword，再寫入當前列表。
    走原子事務，避免清理後寫入前崩潰留下空狀態。"""
    if not char_ids:
        return 0
    return replace_default_entries(keyword, char_ids, manuscript_ids)

# ======== 原圖回溯路由 (優化版) ========
@app.route('/get_original_image/<char_id>')
@login_required
def get_original_image(char_id):
    # Demo 賬號不提供原圖（保護未公開資料，即使前端被繞過也拒絕）
    if session.get('is_demo'):
        return jsonify({
            'success': False,
            'demo_mode': True,
            'error': '演示模式下暫不提供原圖'
        }), 403
    # 原圖限速(60/分鐘/用戶):防爬蟲批量下載原始掃描
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'original'):
        logger.warning(f"[rate_limit] /get_original_image 超限 user={user_id}")
        return jsonify({'success': False, 'error': 'rate_limited'}), 429
    try:
        # 確保圖片索引已加載(懶加載模式;不加這行首次調用會 NoneType 錯誤)
        ensure_data_loaded()
        # 從數據庫查找數據
        char_info = get_char_by_id(char_id)

        if not char_info:
            return jsonify({'success': False, 'error': '找不到對應的字符數據'}), 404

        img_name = char_info.get('img_name', '').strip()
        if not img_name or img_name not in image_index:
            return jsonify({'success': False, 'error': '找不到對應的原始圖片'}), 404

        img_path = image_index[img_name]
        if not os.path.exists(img_path):
            return jsonify({'success': False, 'error': '圖片文件不存在'}), 404

        img = cv2.imread(img_path)
        if img is None:
            return jsonify({'success': False, 'error': '無法讀取圖片'}), 500

        # 獲取座標
        try:
            x = int(float(char_info['x']))
            y = int(float(char_info['y']))
            w = int(float(char_info['w']))
            h = int(float(char_info['h']))
        except (ValueError, TypeError, KeyError) as e:
            return jsonify({'success': False, 'error': f'座標無效或缺失: {str(e)}'}), 400

        # 畫框：線寬按圖片分辨率自適應，避免大圖上顯得過細
        ih, iw = img.shape[:2]
        thickness = max(4, int(round(max(ih, iw) / 500)))
        cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), thickness)

        # 將處理後的圖片編碼為JPEG格式
        _, buffer = cv2.imencode('.jpg', img)
        
        # 使用 BytesIO 將 buffer 轉換為內存中的文件對象
        img_io = io.BytesIO(buffer)

        # 直接發送圖片文件，而不是Base64；坐標寫入響應頭，供前端初始放大定位。
        response = send_file(
            img_io,
            mimetype='image/jpeg',
            as_attachment=False,
            download_name=f'{char_id}_original.jpg'
        )
        response.headers['X-Original-Image-Width'] = str(iw)
        response.headers['X-Original-Image-Height'] = str(ih)
        response.headers['X-Target-Box'] = json.dumps({
            'x': x,
            'y': y,
            'w': w,
            'h': h,
        }, ensure_ascii=False, separators=(',', ':'))
        return response

    except Exception as e:
        logger.error(f"獲取原圖失敗 (char_id: {char_id}): {str(e)}")
        return jsonify({'success': False, 'error': '服務器內部錯誤'}), 500

@app.route('/processed_image/<filename>')
def get_processed_image(filename):
    try:
        return send_file(
            os.path.join(temp_dir, filename),
            mimetype='image/jpeg',
            as_attachment=False,
            download_name=filename
        )
    except Exception as e:
        return str(e), 404

def extract_page_from_other_info(other_info):
    """從other_info中提取頁碼部分"""
    if not other_info or not other_info.startswith('DH_'):
        return 0
    try:
        # 假設最後兩位是頁碼
        return int(other_info[-2:])
    except (ValueError, IndexError):
        try:
            # 如果失敗，嘗試最後一位
            return int(other_info[-1:])
        except (ValueError, IndexError):
            return 0

def sort_data(data, sort_column=None, sort_direction=None):
    if not sort_column or not sort_direction:
        return data

    reverse = sort_direction == 'desc'

    # 針對「年代信息」的特殊排序邏輯
    if sort_column == 'other_info':
        def get_custom_sort_key(item):
            other_info = item.get('other_info', '')
            code = extract_code_from_other_info(other_info)
            page = extract_page_from_other_info(other_info)

            # 根據標記分配排序權重
            situation = item.get('Situation', '')
            where = str(item.get('where', ''))
            operation = item.get('operation', '')

            status_weight = 0
            if situation == 'Bad':
                status_weight = 3  # 殘損
            elif where.startswith('Mis/'):
                status_weight = 2  # 誤入
            elif operation and operation.strip() and operation.split('/')[-1] in ['1', '2', '3', '4']:
                status_weight = 1  # 數字標記

            # 注意標記優先級排序
            h_atten = item.get('h_atten', '')
            r_atten = item.get('r_atten', '')
            attention_priority = 0
            
            if h_atten == 'need' and r_atten == 'need':
                attention_priority = 3  # 同時標註人工和機器，最高優先級
            elif h_atten == 'need':
                attention_priority = 2  # 只標註人工，第二優先級
            elif r_atten == 'need':
                attention_priority = 1  # 只標註機器，第三優先級
            else:
                attention_priority = 0  # 沒有標註，最低優先級

            # 注意：attention_priority 需要反向排序，因為我們希望高優先級的排在前面
            # 使用負數來實現反向排序
            return (code, -attention_priority, status_weight, page)

        # 使用自定義鍵進行排序
        # reverse 標誌將應用於元組的第一個元素（code），這正是我們想要的
        sorted_data = sorted(data, key=get_custom_sort_key, reverse=reverse)
        return sorted_data

    # 對於其他列，使用原始的排序邏輯
    numeric_columns = {'char_id', 'column_no', 'char_no', 'cid', 'x', 'y', 'w', 'h'}

    def get_sort_key(item):
        value = item.get(sort_column, '')
        if sort_column in numeric_columns:
            try:
                return int(value) if value else 0
            except ValueError:
                return 0
        return str(value)  # 確保所有非數字比較都是字符串比較

    sorted_data = sorted(data, key=get_sort_key, reverse=reverse)
    return sorted_data

def get_paginated_data(data, page, per_page, sort_column=None, sort_direction=None):
    # 先進行排序
    sorted_data = sort_data(data, sort_column, sort_direction)
    
    total_items = len(sorted_data)
    # per_page <= 0 視為不限制，全部展示在一頁
    effective_per_page = per_page if per_page and per_page > 0 else (total_items or 1)
    total_pages = math.ceil(total_items / effective_per_page) if total_items > 0 else 1
    page = min(max(1, page), total_pages)  # 確保頁碼在有效範圍內
    
    start = (page - 1) * effective_per_page
    end = start + effective_per_page
    
    paginated_data = sorted_data[start:end] if sorted_data else []
    return paginated_data, total_pages, total_items, effective_per_page

def get_default_template_data():
    student_id = get_current_student_id()
    return {
        'data': [],
        'manuscript_groups': {},
        'current_page': 1,
        'total_pages': 1,
        'per_page': 100,
        'total_items': 0,
        'search_query': '',
        'search_column': '',
        'advanced_search': False,
        'search_params': {},
        'source_filter': DEFAULT_DEFAULT_SOURCE,
        'keyword_variants': [],
        'has_keyword_variants': False,
        'current_student_id': student_id,
        'current_student_name': get_current_student_name(),
        'students_config': STUDENTS_CONFIG,
        'is_admin': (student_id == ADMIN_ID),
        'defaults_only': False,
        'manuscript_collections': [],
        'selected_manuscript_collection': None,
        'selected_manuscript_collection_id': '',
        'active_manuscript_collection_ids': []
    }


def normalize_per_page(per_page: int) -> int:
    """限制 per_page 上限，0 表示不限制。
    上限 1000：避免單頁渲染過多字圖行導致搜索頁卡頓。"""
    if per_page <= 0:
        return 0
    return min(per_page, 1000)


def normalize_search_text(value):
    """统一搜索字符形态，避免康熙部首/兼容字符与普通汉字混用导致漏查。"""
    if value is None:
        return ''
    return unicodedata.normalize('NFKC', str(value)).strip()


def _load_variant_table():
    """读取本地异体字表，建立正字 -> 异体与异体 -> 正字映射。

    variant.txt 每行第一字视为正字。若同一异体在多行出现，保留文件中先出现的
    正字，避免后续互收条目反复覆盖已有归并关系。
    """
    global _VARIANT_TABLE_CACHE
    with _VARIANT_TABLE_CACHE_LOCK:
        if _VARIANT_TABLE_CACHE is not None:
            return _VARIANT_TABLE_CACHE

        canonical_to_variants = defaultdict(set)
        variant_to_canonical = {}
        conflicts = defaultdict(set)

        try:
            with open(VARIANT_TABLE_PATH, 'r', encoding='utf-8') as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    chars = list(line)
                    canonical = normalize_search_text(chars[0])
                    if not canonical:
                        continue
                    canonical_to_variants[canonical].add(canonical)
                    for raw_char in chars:
                        char = normalize_search_text(raw_char)
                        if not char:
                            continue
                        canonical_to_variants[canonical].add(char)
                        if char in variant_to_canonical and variant_to_canonical[char] != canonical:
                            conflicts[char].update({variant_to_canonical[char], canonical})
                            continue
                        variant_to_canonical[char] = canonical
        except FileNotFoundError:
            logger.warning(f"[keyword-variants] 未找到異體字表: {VARIANT_TABLE_PATH}")
        except Exception as e:
            logger.warning(f"[keyword-variants] 讀取異體字表失敗: {e}")

        if conflicts:
            logger.info(f"[keyword-variants] 異體字表存在 {len(conflicts)} 個互收/衝突字形，已採用先出現正字。")

        _VARIANT_TABLE_CACHE = (
            {k: set(v) for k, v in canonical_to_variants.items()},
            dict(variant_to_canonical),
        )
        return _VARIANT_TABLE_CACHE


def canonicalize_keyword(value):
    """返回用于字头总览/筛选召回的标准字头。仅对单字字头套用异体表。"""
    normalized = normalize_search_text(value)
    if not normalized:
        return ''
    if len(normalized) != 1:
        return normalized
    _canonical_to_variants, variant_to_canonical = _load_variant_table()
    return variant_to_canonical.get(normalized, normalized)


def _add_keyword_variant(mapping, keyword):
    raw = (keyword or '').strip()
    if not raw:
        return
    normalized = normalize_search_text(raw)
    if normalized:
        canonical = canonicalize_keyword(normalized)
        mapping[canonical].add(raw)
        mapping[canonical].add(normalized)
        mapping[canonical].add(canonical)


def _build_keyword_variant_map():
    """建立 标准字头 -> 原始字形集合，用于搜索时合并兼容字与本地异体字表。"""
    mapping = defaultdict(set)

    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            for (keyword,) in conn.execute(
                "SELECT DISTINCT default_keyword FROM characters "
                "WHERE default_keyword IS NOT NULL AND TRIM(default_keyword) != ''"
            ).fetchall():
                _add_keyword_variant(mapping, keyword)
    except Exception as e:
        logger.warning(f"[keyword-variants] 讀取 default_keyword 失敗: {e}")

    try:
        with _database.get_db_connection() as conn:
            for table in ('task_assignments', 'task_pool'):
                try:
                    for (keyword,) in conn.execute(
                        f"SELECT DISTINCT keyword FROM {table} "
                        "WHERE keyword IS NOT NULL AND TRIM(keyword) != ''"
                    ).fetchall():
                        _add_keyword_variant(mapping, keyword)
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[keyword-variants] 讀取 students keyword 失敗: {e}")

    for db_names in DEFAULT_SOURCE_TO_DB.values():
        if not db_names:
            continue
        for db_name in db_names:
            db_path = os.path.join(AI_PICK_DB_DIR, db_name)
            if not os.path.exists(db_path):
                continue
            try:
                with sqlite3.connect(db_path) as conn:
                    for (keyword,) in conn.execute(
                        "SELECT DISTINCT keyword FROM ai_selections "
                        "WHERE keyword IS NOT NULL AND TRIM(keyword) != ''"
                    ).fetchall():
                        _add_keyword_variant(mapping, keyword)
            except Exception as e:
                logger.warning(f"[keyword-variants] 讀取 AI keyword 失敗 {db_path}: {e}")

    return {k: set(v) for k, v in mapping.items()}


def get_keyword_variants(keyword):
    """返回同一标准字头下的所有已知原始字形。"""
    canonical = canonicalize_keyword(keyword)
    if not canonical:
        return set()
    global _KEYWORD_VARIANT_CACHE
    with _KEYWORD_VARIANT_CACHE_LOCK:
        if _KEYWORD_VARIANT_CACHE is None:
            _KEYWORD_VARIANT_CACHE = _build_keyword_variant_map()
        variants = set(_KEYWORD_VARIANT_CACHE.get(canonical, set()))
    variants.add(canonical)
    return variants


def get_keyword_search_variants(keyword):
    """返回搜索时应合并召回的字形，额外补上简繁互转结果。"""
    normalized = normalize_search_text(keyword)
    if not normalized:
        return set()

    variants = set(get_keyword_variants(normalized))
    variants.add(normalized)

    if len(normalized) == 1:
        for converter in (_OPENCC_S2T, _OPENCC_T2S):
            if not converter:
                continue
            try:
                converted = normalize_search_text(converter.convert(normalized))
            except Exception:
                continue
            if converted and len(converted) == 1:
                variants.add(converted)
                variants.update(get_keyword_variants(converted))

    return variants


def _normalize_source_filter(raw_value):
    """将来源参数标准化为合法值。"""
    if raw_value in DEFAULT_SOURCE_TO_DB:
        return raw_value
    return DEFAULT_DEFAULT_SOURCE


def _read_ai_pick_char_ids(db_name):
    """读取单个 ai_picks 数据库中的 top5_char_ids，返回 char_id 集合。"""
    db_path = os.path.join(AI_PICK_DB_DIR, db_name)
    try:
        st = os.stat(db_path)
    except FileNotFoundError:
        logger.warning(f"[defaults-source] 未找到 AI DB: {db_path}")
        return set()

    with _AI_SOURCE_CACHE_LOCK:
        cached = _AI_SOURCE_CACHE.get(db_path)
        if cached and cached.get('mtime') == st.st_mtime and cached.get('size') == st.st_size:
            return set(cached.get('ids', set()))

        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT top5_char_ids FROM ai_selections "
                "WHERE top5_char_ids IS NOT NULL AND TRIM(top5_char_ids) != ''"
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.error(f"[defaults-source] 讀取 {db_path} 失敗: {e}")
            return set()

        char_ids = set()
        for (raw_ids,) in rows:
            if not raw_ids:
                continue
            try:
                ids = json.loads(raw_ids)
            except Exception:
                continue
            if not isinstance(ids, list):
                continue
            for cid in ids:
                if not cid:
                    continue
                char_ids.add(str(cid))

        _AI_SOURCE_CACHE[db_path] = {
            'mtime': st.st_mtime,
            'size': st.st_size,
            'ids': set(char_ids),
        }
        return set(char_ids)


def _resolve_source_char_ids(raw_source):
    """根据来源参数，返回 (char_id_set, include_default_in_source)."""
    source = _normalize_source_filter(raw_source)
    if source == 'manual':
        return None, False

    db_spec = DEFAULT_SOURCE_TO_DB.get(source, None)
    if db_spec is None:
        return None, False

    if isinstance(db_spec, str):
        return _read_ai_pick_char_ids(db_spec), False

    ids = set()
    for db_name in db_spec:
        ids.update(_read_ai_pick_char_ids(db_name))

    if source == 'all':
        return ids, True
    return ids, False


def _read_ai_pick_keyword_to_char_ids(db_name):
    """读取单个 ai_picks 数据库，返回 keyword -> char_id集合。"""
    db_path = os.path.join(AI_PICK_DB_DIR, db_name)
    result = defaultdict(set)
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT keyword, top5_char_ids FROM ai_selections "
            "WHERE keyword IS NOT NULL AND TRIM(keyword) != '' "
            "AND top5_char_ids IS NOT NULL AND TRIM(top5_char_ids) != ''"
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"[defaults-summary] 未讀取到 AI DB: {db_path}, {e}")
        return {}

    for keyword, raw_ids in rows:
        try:
            ids = json.loads(raw_ids)
        except Exception:
            continue
        if not isinstance(ids, list):
            continue
        for cid in ids:
            if cid:
                result[str(keyword)].add(str(cid))
    return {k: set(v) for k, v in result.items()}


def _read_ai_pick_char_ids_for_keyword(db_name, keyword):
    """读取单个 ai_picks 数据库中某个字头任务下的候选 char_id。"""
    db_path = os.path.join(AI_PICK_DB_DIR, db_name)
    result = set()
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT top5_char_ids FROM ai_selections "
                "WHERE keyword = ? "
                "AND top5_char_ids IS NOT NULL AND TRIM(top5_char_ids) != ''",
                (keyword,)
            ).fetchall()
    except Exception as e:
        logger.warning(f"[screened-keyword] 未讀取到 AI DB: {db_path}, {e}")
        return result

    for (raw_ids,) in rows:
        try:
            ids = json.loads(raw_ids)
        except Exception:
            continue
        if not isinstance(ids, list):
            continue
        for cid in ids:
            if cid:
                result.add(str(cid))
    return result


def _fetch_char_rows_by_source_map(source_map, page=1, per_page=100, sort_column='other_info',
                                   sort_direction='asc', source_variant_map=None):
    """按 char_id 來源集合查回 characters 詳情，並附加來源標記。"""
    char_ids = [cid for cid in source_map if cid]
    if not char_ids:
        return [], 1, 0

    try:
        page_int = max(1, int(page))
    except (TypeError, ValueError):
        page_int = 1
    try:
        per_page_int = int(per_page)
    except (TypeError, ValueError):
        per_page_int = 100
    if per_page_int <= 0:
        per_page_int = len(char_ids)

    total_count = len(char_ids)
    total_pages = max(1, math.ceil(total_count / per_page_int))
    page_int = min(page_int, total_pages)

    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('CREATE TEMP TABLE IF NOT EXISTS tmp_screened_char_ids (char_id TEXT PRIMARY KEY)')
            conn.execute('DELETE FROM tmp_screened_char_ids')
            conn.executemany(
                'INSERT OR IGNORE INTO tmp_screened_char_ids(char_id) VALUES (?)',
                [(cid,) for cid in char_ids]
            )

            safe_sort = sort_column if sort_column in {
                'char_id', 'cmp_txt', 'txt', 'other_info', 'ocr_txt正', 'ocr_col正', 'default_keyword'
            } else 'other_info'
            dir_upper = 'DESC' if str(sort_direction).upper() == 'DESC' else 'ASC'

            sql = (
                "SELECT * FROM characters "
                "WHERE char_id IN (SELECT char_id FROM tmp_screened_char_ids)"
            )
            if safe_sort == 'other_info':
                attn_dir = 'ASC' if dir_upper == 'DESC' else 'DESC'
                sql += (f' ORDER BY other_info_code {dir_upper}, '
                        f'h_atten_priority {attn_dir}, other_info_page {dir_upper}, char_id ASC')
            else:
                sql += f' ORDER BY {safe_sort} {dir_upper}, char_id ASC'

            rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except Exception as e:
        logger.error(f"[screened-keyword] 查詢字圖詳情失敗: {e}")
        return [], 1, 0

    def source_rank(char_id):
        tags = source_map.get(str(char_id), set())
        if 'manual' in tags or 'gemini_pro' in tags:
            return 0
        if 'gemini_fresh' in tags:
            return 1
        return 2

    if (sort_column or 'other_info') == 'other_info':
        group_order = {}
        for index, row in enumerate(rows):
            group_key = row.get('other_info') or row.get('other_info_code') or ''
            group_order.setdefault(group_key, len(group_order))
            row['_screened_original_index'] = index
        rows.sort(key=lambda row: (
            group_order.get(row.get('other_info') or row.get('other_info_code') or '', 999999999),
            source_rank(row.get('char_id')),
            row.get('_screened_original_index', 0)
        ))

    total_count = len(rows)
    total_pages = max(1, math.ceil(total_count / per_page_int)) if per_page_int > 0 else 1
    page_int = min(page_int, total_pages)
    offset = (page_int - 1) * per_page_int
    rows = rows[offset:offset + per_page_int]

    for row in rows:
        row.pop('_screened_original_index', None)
        char_id = str(row.get('char_id'))
        tags = source_map.get(char_id, set())
        ordered_tags = [label for key, label in (
            ('manual', '人工已確認'),
            ('gemini_pro', 'Gemini Pro 候選'),
            ('gemini_fresh', 'Gemini Fresh 候選'),
        ) if key in tags]
        row['source_tags'] = ordered_tags
        row['source_keys'] = sorted(tags)
        row['is_manual_confirmed'] = 'manual' in tags
        row['matched_keyword_variants'] = sorted((source_variant_map or {}).get(char_id, set()))

    return rows, total_pages, total_count


def _get_screened_chars_by_keyword(keyword, source_filter='all', page=1, per_page=100,
                                   sort_column='other_info', sort_direction='asc'):
    """按字頭任務聚合人工確認與 Gemini 候選。"""
    keyword = normalize_search_text(keyword)
    source_filter = _normalize_source_filter(source_filter)
    if not keyword:
        return [], 1, 0

    source_map = defaultdict(set)
    source_variant_map = defaultdict(set)
    keyword_variants = get_keyword_search_variants(keyword)

    if source_filter in ('manual', 'all'):
        try:
            with sqlite3.connect(CHAR_DB_PATH) as conn:
                placeholders = ','.join(['?'] * len(keyword_variants))
                for char_id, matched_keyword in conn.execute(
                    f"SELECT char_id, default_keyword FROM characters WHERE default_keyword IN ({placeholders})",
                    tuple(keyword_variants)
                ).fetchall():
                    if char_id:
                        cid = str(char_id)
                        source_map[cid].add('manual')
                        source_variant_map[cid].add(str(matched_keyword))
        except Exception as e:
            logger.error(f"[screened-keyword] 讀取人工默認字圖失敗 keyword={keyword}: {e}")

    if source_filter in ('gemini_pro', 'all'):
        for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_pro', []):
            for variant in keyword_variants:
                for char_id in _read_ai_pick_char_ids_for_keyword(db_name, variant):
                    cid = str(char_id)
                    source_map[cid].add('gemini_pro')
                    source_variant_map[cid].add(str(variant))

    if source_filter in ('gemini_fresh', 'all'):
        for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_fresh', []):
            for variant in keyword_variants:
                for char_id in _read_ai_pick_char_ids_for_keyword(db_name, variant):
                    cid = str(char_id)
                    source_map[cid].add('gemini_fresh')
                    source_variant_map[cid].add(str(variant))

    return _fetch_char_rows_by_source_map(
        source_map,
        page=page,
        per_page=per_page,
        sort_column=sort_column,
        sort_direction=sort_direction,
        source_variant_map=source_variant_map
    )


def _build_keyword_source_maps(keyword):
    """建立某字頭下人工/Gemini來源映射，用于给全局候选搜索结果加标签。"""
    keyword = normalize_search_text(keyword)
    source_map = defaultdict(set)
    source_variant_map = defaultdict(set)
    if not keyword:
        return source_map, source_variant_map

    keyword_variants = get_keyword_search_variants(keyword)

    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            placeholders = ','.join(['?'] * len(keyword_variants))
            for char_id, matched_keyword in conn.execute(
                f"SELECT char_id, default_keyword FROM characters WHERE default_keyword IN ({placeholders})",
                tuple(keyword_variants)
            ).fetchall():
                if char_id:
                    cid = str(char_id)
                    source_map[cid].add('manual')
                    source_variant_map[cid].add(str(matched_keyword))
    except Exception as e:
        logger.error(f"[global-candidates] 讀取人工默認字圖失敗 keyword={keyword}: {e}")

    for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_pro', []):
        for variant in keyword_variants:
            for char_id in _read_ai_pick_char_ids_for_keyword(db_name, variant):
                cid = str(char_id)
                source_map[cid].add('gemini_pro')
                source_variant_map[cid].add(str(variant))

    for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_fresh', []):
        for variant in keyword_variants:
            for char_id in _read_ai_pick_char_ids_for_keyword(db_name, variant):
                cid = str(char_id)
                source_map[cid].add('gemini_fresh')
                source_variant_map[cid].add(str(variant))

    return source_map, source_variant_map


def _annotate_rows_with_source_tags(rows, keyword):
    """给全局候选搜索结果叠加人工/Gemini筛选来源标记。"""
    source_map, source_variant_map = _build_keyword_source_maps(keyword)
    for row in rows:
        char_id = str(row.get('char_id') or '')
        tags = source_map.get(char_id, set())
        ordered_tags = [label for key, label in (
            ('manual', '人工已確認'),
            ('gemini_pro', 'Gemini Pro 候選'),
            ('gemini_fresh', 'Gemini Fresh 候選'),
        ) if key in tags]
        row['source_tags'] = ordered_tags
        row['source_keys'] = sorted(tags)
        row['is_manual_confirmed'] = 'manual' in tags
        row['matched_keyword_variants'] = sorted(source_variant_map.get(char_id, set()))
    return rows


AI_AUDIT_STATUS_META = {
    'missing_ai': {
        'label': '強命中無AI',
        'class': 'danger',
        'priority': 10,
        'action': '這類最像漏篩：整庫已有強命中，但沒有人工選定，也沒有 Gemini 記錄。',
    },
    'ai_empty': {
        'label': 'AI空選/無候選',
        'class': 'warn',
        'priority': 20,
        'action': 'Gemini 跑過但沒有給出可見候選，建議人工打開整庫結果復核。',
    },
    'ai_candidate': {
        'label': '有AI候選',
        'class': 'info',
        'priority': 30,
        'action': '已有 Gemini 候選但尚無人工選定，可按候選再確認。',
    },
    'weak_only': {
        'label': '僅弱命中',
        'class': 'muted',
        'priority': 40,
        'action': '主要來自 alternatives，容易混入相關字，可低優先級抽查。',
    },
    'manual_done': {
        'label': '已有人工',
        'class': 'ok',
        'priority': 60,
        'action': '已有人工選定，通常不需要補跑；若仍疑似漏字，再人工核對。',
    },
}


def _quote_sql_identifier(name):
    return '"' + str(name).replace('"', '""') + '"'


def _value_has_any_term(value, terms):
    text = str(value or '')
    if not text:
        return False
    return any(term and term in text for term in terms)


def _row_field_hits(row, fields, terms):
    return [field for field in fields if _value_has_any_term(row.get(field), terms)]


def _parse_ai_top5_count(raw_ids):
    if not raw_ids:
        return 0
    try:
        ids = json.loads(raw_ids)
    except Exception:
        return 0
    if not isinstance(ids, list):
        return 0
    return sum(1 for cid in ids if cid)


def _load_ai_audit_records(db_name, keyword_variants):
    """按寫卷聚合某個 AI 庫對當前字頭的審核記錄。"""
    db_path = os.path.join(AI_PICK_DB_DIR, db_name)
    if not os.path.exists(db_path) or not keyword_variants:
        return {}

    placeholders = ','.join(['?'] * len(keyword_variants))
    records = {}
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT manuscript, keyword, candidate_total, top5_char_ids, "
                "overall_quality, model, created_at "
                f"FROM ai_selections WHERE keyword IN ({placeholders})",
                tuple(keyword_variants)
            ).fetchall()
    except Exception as e:
        logger.warning(f"[ai-coverage] 讀取 AI 記錄失敗 {db_path}: {e}")
        return {}

    for row in rows:
        manuscript = str(row['manuscript'] or '').strip()
        if not manuscript:
            continue
        entry = records.setdefault(manuscript, {
            'has_record': True,
            'record_count': 0,
            'keywords': set(),
            'candidate_total': 0,
            'candidate_total_known': 0,
            'top5_count': 0,
            'empty_records': 0,
            'zero_candidate_records': 0,
            'qualities': set(),
            'models': set(),
            'latest_created_at': '',
        })
        entry['record_count'] += 1
        entry['keywords'].add(str(row['keyword'] or ''))

        candidate_total = row['candidate_total']
        if candidate_total is not None:
            try:
                candidate_total_int = int(candidate_total)
            except (TypeError, ValueError):
                candidate_total_int = 0
            entry['candidate_total'] += max(candidate_total_int, 0)
            entry['candidate_total_known'] += 1
            if candidate_total_int <= 0:
                entry['zero_candidate_records'] += 1

        top5_count = _parse_ai_top5_count(row['top5_char_ids'])
        entry['top5_count'] += top5_count
        if top5_count <= 0:
            entry['empty_records'] += 1

        if row['overall_quality']:
            entry['qualities'].add(str(row['overall_quality']))
        if row['model']:
            entry['models'].add(str(row['model']))
        created_at = str(row['created_at'] or '')
        if created_at and created_at > entry['latest_created_at']:
            entry['latest_created_at'] = created_at

    for entry in records.values():
        entry['keywords'] = sorted(k for k in entry['keywords'] if k)
        entry['qualities'] = sorted(entry['qualities'])
        entry['models'] = sorted(entry['models'])
        entry['has_top5'] = entry['top5_count'] > 0
        entry['all_empty'] = entry['record_count'] > 0 and entry['top5_count'] <= 0

    return records


def _empty_ai_record():
    return {
        'has_record': False,
        'record_count': 0,
        'keywords': [],
        'candidate_total': 0,
        'candidate_total_known': 0,
        'top5_count': 0,
        'empty_records': 0,
        'zero_candidate_records': 0,
        'qualities': [],
        'models': [],
        'latest_created_at': '',
        'has_top5': False,
        'all_empty': False,
    }


def _format_ai_audit_record(record):
    if not record or not record.get('has_record'):
        return '-'
    if record.get('has_top5'):
        return f"有候選 {record.get('top5_count', 0)} / 記錄 {record.get('record_count', 0)}"
    if record.get('zero_candidate_records', 0) >= record.get('record_count', 0):
        return f"無候選 / 記錄 {record.get('record_count', 0)}"
    return f"空選 / 記錄 {record.get('record_count', 0)}"


def _resolve_ai_audit_status(item):
    pro = item.get('gemini_pro') or _empty_ai_record()
    fresh = item.get('gemini_fresh') or _empty_ai_record()
    has_ai_record = bool(pro.get('has_record') or fresh.get('has_record'))
    has_ai_top5 = bool(pro.get('has_top5') or fresh.get('has_top5'))

    if item.get('manual_count', 0) > 0:
        return 'manual_done'
    if item.get('strong_rows', 0) <= 0:
        return 'weak_only'
    if not has_ai_record:
        return 'missing_ai'
    if not has_ai_top5:
        return 'ai_empty'
    return 'ai_candidate'


def _build_ai_coverage_audit(keyword):
    """生成某字頭在整庫中的人工/Gemini 覆蓋審計，僅讀取不寫入。"""
    keyword = normalize_search_text(keyword)
    if not keyword:
        return {
            'keyword': '',
            'variants': [],
            'rows': [],
            'summary': {},
            'status_meta': AI_AUDIT_STATUS_META,
        }

    keyword_variants = sorted(get_keyword_search_variants(keyword))
    if not keyword_variants:
        keyword_variants = [keyword]

    clauses = []
    params = []
    for field in AI_AUDIT_SEARCH_FIELDS:
        quoted_field = _quote_sql_identifier(field)
        for variant in keyword_variants:
            clauses.append(f"{quoted_field} LIKE ?")
            params.append(f"%{variant}%")

    select_fields = [
        'char_id', 'other_info', 'other_info_code', 'other_info_page',
        'ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives',
        'ocr_txt正', 'ocr_col正', 'default_keyword'
    ]
    sql = (
        "SELECT " + ', '.join(_quote_sql_identifier(field) for field in select_fields) +
        " FROM characters WHERE " + " OR ".join(clauses) +
        " ORDER BY other_info_code ASC, h_atten_priority DESC, other_info_page ASC, char_id ASC"
    )

    manuscript_items = {}
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            db_rows = [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
    except Exception as e:
        logger.error(f"[ai-coverage] 查詢整庫命中失敗 keyword={keyword}: {e}")
        db_rows = []

    for row in db_rows:
        manuscript = str(row.get('other_info_code') or '').strip()
        if not manuscript:
            manuscript = extract_code_from_other_info(row.get('other_info') or '') or '未識別寫卷'

        item = manuscript_items.setdefault(manuscript, {
            'manuscript': manuscript,
            'total_rows': 0,
            'strong_rows': 0,
            'auth_rows': 0,
            'ocr_rows': 0,
            'weak_rows': 0,
            'manual_count': 0,
            'other_default_count': 0,
            'sample_char_ids': [],
            'sample_pages': set(),
            'hit_fields': defaultdict(int),
        })
        item['total_rows'] += 1

        auth_hits = _row_field_hits(row, AI_AUDIT_AUTH_FIELDS, keyword_variants)
        ocr_hits = _row_field_hits(row, AI_AUDIT_OCR_FIELDS, keyword_variants)
        weak_hits = _row_field_hits(row, AI_AUDIT_WEAK_FIELDS, keyword_variants)
        strong_hits = auth_hits + ocr_hits

        if strong_hits:
            item['strong_rows'] += 1
        if auth_hits:
            item['auth_rows'] += 1
        if ocr_hits:
            item['ocr_rows'] += 1
        if weak_hits and not strong_hits:
            item['weak_rows'] += 1

        for field in strong_hits + weak_hits:
            item['hit_fields'][field] += 1

        default_keyword = normalize_search_text(row.get('default_keyword') or '')
        if default_keyword in keyword_variants:
            item['manual_count'] += 1
        elif default_keyword:
            item['other_default_count'] += 1

        if len(item['sample_char_ids']) < 5 and row.get('char_id'):
            item['sample_char_ids'].append(str(row['char_id']))
        if row.get('other_info_page') is not None and len(item['sample_pages']) < 5:
            item['sample_pages'].add(str(row['other_info_page']))

    pro_records = {}
    for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_pro', []):
        for manuscript, record in _load_ai_audit_records(db_name, keyword_variants).items():
            pro_records[manuscript] = record

    fresh_records = {}
    for db_name in DEFAULT_SOURCE_TO_DB.get('gemini_fresh', []):
        for manuscript, record in _load_ai_audit_records(db_name, keyword_variants).items():
            fresh_records[manuscript] = record

    all_manuscripts = set(manuscript_items) | set(pro_records) | set(fresh_records)
    rows = []
    for manuscript in all_manuscripts:
        item = manuscript_items.get(manuscript, {
            'manuscript': manuscript,
            'total_rows': 0,
            'strong_rows': 0,
            'auth_rows': 0,
            'ocr_rows': 0,
            'weak_rows': 0,
            'manual_count': 0,
            'other_default_count': 0,
            'sample_char_ids': [],
            'sample_pages': set(),
            'hit_fields': defaultdict(int),
        })
        item['gemini_pro'] = pro_records.get(manuscript, _empty_ai_record())
        item['gemini_fresh'] = fresh_records.get(manuscript, _empty_ai_record())
        status_key = _resolve_ai_audit_status(item)
        item['status_key'] = status_key
        item['status'] = AI_AUDIT_STATUS_META[status_key]
        item['gemini_pro_label'] = _format_ai_audit_record(item['gemini_pro'])
        item['gemini_fresh_label'] = _format_ai_audit_record(item['gemini_fresh'])
        item['sample_pages'] = sorted(item['sample_pages'])
        item['hit_fields'] = dict(sorted(item['hit_fields'].items()))
        rows.append(item)

    rows.sort(key=lambda item: (
        item['status']['priority'],
        0 if item.get('strong_rows', 0) > 0 else 1,
        -item.get('strong_rows', 0),
        -item.get('total_rows', 0),
        item['manuscript']
    ))

    status_counts = defaultdict(int)
    for item in rows:
        status_counts[item['status_key']] += 1

    summary = {
        'total_manuscripts': len(rows),
        'total_rows': sum(item.get('total_rows', 0) for item in rows),
        'strong_manuscripts': sum(1 for item in rows if item.get('strong_rows', 0) > 0),
        'manual_done': status_counts.get('manual_done', 0),
        'missing_ai': status_counts.get('missing_ai', 0),
        'ai_empty': status_counts.get('ai_empty', 0),
        'ai_candidate': status_counts.get('ai_candidate', 0),
        'weak_only': status_counts.get('weak_only', 0),
    }

    return {
        'keyword': keyword,
        'variants': keyword_variants,
        'rows': rows,
        'summary': summary,
        'status_meta': AI_AUDIT_STATUS_META,
    }


def _load_ai_done_pairs():
    """读取正式 Gemini 库里已经跑过的 (manuscript, keyword)，用于找 n=1 漏覆盖候选。"""
    done = set()
    for db_name in DEFAULT_SOURCE_TO_DB.get('global_all', []):
        db_path = os.path.join(AI_PICK_DB_DIR, db_name)
        if not os.path.exists(db_path):
            continue
        try:
            with sqlite3.connect(db_path) as conn:
                for manuscript, keyword in conn.execute(
                    "SELECT manuscript, keyword FROM ai_selections "
                    "WHERE manuscript IS NOT NULL AND keyword IS NOT NULL"
                ).fetchall():
                    done.add((str(manuscript), str(keyword)))
        except Exception as e:
            logger.warning(f"[single-candidates] 讀取 AI done pairs 失敗 {db_path}: {e}")
    return done


def _normalize_single_candidate_source_filter(value):
    if value in ('all', 'txt', 'cmp_txt', 'ocr_col正', 'ocr_col', 'ocr_txt'):
        return value
    return 'all'


def _is_likely_han_char(value):
    value = normalize_search_text(value)
    if len(value) != 1:
        return False
    code = ord(value)
    return (
        0x3400 <= code <= 0x4DBF or
        0x4E00 <= code <= 0x9FFF or
        0xF900 <= code <= 0xFAFF or
        0x20000 <= code <= 0x2EBEF or
        0x30000 <= code <= 0x323AF
    )


def _load_selected_char_ids(char_ids):
    """批量读取当前已写 default_keyword 的 char_id，用来让缓存页避开已选项目。"""
    selected = set()
    clean_ids = [str(cid) for cid in char_ids if cid]
    if not clean_ids:
        return selected
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            for i in range(0, len(clean_ids), 800):
                chunk = clean_ids[i:i + 800]
                placeholders = ','.join(['?'] * len(chunk))
                for (char_id,) in conn.execute(
                    f"SELECT char_id FROM characters "
                    f"WHERE char_id IN ({placeholders}) "
                    "AND default_keyword IS NOT NULL AND TRIM(default_keyword) != ''",
                    tuple(chunk)
                ).fetchall():
                    selected.add(str(char_id))
    except Exception as e:
        logger.warning(f"[single-candidates] 讀取已選 char_id 失敗: {e}")
    return selected


def _build_single_candidate_cache():
    """构建旧 Gemini pending 口径下的 n=1 候选清单。"""
    ai_done = _load_ai_done_pairs()
    sql = """
        SELECT
            *,
            COALESCE(
                NULLIF(txt, ''),
                NULLIF(cmp_txt, ''),
                NULLIF("ocr_col正", ''),
                NULLIF(ocr_col, ''),
                NULLIF(ocr_txt, '')
            ) AS candidate_keyword,
            CASE
                WHEN NULLIF(txt, '') IS NOT NULL THEN 'txt'
                WHEN NULLIF(cmp_txt, '') IS NOT NULL THEN 'cmp_txt'
                WHEN NULLIF("ocr_col正", '') IS NOT NULL THEN 'ocr_col正'
                WHEN NULLIF(ocr_col, '') IS NOT NULL THEN 'ocr_col'
                WHEN NULLIF(ocr_txt, '') IS NOT NULL THEN 'ocr_txt'
                ELSE ''
            END AS candidate_source_field
        FROM characters
        WHERE other_info_code IS NOT NULL
          AND TRIM(other_info_code) != ''
    """
    pair_state = {}
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(sql):
                item = dict(row)
                keyword = normalize_search_text(item.get('candidate_keyword') or '')
                if not _is_likely_han_char(keyword):
                    continue
                manuscript = str(item.get('other_info_code') or '')
                key = (manuscript, keyword)
                state = pair_state.setdefault(key, {
                    'count': 0,
                    'done': 0,
                    'row': None,
                })
                state['count'] += 1
                if normalize_search_text(item.get('default_keyword') or ''):
                    state['done'] += 1
                state['row'] = item
    except Exception as e:
        logger.error(f"[single-candidates] 構建 n=1 候選快取失敗: {e}")
        return []

    rows = []
    for (manuscript, keyword), state in pair_state.items():
        if state['count'] != 1 or state['done'] != 0:
            continue
        if (manuscript, keyword) in ai_done:
            continue
        row = state['row']
        row['candidate_keyword'] = keyword
        row['candidate_count'] = 1
        row['source_label'] = {
            'txt': '人工校對',
            'cmp_txt': 'CBETA',
            'ocr_col正': '列校對（正字）',
            'ocr_col': '列 OCR',
            'ocr_txt': '字 OCR',
        }.get(row.get('candidate_source_field'), row.get('candidate_source_field') or '-')
        rows.append(row)

    rows.sort(key=lambda r: (
        r.get('candidate_keyword') or '',
        r.get('other_info_code') or '',
        r.get('other_info_page') or 0,
        r.get('char_id') or '',
    ))
    logger.info(f"[single-candidates] n=1 快取完成，共 {len(rows)} 條")
    return rows


def _get_single_candidate_cache():
    global _SINGLE_CANDIDATE_CACHE
    with _SINGLE_CANDIDATE_CACHE_LOCK:
        if _SINGLE_CANDIDATE_CACHE is None:
            _SINGLE_CANDIDATE_CACHE = _build_single_candidate_cache()
        return list(_SINGLE_CANDIDATE_CACHE)


def _get_single_candidate_keyword_counts():
    """返回當前未補選 n=1 候選的 keyword -> 數量。"""
    rows = _get_single_candidate_cache()
    selected_ids = _load_selected_char_ids([row.get('char_id') for row in rows])
    counts = Counter()
    for row in rows:
        if str(row.get('char_id') or '') in selected_ids:
            continue
        keyword = normalize_search_text(row.get('candidate_keyword') or '')
        if keyword:
            counts[keyword] += 1
    return counts


def _annotate_single_candidate_rows(rows, keyword):
    """給單字候選行疊加既有人工/Gemini標記與單候選標籤。"""
    rows = _annotate_rows_with_source_tags(rows, keyword)
    for row in rows:
        tags = list(row.get('source_tags') or [])
        if '單字候選' not in tags:
            tags.append('單字候選')
        keys = set(row.get('source_keys') or [])
        keys.add('single_candidate')
        matched = set(row.get('matched_keyword_variants') or [])
        candidate_keyword = normalize_search_text(row.get('candidate_keyword') or '')
        if candidate_keyword:
            matched.add(candidate_keyword)
        row['source_tags'] = tags
        row['source_keys'] = sorted(keys)
        row['matched_keyword_variants'] = sorted(matched)
    return rows


def _sort_single_candidate_rows(rows, sort_column='other_info', sort_direction='asc'):
    direction = (sort_direction or 'asc').lower()
    if sort_column == 'other_info':
        rows.sort(key=lambda r: (
            r.get('other_info_code') or extract_code_from_other_info(r.get('other_info', '')) or '',
            r.get('other_info_page') or 0,
            r.get('other_info') or '',
            r.get('char_id') or '',
        ))
        if direction == 'desc':
            rows.reverse()
        return rows
    if sort_column:
        return sort_data_in_python(rows, sort_column, direction)
    return rows


def _fetch_single_candidate_rows(keyword='', manuscript='', source_filter='all',
                                 page=1, per_page=100,
                                 sort_column='other_info', sort_direction='asc'):
    """按旧 Gemini pending 逻辑找 n=1 候选：未人工选定、未进正式 AI 结果库。"""
    keyword = normalize_search_text(keyword)
    manuscript = normalize_search_text(manuscript)
    source_filter = _normalize_single_candidate_source_filter(source_filter)
    page = max(1, int(page or 1))
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 100
    if per_page > 0:
        per_page = max(1, min(per_page, 1000))
    keyword_variants = set(get_keyword_search_variants(keyword)) if keyword else set()
    rows = []
    for row in _get_single_candidate_cache():
        if keyword_variants and row.get('candidate_keyword') not in keyword_variants:
            continue
        if manuscript and manuscript not in str(row.get('other_info_code') or ''):
            continue
        if source_filter != 'all' and row.get('candidate_source_field') != source_filter:
            continue
        rows.append(row)

    selected_ids = _load_selected_char_ids([row.get('char_id') for row in rows])
    if selected_ids:
        rows = [row for row in rows if str(row.get('char_id') or '') not in selected_ids]

    rows = _sort_single_candidate_rows(rows, sort_column=sort_column, sort_direction=sort_direction)
    total_count = len(rows)
    if per_page <= 0:
        total_pages = 1
        page = 1
        page_rows = rows
        effective_per_page = total_count or 1
    else:
        total_pages = max(1, math.ceil(total_count / per_page)) if total_count else 1
        page = min(page, total_pages)
        offset = (page - 1) * per_page
        page_rows = rows[offset:offset + per_page]
        effective_per_page = per_page
    return page_rows, {
        'total_count': total_count,
        'total_pages': total_pages,
        'page': page,
        'per_page': effective_per_page,
        'keyword': keyword,
        'manuscript': manuscript,
        'source_filter': source_filter,
    }


def _ensure_single_candidate_cleaner_db():
    os.makedirs(os.path.dirname(SINGLE_CANDIDATE_CLEANER_DB_PATH), exist_ok=True)
    with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS single_candidate_discards (
                keyword TEXT NOT NULL,
                char_id TEXT NOT NULL,
                discarded INTEGER NOT NULL DEFAULT 1,
                updated_by TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword, char_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_single_candidate_discards_keyword
            ON single_candidate_discards(keyword, discarded)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS single_candidate_notes (
                keyword TEXT NOT NULL,
                char_id TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                updated_by TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword, char_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_single_candidate_notes_keyword
            ON single_candidate_notes(keyword)
        """)


def _backup_single_candidate_cleaner_db(reason='manual'):
    if not os.path.exists(SINGLE_CANDIDATE_CLEANER_DB_PATH):
        return ''
    os.makedirs(SINGLE_CANDIDATE_CLEANER_BACKUP_DIR, exist_ok=True)
    safe_reason = re.sub(r'[^0-9A-Za-z_-]+', '_', str(reason or 'manual')).strip('_') or 'manual'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(
        SINGLE_CANDIDATE_CLEANER_BACKUP_DIR,
        f'single_candidate_cleaner_{stamp}_{safe_reason}.db'
    )
    with _SINGLE_CANDIDATE_BACKUP_LOCK:
        with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as src:
            src.execute("PRAGMA busy_timeout=30000")
            with sqlite3.connect(backup_path, timeout=30) as dst:
                src.backup(dst)
    logger.info(f"[single-candidate-cleaner] backup created: {backup_path}")
    return backup_path


def _maybe_backup_single_candidate_cleaner_db(interval_seconds=1800):
    global _SINGLE_CANDIDATE_LAST_BACKUP_TS
    now = time.time()
    if now - _SINGLE_CANDIDATE_LAST_BACKUP_TS < interval_seconds:
        return ''
    path = _backup_single_candidate_cleaner_db('auto')
    if path:
        _SINGLE_CANDIDATE_LAST_BACKUP_TS = now
    return path


def _load_single_candidate_discard_map(keyword=None):
    _ensure_single_candidate_cleaner_db()
    params = []
    where = "WHERE discarded=1"
    if keyword:
        where += " AND keyword=?"
        params.append(normalize_search_text(keyword))
    with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        rows = conn.execute(
            f"SELECT keyword, char_id, updated_by, datetime(updated_at, '+8 hours') AS updated_at FROM single_candidate_discards {where}",
            params
        ).fetchall()
    return {
        (str(row['keyword']), str(row['char_id'])): {
            'updated_by': row['updated_by'],
            'updated_at': row['updated_at'],
        }
        for row in rows
    }


def _load_single_candidate_note_map(keyword=None):
    _ensure_single_candidate_cleaner_db()
    params = []
    where = "WHERE note IS NOT NULL AND TRIM(note) != ''"
    if keyword:
        where += " AND keyword=?"
        params.append(normalize_search_text(keyword))
    with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        rows = conn.execute(
            f"SELECT keyword, char_id, note, updated_by, datetime(updated_at, '+8 hours') AS updated_at FROM single_candidate_notes {where}",
            params
        ).fetchall()
    return {
        (str(row['keyword']), str(row['char_id'])): {
            'note': row['note'] or '',
            'updated_by': row['updated_by'],
            'updated_at': row['updated_at'],
        }
        for row in rows
    }


def _save_single_candidate_discard(keyword, char_id, discarded, updated_by=''):
    keyword = normalize_search_text(keyword)
    char_id = normalize_search_text(char_id)
    if not keyword or not char_id:
        return False, 'missing keyword or char_id'
    _ensure_single_candidate_cleaner_db()
    _maybe_backup_single_candidate_cleaner_db()
    with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("""
            INSERT INTO single_candidate_discards
            (keyword, char_id, discarded, updated_by, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(keyword, char_id) DO UPDATE SET
                discarded=excluded.discarded,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
        """, (keyword, char_id, 1 if discarded else 0, updated_by))
    return True, ''


def _save_single_candidate_note(keyword, char_id, note, updated_by=''):
    keyword = normalize_search_text(keyword)
    char_id = normalize_search_text(char_id)
    note = str(note or '').strip()
    if len(note) > 4000:
        note = note[:4000]
    if not keyword or not char_id:
        return False, 'missing keyword or char_id', ''
    _ensure_single_candidate_cleaner_db()
    _maybe_backup_single_candidate_cleaner_db()
    with sqlite3.connect(SINGLE_CANDIDATE_CLEANER_DB_PATH, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("""
            INSERT INTO single_candidate_notes
            (keyword, char_id, note, updated_by, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(keyword, char_id) DO UPDATE SET
                note=excluded.note,
                updated_by=excluded.updated_by,
                updated_at=CURRENT_TIMESTAMP
        """, (keyword, char_id, note, updated_by))
    return True, '', note


def _get_single_candidate_cleaner_keywords():
    rows = _get_single_candidate_cache()
    selected_ids = _load_selected_char_ids([row.get('char_id') for row in rows])
    discard_map = _load_single_candidate_discard_map()
    stats = {}
    order = {}
    for index, row in enumerate(rows):
        char_id = str(row.get('char_id') or '')
        if not char_id or char_id in selected_ids:
            continue
        keyword = normalize_search_text(row.get('candidate_keyword') or '')
        if not keyword:
            continue
        item = stats.setdefault(keyword, {
            'keyword': keyword,
            'total': 0,
            'discarded': 0,
            'kept': 0,
        })
        order.setdefault(keyword, index)
        item['total'] += 1
        if (keyword, char_id) in discard_map:
            item['discarded'] += 1
    for item in stats.values():
        item['kept'] = max(item['total'] - item['discarded'], 0)
    return sorted(stats.values(), key=lambda item: (-item['total'], order.get(item['keyword'], 999999999), item['keyword']))


def _annotate_cleaner_rows(rows, keyword=None):
    keyword = normalize_search_text(keyword or '')
    discard_map = _load_single_candidate_discard_map(keyword if keyword else None)
    note_map = _load_single_candidate_note_map(keyword if keyword else None)
    annotated = []
    for row in rows:
        item = dict(row)
        char_id = str(item.get('char_id') or '')
        row_keyword = keyword or normalize_search_text(item.get('candidate_keyword') or '')
        state = discard_map.get((row_keyword, char_id))
        item['discarded'] = bool(state)
        item['discarded_at'] = state.get('updated_at') if state else ''
        item['discarded_by'] = state.get('updated_by') if state else ''
        note_state = note_map.get((row_keyword, char_id))
        item['note'] = note_state.get('note') if note_state else ''
        item['note_updated_at'] = note_state.get('updated_at') if note_state else ''
        item['note_updated_by'] = note_state.get('updated_by') if note_state else ''
        annotated.append(item)
    return annotated


def _single_candidate_cbeta_mismatch(keyword, cmp_txt):
    keyword = normalize_search_text(keyword)
    cmp_value = normalize_search_text(cmp_txt)
    if not keyword:
        return False
    if not cmp_value or cmp_value in {'-', '—', '－', '□', '缺', '無', '无', 'nan', 'None'}:
        return True
    try:
        keyword_variants = {normalize_search_text(item) for item in get_keyword_search_variants(keyword)}
    except Exception:
        keyword_variants = {keyword}
    keyword_variants = {item for item in keyword_variants if item}
    return cmp_value not in keyword_variants


def _build_single_candidate_cleaner_groups(keywords):
    clean_keywords = [normalize_search_text(kw) for kw in keywords if normalize_search_text(kw)]
    keyword_set = set(clean_keywords)
    if not keyword_set:
        return []

    stats_by_keyword = {item['keyword']: item for item in _get_single_candidate_cleaner_keywords()}
    all_rows = _get_single_candidate_cache()
    selected_ids = _load_selected_char_ids([row.get('char_id') for row in all_rows])
    grouped_map = defaultdict(list)
    for row in all_rows:
        keyword = normalize_search_text(row.get('candidate_keyword') or '')
        if keyword not in keyword_set:
            continue
        char_id = str(row.get('char_id') or '')
        if not char_id or char_id in selected_ids:
            continue
        grouped_map[keyword].append(row)

    groups = []
    for keyword in clean_keywords:
        group_rows = grouped_map.get(keyword, [])
        if not group_rows:
            continue
        group_rows = _sort_single_candidate_rows(group_rows, sort_column='other_info', sort_direction='asc')
        group_rows = _annotate_cleaner_rows(group_rows, keyword)
        rows_payload = []
        for row in group_rows:
            rows_payload.append({
                'char_id': str(row.get('char_id') or ''),
                'keyword': keyword,
                'img_url': img_url_filter(row.get('img_url') or ''),
                'thumb_url': f"/admin/single_candidate_cleaner/thumb/{str(row.get('char_id') or '')}",
                'other_info': row.get('other_info') or '',
                'other_info_code': row.get('other_info_code') or '',
                'cmp_txt': row.get('cmp_txt') or '',
                'txt': row.get('txt') or '',
                'cbeta_mismatch': _single_candidate_cbeta_mismatch(keyword, row.get('cmp_txt') or ''),
                'discarded': bool(row.get('discarded')),
                'note': row.get('note') or '',
            })
        groups.append({
            'keyword': keyword,
            'stats': stats_by_keyword.get(keyword, {
                'keyword': keyword,
                'total': len(rows_payload),
                'discarded': sum(1 for row in rows_payload if row['discarded']),
                'kept': sum(1 for row in rows_payload if not row['discarded']),
            }),
            'rows': rows_payload,
        })
    return groups


def _read_student_manual_keyword_set():
    """读取学生数据库中的人工字头（含已分配任務與任務池）。"""
    db_path = getattr(_database, 'DB_PATH', None)
    if not db_path:
        return set()

    manual_keywords = set()
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row

            for row in conn.execute(
                "SELECT DISTINCT keyword FROM task_assignments "
                "WHERE keyword IS NOT NULL AND TRIM(keyword) != ''"
            ).fetchall():
                keyword = (row['keyword'] or '').strip()
                if keyword:
                    manual_keywords.add(keyword)

            for row in conn.execute(
                "SELECT DISTINCT keyword FROM task_pool "
                "WHERE keyword IS NOT NULL AND TRIM(keyword) != ''"
            ).fetchall():
                keyword = (row['keyword'] or '').strip()
                if keyword:
                    manual_keywords.add(keyword)

            return manual_keywords
    except Exception as e:
        logger.error(f"[defaults-summary] 讀取 students.db 人工字頭失敗: {e}")
        return set()


def _collect_default_keyword_source_summary():
    """
    返回每個字頭的來源標記摘要。
    兼容字合併後以 char_id 集合去重計數，避免同一字圖在多個原始字形下被重複計入。
    每項結構:
      {
        'keyword': str,
        'manual': bool,
        'gemini_pro': bool,
        'gemini_fresh': bool,
        'manual_count': int,
        'gemini_pro_count': int,
        'gemini_fresh_count': int
      }
    """
    summary = {}

    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT default_keyword, char_id "
                "FROM characters "
                "WHERE default_keyword IS NOT NULL AND default_keyword != ''"
            ).fetchall():
                keyword = (row['default_keyword'] or '').strip()
                if not keyword:
                    continue
                entry = summary.setdefault(keyword, {
                    'keyword': keyword,
                    'manual': True,
                    'gemini_pro': False,
                    'gemini_fresh': False,
                    'manual_ids': set(),
                    'gemini_pro_ids': set(),
                    'gemini_fresh_ids': set(),
                })
                if row['char_id']:
                    entry['manual_ids'].add(str(row['char_id']))
    except Exception as e:
        logger.error(f"[defaults-summary] 讀取 manual 字頭失敗: {e}")

    source_maps = {}
    for source_name in ('gemini_pro', 'gemini_fresh'):
        merged = {}
        for db_name in DEFAULT_SOURCE_TO_DB.get(source_name, []):
            db_map = _read_ai_pick_keyword_to_char_ids(db_name)
            for keyword, char_ids in db_map.items():
                merged.setdefault(keyword, set()).update(char_ids)
        source_maps[source_name] = merged

    for source_name, keyword_to_char_ids in source_maps.items():
        for keyword, char_ids in keyword_to_char_ids.items():
            entry = summary.setdefault(keyword, {
                'keyword': keyword,
                'manual': False,
                'gemini_pro': False,
                'gemini_fresh': False,
                'manual_ids': set(),
                'gemini_pro_ids': set(),
                'gemini_fresh_ids': set(),
            })
            if source_name == 'gemini_pro':
                entry['gemini_pro'] = True
                entry['gemini_pro_ids'].update(str(cid) for cid in char_ids if cid)
            elif source_name == 'gemini_fresh':
                entry['gemini_fresh'] = True
                entry['gemini_fresh_ids'].update(str(cid) for cid in char_ids if cid)

    grouped = {}
    for raw_keyword, raw_entry in summary.items():
        normalized_keyword = normalize_search_text(raw_keyword)
        canonical_keyword = canonicalize_keyword(raw_keyword)
        if not canonical_keyword:
            continue
        entry = grouped.setdefault(canonical_keyword, {
            'keyword': canonical_keyword,
            'variants': set(),
            'manual': False,
            'gemini_pro': False,
            'gemini_fresh': False,
            'manual_ids': set(),
            'gemini_pro_ids': set(),
            'gemini_fresh_ids': set(),
        })
        entry['variants'].add(raw_keyword)
        entry['variants'].add(normalized_keyword)
        entry['variants'].add(canonical_keyword)
        entry['manual'] = entry['manual'] or raw_entry.get('manual', False)
        entry['gemini_pro'] = entry['gemini_pro'] or raw_entry.get('gemini_pro', False)
        entry['gemini_fresh'] = entry['gemini_fresh'] or raw_entry.get('gemini_fresh', False)
        entry['manual_ids'].update(raw_entry.get('manual_ids') or set())
        entry['gemini_pro_ids'].update(raw_entry.get('gemini_pro_ids') or set())
        entry['gemini_fresh_ids'].update(raw_entry.get('gemini_fresh_ids') or set())

    progress_order = _load_progress_keyword_order()
    items = list(grouped.values())
    for item in items:
        item['variants'] = sorted(item.get('variants') or [])
        item['has_variants'] = len(item['variants']) > 1
        manual_ids = item.pop('manual_ids', set())
        gemini_pro_ids = item.pop('gemini_pro_ids', set())
        gemini_fresh_ids = item.pop('gemini_fresh_ids', set())
        item['manual_count'] = len(manual_ids)
        item['gemini_pro_count'] = len(gemini_pro_ids)
        item['gemini_fresh_count'] = len(gemini_fresh_ids)
        item['ai_candidate_count'] = len(gemini_pro_ids | gemini_fresh_ids)
        ranks_counts = [progress_order[v] for v in item['variants'] if v in progress_order]
        if item['keyword'] in progress_order:
            ranks_counts.append(progress_order[item['keyword']])
        if ranks_counts:
            item['progress_rank'] = min(rank for rank, _count in ranks_counts)
            item['progress_count'] = max(count for _rank, count in ranks_counts)
        else:
            item['progress_rank'] = 999999999
            item['progress_count'] = 0
    items.sort(key=lambda x: (
        x.get('progress_rank', 999999999),
        -x.get('progress_count', 0),
        x['keyword']
    ))
    return items


def _merge_single_candidate_summary_rows(rows, single_candidate_counts):
    """把只有 n=1 單字候選、尚無人工/Gemini來源的字頭補進來源總覽。"""
    if not single_candidate_counts:
        return rows

    progress_order = _load_progress_keyword_order()
    variant_to_row = {}
    for row in rows:
        for key in set(row.get('variants') or []) | {row.get('keyword', '')}:
            normalized = normalize_search_text(key)
            canonical = canonicalize_keyword(normalized)
            if normalized:
                variant_to_row[normalized] = row
            if canonical:
                variant_to_row[canonical] = row

    additions = {}
    for raw_keyword in single_candidate_counts:
        keyword = normalize_search_text(raw_keyword)
        if not keyword:
            continue
        canonical = canonicalize_keyword(keyword)
        if not canonical:
            continue

        existing = variant_to_row.get(keyword) or variant_to_row.get(canonical)
        if existing:
            variants = set(existing.get('variants') or [])
            variants.update([keyword, canonical])
            existing['variants'] = sorted(v for v in variants if v)
            existing['has_variants'] = len(existing['variants']) > 1
            continue

        entry = additions.setdefault(canonical, {
            'keyword': canonical,
            'variants': set(),
            'manual': False,
            'gemini_pro': False,
            'gemini_fresh': False,
            'manual_count': 0,
            'gemini_pro_count': 0,
            'gemini_fresh_count': 0,
            'ai_candidate_count': 0,
            'progress_rank': 999999999,
            'progress_count': 0,
        })
        entry['variants'].update([keyword, canonical])

    for entry in additions.values():
        variants = sorted(v for v in entry.pop('variants', set()) if v)
        entry['variants'] = variants
        entry['has_variants'] = len(variants) > 1
        ranks_counts = [progress_order[v] for v in variants if v in progress_order]
        if entry['keyword'] in progress_order:
            ranks_counts.append(progress_order[entry['keyword']])
        if ranks_counts:
            entry['progress_rank'] = min(rank for rank, _count in ranks_counts)
            entry['progress_count'] = max(count for _rank, count in ranks_counts)
        rows.append(entry)

    return rows


def _attach_single_candidate_counts_to_rows(rows, single_candidate_counts):
    """把每個單字候選原始字頭只歸屬到一個總覽行，避免合併字形重複計數。"""
    for row in rows:
        row['single_candidate_count'] = 0

    variant_to_row = {}
    for row in rows:
        for key in set(row.get('variants') or []) | {row.get('keyword', '')}:
            normalized = normalize_search_text(key)
            canonical = canonicalize_keyword(normalized)
            if normalized:
                variant_to_row.setdefault(normalized, row)
            if canonical:
                variant_to_row.setdefault(canonical, row)

    for raw_keyword, count in single_candidate_counts.items():
        keyword = normalize_search_text(raw_keyword)
        canonical = canonicalize_keyword(keyword)
        row = variant_to_row.get(keyword) or variant_to_row.get(canonical)
        if not row:
            continue
        row['single_candidate_count'] += count
        variants = set(row.get('variants') or [])
        variants.update(v for v in (keyword, canonical) if v)
        row['variants'] = sorted(variants)
        row['has_variants'] = len(row['variants']) > 1

    return rows


def _normalize_summary_filter(raw_value):
    """来源过滤器标准化。"""
    if raw_value in {'all', 'manual', 'gemini_pro', 'gemini_fresh', 'mixed', 'single_candidate'}:
        return raw_value
    return 'all'


def _normalize_archive_filter(raw_value):
    """封存狀態過濾器標準化。"""
    if raw_value in {'active', 'archived', 'all'}:
        return raw_value
    return 'active'


def _load_progress_keyword_order():
    """讀取原始篩選進度表：內容=字頭，出現次數=全庫頻次，用作來源字頭總覽排序依據。"""
    global _PROGRESS_ORDER_CACHE
    try:
        st = os.stat(PROGRESS_XLSX_PATH)
    except FileNotFoundError:
        logger.warning(f"[defaults-summary] 未找到篩選進度表: {PROGRESS_XLSX_PATH}")
        return {}

    with _PROGRESS_ORDER_CACHE_LOCK:
        cached = _PROGRESS_ORDER_CACHE
        if cached and cached.get('mtime') == st.st_mtime and cached.get('size') == st.st_size:
            return dict(cached.get('data', {}))

        try:
            from openpyxl import load_workbook
            wb = load_workbook(PROGRESS_XLSX_PATH, read_only=True, data_only=True)
            ws = wb['Sheet1'] if 'Sheet1' in wb.sheetnames else wb[wb.sheetnames[0]]
            data = {}
            rank = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or row[0] is None:
                    continue
                keyword = normalize_search_text(row[0])
                if not keyword:
                    continue
                try:
                    count = int(row[1] or 0)
                except (TypeError, ValueError):
                    count = 0
                if keyword in data:
                    # 保留最靠前的排位，但頻次取較大值，避免表內重複時低估。
                    old_rank, old_count = data[keyword]
                    data[keyword] = (old_rank, max(old_count, count))
                    continue
                rank += 1
                data[keyword] = (rank, count)
            wb.close()
        except Exception as e:
            logger.error(f"[defaults-summary] 讀取篩選進度表失敗: {e}")
            data = {}

        _PROGRESS_ORDER_CACHE = {
            'mtime': st.st_mtime,
            'size': st.st_size,
            'data': dict(data),
        }
        return dict(data)


def _ensure_source_keyword_notes_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_keyword_notes (
            keyword TEXT PRIMARY KEY,
            starred INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            memo TEXT NOT NULL DEFAULT '',
            updated_by TEXT DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing_columns = {
        row['name'] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute("PRAGMA table_info(source_keyword_notes)").fetchall()
    }
    if 'archived' not in existing_columns:
        conn.execute(
            "ALTER TABLE source_keyword_notes "
            "ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
        )


def _load_source_keyword_notes():
    """讀取來源字頭總覽的星標與備忘。"""
    notes = {}
    try:
        with _database.get_db_connection() as conn:
            _ensure_source_keyword_notes_table(conn)
            for row in conn.execute(
                "SELECT keyword, starred, archived, memo, updated_by, updated_at FROM source_keyword_notes"
            ).fetchall():
                notes[row['keyword']] = {
                    'starred': bool(row['starred']),
                    'archived': bool(row['archived']),
                    'memo': row['memo'] or '',
                    'updated_by': row['updated_by'] or '',
                    'updated_at': row['updated_at'] or '',
                }
    except Exception as e:
        logger.error(f"[source-notes] 讀取字頭星標/備忘失敗: {e}")
    return notes


def _save_source_keyword_note(keyword, starred=None, memo=None, archived=None, updated_by=''):
    """保存單個字頭的星標或備忘。"""
    keyword = normalize_search_text(keyword)
    if not keyword:
        return False, '字頭不可為空', {}

    try:
        with _database.get_db_connection() as conn:
            _ensure_source_keyword_notes_table(conn)
            current = conn.execute(
                "SELECT starred, archived, memo FROM source_keyword_notes WHERE keyword = ?",
                (keyword,)
            ).fetchone()
            next_starred = int(bool(starred)) if starred is not None else int(current['starred']) if current else 0
            next_archived = int(bool(archived)) if archived is not None else int(current['archived']) if current else 0
            next_memo = str(memo) if memo is not None else (current['memo'] if current else '')
            conn.execute(
                """
                INSERT INTO source_keyword_notes(keyword, starred, archived, memo, updated_by, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(keyword) DO UPDATE SET
                    starred = excluded.starred,
                    archived = excluded.archived,
                    memo = excluded.memo,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (keyword, next_starred, next_archived, next_memo, updated_by)
            )
            conn.commit()
        return True, '', {
            'keyword': keyword,
            'starred': bool(next_starred),
            'archived': bool(next_archived),
            'memo': next_memo
        }
    except Exception as e:
        logger.error(f"[source-notes] 保存字頭星標/備忘失敗 keyword={keyword}: {e}")
        return False, '保存失敗', {}


def _simplify_manuscript_text(value):
    """把卷號、題名、樣例圖片名簡化成便於匹配的形態。"""
    text = normalize_search_text(value).lower()
    return re.sub(r'[\s\-_()（）.·:：/\\]+', '', text)


_MANUSCRIPT_PREFIX_ALIASES = {
    # 常見收藏地/舊藏/圖錄名的檔名前綴。這裡只做受控別名，不引入拼音依賴。
    'anbo': ['安博', '安徽博物院'],
    'bd': ['BD', '北'],
    'beisanjing': ['北三井'],
    'dagu': ['大谷', '大穀'],
    'dunyan': ['敦研'],
    'edun': ['俄敦', 'Дх'],
    'ganbo': ['甘博', '贛博'],
    'gugong': ['故宮'],
    'gugongxin': ['故宮新'],
    'jiade': ['嘉德'],
    'jingbo': ['京博'],
    'jinyi': ['津藝'],
    'kaoguji': ['考古記'],
    'lingmu': ['鈴木'],
    'liyuan': ['栗原'],
    'lvshun': ['旅順'],
    'mik': ['MIK'],
    'qingye': ['清野'],
    'riben': ['日本'],
    'san': ['散'],
    'shanbo': ['陝博', '陝西博物館', '山博'],
    'shangbo': ['上博'],
    'shangtu': ['上圖'],
    'shangtugongsi': ['上圖公司', '上海圖書公司'],
    'shoubo': ['首博'],
    'shouwu': ['守屋'],
    'tubo': ['吐博'],
    'tupu': ['圖譜'],
    'tupub': ['圖譜B'],
    'tupuc': ['圖譜C'],
    'wuniao': ['五鳥'],
    'xinbo': ['新博'],
    'yidizhishi': ['伊地知氏'],
    'yu': ['羽'],
    'yuyi': ['餘乙'],
    'zhienyuan': ['知恩院'],
    'zhongcun': ['中村'],
    'zhongcunsh': ['中村SH'],
    'zhongguolishibowuguan': ['中國歷史博物館'],
}


def _add_simplified_alias(aliases, value):
    simplified = _simplify_manuscript_text(value)
    if simplified:
        aliases.add(simplified)


def _is_meaningful_manuscript_alias(value):
    alias = _simplify_manuscript_text(value)
    if not alias:
        return False
    if alias.isdigit() and len(alias) < 3:
        return False
    return len(alias) >= 3


def _catalog_identifier_aliases(text):
    """從檔名中抽取可與目錄卷號對上的館藏編號。"""
    aliases = set()
    raw_text = normalize_search_text(text).lower()
    simplified = _simplify_manuscript_text(text)
    if not simplified:
        return aliases

    # BD15076, S.1427, P.2907, San0763, YU004, Zhongcun017 等。
    raw_matches = re.findall(r'([a-z]+)\.?([0-9]{1,6})(?![0-9])', raw_text, flags=re.I)
    compact_matches = re.findall(r'([a-z]+)\.?([0-9]{1,6})(?![0-9])', simplified, flags=re.I)
    for prefix, digits in raw_matches + compact_matches:
        p = prefix.lower()
        raw_digits = digits
        n = str(int(raw_digits)) if raw_digits.isdigit() else raw_digits

        def add_prefixed(pref):
            _add_simplified_alias(aliases, f'{pref}{raw_digits}')
            if len(n) >= 3:
                _add_simplified_alias(aliases, f'{pref}{n}')
                if p in ('bd', 's', 'p'):
                    _add_simplified_alias(aliases, f'{pref}{int(raw_digits):05d}')
                if p in ('san', 'yu'):
                    _add_simplified_alias(aliases, f'{pref}{int(raw_digits):04d}')
            if p in ('s', 'p'):
                _add_simplified_alias(aliases, f'{pref}{int(raw_digits):05d}')

        if p in _MANUSCRIPT_PREFIX_ALIASES:
            for mapped in _MANUSCRIPT_PREFIX_ALIASES[p]:
                add_prefixed(mapped)
        if p in ('s', 'p', 'bd', 'mik'):
            add_prefixed(p.upper())

    # 檔名常把守屋卷號放在 crop 後面，如 Shouwu059-crop-185.1。
    m = re.search(r'^shouwu.*?crop[-_ ]*([0-9]+(?:\.[0-9]+)?)', raw_text, flags=re.I)
    if m:
        _add_simplified_alias(aliases, '守屋' + m.group(1))

    # 京博B甲310 在檔名中常寫成 Jingbobjia310。
    m = re.search(r'^jingbob?jia([0-9]+)', raw_text, flags=re.I)
    if m:
        _add_simplified_alias(aliases, '京博B甲' + m.group(1))

    return aliases


def _img_name_aliases(img_name):
    """從圖片名推測可能對應的目錄卷號片段。"""
    raw = normalize_search_text(img_name)
    if not raw:
        return []

    aliases = {raw}
    base = raw.split('_')[0]
    aliases.add(base)
    aliases.add(re.split(r'[-~]', base, maxsplit=1)[0])
    base = re.sub(r'-page.*$', '', base, flags=re.I)
    base = re.sub(r'-crop.*$', '', base, flags=re.I)
    base = re.sub(r'-juanmo.*$', '', base, flags=re.I)
    base = re.sub(r'-quan.*$', '', base, flags=re.I)
    base = re.sub(r'-version.*$', '', base, flags=re.I)
    aliases.add(base)

    m = re.match(r'^([A-Za-z]+)(.*)$', base)
    if m:
        mapped_values = _MANUSCRIPT_PREFIX_ALIASES.get(m.group(1).lower()) or []
        for mapped in mapped_values:
            aliases.add(mapped + m.group(2))

    for alias in list(aliases):
        aliases.update(_catalog_identifier_aliases(alias))

    return [a for a in aliases if _is_meaningful_manuscript_alias(a)]


def _load_manuscript_catalog_rows():
    """讀取敦煌紀年寫卷目錄。目錄 xlsx 有舊版篩選標記，改用 LibreOffice 轉 CSV 讀取。"""
    global _MANUSCRIPT_CATALOG_CACHE
    try:
        st = os.stat(MANUSCRIPT_CATALOG_XLSX_PATH)
    except FileNotFoundError:
        logger.warning(f"[manuscripts] 未找到寫卷目錄表: {MANUSCRIPT_CATALOG_XLSX_PATH}")
        return []

    with _MANUSCRIPT_CATALOG_CACHE_LOCK:
        cached = _MANUSCRIPT_CATALOG_CACHE
        if cached and cached.get('mtime') == st.st_mtime and cached.get('size') == st.st_size:
            return [dict(row) for row in cached.get('rows', [])]

        rows = []
        try:
            with tempfile.TemporaryDirectory(prefix='liuchao_catalog_') as tmpdir:
                subprocess.run(
                    ['/opt/homebrew/bin/soffice', '--headless', '--convert-to', 'csv',
                     '--outdir', tmpdir, MANUSCRIPT_CATALOG_XLSX_PATH],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
                csv_files = [
                    os.path.join(tmpdir, name)
                    for name in os.listdir(tmpdir)
                    if name.lower().endswith('.csv')
                ]
                if not csv_files:
                    raise RuntimeError('LibreOffice 未生成 CSV 文件')
                csv_path = csv_files[0]
                with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
                    reader = csv.DictReader(f)
                    for idx, row in enumerate(reader, start=1):
                        cleaned = {k: normalize_search_text(v) for k, v in row.items() if k}
                        cleaned['_row_index'] = idx
                        search_parts = [
                            value for key, value in cleaned.items()
                            if key != '_row_index' and value
                        ]
                        cleaned['_search_blob'] = _simplify_manuscript_text(' '.join(search_parts))
                        rows.append(cleaned)
        except Exception as e:
            logger.error(f"[manuscripts] 讀取寫卷目錄表失敗: {e}")
            rows = []

        _MANUSCRIPT_CATALOG_CACHE = {
            'mtime': st.st_mtime,
            'size': st.st_size,
            'rows': [dict(row) for row in rows],
        }
        return [dict(row) for row in rows]


_MANUSCRIPT_CATALOG_DETAIL_FIELDS = [
    '上傳狀態', '總字數', '負責人', '開始時間', '結束時間', '複查', '復合',
    '結算情況', '館藏地', '卷號', '題名', '錄文', '原卷紀年', '西元紀年',
    '西元紀', '圖片出處', '題記録文', '備註', '草書', '綴合內容',
]


def _catalog_detail_items(catalog):
    """把 Excel 目錄行整理成前端可直接展示的欄位列表。"""
    details = []
    for field in _MANUSCRIPT_CATALOG_DETAIL_FIELDS:
        value = normalize_search_text((catalog or {}).get(field, ''))
        if value:
            details.append({'label': field, 'value': value})
    return details


def _get_manuscript_code_summaries():
    """從 characters.db 汇总每個寫卷碼的頁數、字圖數和樣例圖片。"""
    rows = []
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                """
                SELECT other_info_code,
                       COUNT(*) AS char_count,
                       COUNT(DISTINCT other_info) AS page_count,
                       MIN(other_info_page) AS min_page,
                       MAX(other_info_page) AS max_page,
                       MIN(img_name) AS sample_img_name,
                       MIN(other_info) AS sample_other_info,
                       GROUP_CONCAT(DISTINCT RTRIM(SUBSTR(other_info, LENGTH(other_info_code) + 1), '0123456789')) AS marker_summary
                FROM characters
                WHERE other_info_code IS NOT NULL AND other_info_code <> ''
                GROUP BY other_info_code
                ORDER BY CAST(SUBSTR(other_info_code, 4, 3) AS INTEGER), other_info_code
                """
            ):
                item = dict(row)
                code = item.get('other_info_code') or ''
                item['year_hint'] = code[3:6] if len(code) >= 6 else ''
                markers = [
                    marker for marker in str(item.get('marker_summary') or '').split(',')
                    if marker
                ]
                item['marker_summary'] = ' / '.join(markers) if markers else ''
                rows.append(item)
    except Exception as e:
        logger.error(f"[manuscripts] 讀取寫卷碼匯總失敗: {e}")
    return rows


def _best_catalog_match(summary, catalog_rows):
    """根據紀年和樣例圖片名，為一個寫卷碼找最可能的目錄行。"""
    year_hint = str(summary.get('year_hint') or '')
    aliases = [_simplify_manuscript_text(a) for a in _img_name_aliases(summary.get('sample_img_name'))]
    aliases = [a for a in aliases if _is_meaningful_manuscript_alias(a)]

    best = None
    best_score = 0
    for row in catalog_rows:
        score = 0
        row_year = str(row.get('西元紀') or '')
        blob = row.get('_search_blob') or ''
        volume = _simplify_manuscript_text(row.get('卷號') or '')

        if year_hint and row_year == year_hint:
            score += 40
        for alias in aliases:
            if alias and (alias in blob or alias in volume or (volume and volume in alias)):
                score += 60
                break
        if score > best_score:
            best = row
            best_score = score

    return best if best_score >= 60 else None


def _filter_manuscript_rows(query, code_rows, catalog_rows):
    q = normalize_search_text(query)
    q_simple = _simplify_manuscript_text(q)
    catalog_by_code = {}
    rows = []
    matched_catalog_indexes = set()

    for summary in code_rows:
        match = _best_catalog_match(summary, catalog_rows)
        if match:
            catalog_by_code[summary['other_info_code']] = match
            matched_catalog_indexes.add(match.get('_row_index'))
        search_blob = _simplify_manuscript_text(' '.join([
            str(summary.get('other_info_code') or ''),
            str(summary.get('sample_img_name') or ''),
            str(summary.get('sample_other_info') or ''),
            match.get('_search_blob', '') if match else '',
        ]))
        item = dict(summary)
        item['catalog'] = match or {}
        item['matched_catalog'] = bool(match)
        item['catalog_only'] = False
        item['catalog_details'] = _catalog_detail_items(match or {})
        item['_row_search_blob'] = search_blob
        rows.append(item)

    for cat in catalog_rows:
        if cat.get('_row_index') in matched_catalog_indexes:
            continue
        cat_blob = cat.get('_search_blob') or ''
        item = {
            'other_info_code': '',
            'year_hint': str(cat.get('西元紀') or ''),
            'char_count': 0,
            'page_count': 0,
            'min_page': '',
            'max_page': '',
            'sample_img_name': '',
            'sample_other_info': '',
            'marker_summary': '',
            'catalog': cat,
            'matched_catalog': False,
            'catalog_only': True,
            'catalog_details': _catalog_detail_items(cat),
            '_row_search_blob': cat_blob,
        }
        rows.append(item)

    rows.sort(key=lambda r: (
        int(str(r.get('year_hint') or '9999') or 9999),
        1 if r.get('catalog_only') else 0,
        str(r.get('other_info_code') or '')
    ))
    _assign_publication_numbers(rows)
    if q_simple:
        rows = [
            row for row in rows
            if q_simple in _simplify_manuscript_text(' '.join([
                row.get('_row_search_blob', ''),
                row.get('publication_id', ''),
                row.get('publication_label', ''),
            ]))
        ]
    return rows


def _assign_publication_numbers(rows):
    """按西元紀分組生成出版引用編號，如 357-001 / 357年1號。"""
    counters = defaultdict(int)
    for row in rows:
        catalog = row.get('catalog') or {}
        year = normalize_search_text(catalog.get('西元紀') or row.get('year_hint') or '')
        if not year:
            year = '未定年'
        counters[year] += 1
        seq = counters[year]
        if year.isdigit():
            row['publication_id'] = f'{year}-{seq:03d}'
            row['publication_label'] = f'{year}年{seq}號'
        else:
            row['publication_id'] = f'{year}-{seq:03d}'
            row['publication_label'] = f'{year}{seq}號'


def _manuscript_sequence_order_sql():
    """寫卷內字圖的穩定閱讀順序。"""
    return (
        "other_info_page ASC, "
        "column_no ASC, "
        "char_no ASC, "
        "y ASC, "
        "x ASC, "
        "char_id ASC"
    )


def _manuscript_clean_text(value):
    value = normalize_search_text(value)
    if value.lower() in ('nan', 'none', 'null'):
        return ''
    return value


def _manuscript_row_keyword(row):
    """同一寫卷內聚類用字頭。優先使用校勘/比對字頭，缺失時再回退 OCR 與代表字。"""
    for field in ('cmp_txt', 'txt', 'ocr_txt正', 'ocr_col正', 'ocr_txt', 'ocr_col', 'default_keyword'):
        value = _manuscript_clean_text((row or {}).get(field))
        if value:
            return value
    return '未識別'


def _manuscript_cluster_key(row):
    keyword = _manuscript_row_keyword(row)
    if len(keyword) == 1:
        return canonicalize_keyword(keyword)
    return keyword


def _get_manuscript_browse_rows(query=''):
    code_rows = _get_manuscript_code_summaries()
    catalog_rows = _load_manuscript_catalog_rows()
    rows = _filter_manuscript_rows(query, code_rows, catalog_rows)
    return rows, code_rows, catalog_rows


def _get_manuscript_meta(manuscript_code):
    manuscript_code = normalize_search_text(manuscript_code)
    code_rows = _get_manuscript_code_summaries()
    summary = next(
        (row for row in code_rows if normalize_search_text(row.get('other_info_code')) == manuscript_code),
        None
    )
    if not summary:
        return None

    catalog_rows = _load_manuscript_catalog_rows()
    match = _best_catalog_match(summary, catalog_rows)
    meta = dict(summary)
    meta['catalog'] = match or {}
    meta['matched_catalog'] = bool(match)
    meta['catalog_only'] = False
    meta['catalog_details'] = _catalog_detail_items(match or {})
    _assign_publication_numbers([meta])
    return meta


def _fetch_manuscript_sequence_rows(manuscript_code, include_images=False):
    fields = [
        'char_id', 'img_name', 'other_info', 'other_info_code', 'other_info_page',
        'column_no', 'char_no', 'cid', 'x', 'y', 'w', 'h',
        'ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives',
        'ocr_txt正', 'ocr_col正', 'default_keyword', 'h_atten', 'r_atten'
    ]
    if include_images:
        fields.append('img_url')

    quoted_fields = ', '.join(_quote_sql_identifier(field) for field in fields)
    sql = (
        f"SELECT {quoted_fields} FROM characters "
        "WHERE other_info_code = ? "
        f"ORDER BY {_manuscript_sequence_order_sql()}"
    )
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, (manuscript_code,)).fetchall()]
    except Exception as e:
        logger.error(f"[manuscript-browse] 讀取寫卷失敗 code={manuscript_code}: {e}")
        return []


def _iter_chunks(items, size=800):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _get_annotation_summary_for_char_ids(char_ids):
    char_ids = [cid for cid in char_ids if cid]
    if not char_ids:
        return {}

    result = {}
    try:
        with _database.get_db_connection() as conn:
            for chunk in _iter_chunks(char_ids):
                placeholders = ','.join(['?'] * len(chunk))
                rows = conn.execute(
                    f"""
                    SELECT char_id, Situation, where_field AS "where", operation, remark
                    FROM student_annotations
                    WHERE char_id IN ({placeholders})
                    """,
                    chunk
                ).fetchall()
                for row in rows:
                    cid = row['char_id']
                    summary = result.setdefault(cid, {
                        'total': 0,
                        'bad': 0,
                        'mis': 0,
                        'remark': 0,
                        'numbered': defaultdict(int),
                    })
                    situation = _clean_mark_value(row['Situation'])
                    where = _clean_mark_value(row['where'])
                    operation = _clean_mark_value(row['operation'])
                    remark = _clean_mark_value(row['remark'])
                    if not any((situation, where, operation, remark)):
                        continue
                    summary['total'] += 1
                    if situation == 'Bad':
                        summary['bad'] += 1
                    if where.startswith('Mis/'):
                        summary['mis'] += 1
                    number_value = operation.split('/')[-1] if '/' in operation else operation
                    if number_value in ('1', '2', '3', '4'):
                        summary['numbered'][number_value] += 1
                    if remark:
                        summary['remark'] += 1
    except Exception as e:
        logger.error(f"[manuscript-browse] 讀取標記匯總失敗: {e}")
        return {}

    for summary in result.values():
        summary['numbered'] = dict(summary['numbered'])
    return result


def _merge_marks_with_history_chunked(rows, student_id):
    if not rows:
        return rows
    char_ids = [row.get('char_id') for row in rows if row.get('char_id')]
    student_marks_dict = {}
    all_marks_dict = {}
    if student_id and char_ids:
        for chunk in _iter_chunks(char_ids):
            for mark in get_student_annotations_by_char_ids(student_id, chunk):
                student_marks_dict[mark['char_id']] = mark
            all_marks_dict.update(get_all_annotations_by_char_ids(chunk))
    return merge_student_marks(rows, student_marks_dict, all_marks_dict, student_id)


def _build_manuscript_char_clusters(rows):
    annotation_summary = _get_annotation_summary_for_char_ids([row.get('char_id') for row in rows])
    clusters = {}
    preview_limit = 3
    for index, row in enumerate(rows):
        key = _manuscript_cluster_key(row)
        keyword = _manuscript_row_keyword(row)
        cluster = clusters.setdefault(key, {
            'key': key,
            'display': keyword,
            'count': 0,
            'first_index': index,
            'first_page': row.get('other_info_page'),
            'first_column': row.get('column_no'),
            'first_char_no': row.get('char_no'),
            'first_char_id': row.get('char_id'),
            'default_count': 0,
            'attention_count': 0,
            'marked_count': 0,
            'bad_count': 0,
            'mis_count': 0,
            'remark_count': 0,
            'numbered_counts': defaultdict(int),
            'forms': Counter(),
            'matched_variants': set(),
            'representative_rows': [],
            'representative_total': 0,
        })
        cluster['count'] += 1
        if _manuscript_clean_text(row.get('default_keyword')):
            cluster['default_count'] += 1
            cluster['representative_total'] += 1
            if row.get('img_url') and len(cluster['representative_rows']) < preview_limit:
                cluster['representative_rows'].append({
                    'char_id': row.get('char_id'),
                    'img_url': row.get('img_url'),
                    'default_keyword': _manuscript_clean_text(row.get('default_keyword')),
                    'page': row.get('other_info_page'),
                    'column': row.get('column_no'),
                    'char_no': row.get('char_no'),
                    'text': _manuscript_clean_text(row.get('txt')) or _manuscript_clean_text(row.get('cmp_txt')),
                })
        if row.get('h_atten') == 'need' or row.get('r_atten') == 'need':
            cluster['attention_count'] += 1

        for field in ('txt', 'cmp_txt', 'ocr_txt正', 'ocr_col正'):
            form = _manuscript_clean_text(row.get(field))
            if form:
                cluster['forms'][form] += 1

        if keyword != key:
            cluster['matched_variants'].add(keyword)

        ann = annotation_summary.get(row.get('char_id')) or {}
        if ann.get('total'):
            cluster['marked_count'] += 1
            cluster['bad_count'] += ann.get('bad', 0)
            cluster['mis_count'] += ann.get('mis', 0)
            cluster['remark_count'] += ann.get('remark', 0)
            for number_value, count in (ann.get('numbered') or {}).items():
                cluster['numbered_counts'][number_value] += count

    result = []
    for cluster in clusters.values():
        cluster['forms'] = [
            {'value': value, 'count': count}
            for value, count in cluster['forms'].most_common(6)
        ]
        cluster['form_count'] = len(cluster['forms'])
        cluster['matched_variants'] = sorted(cluster['matched_variants'])
        cluster['numbered_counts'] = dict(sorted(cluster['numbered_counts'].items()))
        cluster['has_multiple_forms'] = cluster['form_count'] > 1 or bool(cluster['numbered_counts'])
        cluster['hidden_representative_count'] = max(
            0,
            cluster.get('representative_total', 0) - len(cluster.get('representative_rows') or [])
        )
        result.append(cluster)

    result.sort(key=lambda item: (-item.get('count', 0), item['first_index'], item['key']))
    return result


def _filter_manuscript_rows_by_cluster(rows, cluster_key):
    target = canonicalize_keyword(cluster_key) if len(cluster_key) == 1 else cluster_key
    return [row for row in rows if _manuscript_cluster_key(row) == target]


def _fetch_manuscript_rows_for_cluster(manuscript_code, cluster_key):
    if normalize_search_text(cluster_key) == '未識別':
        return _filter_manuscript_rows_by_cluster(
            _fetch_manuscript_sequence_rows(manuscript_code, include_images=True),
            cluster_key
        )

    variants = {normalize_search_text(cluster_key)}
    if len(cluster_key) == 1:
        variants.update(get_keyword_search_variants(cluster_key))
    variants = sorted(v for v in variants if v)
    if not variants:
        return []

    fields = [
        'char_id', 'img_name', 'img_url', 'other_info', 'other_info_code', 'other_info_page',
        'column_no', 'char_no', 'cid', 'x', 'y', 'w', 'h',
        'ocr_txt', 'ocr_col', 'cmp_txt', 'txt', 'alternatives',
        'ocr_txt正', 'ocr_col正', 'default_keyword', 'h_atten', 'r_atten'
    ]
    match_fields = ('cmp_txt', 'txt', 'ocr_txt正', 'ocr_col正', 'ocr_txt', 'ocr_col', 'default_keyword')
    placeholders = ','.join(['?'] * len(variants))
    clauses = [
        f"{_quote_sql_identifier(field)} IN ({placeholders})"
        for field in match_fields
    ]
    params = [manuscript_code]
    for _field in match_fields:
        params.extend(variants)

    quoted_fields = ', '.join(_quote_sql_identifier(field) for field in fields)
    sql = (
        f"SELECT {quoted_fields} FROM characters "
        "WHERE other_info_code = ? "
        f"AND ({' OR '.join(clauses)}) "
        f"ORDER BY {_manuscript_sequence_order_sql()}"
    )
    try:
        with sqlite3.connect(CHAR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            candidates = [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as e:
        logger.error(
            f"[manuscript-browse] 讀取寫卷字圖組失敗 code={manuscript_code} key={cluster_key}: {e}"
        )
        return []
    return _filter_manuscript_rows_by_cluster(candidates, cluster_key)


def _build_manuscript_char_stats(rows):
    defaults = sum(1 for row in rows if _manuscript_clean_text(row.get('default_keyword')))
    attention = sum(1 for row in rows if row.get('h_atten') == 'need' or row.get('r_atten') == 'need')
    pages = sorted({row.get('other_info_page') for row in rows if row.get('other_info_page') is not None})
    forms = Counter()
    for row in rows:
        form = _manuscript_clean_text(row.get('txt')) or _manuscript_clean_text(row.get('ocr_txt正'))
        if form:
            forms[form] += 1
    return {
        'total': len(rows),
        'default_count': defaults,
        'attention_count': attention,
        'page_count': len(pages),
        'pages': pages[:12],
        'forms': [{'value': value, 'count': count} for value, count in forms.most_common(12)],
    }


# ======== DB（default_keyword）辅助 ========
def ensure_default_db(char_data_rows):
    """确保默认关键词的DB存在并包含全部数据及default_keyword列"""
    os.makedirs(os.path.dirname(DEFAULT_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    cur = conn.cursor()

    # 检查表
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chars'")
    exists = cur.fetchone() is not None

    need_rebuild = False

    if not exists:
        need_rebuild = True
    else:
        cur.execute("PRAGMA table_info(chars)")
        cols = [r[1] for r in cur.fetchall()]
        if 'default_keyword' not in cols:
            cur.execute("ALTER TABLE chars ADD COLUMN default_keyword TEXT")
            conn.commit()
        # 如果表为空，重新导入
        cur.execute("SELECT COUNT(1) FROM chars")
        row_count = cur.fetchone()[0]
        if row_count == 0:
            need_rebuild = True

    if need_rebuild:
        if not char_data_rows:
            conn.close()
            raise RuntimeError("初始化DB需要已加载的char_data数据")

        cur.execute("DROP TABLE IF EXISTS chars")

        cols = list(char_data_rows[0].keys())
        cols_defs = []
        for c in cols:
            if c == 'char_id':
                cols_defs.append('"char_id" TEXT PRIMARY KEY')
            else:
                cols_defs.append(f'"{c}" TEXT')
        cols_defs.append('"default_keyword" TEXT')
        cur.execute(f"CREATE TABLE chars ({', '.join(cols_defs)})")

        # 分批插入（列名全部加引号以避开保留字）
        batch_size = 5000
        placeholders = ','.join(['?'] * (len(cols) + 1))
        col_order = cols + ['default_keyword']
        col_order_quoted = [f'"{c}"' for c in col_order]
        for i in range(0, len(char_data_rows), batch_size):
            batch = char_data_rows[i:i+batch_size]
            values = []
            for row in batch:
                values.append([str(row.get(c, '') or '') for c in cols] + [''])
            cur.executemany(
                f"INSERT OR REPLACE INTO chars ({','.join(col_order_quoted)}) VALUES ({placeholders})",
                values
            )
        conn.commit()

    return conn

def process_manuscript_groups(data):
    """按写卷分组并统计各种状态"""
    groups = {}
    for item in data:
        # 提取写卷编号 (如DH_270_020Y)
        manuscript_id = extract_code_from_other_info(item.get('other_info',''))
        
        if not manuscript_id:
            manuscript_id = '未知写卷'
        
        if manuscript_id not in groups:
            groups[manuscript_id] = {
                'items': [],
                'count': 0,
                'damaged_count': 0,
                'misplaced_count': 0,
                'numbered_count': 0,
                'attention_count': 0,
                'unmarked_count': 0,
                'numbered_by_value': {},  # 按字形标记值分组统计，如 {'1': 5, '2': 3}
                'status_marked_count': 0,  # 有状况或正误标记的数量
                'all_damaged': True,
                'has_damaged': False,
                'has_misplaced': False,
                'has_numbered': False,
                'has_attention': False,
                'has_unmarked': False,
                'has_status_marked': False
            }
        
        groups[manuscript_id]['items'].append(item)
        groups[manuscript_id]['count'] += 1
        
        # 统计各种状态
        situation = item.get('Situation', '')
        where = str(item.get('where', ''))
        operation = item.get('operation', '')
        h_atten = item.get('h_atten', '')
        r_atten = item.get('r_atten', '')
        
        # 判断是否有任何标记
        has_situation = situation == 'Bad'
        has_where = where.startswith('Mis/')
        has_operation = operation and operation.strip() and operation.split('/')[-1] in ['1', '2', '3', '4']
        
        # 残损统计
        if has_situation:
            groups[manuscript_id]['damaged_count'] += 1
            groups[manuscript_id]['has_damaged'] = True
        else:
            groups[manuscript_id]['all_damaged'] = False
        
        # 误入统计
        if has_where:
            groups[manuscript_id]['misplaced_count'] += 1
            groups[manuscript_id]['has_misplaced'] = True
        
        # 数字标记统计
        if has_operation:
            groups[manuscript_id]['numbered_count'] += 1
            groups[manuscript_id]['has_numbered'] = True
            # 按字形标记值分组统计
            number_value = operation.split('/')[-1]
            if number_value not in groups[manuscript_id]['numbered_by_value']:
                groups[manuscript_id]['numbered_by_value'][number_value] = 0
            groups[manuscript_id]['numbered_by_value'][number_value] += 1
        
        # 注意标记统计
        if h_atten == 'need' or r_atten == 'need':
            groups[manuscript_id]['attention_count'] += 1
            groups[manuscript_id]['has_attention'] = True
        
        # 新分类统计
        # (1) 无任何标记（状况、正误、字形）
        if not has_situation and not has_where and not has_operation:
            groups[manuscript_id]['unmarked_count'] += 1
            groups[manuscript_id]['has_unmarked'] = True
        
        # (3) 有状况或正误标记
        if has_situation or has_where:
            groups[manuscript_id]['status_marked_count'] += 1
            groups[manuscript_id]['has_status_marked'] = True
    
    # 按写卷编号排序
    sorted_groups = dict(sorted(groups.items()))
    return sorted_groups


def _clean_manuscript_id_list(manuscript_ids):
    clean = []
    seen = set()
    for manuscript_id in manuscript_ids or []:
        mid = normalize_search_text(manuscript_id)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        clean.append(mid)
    return clean


def _row_manuscript_id(row):
    row = row or {}
    mid = extract_code_from_other_info(row.get('other_info', ''))
    if not mid:
        mid = normalize_search_text(row.get('other_info_code') or '')
    return mid or '未知写卷'


def _filter_rows_by_manuscripts(rows, manuscript_ids):
    wanted = set(_clean_manuscript_id_list(manuscript_ids))
    if not wanted:
        return list(rows or [])
    return [row for row in (rows or []) if _row_manuscript_id(row) in wanted]


def _paginate_python_rows(rows, page=1, per_page=100):
    rows = list(rows or [])
    total_items = len(rows)
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(per_page)
    except (TypeError, ValueError):
        per_page = 100
    if per_page <= 0:
        effective_per_page = total_items or 1
        return rows, 1, total_items, effective_per_page, 1

    total_pages = max(1, math.ceil(total_items / per_page))
    page = min(page, total_pages)
    offset = (page - 1) * per_page
    return rows[offset:offset + per_page], total_pages, total_items, per_page, page


def _safe_filename_part(value, fallback='export'):
    text = normalize_search_text(value) or fallback
    return re.sub(r'[\\/:*?"<>|\s]+', '_', text).strip('_') or fallback


def _char_image_path(row):
    img_url = normalize_search_text((row or {}).get('img_url', ''))
    if img_url.startswith('/static/'):
        rel = img_url[len('/static/'):]
    elif img_url.startswith('/img/'):
        rel = img_url[len('/img/'):]
    else:
        rel = img_url.lstrip('/')
    if not rel:
        return ''
    path = os.path.abspath(os.path.join(IMAGE_FOLDER_PATH, rel))
    image_root = os.path.abspath(IMAGE_FOLDER_PATH)
    if not path.startswith(image_root + os.sep) and path != image_root:
        return ''
    return path if os.path.exists(path) else ''


def _single_candidate_row_by_char_id(char_id):
    char_id = normalize_search_text(char_id)
    if not char_id:
        return None
    for row in _get_single_candidate_cache():
        if str(row.get('char_id') or '') == char_id:
            return row
    return None


def _single_candidate_thumb_cache_path(char_id, image_path, width=220, height=170):
    try:
        stat = os.stat(image_path)
        sig = f"{char_id}|{image_path}|{stat.st_mtime_ns}|{stat.st_size}|{width}x{height}"
    except OSError:
        sig = f"{char_id}|{image_path}|{width}x{height}"
    name = _hashlib.sha256(sig.encode('utf-8')).hexdigest()[:32] + '.jpg'
    return os.path.join(SINGLE_CANDIDATE_THUMB_DIR, name)


def _make_single_candidate_thumb(image_path, out_path, width=220, height=170):
    img = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return False

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    border_size = max(2, min(10, h // 8, w // 8))
    border = np.concatenate([
        gray[:border_size, :].reshape(-1),
        gray[-border_size:, :].reshape(-1),
        gray[:, :border_size].reshape(-1),
        gray[:, -border_size:].reshape(-1),
    ])
    bg = float(np.median(border)) if border.size else 245.0
    dark_mask = gray < max(0, bg - 18)
    edges = cv2.Canny(gray, 35, 120)
    mask = np.logical_or(dark_mask, edges > 0).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    ys, xs = np.where(mask > 0)
    if len(xs) > 12 and len(ys) > 12:
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        pad = max(4, int(round(max(x2 - x1, y2 - y1) * 0.08)))
        x1 = max(0, x1 - pad)
        y1 = max(0, y1 - pad)
        x2 = min(w, x2 + pad)
        y2 = min(h, y2 + pad)
        crop = img[y1:y2, x1:x2]
    else:
        crop = img

    ch, cw = crop.shape[:2]
    if ch <= 0 or cw <= 0:
        return False

    canvas = np.full((height, width, 3), 255, dtype=np.uint8)
    max_w = int(width * 0.92)
    max_h = int(height * 0.92)
    scale = min(max_w / cw, max_h / ch)
    new_w = max(1, int(round(cw * scale)))
    new_h = max(1, int(round(ch * scale)))
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    resized = cv2.resize(crop, (new_w, new_h), interpolation=interpolation)
    x = (width - new_w) // 2
    y = (height - new_h) // 2
    canvas[y:y + new_h, x:x + new_w] = resized

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    return bool(cv2.imwrite(out_path, canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 88]))


@app.route('/admin/single_candidate_cleaner/thumb/<path:char_id>')
@login_required
def single_candidate_cleaner_thumb(char_id):
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'img', limit=6000):
        return '請求過於頻繁，請稍後再試', 429

    row = _single_candidate_row_by_char_id(char_id)
    if not row:
        return '字圖不存在', 404
    image_path = _char_image_path(row)
    if not image_path:
        return '圖片未找到', 404

    cache_path = _single_candidate_thumb_cache_path(char_id, image_path)
    if not os.path.exists(cache_path):
        if not _make_single_candidate_thumb(image_path, cache_path):
            return send_file(image_path, max_age=86400, conditional=True)
    return send_file(cache_path, mimetype='image/jpeg', max_age=86400, conditional=True)


def _original_image_with_box_bytes(row, max_width=1800):
    """生成帶紅框的整頁原圖 JPEG。只供 Word 導出使用，不走前端限速。"""
    ensure_data_loaded()
    # img_name 是文件索引鍵，不能做 NFKC 規範化；例如羅馬數字 ⅲ 會被改成 iii。
    img_name = str((row or {}).get('img_name', '')).strip()
    if not img_name or not image_index or img_name not in image_index:
        return None
    img_path = image_index.get(img_name)
    if not img_path or not os.path.exists(img_path):
        return None

    img = cv2.imread(img_path)
    if img is None:
        return None

    try:
        x = int(float(row['x']))
        y = int(float(row['y']))
        w = int(float(row['w']))
        h = int(float(row['h']))
    except (ValueError, TypeError, KeyError):
        return None

    ih, iw = img.shape[:2]
    thickness = max(4, int(round(max(ih, iw) / 500)))
    cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), thickness)
    if iw > max_width:
        scale = max_width / float(iw)
        img = cv2.resize(img, (max_width, max(1, int(ih * scale))), interpolation=cv2.INTER_AREA)
    ok, buffer = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    return buffer.tobytes() if ok else None


def _build_manuscript_export_metadata(rows):
    catalog_rows = _load_manuscript_catalog_rows()
    summary_by_code = {row.get('other_info_code'): row for row in _get_manuscript_code_summaries()}
    filtered_rows = _filter_manuscript_rows('', list(summary_by_code.values()), catalog_rows)
    metadata = {}
    for item in filtered_rows:
        code = item.get('other_info_code')
        if code:
            metadata[code] = item

    for row in rows:
        code = extract_code_from_other_info(row.get('other_info', '')) or row.get('other_info_code') or ''
        if code and code not in metadata:
            summary = {
                'other_info_code': code,
                'year_hint': code[3:6] if len(code) >= 6 else '',
                'sample_img_name': row.get('img_name', ''),
                'sample_other_info': row.get('other_info', ''),
                'char_count': '',
                'page_count': '',
                'min_page': '',
                'max_page': '',
                'marker_summary': '',
            }
            match = _best_catalog_match(summary, catalog_rows)
            metadata[code] = {
                **summary,
                'catalog': match or {},
                'matched_catalog': bool(match),
                'catalog_details': _catalog_detail_items(match or {}),
                'publication_id': '',
                'publication_label': '',
            }
    markers_by_code = defaultdict(set)
    for row in rows:
        code = extract_code_from_other_info(row.get('other_info', '')) or row.get('other_info_code') or ''
        for marker in _extract_manuscript_markers(row.get('other_info', ''), code):
            markers_by_code[code].add(marker)
    for code, markers in markers_by_code.items():
        if not code:
            continue
        item = metadata.setdefault(code, {
            'other_info_code': code,
            'year_hint': code[3:6] if len(code) >= 6 else '',
            'catalog': {},
            'matched_catalog': False,
            'catalog_details': [],
            'publication_id': '',
            'publication_label': '',
        })
        if not item.get('marker_summary'):
            item['marker_summary'] = ' / '.join(sorted(markers))
    return metadata


def _plain_meta_line(label, value):
    value = normalize_search_text(value)
    return f'{label}: {value}' if value else ''


def _docx_text(value):
    return xml_escape(normalize_search_text(value))


def _docx_run(text='', bold=False, color=None, size=22):
    props = []
    if bold:
        props.append('<w:b/>')
    if color:
        props.append(f'<w:color w:val="{color}"/>')
    if size:
        props.append(f'<w:sz w:val="{int(size)}"/>')
    rpr = f'<w:rPr>{"".join(props)}</w:rPr>' if props else ''
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_docx_text(text)}</w:t></w:r>'


def _docx_para(text='', style=None, bold=False, color=None, size=22):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{ppr}{_docx_run(text, bold=bold, color=color, size=size)}</w:p>'


def _docx_page_break():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _image_dimensions(path=None, data=None):
    try:
        if path:
            img = cv2.imread(path)
        else:
            arr = np.frombuffer(data, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return 800, 600
        h, w = img.shape[:2]
        return w or 800, h or 600
    except Exception:
        return 800, 600


def _docx_image_run(rid, width_px, height_px, target_width_inches=0.72):
    max_cx = int(target_width_inches * 914400)
    ratio = height_px / float(width_px or 1)
    cx = max_cx
    cy = int(cx * ratio)
    return f'''
      <w:r>
        <w:drawing>
          <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
            <wp:extent cx="{cx}" cy="{cy}"/>
            <wp:docPr id="{rid[3:] if rid.startswith('rId') else rid}" name="image"/>
            <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
                  <pic:nvPicPr><pic:cNvPr id="0" name="image"/><pic:cNvPicPr/></pic:nvPicPr>
                  <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
                  <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
                </pic:pic>
              </a:graphicData>
            </a:graphic>
          </wp:inline>
        </w:drawing>
      </w:r>'''


def _docx_images_row(image_runs):
    return f'<w:p>{"".join(image_runs)}</w:p>'


def _docx_image_paragraph(rid, width_px, height_px, target_width_inches=2.0):
    max_cx = int(target_width_inches * 914400)
    ratio = height_px / float(width_px or 1)
    cx = max_cx
    cy = int(cx * ratio)
    return f'''
    <w:p>
      <w:r>
        <w:drawing>
          <wp:inline distT="0" distB="0" distL="0" distR="0" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
            <wp:extent cx="{cx}" cy="{cy}"/>
            <wp:docPr id="{rid[3:] if rid.startswith('rId') else rid}" name="image"/>
            <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
                <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
                  <pic:nvPicPr><pic:cNvPr id="0" name="image"/><pic:cNvPicPr/></pic:nvPicPr>
                  <pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
                  <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
                </pic:pic>
              </a:graphicData>
            </a:graphic>
          </wp:inline>
        </w:drawing>
      </w:r>
    </w:p>'''


def _write_docx_package(output_io, document_body, rels, media_files):
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体"/><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="180"/></w:pPr><w:rPr><w:b/><w:rFonts w:eastAsia="宋体"/><w:sz w:val="36"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:color w:val="1F2937"/><w:sz w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="220" w:after="100"/></w:pPr><w:rPr><w:b/><w:color w:val="2563EB"/><w:sz w:val="26"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:outlineLvl w:val="2"/><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:b/><w:color w:val="374151"/><w:sz w:val="23"/></w:rPr></w:style>
</w:styles>'''
    doc_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
 <w:body>
  {document_body}
  <w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="850" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>
 </w:body>
</w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="jpg" ContentType="image/jpeg"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    root_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    doc_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    doc_rels.append('<Relationship Id="rStyle" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    for rid, target in rels:
        doc_rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>')
    doc_rels.append('</Relationships>')

    with zipfile.ZipFile(output_io, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', content_types)
        zf.writestr('_rels/.rels', root_rels)
        zf.writestr('word/document.xml', doc_xml)
        zf.writestr('word/styles.xml', styles_xml)
        zf.writestr('word/_rels/document.xml.rels', ''.join(doc_rels))
        for name, payload in media_files:
            zf.writestr(f'word/media/{name}', payload)


def _add_docx_image(media, rels, image_idx, source_path=None, payload=None, ext='.jpg'):
    if source_path:
        ext = os.path.splitext(source_path)[1].lower()
        ext = '.jpg' if ext in ('.jpeg', '.jpg') else '.png'
        with open(source_path, 'rb') as f:
            payload = f.read()
        width, height = _image_dimensions(path=source_path)
    else:
        width, height = _image_dimensions(data=payload)
    name = f'image{image_idx}{ext}'
    media.append((name, payload))
    rid = f'rId{image_idx}'
    rels.append((rid, f'media/{name}'))
    return rid, width, height, image_idx + 1


_MANUSCRIPT_MARKER_LABELS = {
    'Y': '疑偽',
    'T': '吐魯番',
    'M': '略符M',
    'N': '略符N',
}


def _extract_manuscript_markers(other_info, code):
    text = normalize_search_text(other_info)
    code = normalize_search_text(code)
    if not text or not code or not text.startswith(code):
        return []
    suffix = text[len(code):]
    return re.findall(r'[A-Za-z]+', suffix)


def _format_manuscript_marker_note(meta):
    raw = normalize_search_text((meta or {}).get('marker_summary', ''))
    if not raw:
        return ''
    markers = []
    for part in re.findall(r'[A-Za-z]+', raw):
        for ch in part:
            if ch not in markers:
                markers.append(ch)
    labels = [_MANUSCRIPT_MARKER_LABELS.get(ch, f'略符{ch}') for ch in markers]
    return f'（{"；".join(labels)}）' if labels else ''


def _word_group_title(code, meta):
    catalog = meta.get('catalog') or {}
    year = normalize_search_text(catalog.get('西元紀') or meta.get('year_hint') or '')
    title_parts = [code]
    if year:
        title_parts.append(f'{year}年')
    if meta.get('publication_label'):
        title_parts.append(meta.get('publication_label'))
    return '　'.join(title_parts) + _format_manuscript_marker_note(meta)


def _build_defaults_word_doc(rows, keyword, source_filter):
    metadata_by_code = _build_manuscript_export_metadata(rows)
    body = []
    rels = []
    media = []
    image_idx = 1

    source_label = {
        'manual': '只看人工篩選',
        'gemini_pro': '只看 Gemini Pro',
        'gemini_fresh': '只看 Gemini Fresh',
        'all': '人工+Gemini 全部',
        'global_all': '整庫查詢+篩選標記',
        'single_candidate': '單字查詢',
    }.get(source_filter, source_filter)
    body.append(_docx_para(f'字頭「{keyword or "全部"}」字圖導出', style='Title'))
    body.append(_docx_para(f'來源: {source_label}　總數: {len(rows)}　導出時間: {datetime.now().strftime("%Y-%m-%d %H:%M")}'))
    body.append(_docx_para('提示: 本文件使用 Word 標題層級組織內容，可在 Word 導航窗格中展開或折疊各年代/寫卷小節。'))

    grouped = process_manuscript_groups(rows)
    body.append(_docx_para('上編　字圖年代簡表', style='Heading1'))
    body.append(_docx_para('本編只列年代、寫卷號與字圖，便於快速比較各時代字形。', size=20, color='4B5563'))
    year_groups = defaultdict(list)
    for code, group in grouped.items():
        meta = metadata_by_code.get(code, {})
        catalog = meta.get('catalog') or {}
        year = normalize_search_text(catalog.get('西元紀') or meta.get('year_hint') or '未定年')
        year_groups[year].append((code, group, meta))

    def year_sort_key(value):
        return (0, int(value)) if str(value).isdigit() else (1, str(value))

    for year in sorted(year_groups.keys(), key=year_sort_key):
        year_label = f'{year}年' if str(year).isdigit() else str(year)
        body.append(_docx_para(year_label, style='Heading2'))
        for code, group, meta in year_groups[year]:
            body.append(_docx_para(_word_group_title(code, meta), style='Heading3'))
            image_runs = []
            for row in group.get('items', []):
                char_path = _char_image_path(row)
                if not char_path:
                    continue
                rid, w, h, image_idx = _add_docx_image(media, rels, image_idx, source_path=char_path)
                image_runs.append(_docx_image_run(rid, w, h, target_width_inches=0.62))
                if len(image_runs) >= 10:
                    body.append(_docx_images_row(image_runs))
                    image_runs = []
            if image_runs:
                body.append(_docx_images_row(image_runs))

    body.append(_docx_page_break())
    body.append(_docx_para('下編　字圖詳表', style='Heading1'))
    body.append(_docx_para('本編列出每一號的校對欄位、字圖與紅框定位原圖。每一號另起一頁。', size=20, color='4B5563'))
    first_detail_group = True
    for code, group in grouped.items():
        meta = metadata_by_code.get(code, {})
        catalog = meta.get('catalog') or {}
        if first_detail_group:
            first_detail_group = False
        else:
            body.append(_docx_page_break())
        body.append(_docx_para(_word_group_title(code, meta), style='Heading2'))

        detail_lines = [
            _plain_meta_line('館藏地', catalog.get('館藏地')),
            _plain_meta_line('卷號', catalog.get('卷號')),
            _plain_meta_line('題名', catalog.get('題名')),
            _plain_meta_line('原卷紀年', catalog.get('原卷紀年')),
            _plain_meta_line('西元紀年', catalog.get('西元紀年') or catalog.get('西元紀')),
            _plain_meta_line('圖片出處', catalog.get('圖片出處')),
            _plain_meta_line('題記録文', catalog.get('題記録文')),
            _plain_meta_line('備註', catalog.get('備註')),
        ]
        detail_text = '；'.join([line for line in detail_lines if line])
        if detail_text:
            body.append(_docx_para(detail_text, size=20, color='4B5563'))
        elif code != '未知写卷':
            body.append(_docx_para('未匹配到寫卷目錄詳情。', size=20, color='6B7280'))

        for idx, row in enumerate(group.get('items', []), start=1):
            row_title = f'{idx}. {row.get("other_info", "")}　字圖: {row.get("default_keyword") or row.get("txt") or row.get("ocr_txt正") or ""}'
            body.append(_docx_para(row_title, style='Heading3'))
            meta_line = '；'.join(filter(None, [
                _plain_meta_line('CBETA', row.get('cmp_txt')),
                _plain_meta_line('人工校對', row.get('txt')),
                _plain_meta_line('字校對', row.get('ocr_txt正')),
                _plain_meta_line('列校對', row.get('ocr_col正')),
                _plain_meta_line('來源標記', '、'.join(row.get('source_tags') or [])),
                _plain_meta_line('備註', row.get('備註') or row.get('remark')),
            ]))
            if meta_line:
                body.append(_docx_para(meta_line, size=20))

            char_path = _char_image_path(row)
            if char_path:
                rid, w, h, image_idx = _add_docx_image(media, rels, image_idx, source_path=char_path)
                body.append(_docx_para('字圖', bold=True, size=20))
                body.append(_docx_image_paragraph(rid, w, h, target_width_inches=1.25))

            original_bytes = _original_image_with_box_bytes(row)
            if original_bytes:
                rid, w, h, image_idx = _add_docx_image(
                    media,
                    rels,
                    image_idx,
                    payload=original_bytes,
                    ext='.jpg'
                )
                body.append(_docx_para('原圖定位（紅框標示）', bold=True, size=20))
                body.append(_docx_image_paragraph(rid, w, h, target_width_inches=5.8))

    output = io.BytesIO()
    _write_docx_package(output, '\n'.join(body), rels, media)
    output.seek(0)
    return output


# ======== 登入/登出相關 ========

# 登入速率限制：按 IP 的滑動窗口，每分鐘最多 10 次嘗試
_login_attempts = {}
_login_attempts_lock = threading.Lock()

def _login_rate_limit_check(ip, max_attempts=10, window_sec=60):
    """返回 True 表示允許嘗試；False 表示已超限。
    採用簡單滑動窗口，不區分成功/失敗（成功後前端不會重試）。"""
    import time as _t
    now = _t.time()
    with _login_attempts_lock:
        arr = _login_attempts.get(ip, [])
        arr = [t for t in arr if now - t < window_sec]
        if len(arr) >= max_attempts:
            _login_attempts[ip] = arr
            return False
        arr.append(now)
        _login_attempts[ip] = arr
        # 簡易容量限制，避免內存被大量 IP 填滿
        if len(_login_attempts) > 10000:
            # 淘汰最舊的一個
            oldest = min(_login_attempts.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
            _login_attempts.pop(oldest[0], None)
        return True


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        # 速率限制：按 IP（X-Forwarded-For 優先，nginx 代理場景）
        client_ip = (request.headers.get('X-Forwarded-For', '') or request.remote_addr or '0.0.0.0').split(',')[0].strip()
        if not _login_rate_limit_check(client_ip):
            logger.warning(f"[login] rate limited IP={client_ip}")
            return jsonify({'success': False, 'error': '嘗試過於頻繁，請稍後再試'}), 429

        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        users = load_users()
        stored = users.get(username, '') if isinstance(users.get(username), str) else ''
        # 支持兩種格式，平滑遷移：
        #   - 雜湊格式（pbkdf2: / scrypt: 前綴）→ 用 werkzeug 校驗
        #   - 舊明文格式 → 等號比較（遷移完成後可移除）
        if stored and stored.startswith(('pbkdf2:', 'scrypt:')):
            from werkzeug.security import check_password_hash
            ok = check_password_hash(stored, password)
        else:
            ok = bool(stored) and stored == password
        if username in users and ok:
            # 檢查賬號是否被禁用
            if not is_account_enabled(username):
                return jsonify({'success': False, 'error': '此賬號已被禁用，請聯繫管理員'})
            session.clear()  # 防 session fixation：清掉登入前的會話數據
            session.permanent = True
            session['user'] = username
            log_activity(username, '登錄')
            return jsonify({'success': True, 'message': '登入成功'})
        else:
            return jsonify({'success': False, 'error': '帳號或密碼錯誤'})
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    session.pop('is_demo', None)
    return '', 204


# 健康檢查：無需登錄。Load balancer / 監控 / systemd 都能用。
# 驗證 DB 可連、可讀一條記錄。
@app.route('/health')
def health_check():
    try:
        from char_database import get_char_db_connection
        with get_char_db_connection() as _conn:
            _conn.execute('SELECT 1').fetchone()
        from database import get_db_connection as _stu_conn
        with _stu_conn() as _c2:
            _c2.execute('SELECT 1').fetchone()
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"[health] failed: {e}")
        return jsonify({'status': 'error', 'error': str(e)[:200]}), 500


# ======== 演示模式（無需登錄，讓潛在學生先試用）========
DEMO_ACCOUNT_ID = 'demo'

@app.route('/demo')
def demo_login():
    """公開演示入口。自動登入為 demo 賬號，跳過 dashboard 直達第一個字，
    所有寫入操作被攔截。"""
    session.clear()
    session.permanent = True
    session['user'] = DEMO_ACCOUNT_ID
    session['is_demo'] = True
    try:
        # 記錄詳細訪問信息用於統計：IP + UA + Referer
        ip = (request.headers.get('X-Forwarded-For', '') or request.remote_addr or '').split(',')[0].strip()
        ua = (request.headers.get('User-Agent', '') or '')[:200]
        ref = (request.headers.get('Referer', '') or '')[:200]
        detail = f'ip={ip} | ua={ua} | ref={ref}'
        log_activity(DEMO_ACCOUNT_ID, '演示訪問', detail[:500])
    except Exception:
        pass
    # 直接把 demo 的第一個任務字塞進 session，跳到工作台
    try:
        tasks = get_student_tasks(DEMO_ACCOUNT_ID) or []
        if tasks:
            session['active_keyword'] = tasks[0].get('keyword', '')
            session['active_page'] = 1
            return redirect('/')
    except Exception as e:
        logger.warning(f"[demo] 取任務失敗: {e}")
    # 沒任務就退回 dashboard 兜底
    return redirect('/dashboard')

# 全局寫入攔截：demo 賬號的所有 POST/PUT/DELETE/PATCH 返回假成功，不碰 DB
@app.before_request
def _demo_write_guard():
    if not session.get('is_demo'):
        return None
    if request.method not in ('POST', 'PUT', 'DELETE', 'PATCH'):
        return None
    # demo 賬號的心跳也不寫（避免污染 activity_log）
    return jsonify({
        'success': True,
        'demo_mode': True,
        'message': '演示模式：操作已攔截，不會寫入資料庫'
    })

# 模板可讀的 demo 狀態
@app.context_processor
def _inject_demo_flag():
    return {
        'is_demo_mode': session.get('is_demo', False),
        'DEMO_ACCOUNT_ID': DEMO_ACCOUNT_ID,
    }


# ======== 任務列表頁面 ========
@app.route('/go/<token>')
@login_required
def go_task(token):
    """通過任務 token 進入校勘頁（URL 不暴露關鍵字）。"""
    student_id = session.get('user', '')
    if not student_id:
        return redirect('/login')
    if student_id == ADMIN_ID:
        # 管理員仍可用 ?q= 直接搜索；這條路徑交由其他入口
        return redirect('/')
    keyword = _resolve_task_token(student_id, token)
    if not keyword:
        return redirect('/dashboard')
    session['active_keyword'] = keyword
    session['active_page'] = 1
    return redirect('/')


@app.route('/dashboard')
@login_required
def dashboard():
    student_id = session.get('user', '')

    # 管理員默認進入後台面板（搜索頁加載慢，不作為登入後首屏）
    if student_id == ADMIN_ID:
        return redirect('/admin')
    # 進入 dashboard 即離開"當前任務"，清除 session 中的 active_keyword
    session.pop('active_keyword', None)
    session.pop('active_page', None)

    tasks = get_student_tasks(student_id)
    task_stats = get_student_task_stats(student_id)

    # 為每個任務生成不可猜的 token，供模板生成 /go/<token> 鏈接
    for t in tasks:
        kw = t.get('keyword', '')
        t['token'] = _task_token(student_id, kw) if kw else ''

    pending_tasks = [t for t in tasks if t['status'] == 'pending']
    completed_tasks = [t for t in tasks if t['status'] == 'completed']

    total = task_stats.get('total', 0)
    completed = task_stats.get('completed', 0)
    percent = round(completed / total * 100) if total > 0 else 0

    total_freq = sum(t.get('frequency', 0) for t in tasks)
    freq_display = f"{total_freq/10000:.1f}萬次" if total_freq >= 10000 else f"{total_freq}次"

    stats = {
        'total': total,
        'completed': completed,
        'pending': task_stats.get('pending', 0),
        'percent': percent,
        'total_freq_display': freq_display
    }

    # 後台預熱：把該學生所有任務字的 COUNT 緩存先算好
    # 這樣學生從 dashboard 點進任何一個字，都走緩存命中路徑（~0.03s）
    # 而不是冷查詢（~0.4s）
    _warm_student_task_cache_async(student_id, tasks)

    return render_template('dashboard.html',
                           current_user=student_id,
                           stats=stats,
                           pending_tasks=pending_tasks,
                           completed_tasks=completed_tasks)


# 記錄每個學生最近一次觸發預熱的時間，避免頻繁刷 dashboard 時重複啟動線程
_warm_tracker = {}
_warm_tracker_lock = threading.Lock()
_WARM_COOLDOWN_SEC = 60  # 同一學生 60 秒內只預熱一次

def _warm_student_task_cache_async(student_id, tasks):
    """後台線程預熱學生任務字的 COUNT 緩存。非阻塞。"""
    if not tasks:
        return
    now = time.time()
    with _warm_tracker_lock:
        last = _warm_tracker.get(student_id, 0)
        if now - last < _WARM_COOLDOWN_SEC:
            return  # 冷卻期內不重複
        _warm_tracker[student_id] = now

    # pending 任務優先預熱（學生更可能先點未完成的）
    pending_kw = [t.get('keyword') for t in tasks
                  if t.get('keyword') and t.get('status') == 'pending']
    other_kw = [t.get('keyword') for t in tasks
                if t.get('keyword') and t.get('status') != 'pending']
    keywords = pending_kw + other_kw
    if not keywords:
        return

    def _run():
        try:
            t0 = time.time()
            n = warm_basic_search_counts_batch(keywords)
            logger.info(f"[預熱] 學生 {student_id}: 預熱 {n}/{len(keywords)} 個字，耗時 {time.time()-t0:.2f}s")
        except Exception as e:
            logger.error(f"[預熱] 學生 {student_id} 預熱失敗: {e}")

    thread = threading.Thread(target=_run, daemon=True, name=f"warm-{student_id}")
    thread.start()


# ======== 管理後台 ========
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('user') != ADMIN_ID:
            return jsonify({'error': '無權限'}), 403
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin')
@login_required
@admin_required
def admin_page():
    accounts = get_admin_overview()
    users = load_users()
    # 不再把密碼（明文或雜湊）塞進模板上下文；admin 可透過「重置密碼」按鈕生成新密碼

    # Add accounts that have no tasks (from users.json)
    # First, read all account_settings for missing accounts
    import sqlite3 as _sqlite3
    _settings = {}
    try:
        _conn = _sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), '網頁部署', 'students.db'))
        _conn.row_factory = _sqlite3.Row
        for row in _conn.execute('SELECT * FROM account_settings').fetchall():
            _settings[row['student_id']] = dict(row)
        _conn.close()
    except Exception:
        pass

    existing_ids = {a['student_id'] for a in accounts}
    for uid in sorted(users.keys()):
        if uid not in existing_ids:
            s = _settings.get(uid, {})
            accounts.append({
                'student_id': uid,
                'display_name': s.get('display_name', ''),
                'enabled': s.get('enabled', 1),
                'note': s.get('note', ''),
                'total': 0, 'completed': 0, 'pending': 0,
                'total_freq': 0, 'completed_freq': 0, 'pending_freq': 0,
                'last_active': None
            })

    accounts.sort(key=lambda a: a['student_id'])

    # 附加活躍時間數據
    active_times = get_all_active_times(days=30)
    for acct in accounts:
        acct['active_minutes'] = active_times.get(acct['student_id'], 0)

    total_tasks = sum(a['total'] for a in accounts)
    total_completed = sum(a['completed'] for a in accounts)
    total_pending = sum(a['pending'] for a in accounts)
    active_accounts = sum(1 for a in accounts if a['last_active'])

    # 區分原有分配和新分配
    def _acct_num(sid):
        try:
            return int(sid.split('_')[1])
        except (ValueError, IndexError):
            return -1
    old_tasks = sum(a['total'] for a in accounts if 0 < _acct_num(a['student_id']) <= 30)
    new_tasks = sum(a['total'] for a in accounts if _acct_num(a['student_id']) > 30)

    global_stats = {
        'total_accounts': len(accounts),
        'active_accounts': active_accounts,
        'total_tasks': total_tasks,
        'total_completed': total_completed,
        'total_pending': total_pending,
        'percent': round(total_completed / total_tasks * 100) if total_tasks > 0 else 0,
        'old_tasks': old_tasks,
        'new_tasks': new_tasks
    }

    pool_stats = get_pool_stats()
    # 計算下一個 scholar 編號
    scholar_nums = []
    for a in accounts:
        if a['student_id'].startswith('scholar_'):
            try:
                scholar_nums.append(int(a['student_id'].split('_')[1]))
            except (ValueError, IndexError):
                pass
    next_scholar_num = f"{max(scholar_nums) + 1:02d}" if scholar_nums else "01"

    return render_template('admin.html', accounts=accounts, global_stats=global_stats,
                           pool_stats=pool_stats, next_scholar_num=next_scholar_num)

@app.route('/admin/defaults_sources')
@login_required
@admin_required
def admin_defaults_sources():
    source_filter = _normalize_summary_filter(request.args.get('source', 'all'))
    archive_filter = _normalize_archive_filter(request.args.get('archive', 'active'))
    keyword_filter = normalize_search_text(request.args.get('q', ''))
    focus_filter = request.args.get('focus', '').strip()

    rows = _collect_default_keyword_source_summary()
    single_candidate_counts = _get_single_candidate_keyword_counts()
    rows = _merge_single_candidate_summary_rows(rows, single_candidate_counts)
    rows = _attach_single_candidate_counts_to_rows(rows, single_candidate_counts)
    keyword_notes = _load_source_keyword_notes()
    for row in rows:
        note = keyword_notes.get(row['keyword'], {})
        row['starred'] = bool(note.get('starred'))
        row['archived'] = bool(note.get('archived'))
        row['memo'] = note.get('memo', '')

    if source_filter == 'manual':
        rows = [r for r in rows if r['manual']]
    elif source_filter == 'gemini_pro':
        rows = [r for r in rows if r['gemini_pro']]
    elif source_filter == 'gemini_fresh':
        rows = [r for r in rows if r['gemini_fresh']]
    elif source_filter == 'mixed':
        rows = [
            r for r in rows
            if int(r['manual']) + int(r['gemini_pro']) + int(r['gemini_fresh']) >= 2
        ]
    elif source_filter == 'single_candidate':
        rows = [r for r in rows if r.get('single_candidate_count', 0) > 0]

    if keyword_filter:
        kw = keyword_filter.lower()
        rows = [
            r for r in rows
            if kw in r['keyword'].lower()
            or any(kw in str(v).lower() for v in r.get('variants', []))
        ]
    if archive_filter == 'active':
        rows = [r for r in rows if not r.get('archived')]
    elif archive_filter == 'archived':
        rows = [r for r in rows if r.get('archived')]
    if focus_filter == 'starred':
        rows = [r for r in rows if r.get('starred')]

    manual_total = sum(1 for r in rows if r['manual_count'] > 0)
    ai_candidate_total = sum(1 for r in rows if r.get('ai_candidate_count', 0) > 0)
    ai_only_total = sum(1 for r in rows if r['manual_count'] <= 0 and r.get('ai_candidate_count', 0) > 0)
    progress_matched_total = sum(1 for r in rows if r.get('progress_count', 0) > 0)
    pure_manual_total = sum(1 for r in rows if r['manual_count'] > 0 and r.get('ai_candidate_count', 0) <= 0)
    single_candidate_keyword_total = sum(1 for r in rows if r.get('single_candidate_count', 0) > 0)
    single_candidate_item_total = sum(r.get('single_candidate_count', 0) for r in rows)
    starred_total = sum(1 for note in keyword_notes.values() if note.get('starred'))
    archived_total = sum(1 for note in keyword_notes.values() if note.get('archived'))

    for row in rows:
        has_manual = row['manual']
        has_pro = row['gemini_pro']
        has_fresh = row['gemini_fresh']
        has_single = row.get('single_candidate_count', 0) > 0
        if row.get('ai_candidate_count', 0) > 0:
            row['source_group'] = 'ai_queue'
        elif has_single:
            row['source_group'] = 'single_candidate'
        else:
            row['source_group'] = 'manual_only'
        if has_manual and has_pro and has_fresh:
            row['source_badge'] = '人工+Gemini Pro+Gemini Fresh'
            row['source_param'] = 'all'
        elif has_manual and has_pro:
            row['source_badge'] = '人工+Gemini Pro'
            row['source_param'] = 'all'
        elif has_manual and has_fresh:
            row['source_badge'] = '人工+Gemini Fresh'
            row['source_param'] = 'all'
        elif has_pro and has_fresh:
            row['source_badge'] = 'Gemini Pro+Gemini Fresh'
            row['source_param'] = 'all'
        elif has_pro:
            row['source_badge'] = 'Gemini Pro'
            row['source_param'] = 'gemini_pro'
        elif has_fresh:
            row['source_badge'] = 'Gemini Fresh'
            row['source_param'] = 'gemini_fresh'
        elif has_single:
            row['source_badge'] = '單字候選'
            row['source_param'] = 'single_candidate'
        else:
            row['source_badge'] = '人工'
            row['source_param'] = 'manual'

    rows.sort(key=lambda r: (
        r.get('progress_rank', 999999999),
        -r.get('progress_count', 0),
        r['keyword']
    ))

    return render_template(
        'admin_sources.html',
        rows=rows,
        keyword_filter=keyword_filter,
        source_filter=source_filter,
        archive_filter=archive_filter,
        focus_filter=focus_filter,
        total_count=len(rows),
        manual_total=manual_total,
        ai_candidate_total=ai_candidate_total,
        ai_only_total=ai_only_total,
        progress_matched_total=progress_matched_total,
        pure_manual_total=pure_manual_total,
        single_candidate_keyword_total=single_candidate_keyword_total,
        single_candidate_item_total=single_candidate_item_total,
        starred_total=starred_total,
        archived_total=archived_total
    )


@app.route('/admin/defaults_sources/note', methods=['POST'])
@login_required
@admin_required
def admin_defaults_sources_note():
    data = request.get_json(force=True) or {}
    keyword = data.get('keyword', '')
    starred = data.get('starred') if 'starred' in data else None
    archived = data.get('archived') if 'archived' in data else None
    memo = data.get('memo') if 'memo' in data else None
    if isinstance(memo, str) and len(memo) > 2000:
        memo = memo[:2000]
    success, error, note = _save_source_keyword_note(
        keyword,
        starred=starred,
        memo=memo,
        archived=archived,
        updated_by=session.get('user', '')
    )
    status = 200 if success else 400
    return jsonify({'success': success, 'error': error, 'note': note}), status


@app.route('/admin/ai_coverage_audit')
@login_required
@admin_required
def admin_ai_coverage_audit():
    keyword = normalize_search_text(request.args.get('q', ''))
    audit = _build_ai_coverage_audit(keyword) if keyword else {
        'keyword': '',
        'variants': [],
        'rows': [],
        'summary': {},
        'status_meta': AI_AUDIT_STATUS_META,
    }
    return render_template('admin_coverage.html', keyword=keyword, audit=audit)


@app.route('/admin/ai_coverage_audit.csv')
@login_required
@admin_required
def admin_ai_coverage_audit_csv():
    keyword = normalize_search_text(request.args.get('q', ''))
    if not keyword:
        return Response('missing q\n', status=400, mimetype='text/plain; charset=utf-8')

    audit = _build_ai_coverage_audit(keyword)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        '字頭', '合併字形', '寫卷', '狀態',
        '整庫命中', '強命中', '人工/CBETA命中', 'OCR命中', '僅弱命中',
        '人工選定', '其他默認', 'Gemini Pro', 'Gemini Fresh',
        '樣例char_id', '樣例頁碼', '建議'
    ])
    variants_text = ' '.join(audit.get('variants') or [])
    for row in audit.get('rows', []):
        writer.writerow([
            audit.get('keyword', keyword),
            variants_text,
            row.get('manuscript', ''),
            row.get('status', {}).get('label', ''),
            row.get('total_rows', 0),
            row.get('strong_rows', 0),
            row.get('auth_rows', 0),
            row.get('ocr_rows', 0),
            row.get('weak_rows', 0),
            row.get('manual_count', 0),
            row.get('other_default_count', 0),
            row.get('gemini_pro_label', ''),
            row.get('gemini_fresh_label', ''),
            ' '.join(row.get('sample_char_ids') or []),
            ' '.join(row.get('sample_pages') or []),
            row.get('status', {}).get('action', ''),
        ])

    filename = f"ai_coverage_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        '\ufeff' + output.getvalue(),
        content_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@app.route('/admin/single_candidates')
@login_required
@admin_required
def admin_single_candidates():
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get('per_page', 100))
    except (TypeError, ValueError):
        per_page = 100

    keyword = normalize_search_text(request.args.get('q', ''))
    manuscript = normalize_search_text(request.args.get('manuscript', ''))
    source_filter = _normalize_single_candidate_source_filter(request.args.get('source', 'all'))
    rows, pager = _fetch_single_candidate_rows(
        keyword=keyword,
        manuscript=manuscript,
        source_filter=source_filter,
        page=page,
        per_page=per_page,
    )
    return render_template(
        'admin_single_candidates.html',
        rows=rows,
        pager=pager,
        source_options=[
            ('all', '全部來源'),
            ('txt', '人工校對'),
            ('cmp_txt', 'CBETA'),
            ('ocr_col正', '列校對（正字）'),
            ('ocr_col', '列 OCR'),
            ('ocr_txt', '字 OCR'),
        ]
    )


@app.route('/admin/single_candidate_cleaner')
@login_required
@admin_required
def admin_single_candidate_cleaner():
    keyword_stats = _get_single_candidate_cleaner_keywords()
    requested_keyword = normalize_search_text(request.args.get('q', ''))
    selected_keyword = requested_keyword or (keyword_stats[0]['keyword'] if keyword_stats else '')
    stats_by_keyword = {item['keyword']: item for item in keyword_stats}
    keyword_order = [item['keyword'] for item in keyword_stats]
    try:
        selected_index = keyword_order.index(selected_keyword)
    except ValueError:
        selected_index = 0
        selected_keyword = keyword_order[0] if keyword_order else ''

    current_stats = stats_by_keyword.get(selected_keyword, {'total': 0, 'discarded': 0, 'kept': 0})
    current_total = current_stats.get('total', 0)
    current_discarded = current_stats.get('discarded', 0)
    current_kept = current_stats.get('kept', 0)
    overall_total = sum(item['total'] for item in keyword_stats)
    overall_discarded = sum(item['discarded'] for item in keyword_stats)
    overall_kept = max(overall_total - overall_discarded, 0)

    return render_template(
        'admin_candidate_review.html',
        keyword_stats=keyword_stats,
        keyword_order=keyword_order,
        initial_index=selected_index,
        selected_keyword=selected_keyword,
        current_total=current_total,
        current_discarded=current_discarded,
        current_kept=current_kept,
        overall_total=overall_total,
        overall_discarded=overall_discarded,
        overall_kept=overall_kept,
    )


@app.route('/admin/single_candidate_cleaner/sections')
@login_required
@admin_required
def admin_single_candidate_cleaner_sections():
    keyword_stats = _get_single_candidate_cleaner_keywords()
    keyword_order = [item['keyword'] for item in keyword_stats]
    try:
        start = int(request.args.get('start', 0))
    except (TypeError, ValueError):
        start = 0
    try:
        limit = int(request.args.get('limit', 20))
    except (TypeError, ValueError):
        limit = 20
    start = max(0, min(start, len(keyword_order)))
    limit = max(1, min(limit, 80))
    keywords = keyword_order[start:start + limit]
    groups = _build_single_candidate_cleaner_groups(keywords)
    return jsonify({
        'success': True,
        'start': start,
        'limit': limit,
        'next_start': start + len(keywords),
        'has_more': start + len(keywords) < len(keyword_order),
        'groups': groups,
    })


@app.route('/admin/single_candidate_cleaner/backup', methods=['POST'])
@login_required
@admin_required
def admin_single_candidate_cleaner_backup():
    _ensure_single_candidate_cleaner_db()
    try:
        path = _backup_single_candidate_cleaner_db('manual')
        return jsonify({'success': True, 'path': path})
    except Exception as e:
        logger.error(f"[single-candidate-cleaner] backup failed: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/single_candidate_cleaner/discard', methods=['POST'])
@login_required
@admin_required
def admin_single_candidate_cleaner_discard():
    data = request.get_json(force=True) or {}
    keyword = normalize_search_text(data.get('keyword', ''))
    char_id = normalize_search_text(data.get('char_id', ''))
    discarded = bool(data.get('discarded'))
    if not keyword or not char_id:
        return jsonify({'success': False, 'error': 'missing keyword or char_id'}), 400

    valid = False
    for row in _get_single_candidate_cache():
        if (
            normalize_search_text(row.get('candidate_keyword') or '') == keyword
            and normalize_search_text(row.get('char_id') or '') == char_id
        ):
            valid = True
            break
    if not valid:
        return jsonify({'success': False, 'error': 'not a single-candidate row'}), 400

    success, error = _save_single_candidate_discard(
        keyword,
        char_id,
        discarded,
        updated_by=session.get('user', '')
    )
    status = 200 if success else 400
    return jsonify({
        'success': success,
        'error': error,
        'keyword': keyword,
        'char_id': char_id,
        'discarded': discarded,
    }), status


@app.route('/admin/single_candidate_cleaner/note', methods=['POST'])
@login_required
@admin_required
def admin_single_candidate_cleaner_note():
    data = request.get_json(force=True) or {}
    keyword = normalize_search_text(data.get('keyword', ''))
    char_id = normalize_search_text(data.get('char_id', ''))
    note = data.get('note', '')
    if not keyword or not char_id:
        return jsonify({'success': False, 'error': 'missing keyword or char_id'}), 400

    valid = False
    for row in _get_single_candidate_cache():
        if (
            normalize_search_text(row.get('candidate_keyword') or '') == keyword
            and normalize_search_text(row.get('char_id') or '') == char_id
        ):
            valid = True
            break
    if not valid:
        return jsonify({'success': False, 'error': 'not a single-candidate row'}), 400

    success, error, saved_note = _save_single_candidate_note(
        keyword,
        char_id,
        note,
        updated_by=session.get('user', '')
    )
    status = 200 if success else 400
    return jsonify({
        'success': success,
        'error': error,
        'keyword': keyword,
        'char_id': char_id,
        'note': saved_note,
    }), status


@app.route('/admin/manuscripts')
@login_required
@admin_required
def admin_manuscripts():
    query = normalize_search_text(request.args.get('q', ''))
    code_rows = _get_manuscript_code_summaries()
    catalog_rows = _load_manuscript_catalog_rows()
    rows = _filter_manuscript_rows(query, code_rows, catalog_rows)

    matched_count = sum(1 for row in rows if row.get('matched_catalog'))
    catalog_only_count = sum(1 for row in rows if row.get('catalog_only'))
    return render_template(
        'admin_manuscripts.html',
        rows=rows,
        query=query,
        total_codes=len(code_rows),
        total_catalog=len(catalog_rows),
        total_results=len(rows),
        matched_count=matched_count,
        catalog_only_count=catalog_only_count,
        limited=False,
        catalog_path=MANUSCRIPT_CATALOG_XLSX_PATH,
        excel_fields=_MANUSCRIPT_CATALOG_DETAIL_FIELDS,
    )


@app.route('/manuscripts')
@login_required
@admin_required
def manuscripts_index():
    query = normalize_search_text(request.args.get('q', ''))
    rows, code_rows, catalog_rows = _get_manuscript_browse_rows(query)
    matched_count = sum(1 for row in rows if row.get('matched_catalog'))
    catalog_only_count = sum(1 for row in rows if row.get('catalog_only'))
    total_char_count = sum(int(row.get('char_count') or 0) for row in rows)
    total_page_count = sum(int(row.get('page_count') or 0) for row in rows)
    return render_template(
        'manuscripts.html',
        rows=rows,
        query=query,
        total_codes=len(code_rows),
        total_catalog=len(catalog_rows),
        total_results=len(rows),
        matched_count=matched_count,
        catalog_only_count=catalog_only_count,
        total_char_count=total_char_count,
        total_page_count=total_page_count,
    )


@app.route('/manuscripts/<manuscript_code>')
@login_required
@admin_required
def manuscript_detail(manuscript_code):
    manuscript_code = normalize_search_text(manuscript_code)
    meta = _get_manuscript_meta(manuscript_code)
    if not meta:
        return f'未找到寫卷: {manuscript_code}', 404

    rows = _fetch_manuscript_sequence_rows(manuscript_code, include_images=True)
    clusters = _build_manuscript_char_clusters(rows)
    stats = {
        'char_count': len(rows),
        'cluster_count': len(clusters),
        'default_cluster_count': sum(1 for item in clusters if item.get('default_count')),
        'marked_cluster_count': sum(1 for item in clusters if item.get('marked_count')),
        'multi_form_cluster_count': sum(1 for item in clusters if item.get('has_multiple_forms')),
        'attention_cluster_count': sum(1 for item in clusters if item.get('attention_count')),
    }
    return render_template(
        'manuscript_detail.html',
        manuscript=meta,
        clusters=clusters,
        stats=stats,
    )


@app.route('/manuscripts/<manuscript_code>/chars/<path:char_key>')
@login_required
@admin_required
def manuscript_char_detail(manuscript_code, char_key):
    manuscript_code = normalize_search_text(manuscript_code)
    char_key = normalize_search_text(char_key)
    meta = _get_manuscript_meta(manuscript_code)
    if not meta:
        return f'未找到寫卷: {manuscript_code}', 404

    rows = _fetch_manuscript_rows_for_cluster(manuscript_code, char_key)
    if not rows:
        return f'未找到 {manuscript_code} 中的字: {char_key}', 404

    rows = _merge_marks_with_history_chunked(rows, get_current_student_id())
    for row in rows:
        row['mark_summary'] = _summarize_student_mark(row)
        row['other_mark_summaries'] = [
            {
                'student_id': mark.get('student_id', ''),
                'summary': _summarize_student_mark(mark),
            }
            for mark in row.get('other_marks', [])
            if _summarize_student_mark(mark)
        ]

    stats = _build_manuscript_char_stats(rows)
    return render_template(
        'manuscript_char.html',
        manuscript=meta,
        char_key=_manuscript_cluster_key(rows[0]),
        display_char=_manuscript_row_keyword(rows[0]),
        rows=rows,
        stats=stats,
    )


@app.route('/admin/toggle_account', methods=['POST'])
@login_required
@admin_required
def admin_toggle_account():
    data = request.get_json(force=True)
    student_id = data.get('student_id', '')
    enabled = data.get('enabled', True)
    success = toggle_account(student_id, enabled)
    return jsonify({'success': success})

@app.route('/admin/update_note', methods=['POST'])
@login_required
@admin_required
def admin_update_note():
    data = request.get_json(force=True)
    success = update_account_note(data.get('student_id', ''), data.get('note', ''))
    return jsonify({'success': success})

@app.route('/admin/update_name', methods=['POST'])
@login_required
@admin_required
def admin_update_name():
    data = request.get_json(force=True)
    success = update_account_display_name(data.get('student_id', ''), data.get('display_name', ''))
    return jsonify({'success': success})

@app.route('/admin/student_detail/<student_id>')
@login_required
@admin_required
def admin_student_detail(student_id):
    tasks = get_student_tasks(student_id)
    activities = get_student_activity(student_id, limit=30)
    sessions = get_student_sessions(student_id, days=30)
    return jsonify({'tasks': tasks, 'activities': activities, 'sessions': sessions})

@app.route('/admin/reset_task', methods=['POST'])
@login_required
@admin_required
def admin_reset_task():
    """管理員重置任務狀態為未完成"""
    data = request.get_json(force=True)
    student_id = data.get('student_id', '')
    keyword = data.get('keyword', '')
    success = reset_task_status(student_id, keyword)
    return jsonify({'success': success})

@app.route('/admin/create_account', methods=['POST'])
@login_required
@admin_required
def admin_create_account():
    """創建新賬號"""
    data = request.get_json(force=True)
    account_id = data.get('account_id', '').strip()
    password = data.get('password', '').strip()
    display_name = data.get('display_name', '').strip()

    if not account_id or not password:
        return jsonify({'success': False, 'error': '賬號和密碼不能為空'})

    # 檢查是否已存在
    users = load_users()
    if account_id in users:
        return jsonify({'success': False, 'error': f'賬號 {account_id} 已存在'})

    # 寫入 users.json（密碼雜湊儲存）
    from werkzeug.security import generate_password_hash
    users[account_id] = generate_password_hash(password)
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

    # 寫入 STUDENTS_CONFIG
    STUDENTS_CONFIG[account_id] = display_name or account_id

    # 創建 account_settings
    import sqlite3 as _sq
    _conn = _sq.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), '網頁部署', 'students.db'))
    _conn.execute('''
        INSERT OR IGNORE INTO account_settings (student_id, enabled, display_name)
        VALUES (?, 1, ?)
    ''', (account_id, display_name))
    _conn.commit()
    _conn.close()

    logger.info(f"創建新賬號: {account_id}")
    return jsonify({'success': True, 'account_id': account_id})


@app.route('/admin/reset_password', methods=['POST'])
@login_required
@admin_required
def admin_reset_password():
    """重置指定賬號的密碼：服務端生成 8 字元隨機密碼並雜湊存檔，
    返回明文讓 admin 複製一次給學生。"""
    data = request.get_json(force=True)
    account_id = (data.get('account_id') or '').strip()
    if not account_id:
        return jsonify({'success': False, 'error': '缺少 account_id'}), 400

    users = load_users()
    if account_id not in users:
        return jsonify({'success': False, 'error': f'賬號 {account_id} 不存在'}), 404

    # 管理員自己的密碼不走這條，避免誤操作把 admin 鎖掉
    if account_id == ADMIN_ID:
        return jsonify({'success': False, 'error': '管理員密碼請手動重置'}), 400

    import secrets
    import string
    alphabet = string.ascii_letters + string.digits
    new_plain = ''.join(secrets.choice(alphabet) for _ in range(8))

    from werkzeug.security import generate_password_hash
    users[account_id] = generate_password_hash(new_plain)
    with open(USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

    log_activity(ADMIN_ID, '重置密碼', f'目標={account_id}')
    # 明文只回傳給當前 admin，不落其他日誌
    return jsonify({'success': True, 'account_id': account_id, 'new_password': new_plain})


@app.route('/admin/pool_stats')
@login_required
@admin_required
def admin_pool_stats():
    """獲取任務池統計"""
    stats = get_pool_stats()
    keywords = get_pool_keywords(limit=8000)  # 返回全部供前端預覽
    return jsonify({'stats': stats, 'keywords': keywords})

@app.route('/admin/invalidate_cache', methods=['POST'])
@login_required
@admin_required
def admin_invalidate_cache():
    """手動清空 COUNT 緩存。在數據導入 / 回填後調用，不必重啟 Flask。"""
    n = invalidate_count_cache()
    logger.info(f"[管理] 清空 COUNT 緩存，清掉 {n} 條")
    return jsonify({'success': True, 'cleared': n})

@app.route('/admin/demo_stats')
@login_required
@admin_required
def admin_demo_stats():
    """演示模式訪問統計。"""
    from database import get_db_connection
    stats = {'total_visits': 0, 'by_day': [], 'recent_visits': [], 'unique_visitors': 0}
    try:
        with get_db_connection() as conn:
            # 總訪問數
            stats['total_visits'] = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE student_id='demo' AND action='演示訪問'"
            ).fetchone()[0]

            # 按日分組（北京時區）
            rows = conn.execute("""
                SELECT DATE(datetime(created_at, '+8 hours')) as d, COUNT(*) as n
                FROM activity_log
                WHERE student_id='demo' AND action='演示訪問'
                  AND created_at >= datetime('now', '-30 days')
                GROUP BY d ORDER BY d DESC
            """).fetchall()
            stats['by_day'] = [{'date': r['d'], 'count': r['n']} for r in rows]

            # 最近 50 次（解析 detail 字段）
            rows = conn.execute("""
                SELECT datetime(created_at, '+8 hours') as t, detail
                FROM activity_log
                WHERE student_id='demo' AND action='演示訪問'
                ORDER BY created_at DESC LIMIT 50
            """).fetchall()
            recent = []
            unique_keys = set()
            for r in rows:
                d = r['detail'] or ''
                ip = ua = ref = ''
                for part in d.split(' | '):
                    if part.startswith('ip='): ip = part[3:]
                    elif part.startswith('ua='): ua = part[3:]
                    elif part.startswith('ref='): ref = part[4:]
                recent.append({'time': r['t'], 'ip': ip, 'ua': ua[:80], 'ref': ref[:80]})
                unique_keys.add(f'{ip}|{ua[:50]}')
            stats['recent_visits'] = recent

            # 獨立訪客（近 30 天，按 IP+UA 組合去重）
            rows = conn.execute("""
                SELECT detail FROM activity_log
                WHERE student_id='demo' AND action='演示訪問'
                  AND created_at >= datetime('now', '-30 days')
            """).fetchall()
            uniq = set()
            for r in rows:
                d = r['detail'] or ''
                ip = ua = ''
                for part in d.split(' | '):
                    if part.startswith('ip='): ip = part[3:]
                    elif part.startswith('ua='): ua = part[3:]
                uniq.add(f'{ip}|{ua[:50]}')
            stats['unique_visitors'] = len(uniq)
    except Exception as e:
        logger.error(f"demo_stats failed: {e}")
        stats['error'] = str(e)

    return render_template('demo_stats.html', stats=stats)

@app.route('/admin/assign_tasks', methods=['POST'])
@login_required
@admin_required
def admin_assign_tasks():
    """從池子分配任務給學生"""
    data = request.get_json(force=True)
    student_id = data.get('student_id', '')
    count = int(data.get('count', 0))
    if not student_id or count <= 0:
        return jsonify({'success': False, 'error': '請指定賬號和數量'})
    result = assign_from_pool(student_id, count)
    return jsonify({'success': True, **result})

@app.route('/admin/return_task', methods=['POST'])
@login_required
@admin_required
def admin_return_task():
    """將任務退回池子"""
    data = request.get_json(force=True)
    student_id = data.get('student_id', '')
    keyword = data.get('keyword', '')
    success = return_to_pool(student_id, keyword)
    return jsonify({'success': success})


# ======== 受保護的圖片路由 + 反爬限速 ========
# 按 (endpoint, user_id) 分桶的滑動窗口限速,不同接口獨立計數
# admin 豁免(方便運維 / 管理工具走 admin 身份)
_rate_limit_data = {}   # { (endpoint, user_id): [timestamps] }
_rate_limit_lock = threading.Lock()

# 各接口的默認限額(每分鐘 / 每用戶)
_RATE_LIMITS = {
    'img': 600,     # 字圖:從 3000 降到 600,人肉下載 98 萬字圖需 27 小時
    'search': 30,   # 搜索:30 次/分鐘,正常用戶遠遠用不完
    'dict': 60,     # 字典:自動觸發 + 手動查,60 夠用
    'original': 60, # 原圖查看(/get_original_image):不會頻繁觸發
}

def _check_rate_limit(user_id, endpoint='img', limit=None):
    """檢查用戶在指定接口上是否超出限速。admin 永遠豁免。"""
    if user_id == ADMIN_ID:
        return True
    if limit is None:
        limit = _RATE_LIMITS.get(endpoint, 60)
    import time
    now = time.time()
    key = (endpoint, user_id)
    with _rate_limit_lock:
        arr = _rate_limit_data.get(key, [])
        arr = [t for t in arr if now - t < 60]
        if len(arr) >= limit:
            _rate_limit_data[key] = arr
            return False
        arr.append(now)
        _rate_limit_data[key] = arr
        # 簡易內存回收:超過 20000 個桶時清最老的
        if len(_rate_limit_data) > 20000:
            _rate_limit_data.pop(next(iter(_rate_limit_data)), None)
        return True

@app.route('/img/<path:filepath>')
@login_required
def serve_protected_image(filepath):
    """受保護的圖片路由：需要登錄且有限速(600/分鐘)"""
    from flask import send_from_directory
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'img'):
        logger.warning(f"[rate_limit] /img/ 超限 user={user_id}")
        return '請求過於頻繁，請稍後再試', 429

    # 安全檢查：防止目錄遍歷
    if '..' in filepath:
        return '非法路徑', 403

    try:
        return send_from_directory(IMAGE_FOLDER_PATH, filepath,
                                    max_age=86400, conditional=True)
    except FileNotFoundError:
        return '圖片未找到', 404


# ======== 詞典查詢（敦煌俗字典 / 教育部異體字字典） ========
@app.route('/dict/<char>')
@login_required
def dict_lookup(char):
    if dict_database is None or not dict_database.available():
        return jsonify({'ok': False, 'error': 'dict_unavailable'}), 503
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'dict'):
        logger.warning(f"[rate_limit] /dict/ 超限 user={user_id}")
        return jsonify({'ok': False, 'error': 'rate_limited'}), 429
    char = (char or '').strip()
    if not char:
        return jsonify({'ok': False, 'error': 'empty'}), 400
    result = dict_database.lookup(char[0])
    return jsonify({'ok': True, 'char': char[0], 'entries': result})


@app.route('/dict_res/<source>/<path:name>')
@login_required
def dict_resource(source, name):
    # 字典圖片也限速(和字典查詢共享 'img' 桶,當作圖片處理更合適)
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'img'):
        return '請求過於頻繁', 429
    if dict_database is None:
        return '詞典未啟用', 404
    p = dict_database.asset_path(source, name)
    if not p:
        return '資源未找到', 404
    return send_file(p, max_age=604800, conditional=True)


# ======== 完成任務 ========
@app.route('/complete_task', methods=['POST'])
@login_required
def complete_task():
    """學生手動標記任務完成"""
    student_id = session.get('user', '')
    data = request.get_json(force=True)
    keyword = (data.get('keyword') or '').strip()
    if not keyword:
        return jsonify({'success': False, 'error': '缺少關鍵字'})
    mark_task_completed(student_id, keyword)
    log_activity(student_id, '完成任務', f'字={keyword}')
    return jsonify({'success': True})


# ======== 心跳監控 ========
# 心跳服務端節流：每個賬號每 50 秒最多寫一次 activity_log，
# 防止惡意客戶端灌日志（客戶端默認 60s，這裡留 10s 緩衝避免誤傷）
_heartbeat_last = {}
_heartbeat_last_lock = threading.Lock()

@app.route('/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    student_id = session.get('user', '')
    if student_id and student_id != ADMIN_ID:
        import time as _t
        now = _t.time()
        with _heartbeat_last_lock:
            last = _heartbeat_last.get(student_id, 0)
            if now - last < 50:
                return jsonify({'ok': True, 'throttled': True})
            _heartbeat_last[student_id] = now
        data = request.get_json(force=True) if request.is_json else {}
        # 構建行為摘要字符串
        parts = []
        page = data.get('page', '')
        if page:
            parts.append(f'p={page}')
        ss = data.get('scroll_slow', 0)
        sf = data.get('scroll_fast', 0)
        if ss or sf:
            parts.append(f'滾:慢{ss}/快{sf}')
        bc = data.get('btn_clicks', 0)
        if bc:
            avg = data.get('avg_btn_interval', 0)
            parts.append(f'按鈕:{bc}次,間隔{avg}s')
        clicks = data.get('clicks', 0)
        if clicks:
            parts.append(f'點擊:{clicks}')
        mm = data.get('mouse_moves', 0)
        if mm:
            parts.append(f'滑鼠:{mm}')
        detail = ' | '.join(parts) if parts else ''
        record_heartbeat(student_id, detail)
    return jsonify({'ok': True})


@app.route('/save_default_keyword', methods=['POST'])
@login_required
def save_default_keyword():
    try:
        data = request.get_json(force=True)
        keyword = (data.get('keyword') or '').strip()
        char_ids = data.get('char_ids') or []
        manuscript_ids = data.get('manuscript_ids') or []
        if not keyword:
            return jsonify({'success': False, 'error': '缺少 keyword（請先輸入搜索詞）'}), 400
        if not char_ids:
            return jsonify({'success': False, 'error': '未提供要寫入的行'}), 400

        # 權限校驗：非管理員只能操作自己被分配的關鍵詞
        student_id = session.get('user', '')
        if student_id != ADMIN_ID and not is_keyword_assigned(student_id, keyword):
            return jsonify({'success': False, 'error': '無權限修改此關鍵詞'}), 403

        ensure_data_loaded()
        updated = save_default_entries(char_ids, keyword, manuscript_ids)

        # 記錄操作（不自動標記完成，由學生手動確認）
        student_id = session.get('user', '')
        if student_id and keyword:
            log_activity(student_id, '寫入DB', f'字={keyword}, 更新{updated}條')

        return jsonify({'success': True, 'updated': updated})
    except Exception as e:
        logger.error(f"寫入default_keyword失敗: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/toggle_default_keyword', methods=['POST'])
@login_required
def toggle_default_keyword():
    """第二輪：單行即時切換 default_keyword（add/remove），不走批量寫入。"""
    try:
        data = request.get_json(force=True)
        keyword = (data.get('keyword') or '').strip()
        char_id = (data.get('char_id') or '').strip()
        action = data.get('action')  # 'add' or 'remove'

        if not keyword or not char_id or action not in ('add', 'remove'):
            return jsonify({'success': False, 'error': '參數不完整'}), 400

        # 權限校驗：非管理員只能操作自己被分配的關鍵詞
        student_id = session.get('user', '')
        if student_id != ADMIN_ID and not is_keyword_assigned(student_id, keyword):
            return jsonify({'success': False, 'error': '無權限修改此關鍵詞'}), 403

        ensure_data_loaded()

        if action == 'add':
            updated = update_default_keyword([char_id], keyword)
        else:
            # 只清除當前 keyword 匹配的值，防止誤刪其他 keyword 的選定
            from char_database import get_char_db_connection
            with get_char_db_connection() as conn:
                cursor = conn.execute(
                    "UPDATE characters SET default_keyword = '' WHERE char_id = ? AND default_keyword = ?",
                    [char_id, keyword])
                updated = cursor.rowcount
                conn.commit()

        student_id = session.get('user', '')
        if student_id:
            log_activity(student_id, '直接切換默認行',
                         f'字={keyword}, action={action}, char_id={char_id}')

        return jsonify({'success': True, 'updated': updated, 'action': action})
    except Exception as e:
        logger.error(f"toggle_default_keyword失敗: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ======== 修改所有需要身份的路由，加@login_required ========
@app.route('/')
@login_required
def index():
    import time
    start_time = time.time()

    # 主搜索頁也限速(search 桶),防爬
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'search'):
        logger.warning(f"[rate_limit] / 超限 user={user_id}")
        return '請求過於頻繁,請稍後再試', 429

    ensure_data_loaded()  # 確保圖片索引已加載
    t1 = time.time()
    logger.debug(f"[性能] ensure_data_loaded: {t1-start_time:.3f}秒")

    try:
        page = int(request.args.get('page', 1))
        query = normalize_search_text(request.args.get('q', ''))
        # When a keyword is being viewed, stay above the client-side
        # auto-reload threshold (500 visible rows) without going so high
        # that rendering the grid locks the main thread for seconds.
        default_per_page = 1500 if query else 100
        per_page = normalize_per_page(int(request.args.get('per_page', default_per_page)))
        if not query:
            per_page = min(per_page, 100)
        sort_column = request.args.get('sort')
        sort_direction = request.args.get('direction')
        search_column = request.args.get('column', '')
        manuscript = request.args.get('manuscript', '').strip()
    except ValueError:
        page = 1
        per_page = 100
        query = ''
        search_column = ''
        manuscript = ''

    # 任務權限控制（student_01 為管理員，不受限制）
    student_id = session.get('user', '')
    is_admin = (student_id == ADMIN_ID)

    if not is_admin:
        # 非管理員：不接受 URL 中的 ?q=，關鍵字一律從 session 讀取
        # 學生必須從 dashboard 的 /go/<token> 進入，token 會把 keyword 放進 session
        query = (session.get('active_keyword') or '').strip()
        search_column = ''
        if not query:
            return redirect('/dashboard')
        if not is_keyword_assigned(student_id, query):
            session.pop('active_keyword', None)
            return redirect('/dashboard')
        # 記錄學生訪問（僅第一頁時記錄，避免翻頁重複記錄）
        if page == 1 and query:
            log_activity(student_id, '查看任務', f'字={query}')

    # 默認按年代信息排序
    if not sort_column:
        sort_column = 'char_id'
        sort_direction = 'asc'

    template_data = get_default_template_data()
    template_data['is_admin'] = is_admin

    # 如果有搜索参数，执行搜索（SQL分頁）
    if query:
        # 演示模式：為了讓試用體驗聚焦，只取前 5 個寫卷
        # 先多拉一些行（確保覆蓋 5+ 個寫卷），再在 Python 層裁剪
        is_demo_session = session.get('is_demo', False)
        if is_demo_session:
            fetch_per_page = 500
            demo_max_manuscripts = 5
        else:
            fetch_per_page = per_page

        t2 = time.time()
        paginated_data, total_pages, total_items, effective_per_page = search_chars(
            query,
            search_type='basic',
            search_column=search_column,
            sort_column=sort_column,
            sort_direction=sort_direction,
            page=1 if is_demo_session else page,
            per_page=fetch_per_page,
            manuscript=manuscript if is_admin else None,  # 僅管理員可用寫卷過濾
        )
        t3 = time.time()
        logger.info(f"[性能] search_chars: {t3-t2:.3f}秒")

        # Demo：裁剪成前 N 個寫卷的全部行
        if is_demo_session:
            seen_manuscripts = []
            filtered = []
            for row in paginated_data:
                mid = extract_code_from_other_info(row.get('other_info', ''))
                if mid not in seen_manuscripts:
                    if len(seen_manuscripts) >= demo_max_manuscripts:
                        continue
                    seen_manuscripts.append(mid)
                filtered.append(row)
            paginated_data = filtered
            total_items = len(filtered)
            total_pages = 1
            effective_per_page = total_items
            logger.info(f"[demo] 裁剪到 {len(seen_manuscripts)} 個寫卷，共 {total_items} 行")

        manuscript_groups = process_manuscript_groups(paginated_data)
        t4 = time.time()
        logger.info(f"[性能] process_manuscript_groups: {t4-t3:.3f}秒")

        template_data.update({
            'data': paginated_data,
            'manuscript_groups': manuscript_groups,
            'search_query': query,
            'search_column': search_column,
            'current_page': 1 if is_demo_session else page,
            'total_pages': total_pages,
            'per_page': effective_per_page,
            'total_items': total_items,
            'sort_column': sort_column,
            'sort_direction': sort_direction
        })
    else:
        # 沒有搜索參數時從數據庫獲取分頁數據（SQL 分頁+排序）
        t2 = time.time()
        data, total_pages, total_count = get_paginated_chars(
            page=page,
            per_page=per_page,
            sort_column=sort_column,
            sort_direction=sort_direction
        )
        t3 = time.time()
        logger.info(f"[性能] get_paginated_chars: {t3-t2:.3f}秒, 数据量: {len(data)}")

        # 如果是按other_info排序，需要在Python層面排序和分頁
        if sort_column == 'other_info' and len(data) > per_page:
            t3_1 = time.time()
            data = merge_marks_with_history(data, get_current_student_id())

            t3_2 = time.time()
            logger.info(f"[性能] 合併學生標記: {t3_2-t3_1:.3f}秒")

            # Python層面排序
            sorted_data = sort_data_in_python(data, sort_column=sort_column, sort_direction=sort_direction)
            t3_3 = time.time()
            logger.info(f"[性能] Python排序: {t3_3-t3_2:.3f}秒")

            # 分頁
            start = (page - 1) * per_page
            end = start + per_page
            paginated_data = sorted_data[start:end]
            t3_4 = time.time()
            logger.info(f"[性能] 分頁: {t3_4-t3_3:.3f}秒")
        else:
            # 其他排序方式，數據已經是分頁好的
            paginated_data = data

            # 合併學生標記（含前人成果）
            paginated_data = merge_marks_with_history(paginated_data, get_current_student_id())

        t7 = time.time()
        manuscript_groups = process_manuscript_groups(paginated_data)
        t8 = time.time()
        logger.info(f"[性能] process_manuscript_groups: {t8-t7:.3f}秒, 分组数: {len(manuscript_groups)}")

        template_data.update({
            'data': paginated_data,
            'manuscript_groups': manuscript_groups,
            'current_page': page,
            'total_pages': total_pages,
            'per_page': per_page,
            'total_items': total_count,
            'sort_column': sort_column,
            'sort_direction': sort_direction
        })

    t9 = time.time()
    result = render_template(HTML_FILE, **template_data)
    t10 = time.time()
    logger.info(f"[性能] render_template: {t10-t9:.3f}秒")
    logger.info(f"[性能] 总耗时: {t10-start_time:.3f}秒")

    return result

@app.route('/search', methods=['GET'])
@login_required
def search():
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'search'):
        logger.warning(f"[rate_limit] /search 超限 user={user_id}")
        return jsonify({'error': 'rate_limited', 'message': '搜索過於頻繁,請稍後再試'}), 429
    ensure_data_loaded()  # 確保數據已加載
    template_data = get_default_template_data()
    
    try:
        page = int(request.args.get('page', 1))
        per_page = normalize_per_page(int(request.args.get('per_page', 100)))
        sort_column = request.args.get('sort')
        sort_direction = request.args.get('direction')
    except ValueError:
        page = 1
        per_page = 100

    query = normalize_search_text(request.args.get('q', ''))
    search_column = request.args.get('column', '')

    # 任務權限控制（student_01 為管理員，不受限制）
    student_id = session.get('user', '')
    is_admin = (student_id == ADMIN_ID)

    if not is_admin:
        if not query:
            return redirect('/dashboard')
        if not is_keyword_assigned(student_id, query):
            return redirect('/dashboard')

    # 默認按年代信息排序
    if not sort_column:
        sort_column = 'other_info'
        sort_direction = 'asc'

    root_logger.info(f"收到搜索请求: 查询词='{query}', 列='{search_column}'")
    
    if not query:
        root_logger.info("搜索词为空，返回空结果")
        return render_template(HTML_FILE, **template_data)
    
    paginated_results, total_pages, total_items, effective_per_page = search_chars(
        query,
        search_type='basic',
        search_column=search_column,
        sort_column=sort_column,
        sort_direction=sort_direction,
        page=page,
        per_page=per_page
    )
    root_logger.info(f"搜索结果数量: {total_items}, 本頁 {len(paginated_results)}")
    
    template_data.update({
        'data': paginated_results,
        'manuscript_groups': process_manuscript_groups(paginated_results),
        'search_query': query,
        'search_column': search_column,
        'current_page': page,
        'total_pages': total_pages,
        'per_page': effective_per_page,
        'total_items': total_items,
        'sort_column': sort_column,
        'sort_direction': sort_direction
    })
    
    return render_template(HTML_FILE, **template_data)

@app.route('/defaults')
@login_required
def defaults_only():
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'search'):
        logger.warning(f"[rate_limit] /defaults 超限 user={user_id}")
        return '請求過於頻繁,請稍後再試', 429

    ensure_data_loaded()

    template_data = get_default_template_data()
    template_data['defaults_only'] = True
    template_data['is_admin'] = (session.get('user', '') == ADMIN_ID)

    try:
        page = int(request.args.get('page', 1))
        per_page = normalize_per_page(int(request.args.get('per_page', 100)))
        sort_column = request.args.get('sort')
        sort_direction = request.args.get('direction')
    except ValueError:
        page = 1
        per_page = 100

    query = normalize_search_text(request.args.get('q', ''))
    search_column = request.args.get('column', '').strip()
    search_value = normalize_search_text(request.args.get('search_value', ''))
    search_logic = request.args.get('search_logic', 'and')
    targets = request.args.getlist('targets')
    source_filter = request.args.get('source', DEFAULT_DEFAULT_SOURCE)
    source_filter = _normalize_source_filter(source_filter)
    template_data['source_filter'] = source_filter
    source_char_ids, include_default_in_source = _resolve_source_char_ids(source_filter)
    collection_id = request.args.get('collection_id', '').strip()
    if not targets and request.args.get('targets'):
        targets = [t for t in request.args.get('targets').split(',') if t]
    if search_column and search_column not in _DEFAULTS_SEARCH_TARGETS:
        search_column = ''

    if not sort_column:
        sort_column = 'other_info'
        sort_direction = 'asc'

    manuscript_collections = get_manuscript_collections(user_id, query) if query else []
    selected_collection = get_manuscript_collection(user_id, query, collection_id) if query and collection_id else None
    active_collection_ids = selected_collection.get('manuscript_ids', []) if selected_collection else []
    collection_filter_active = bool(active_collection_ids)
    fetch_page = 1 if collection_filter_active else page
    fetch_per_page = 0 if collection_filter_active else per_page
    template_data.update({
        'manuscript_collections': manuscript_collections,
        'selected_manuscript_collection': selected_collection,
        'selected_manuscript_collection_id': str(selected_collection['id']) if selected_collection else '',
        'active_manuscript_collection_ids': active_collection_ids
    })

    if query:
        template_data['search_query'] = query
        template_data['search_column'] = search_column
        keyword_variants = sorted(get_keyword_search_variants(query))
        template_data['keyword_variants'] = keyword_variants
        template_data['has_keyword_variants'] = len(keyword_variants) > 1
        if source_filter == 'single_candidate':
            paginated_data, pager = _fetch_single_candidate_rows(
                keyword=query,
                page=fetch_page,
                per_page=fetch_per_page,
                sort_column=sort_column,
                sort_direction=sort_direction
            )
            paginated_data = _annotate_single_candidate_rows(paginated_data, query)
            total_pages = max(1, pager['total_pages'])
            total_items = pager['total_count']
            effective_per_page = pager['per_page']
            if not collection_filter_active:
                page = pager['page']
        elif source_filter == 'global_all':
            paginated_data, total_pages, total_items, effective_per_page = search_chars(
                query,
                search_type='basic',
                search_column=None if search_column == 'default_keyword' else search_column,
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=fetch_page,
                per_page=fetch_per_page,
                query_variants=keyword_variants
            )
            paginated_data = _annotate_rows_with_source_tags(paginated_data, query)
            if not collection_filter_active:
                total_pages = max(1, total_pages)
                page = min(max(1, page), total_pages)
        elif not search_column or search_column == 'default_keyword':
            paginated_data, total_pages, total_items = _get_screened_chars_by_keyword(
                query,
                source_filter=source_filter,
                page=fetch_page,
                per_page=fetch_per_page,
                sort_column=sort_column,
                sort_direction=sort_direction
            )
            effective_per_page = per_page if per_page and per_page > 0 else (total_items or 1)
            if not collection_filter_active:
                total_pages = max(1, total_pages)
                page = min(max(1, page), total_pages)
        else:
            paginated_data, total_items = search_default_chars_paginated(
                query,
                search_column=search_column,
                search_type='basic',
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=fetch_page,
                per_page=fetch_per_page,
                source_char_ids=source_char_ids,
                include_default_in_source=include_default_in_source
            )
            effective_per_page = per_page if per_page and per_page > 0 else (total_items or 1)
            if not collection_filter_active:
                total_pages = math.ceil(total_items / effective_per_page) if total_items > 0 else 1
                page = min(max(1, page), total_pages)
    elif search_value and targets:
        template_data['advanced_search'] = True
        template_data['search_params'] = {
            'search_logic': search_logic,
            'search_value': search_value,
            'targets': targets
        }
        if isinstance(search_value, str):
            search_value = search_value.strip()
        valid_targets = [t for t in targets if t in _DEFAULTS_SEARCH_TARGETS]
        template_data['search_params']['targets'] = valid_targets

        if valid_targets:
            paginated_data, total_items = search_default_chars_paginated(
                search_value,
                search_type='advanced',
                search_logic=search_logic,
                targets=valid_targets,
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=page,
                per_page=per_page,
                source_char_ids=source_char_ids,
                include_default_in_source=include_default_in_source
            )
        else:
            # 舊版鏈接或本地快取帶了脏 targets 時，回退為基礎搜索，避免整頁空白。
            paginated_data, total_items = search_default_chars_paginated(
                search_value,
                search_type='basic',
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=page,
                per_page=per_page,
                source_char_ids=source_char_ids,
                include_default_in_source=include_default_in_source
            )
        effective_per_page = per_page if per_page and per_page > 0 else (total_items or 1)
        total_pages = math.ceil(total_items / effective_per_page) if total_items > 0 else 1
        page = min(max(1, page), total_pages)
    else:
        if source_filter == 'single_candidate':
            paginated_data, total_pages, total_items = [], 1, 0
            effective_per_page = per_page
        else:
            paginated_data, total_pages, total_items = get_default_chars_paginated(
                page=fetch_page,
                per_page=fetch_per_page,
                sort_column=sort_column,
                sort_direction=sort_direction,
                source_char_ids=source_char_ids,
                include_default_in_source=include_default_in_source
            )
            effective_per_page = per_page
            if not collection_filter_active:
                total_pages = max(1, total_pages)
                page = min(max(1, page), total_pages)

    if per_page == 0:
        effective_per_page = total_items or 1

    if collection_filter_active:
        filtered_rows = _filter_rows_by_manuscripts(paginated_data, active_collection_ids)
        paginated_data, total_pages, total_items, effective_per_page, page = _paginate_python_rows(
            filtered_rows,
            page=page,
            per_page=per_page
        )

    paginated_data = merge_marks_with_history(
        paginated_data,
        get_current_student_id(),
        context_keyword=query
    )
    manuscript_groups = process_manuscript_groups(paginated_data)

    template_data.update({
        'data': paginated_data,
        'manuscript_groups': manuscript_groups,
        'current_page': page,
        'total_pages': total_pages,
        'per_page': effective_per_page,
        'total_items': total_items,
        'sort_column': sort_column,
        'sort_direction': sort_direction
    })

    return render_template(HTML_FILE, **template_data)


@app.route('/defaults/manuscript_collections', methods=['POST'])
@login_required
def save_defaults_manuscript_collection():
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'write'):
        logger.warning(f"[rate_limit] /defaults/manuscript_collections 超限 user={user_id}")
        return jsonify({'success': False, 'error': '請求過於頻繁,請稍後再試'}), 429

    data = request.get_json(silent=True) or {}
    keyword = normalize_search_text(data.get('keyword', ''))
    name = normalize_search_text(data.get('name', ''))
    note = normalize_search_text(data.get('note', ''))
    manuscript_ids = _clean_manuscript_id_list(data.get('manuscript_ids') or [])

    if not keyword:
        return jsonify({'success': False, 'error': '缺少字頭'}), 400
    if not name:
        return jsonify({'success': False, 'error': '請輸入分類名稱'}), 400
    if not manuscript_ids:
        return jsonify({'success': False, 'error': '請先選中至少一個寫卷'}), 400

    collection = save_manuscript_collection(
        get_current_student_id(),
        keyword,
        name,
        manuscript_ids,
        note=note
    )
    if not collection:
        return jsonify({'success': False, 'error': '保存失敗'}), 500

    log_activity(
        get_current_student_id(),
        'save_manuscript_collection',
        f"{keyword}:{name}:{len(manuscript_ids)}"
    )
    return jsonify({'success': True, 'collection': collection})


@app.route('/defaults/manuscript_collections/<int:collection_id>', methods=['DELETE'])
@login_required
def delete_defaults_manuscript_collection(collection_id):
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'write'):
        logger.warning(f"[rate_limit] /defaults/manuscript_collections DELETE 超限 user={user_id}")
        return jsonify({'success': False, 'error': '請求過於頻繁,請稍後再試'}), 429

    keyword = normalize_search_text(request.args.get('q', ''))
    if not keyword:
        return jsonify({'success': False, 'error': '缺少字頭'}), 400

    ok = delete_manuscript_collection(get_current_student_id(), keyword, collection_id)
    if not ok:
        return jsonify({'success': False, 'error': '未找到分類'}), 404

    log_activity(
        get_current_student_id(),
        'delete_manuscript_collection',
        f"{keyword}:{collection_id}"
    )
    return jsonify({'success': True})


@app.route('/defaults/export_word')
@login_required
def export_defaults_word():
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'search'):
        logger.warning(f"[rate_limit] /defaults/export_word 超限 user={user_id}")
        return '請求過於頻繁,請稍後再試', 429

    ensure_data_loaded()

    query = normalize_search_text(request.args.get('q', ''))
    source_filter = _normalize_source_filter(request.args.get('source', DEFAULT_DEFAULT_SOURCE))
    search_column = request.args.get('column', '').strip()
    sort_column = request.args.get('sort') or 'other_info'
    sort_direction = request.args.get('direction') or 'asc'
    collection_id = request.args.get('collection_id', '').strip()
    if search_column and search_column not in _DEFAULTS_SEARCH_TARGETS:
        search_column = ''
    if not query:
        return '請先輸入要導出的字頭', 400

    try:
        if source_filter == 'single_candidate':
            rows, pager = _fetch_single_candidate_rows(
                keyword=query,
                page=1,
                per_page=0,
                sort_column=sort_column,
                sort_direction=sort_direction
            )
            rows = _annotate_single_candidate_rows(rows, query)
            total_items = pager['total_count']
        elif source_filter == 'global_all':
            keyword_variants = sorted(get_keyword_search_variants(query))
            rows, _, total_items, _ = search_chars(
                query,
                search_type='basic',
                search_column=None if search_column == 'default_keyword' else search_column,
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=1,
                per_page=0,
                query_variants=keyword_variants
            )
            rows = _annotate_rows_with_source_tags(rows, query)
        elif not search_column or search_column == 'default_keyword':
            rows, _, total_items = _get_screened_chars_by_keyword(
                query,
                source_filter=source_filter,
                page=1,
                per_page=0,
                sort_column=sort_column,
                sort_direction=sort_direction
            )
        else:
            source_char_ids, include_default_in_source = _resolve_source_char_ids(source_filter)
            rows, total_items = search_default_chars_paginated(
                query,
                search_column=search_column,
                search_type='basic',
                sort_column=sort_column,
                sort_direction=sort_direction,
                page=1,
                per_page=0,
                source_char_ids=source_char_ids,
                include_default_in_source=include_default_in_source
            )
            rows = _annotate_rows_with_source_tags(rows, query)

        rows = merge_marks_with_history(rows, get_current_student_id(), context_keyword=query)
        selected_collection = get_manuscript_collection(user_id, query, collection_id) if collection_id else None
        if selected_collection:
            rows = _filter_rows_by_manuscripts(rows, selected_collection.get('manuscript_ids', []))
        logger.info(f"[word-export] user={user_id} keyword={query} source={source_filter} rows={len(rows)}/{total_items}")
        output = _build_defaults_word_doc(rows, query, source_filter)
        collection_suffix = f"_{_safe_filename_part(selected_collection.get('name'))}" if selected_collection else ''
        filename = f'{_safe_filename_part(query)}{collection_suffix}_字圖導出_{datetime.now().strftime("%Y%m%d_%H%M")}.docx'
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.exception(f"[word-export] 導出失敗 keyword={query}: {e}")
        return 'Word 導出失敗，請查看服務器日誌', 500

@app.route('/advanced_search', methods=['GET'])
@login_required
def advanced_search():
    user_id = session.get('user', 'unknown')
    if not _check_rate_limit(user_id, 'search'):
        logger.warning(f"[rate_limit] /advanced_search 超限 user={user_id}")
        return jsonify({'error': 'rate_limited'}), 429
    ensure_data_loaded()  # 確保數據已加載
    template_data = get_default_template_data()
    template_data['advanced_search'] = True
    
    try:
        page = int(request.args.get('page', 1))
        per_page = normalize_per_page(int(request.args.get('per_page', 100)))
        sort_column = request.args.get('sort')
        sort_direction = request.args.get('direction')
    except ValueError:
        page = 1
        per_page = 100

    search_logic = request.args.get('search_logic', 'and')
    search_value = normalize_search_text(request.args.get('search_value', ''))
    
    # 处理 targets 参数，确保正确处理 URL 编码
    targets = request.args.getlist('targets')
    if not targets:
        # 如果 targets 为空，尝试从单个 targets 参数中解析
        targets_str = request.args.get('targets', '')
        if targets_str:
            targets = [target for target in targets_str.split(',') if target]
    
    search_params = {
        'search_logic': search_logic,
        'search_value': search_value,
        'targets': targets
    }

    # 默認按年代信息排序
    if not sort_column:
        sort_column = 'other_info'
        sort_direction = 'asc'
    
    if not search_value or not targets:
        template_data['search_params'] = search_params
        return render_template(HTML_FILE, **template_data)
    
    paginated_results, total_pages, total_items, effective_per_page = search_chars(
        search_value,
        search_type='advanced',
        search_logic=search_logic,
        targets=targets,
        sort_column=sort_column,
        sort_direction=sort_direction,
        page=page,
        per_page=per_page
    )
    
    template_data.update({
        'data': paginated_results,
        'manuscript_groups': process_manuscript_groups(paginated_results),
        'search_params': search_params,
        'current_page': page,
        'total_pages': total_pages,
        'per_page': effective_per_page,
        'total_items': total_items,
        'sort_column': sort_column,
        'sort_direction': sort_direction
    })
    
    return render_template(HTML_FILE, **template_data)

# ======== 搜索功能 ========
def search_chars(query, search_type='basic', search_column=None, search_logic='and', targets=None,
                 sort_column=None, sort_direction=None, page=1, per_page=100, **kwargs):
    """使用數據庫搜索字符（SQL 分頁）"""
    root_logger.info("="*50)
    root_logger.info(f"開始搜索:")
    root_logger.info(f"查詢詞: '{query}'")
    root_logger.info(f"搜索類型: '{search_type}'")
    root_logger.info(f"搜索列: '{search_column}'")
    root_logger.info("="*50)
    
    if not query or not query.strip():
        root_logger.info("搜索詞為空，返回空結果")
        return [], 1, 0, per_page
        
    query = query.strip()
    
    # 從數據庫搜索（帶分頁）
    results, total_items = search_chars_paginated(
        query=query,
        search_column=search_column,
        search_type=search_type,
        search_logic=search_logic,
        targets=targets,
        manuscript=kwargs.get('manuscript'),
        query_variants=kwargs.get('query_variants'),
        sort_column=sort_column,
        sort_direction=sort_direction,
        page=page,
        per_page=per_page
    )
    
    root_logger.info(f"數據庫搜索找到 {total_items} 個結果，本頁 {len(results)} 條")
    
    # 合併學生的標記數據
    results = merge_marks_with_history(results, get_current_student_id(), context_keyword=query)
        
    # SQL 已排序，避免 Python 全量排序
    effective_per_page = per_page if per_page and per_page > 0 else (total_items or 1)
    total_pages = math.ceil(total_items / effective_per_page) if total_items > 0 else 1
    page = min(max(1, page), total_pages)

    root_logger.info("="*50)
    root_logger.info(f"搜索完成，當前頁 {page}/{total_pages}，本頁 {len(results)} 條，總數 {total_items}")
    root_logger.info("="*50)
    return results, total_pages, total_items, effective_per_page

@app.route('/download_row', methods=['POST'])
@login_required
def download_row():
    ensure_data_loaded()  # 確保數據已加載
    try:
        row_data = request.get_json()
        char_id = row_data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 更新或创建记录
        success = add_student_annotation(
            student_id, 
            char_id, 
            situation='Bad',
            where_field=current_annotation.get('where', ''),
            operation=current_annotation.get('operation', ''),
            remark=current_annotation.get('備註', '')
        )
        
        if success:
            return jsonify({'success': True, 'message': '數據已成功標記為殘損'})
        else:
            return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500

    except Exception as e:
        logger.error(f"標記殘損失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/cancel_download', methods=['POST'])
@login_required
def cancel_download():
    try:
        row_data = request.get_json()
        char_id = row_data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 如果没有标注数据，直接返回
        if not current_annotation:
            return jsonify({'success': True, 'message': '該數據未被標記，無需操作'})
        
        # 检查其他标记字段是否为空
        other_annotations = ['where', 'operation', '備註']
        is_other_empty = all(not current_annotation.get(col, '') for col in other_annotations)
        
        if is_other_empty:
            # 如果其他字段也为空，删除整条记录
            success = delete_student_annotation(student_id, char_id)
            if success:
                return jsonify({'success': True, 'message': '已取消殘損標記並移除記錄'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
        else:
            # 如果其他字段不为空，只更新Situation字段
            success = add_student_annotation(
                student_id, 
                char_id, 
                situation='',  # 清空Situation
                where_field=current_annotation.get('where', ''),
                operation=current_annotation.get('operation', ''),
                remark=current_annotation.get('備註', '')
            )
            
            if success:
                return jsonify({'success': True, 'message': '已取消殘損標記'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
            
    except Exception as e:
        logger.error(f"取消殘損標記失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/where_row', methods=['POST'])
@login_required
def where_row():
    try:
        row_data = request.get_json()
        char_id = row_data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()

        # 從數據庫獲取源字符數據
        source_row = get_char_by_id(char_id)
        if not source_row:
            return jsonify({'success': False, 'error': '在主數據中找不到源字符'}), 404
            
        ocr_txt_zheng = source_row.get('ocr_txt正', '')
        ocr_col_zheng = source_row.get('ocr_col正', '')
        where_value = f'Mis/{ocr_txt_zheng}/{ocr_col_zheng}'

        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 更新或创建记录
        success = add_student_annotation(
            student_id, 
            char_id, 
            situation=current_annotation.get('Situation', ''),
            where_field=where_value,  # 更新where字段
            operation=current_annotation.get('operation', ''),
            remark=current_annotation.get('備註', '')
        )
        
        if success:
            return jsonify({'success': True, 'message': '數據已成功標記為誤入'})
        else:
            return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500

    except Exception as e:
        logger.error(f"標記誤入失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/cancel_where', methods=['POST'])
@login_required
def cancel_where():
    try:
        row_data = request.get_json()
        char_id = row_data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 如果没有标注数据，直接返回
        if not current_annotation:
            return jsonify({'success': True, 'message': '該數據未被標記，無需操作'})
        
        # 检查其他标记字段是否为空
        other_annotations = ['Situation', 'operation', '備註']
        is_other_empty = all(not current_annotation.get(col, '') for col in other_annotations)
        
        if is_other_empty:
            # 如果其他字段也为空，删除整条记录
            success = delete_student_annotation(student_id, char_id)
            if success:
                return jsonify({'success': True, 'message': '已取消誤入標記並移除記錄'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
        else:
            # 如果其他字段不为空，只更新where字段
            success = add_student_annotation(
                student_id, 
                char_id, 
                situation=current_annotation.get('Situation', ''),
                where_field='',  # 清空where字段
                operation=current_annotation.get('operation', ''),
                remark=current_annotation.get('備註', '')
            )
            
            if success:
                return jsonify({'success': True, 'message': '已取消誤入標記'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
            
    except Exception as e:
        logger.error(f"取消誤入標記失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/mark_number', methods=['POST'])
@login_required
def mark_number():
    try:
        data = request.get_json()
        char_id = data.get('char_id')
        number = data.get('number')
        
        if not char_id or not number or number not in ['1', '2', '3', '4']:
            return jsonify({'success': False, 'error': '缺少或無效的參數'}), 400
        
        student_id = get_current_student_id()
        
        # 從數據庫獲取源字符數據
        source_row = get_char_by_id(char_id)
        if not source_row:
            return jsonify({'success': False, 'error': '在主數據中找不到源字符'}), 404

        other_info = source_row.get('other_info', '')
        ocr_txt_zheng = source_row.get('ocr_txt正', '')
        code = extract_code_from_other_info(other_info)
        operation_value = f'{code}/{ocr_txt_zheng}/{number}'
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 更新或创建记录
        success = add_student_annotation(
            student_id, 
            char_id, 
            situation=current_annotation.get('Situation', ''),
            where_field=current_annotation.get('where', ''),
            operation=operation_value,  # 更新operation字段
            remark=current_annotation.get('備註', '')
        )
        
        if success:
            logger.info(f"學生 {student_id} 標記字符 {char_id} 為數字 {number}")
            return jsonify({'success': True, 'message': f'已標記為數字 {number}'})
        else:
            return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
        
    except Exception as e:
        logger.error(f"標記數字失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/cancel_number', methods=['POST'])
@login_required
def cancel_number():
    try:
        data = request.get_json()
        char_id = data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 如果没有标注数据，直接返回
        if not current_annotation:
            return jsonify({'success': True, 'message': '該數據未被標記，無需操作'})
        
        # 检查其他标记字段是否为空
        other_annotations = ['Situation', 'where', '備註']
        is_other_empty = all(not current_annotation.get(col, '') for col in other_annotations)
        
        if is_other_empty:
            # 如果其他字段也为空，删除整条记录
            success = delete_student_annotation(student_id, char_id)
            if success:
                return jsonify({'success': True, 'message': '已取消數字標記並移除記錄'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
        else:
            # 如果其他字段不为空，只更新operation字段
            success = add_student_annotation(
                student_id, 
                char_id, 
                situation=current_annotation.get('Situation', ''),
                where_field=current_annotation.get('where', ''),
                operation='',  # 清空operation字段
                remark=current_annotation.get('備註', '')
            )
            
            if success:
                return jsonify({'success': True, 'message': '已取消數字標記'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
            
    except Exception as e:
        logger.error(f"取消數字標記失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500



@app.route('/save_remark', methods=['POST'])
@login_required
def save_remark():
    ensure_data_loaded()
    try:
        data = request.get_json()
        char_id = data.get('char_id')
        remark = data.get('remark', '').strip()

        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 更新或创建记录
        success = add_student_annotation(
            student_id, 
            char_id, 
            situation=current_annotation.get('Situation', ''),
            where_field=current_annotation.get('where', ''),
            operation=current_annotation.get('operation', ''),
            remark=remark  # 更新remark字段
        )
        
        if success:
            return jsonify({'success': True, 'message': '備註已保存'})
        else:
            return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500

    except Exception as e:
        logger.error(f"保存備註失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/cancel_remark', methods=['POST'])
@login_required
def cancel_remark():
    try:
        data = request.get_json()
        char_id = data.get('char_id')
        if not char_id:
            return jsonify({'success': False, 'error': '缺少字符ID'}), 400

        student_id = get_current_student_id()
        
        # 从数据库获取当前标注数据
        student_annotations = get_student_annotations(student_id)
        current_annotation = get_student_annotation_by_char_id(student_annotations, char_id)
        
        # 如果没有标注数据，直接返回
        if not current_annotation:
            return jsonify({'success': True, 'message': '該數據未被標記，無需操作'})
        
        # 检查其他标记字段是否为空
        other_annotations = ['Situation', 'where', 'operation']
        is_other_empty = all(not current_annotation.get(col, '') for col in other_annotations)
        
        if is_other_empty:
            # 如果其他字段也为空，删除整条记录
            success = delete_student_annotation(student_id, char_id)
            if success:
                return jsonify({'success': True, 'message': '已取消備註並移除記錄'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500
        else:
            # 如果其他字段不为空，只更新remark字段
            success = add_student_annotation(
                student_id, 
                char_id, 
                situation=current_annotation.get('Situation', ''),
                where_field=current_annotation.get('where', ''),
                operation=current_annotation.get('operation', ''),
                remark=''  # 清空remark字段
            )
            
            if success:
                return jsonify({'success': True, 'message': '已取消備註'})
            else:
                return jsonify({'success': False, 'error': '數據庫操作失敗'}), 500

    except Exception as e:
        logger.error(f"取消備註失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ======== 學生管理相關函數 ========
# 导入数据库模块
from database import init_database, get_student_annotations, add_student_annotation, delete_student_annotation, record_file_size, check_file_size_anomaly, export_student_data_to_csv

# 导入工具函数
from annotation_lookup import get_student_annotation_by_char_id, get_all_student_annotations_dict

# 注：資料庫初始化由入口負責（wsgi.py 或 __main__ 塊），不在模塊加載時執行，
# 避免 import 時產生副作用、雙重初始化。

def get_student_file_path(student_id):
    """獲取學生的專屬文件路徑"""
    return os.path.join(DOWNLOAD_DIR, f'student_{student_id}_data.csv')

def get_student_data(student_id):
    """獲取學生的已下載數據"""
    try:
        # 从数据库获取学生数据
        data = get_student_annotations(student_id)
        return data
    except Exception as e:
        logger.error(f"讀取學生數據失敗 {student_id}: {str(e)}")
        return {}

def check_student_downloaded(char_id, student_id):
    """檢查學生是否已下載某個字符"""
    student_data = get_student_data(student_id)
    return any(row.get('char_id') == char_id for row in student_data)

def get_current_student_id():
    """獲取當前學生ID"""
    return session.get('user', None)

def extract_code_from_other_info(other_info):
    """
    從 other_info 中提取寫卷編碼，用於分組。

    改進規則（按「年代_寫卷編號」分組）：
    - 取末段（最後一個下劃線之後）的前 3 個字符作為寫卷編號，其餘視為頁碼。
    - 分組鍵固定為：`DH_<年代>_<寫卷前三位>`，可包含字母（如 020Y）。

    示例：
    - DH_458_400001  -> DH_458_400
    - DH_458_4000010 -> DH_458_400  （同一卷）
    - DH_417_250004  -> DH_417_250
    - DH_417_2500019 -> DH_417_250  （同一卷）
    - DH_270_020Y01  -> DH_270_020
    - DH_533_126Y00016 -> DH_533_126
    """
    if not other_info:
        return ''

    if not other_info.startswith('DH_'):
        return other_info

    parts = other_info.split('_')
    if len(parts) < 3:
        return other_info

    head = '_'.join(parts[:-1])
    tail = parts[-1]
    base_tail = tail[:3] if len(tail) > 3 else tail
    return f"{head}_{base_tail}"

def get_current_student_name():
    """獲取當前學生姓名"""
    student_id = get_current_student_id()
    if student_id:
        return STUDENTS_CONFIG.get(student_id, student_id)
    return None

def ensure_student_file_exists(student_id):
    """確保學生文件存在"""
    file_path = get_student_file_path(student_id)
    if not os.path.exists(file_path):
        # 創建空的CSV文件，包含所有必要的列
        # 注意：h_atten 和 r_atten 是原始 CSV 中的欄位，不應該在學生文件中
        columns = [
            'char_id', 'img_name', 'ocr_txt', 'ocr_col', 'cmp_txt', 'txt',
            'alternatives', 'column_no', 'char_no', 'cid', 'other_info',
            'ocr_txt正', 'ocr_col正', 'x', 'y', 'w', 'h', 'operation', 'Situation', 'where', '備註'
        ]
        df = pd.DataFrame(columns=columns)
        df.to_csv(file_path, index=False, encoding='utf-8-sig')
        logger.info(f"創建學生文件: {file_path}")

# ======== 學生管理路由 ========
@app.route('/set_student', methods=['POST'])
@login_required
def set_student():
    """設置學生身份"""
    try:
        data = request.get_json()
        student_id = data.get('student_id')
        
        if student_id not in STUDENTS_CONFIG:
            return jsonify({'success': False, 'error': '無效的學生ID'})
        
        session['current_student_id'] = student_id
        ensure_student_file_exists(student_id)
        
        return jsonify({
            'success': True, 
            'student_name': STUDENTS_CONFIG[student_id],
            'message': f'已設置為 {STUDENTS_CONFIG[student_id]}'
        })
    except Exception as e:
        logger.error(f"設置學生身份失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/get_student_list')
@login_required
def get_student_list():
    """獲取學生列表"""
    return jsonify({
        'success': True,
        'students': STUDENTS_CONFIG
    })

@app.route('/get_current_student')
@login_required
def get_current_student():
    """獲取當前學生信息"""
    student_id = get_current_student_id()
    if student_id:
        return jsonify({
            'success': True,
            'student_id': student_id,
            'student_name': STUDENTS_CONFIG.get(student_id, student_id)
        })
    return jsonify({'success': False, 'message': '未設置學生身份'})

@app.route('/get_student_data')
@login_required
def get_student_data_route():
    """獲取當前學生的數據"""
    student_id = get_current_student_id()
    if not student_id:
        return jsonify({'success': False, 'error': '未設置學生身份'})
    
    try:
        data = get_student_data(student_id)
        
        # 清理数据，处理NaN值和其他无法序列化的类型
        cleaned_data = []
        for row in data:
            cleaned_row = {}
            for key, value in row.items():
                # 处理NaN值
                if pd.isna(value):
                    cleaned_row[key] = ''
                # 处理其他无法序列化的类型
                elif isinstance(value, (float, int)) and (math.isnan(value) if isinstance(value, float) else False):
                    cleaned_row[key] = ''
                else:
                    cleaned_row[key] = str(value) if value is not None else ''
            cleaned_data.append(cleaned_row)
        
        return jsonify({
            'success': True,
            'data': cleaned_data,
            'count': len(cleaned_data)
        })
    except Exception as e:
        logger.error(f"獲取學生數據失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/export_student_csv')
@login_required
def export_student_csv():
    """導出學生CSV文件"""
    student_id = get_current_student_id()
    if not student_id:
        return jsonify({'success': False, 'error': '未設置學生身份'})
    
    try:
        # 从数据库导出数据到CSV文件
        file_path = get_student_file_path(student_id)
        success = export_student_data_to_csv(student_id, file_path)
        
        if not success:
            return jsonify({'success': False, 'error': '導出學生數據失敗'})
        
        student_name = STUDENTS_CONFIG.get(student_id, student_id)
        filename = f'{student_name}_數據_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        return send_file(
            file_path,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"導出學生CSV失敗: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

def load_users():
    with open(USERS_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

@app.errorhandler(401)
def unauthorized(e):
    return redirect('/login')

# ======== AI 代表字審核 blueprint ========
try:
    from ai_review_blueprint import ai_review_bp
    app.register_blueprint(ai_review_bp)
    logger.info("[AI Review] blueprint 已掛載 → /ai_review")
except Exception as _e:
    logger.warning(f"[AI Review] 掛載失敗: {_e}")

# ======== 主程式入口 ========
if __name__ == '__main__':
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='启动Flask应用')
    parser.add_argument('--port', type=int, default=5010, help='指定要使用的端口号')
    parser.add_argument('--host', type=str, default='0.0.0.0', help='指定要使用的主机地址')
    parser.add_argument('--debug', action='store_true', help='启用调试模式')
    
    # 解析命令行参数
    args = parser.parse_args()
    
    # 初始化數據庫
    init_database()
    init_char_database()  # 初始化字符數據庫
    
    # 確保圖片索引緩存已生成
    try:
        if not os.path.exists(INDEX_CACHE_FILE):
            print("正在生成圖片索引緩存...")
            load_image_index(INDEX_CACHE_FILE, ORIGINAL_IMAGE_FOLDER)
            print("圖片索引緩存生成完成")
    except Exception as e:
        print(f"緩存生成失敗: {str(e)}")
        print("請檢查文件路徑是否正確")
    
    # 檢查必要文件是否存在
    if not os.path.exists(HTML_TEMPLATE_PATH):
        print(f"警告: 模板目錄不存在: {HTML_TEMPLATE_PATH}")
    
    if not os.path.exists(IMAGE_FOLDER_PATH):
        print(f"警告: 圖片目錄不存在: {IMAGE_FOLDER_PATH}")
    
    # 使用命令行参数指定的端口，并禁用重载器
    print(f"啟動服務器: http://{args.host}:{args.port}")
    app.run(debug=args.debug, port=args.port, host=args.host, use_reloader=False)

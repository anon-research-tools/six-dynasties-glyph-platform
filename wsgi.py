"""WSGI 入口：供 gunicorn 使用。

主應用文件名包含中文和空格，gunicorn 無法直接 import，所以用 importlib
動態加載。同時負責資料庫和圖片索引的啟動初始化（主文件的 __main__ 塊
在 gunicorn 模式下不會執行）。

啟動命令：
    gunicorn -c gunicorn_config.py wsgi:app
"""

import os
import sys
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
MAIN_FILE = BASE_DIR / "「text」4 全部字圖的主页2025年06月25日09.py"

if not MAIN_FILE.exists():
    raise FileNotFoundError(f"找不到主應用文件: {MAIN_FILE}")

# 動態加載主模塊
_spec = importlib.util.spec_from_file_location("flask_app_main", str(MAIN_FILE))
_mod = importlib.util.module_from_spec(_spec)
sys.modules["flask_app_main"] = _mod
_spec.loader.exec_module(_mod)

# 把 __main__ 裡做的初始化搬進來
_mod.init_database()
_mod.init_char_database()

try:
    if not os.path.exists(_mod.INDEX_CACHE_FILE):
        print("正在生成圖片索引緩存...")
        _mod.load_image_index(_mod.INDEX_CACHE_FILE, _mod.ORIGINAL_IMAGE_FOLDER)
        print("圖片索引緩存生成完成")
except Exception as e:
    print(f"緩存生成失敗: {e}")

# 暴露給 gunicorn
app = _mod.app

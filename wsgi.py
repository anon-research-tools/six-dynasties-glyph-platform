"""WSGI 入口：供 gunicorn 使用。

gunicorn 以 wsgi:app 加载本文件。本文件再载入 app.py，并完成数据库初始化。
app.py 的 __main__ 块在 gunicorn 模式下不会执行。

启动命令：
    gunicorn -c gunicorn_config.py wsgi:app
"""

import os
import sys
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
MAIN_FILE = BASE_DIR / "app.py"

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

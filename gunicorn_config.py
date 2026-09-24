"""Gunicorn 配置 — 針對本項目的 10-20 人並發場景。

啟動：gunicorn -c gunicorn_config.py wsgi:app

設計決策：
- 1 worker + 多線程：保持單進程，讓 COUNT 緩存 / Session / 預熱線程
  全部共享一份狀態。10 人並發 SQLite WAL 完全扛得住，瓶頸不在 CPU。
- 8 個線程：學生操作大多是等 DB IO，線程切換成本低。
- 保守的超時：SQLite 寫衝突 busy_timeout 已設 5s，給 HTTP 請求留 60s
  兜底足夠。
- preload_app=True：worker 啟動前預載應用，加快響應；但這樣會讓
  gunicorn 主進程持有 DB 連接 → 我們沒有在 import 階段開連接，安全。
"""

import os

# 綁定地址：監聽所有接口（雲服務器用得上）
bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:5010')

# 單 worker 多線程
workers = 1
threads = int(os.environ.get('GUNICORN_THREADS', '8'))
worker_class = 'gthread'

# 超時：60 秒內沒響應視為卡死
timeout = 60
graceful_timeout = 30
keepalive = 5

# 日誌
accesslog = '-'    # stdout
errorlog = '-'     # stderr
loglevel = 'info'
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss'

# 預載應用：啟動稍慢但 worker 響應更快
preload_app = True

# 最大請求數後自動重啟 worker（預防內存泄漏）
max_requests = 2000
max_requests_jitter = 200

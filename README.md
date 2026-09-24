# 六朝写经异体字编年字典 · 在线整理平台

这是整理平台的程序。字图、原卷、数据库、账号和标注记录不在本仓库中。没有这些数据时，程序可以启动，检索和出图没有结果。

本地原件在 `/Users/lizhouyuan/PycharmProjects/深度學習/1 六朝寫經/「測試」線上佈局`。下表「本地文件」都相对这个目录。公开仓库里改了文件名，本地原件没有改。

## 各部分怎么配合

一次整理沿着这条线走：

1. 读者打开 `templates/login.html`，账号写在本地的 `users.json`，不在仓库里。
2. 登录后进入 `templates/dashboard.html`，看到分配给自己的字头。任务存在标注库。
3. 点进某个字头，主界面 `templates/workspace.html` 由 `app.py` 填数据。字图、释文、坐标来自字图库；残损、误入、字形分类和备注来自标注库，按账号分开。
4. 选定代表字形后，写回字图库的 `default_keyword`。模型候选只作提示，不自动成为代表字形。
5. 查看原卷时，`app.py` 用字图库里已有的坐标，在整页扫描图上框出该字。
6. 需要对照辞书时，`dict_database.py` 读取本地辞书库。辞书全文不在本仓库。
7. 按字头导出 Word 时，`app.py` 把年代简表、校对字段、单字图和红框原图写进一个文件。
8. 管理页负责分派字头、查看写卷目录、核对模型覆盖。普通整理不经过这些页面。

字图库回答「这是哪张字、在哪一卷、坐标是多少、释文是什么」。标注库回答「谁标了什么、字头任务分给了谁」。两者用字图编号相连，不把每个人的标注写进字图库。

## 文件

| 公开文件 | 本地文件 | 作用 |
| --- | --- | --- |
| `app.py` | `「text」4 全部字圖的主页2025年06月25日09.py` | 入口。检索、标注、原图、导出、页面路由都在这里 |
| `templates/workspace.html` | `「text」7. [全部]六朝寫經異體字典架構2025年06月25日09.html` | 字图整理主界面 |
| `templates/login.html` | `login.html` | 登录 |
| `templates/dashboard.html` | `dashboard.html` | 登录后的任务列表 |
| `templates/admin.html` | `admin.html` | 分派字头、查看账号 |
| `templates/admin_sources.html` | `admin_default_sources.html` | 人工确认与模型候选的字头总览 |
| `templates/admin_coverage.html` | `admin_ai_coverage_audit.html` | 模型是否覆盖到已有字头 |
| `templates/admin_single_candidates.html` | `admin_single_candidates.html` | 只有一条候选的字头 |
| `templates/admin_candidate_review.html` | `admin_single_candidate_cleaner.html` | 复核这些单条候选 |
| `templates/admin_manuscripts.html` | `admin_manuscripts.html` | 写卷目录与库内写卷码的对照 |
| `templates/manuscripts.html` | `manuscripts.html` | 按写卷浏览 |
| `templates/manuscript_detail.html` | `manuscript_detail.html` | 一卷之内的字图 |
| `templates/manuscript_char.html` | `manuscript_char.html` | 一卷中某一个字的各张字图 |
| `templates/demo_stats.html` | `demo_stats.html` | 演示账号的使用统计 |
| `char_database.py` | 同名 | 字图库 `characters.db` |
| `database.py` | 同名 | 标注库 `students.db`：标注、任务、日志 |
| `data_helpers.py` | 同名 | 把字图记录和标注合并、排序 |
| `annotation_lookup.py` | `utils.py` | 按字图编号取出一条标注 |
| `dict_database.py` | 同名 | 读取辞书库并改写页面里的图片地址 |
| `ai_review_blueprint.py` | 同名 | 模型候选的审核页，挂在 `/ai_review` |
| `wsgi.py` | 同名 | 生产环境入口，加载 `app.py` |
| `gunicorn_config.py` | 同名 | 生产环境进程配置 |
| `start_server.sh` | 同名 | 用 gunicorn 启动 |
| `requirements.txt` | 同名 | Python 依赖 |
| `static/` | `static/` | 页面样式和脚本 |
| `product_manual.md` | 同名 | 整理时的操作说明：残损、误入、代表字形、任务怎么点 |

## 运行

```bash
pip install -r requirements.txt
export SIX_DYN_IMAGE_FOLDER="/path/to/character-images"
export SIX_DYN_ORIGINAL_FOLDER="/path/to/manuscript-pages"
export SIX_DYN_CATALOG_XLSX="/path/to/catalog.xlsx"
python3 app.py --port 5010
```

数据库文件放在本目录的 `網頁部署/` 下，文件名是 `characters.db` 和 `students.db`。这两个文件不要提交。账号写在 `users.json`，也不要提交。

生产环境用 `./start_server.sh 5010`。它通过 `wsgi.py` 加载 `app.py`。

## 不包含的内容

- 单字图像和原卷扫描图
- `characters.db`、`students.db`、辞书库、模型候选库
- 账号、密码、日志和标注明细
- 第三方辞书全文

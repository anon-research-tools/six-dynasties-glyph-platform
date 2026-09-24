# 六朝写经异体字编年平台

本仓库公布整理平台的程序，供方法复核。单字图像、原卷扫描、数据库、账号与标注记录均不在其中。未配置数据时，服务可以启动，检索与出图没有结果。

## 数据如何分工

一条字图记录回答四个问题：图像编号、所属写卷、在原卷上的坐标、以及机器识别、佛典对字和人工校对。纪年、题名与题记记在写卷目录中，通过写卷编号与字图相连。

标注、任务和日志单独成库，按账号存放。代表字形确认之后，才写回字图库。模型给出的候选只供复核，不自动成为代表字形。查看原卷时，程序使用库中已有坐标，在整页扫描图上标出该字。

## 一次整理如何走完

1. 登录后进入任务列表，看到分配给当前账号的字头。
2. 打开一个字头，主界面按写卷排列字图。字图来自字图库，残损、误入、字形分类和备注来自标注库。
3. 选定代表字形并写回字图库。需要核对时，可调出辞书，或回到标有红框的原卷。
4. 按字头导出 Word。上编按年代排列字图，下编列出校对字段、单字图和原卷定位。
5. 管理端负责分派字头、核对写卷目录，并区分人工确认与模型候选。

## 程序组成

| 文件 | 作用 |
| --- | --- |
| `app.py` | 服务入口：检索、标注、原卷回溯、字头导出 |
| `templates/workspace.html` | 字图整理主界面 |
| `templates/login.html` | 登录 |
| `templates/dashboard.html` | 当前账号的字头任务 |
| `templates/manuscripts.html` | 按写卷浏览 |
| `templates/manuscript_detail.html` | 一卷之内的字图 |
| `templates/manuscript_char.html` | 同一写卷中某一字的各张字图 |
| `templates/admin.html` | 分派字头与账号 |
| `templates/admin_sources.html` | 人工确认与模型候选的字头总览 |
| `templates/admin_coverage.html` | 模型覆盖情况 |
| `templates/admin_single_candidates.html` | 仅有一条候选的字头 |
| `templates/admin_candidate_review.html` | 复核这些候选 |
| `templates/admin_manuscripts.html` | 写卷目录与库内编号的对照 |
| `char_database.py` | 字图库 |
| `database.py` | 标注、任务与日志 |
| `data_helpers.py` | 合并字图记录与标注，并处理排序 |
| `annotation_lookup.py` | 按图像编号读取一条标注 |
| `dict_database.py` | 读取辞书，供对照 |
| `ai_review_blueprint.py` | 模型候选的审核，路径为 `/ai_review` |
| `wsgi.py` | 生产环境入口 |
| `static/` | 样式与脚本 |

## 准备与启动

将下列文件放在 `data/` 目录，或用环境变量另行指定：

| 环境变量 | 内容 |
| --- | --- |
| `SIX_DYN_CHAR_DB` | 字图库，默认 `data/characters.db` |
| `SIX_DYN_STUDENTS_DB` | 标注库，默认 `data/students.db` |
| `SIX_DYN_IMAGE_FOLDER` | 单字图像目录 |
| `SIX_DYN_ORIGINAL_FOLDER` | 原卷扫描图目录 |
| `SIX_DYN_CATALOG_XLSX` | 写卷目录 |
| `SIX_DYN_VARIANT_TABLE` | 异体对照表，默认 `data/variant.txt` |

账号写在仓库外的 `users.json`。辞书库为 `data/dictionaries.db`，其文本不随程序发布。

```bash
pip install -r requirements.txt
export SIX_DYN_IMAGE_FOLDER="/path/to/character-images"
export SIX_DYN_ORIGINAL_FOLDER="/path/to/manuscript-pages"
export SIX_DYN_CATALOG_XLSX="/path/to/catalog.xlsx"
python3 app.py --port 5010
```

生产环境使用 `./start_server.sh 5010`。该脚本通过 `wsgi.py` 加载 `app.py`。

## 发布范围

本仓库只含程序。下列内容不发布，也不随程序授予使用许可：

- 单字图像与原卷扫描图
- 字图库、标注库、辞书库与模型候选库
- 账号、口令、日志与标注明细
- 第三方辞书全文

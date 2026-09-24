# 六朝写经异体字编年平台

把写经单字图像、写卷目录和协作标注放在同一条整理流程里：按字头检索与分派，确认代表字形，按库中坐标回到原卷，并按年代导出字表。

![整理界面：按字头检索、标注，并回到原卷](docs/workspace.png)

## 功能

- **协作整理。** 字头分到账号。标注残损、误入、字形分类和备注，各自保存，并通过图像编号回到同一张字图。
- **代表字形。** 选定的字图写回字图库，作为编年字表的依据。模型候选与人工结果分源显示，核对之后进入编年序列。
- **检索与原卷。** 在机器识别、佛典对字和人工校对之间检索；兼容汉字与康熙部首归入同一次查找。打开字图时，按已存坐标在整页扫描图上框出该字，并可对照辞书。
- **年代字表。** 一个字头导出为 Word。上编按写卷目录中的西元纪年排列字图，下编给出校对字段、单字图像和红框原卷。
- **按卷回看。** 从写卷目录进入某一卷、某一个字，查看该卷中的切图、题名和题记。

字图库沿用同一表结构时，上述流程可以在自备的字图、目录和账号上运行。界面中的「魔」及年代、写卷、标注均为示意。

## 数据如何汇合

写卷目录提供西元纪年、题名和题记。字图库保存图像编号、写卷编号、切图坐标和三层释文。标注库按账号记录整理结果与字头任务。三者在整理界面汇合，再生成年代简表和详表。

![字图库、写卷目录与标注库如何汇入整理界面](docs/architecture.png)

整理时的顺序是：打开分配到的字头，按写卷核对字图，写回代表字形，需要时打开辞书或红框原卷，然后导出该字头的字表。

## 快速开始

准备字图库、与记录对应的单字图像，以及 `users.json` 中的账号。原卷画框和字表中的西元纪年，再接入原卷图像与写卷目录。

```bash
pip install -r requirements.txt
export SIX_DYN_IMAGE_FOLDER="/path/to/character-images"
export SIX_DYN_ORIGINAL_FOLDER="/path/to/manuscript-pages"
export SIX_DYN_CATALOG_XLSX="/path/to/catalog.xlsx"
python3 app.py --port 5010
```

生产环境使用 `./start_server.sh 5010`。

| 环境变量 | 接入内容 |
| --- | --- |
| `SIX_DYN_CHAR_DB` | 字图库，默认 `data/characters.db` |
| `SIX_DYN_STUDENTS_DB` | 标注与任务库，默认 `data/students.db` |
| `SIX_DYN_IMAGE_FOLDER` | 单字图像目录 |
| `SIX_DYN_ORIGINAL_FOLDER` | 原卷扫描图目录 |
| `SIX_DYN_CATALOG_XLSX` | 写卷目录 |
| `SIX_DYN_VARIANT_TABLE` | 异体对照表，默认 `data/variant.txt` |

辞书文件为 `data/dictionaries.db`。

## 程序

| 能力 | 位置 |
| --- | --- |
| 检索、标注、原卷回溯、导出 | `app.py`，`templates/workspace.html` |
| 登录与字头任务 | `templates/login.html`，`templates/dashboard.html` |
| 字图库 | `char_database.py` |
| 标注、任务与日志 | `database.py` |
| 写卷浏览 | `templates/manuscripts.html` |
| 任务分派与来源区分 | `templates/admin.html`，`templates/admin_sources.html` |
| 辞书对照 | `dict_database.py` |
| 模型候选复核 | `ai_review_blueprint.py` |

## 引用

使用本程序时，请注明仓库：

李周渊，六朝写经异体字编年平台，https://github.com/anon-research-tools/six-dynasties-glyph-platform

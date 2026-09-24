# 六朝写经异体字编年字典 · 在线整理平台

这是整理平台的程序与数据架构，供方法复核。字图、原卷影像、全量数据库、账号和标注记录不在本仓库中。

## 架构

浏览器页面由 HTML、CSS 与 JavaScript 构成，服务端为 Python Flask。

| 部分 | 文件 | 作用 |
| --- | --- | --- |
| 服务入口 | `「text」4 全部字圖的主页2025年06月25日09.py` | 路由、检索、标注、原图回溯、字头导出 |
| 主界面 | `「text」7. [全部]六朝寫經異體字典架構2025年06月25日09.html` | 字图整理与标注 |
| 字图库 | `char_database.py` | `characters.db`：字图、释文、坐标、代表字形 |
| 标注库 | `database.py` | `students.db`：按账号存放的标注、任务与日志 |
| 辞书 | `dict_database.py` | 本地辞书库的读取与页面改写 |
| 模型候选 | `ai_review_blueprint.py` | 人工确认与模型候选分源展示 |

每张字图一条记录，含图像编号、写卷编号、切图坐标、机器识别、CBETA 对字与人工校对。纪年、题名与题记在写卷目录中，经写卷编号与字图相连。标注、代表字形与模型候选分库存放；只有经过人工确认的代表字形才进入编年序列。原图回溯使用库中已有坐标，在整页扫描图上标出该字，不在查询时重新做字形检测。

## 运行前需要自行准备

程序不包含数据。至少需要：

- `網頁部署/characters.db`
- 字图目录，用环境变量 `SIX_DYN_IMAGE_FOLDER` 指向
- 原卷目录，用环境变量 `SIX_DYN_ORIGINAL_FOLDER` 指向
- 写卷目录表，用环境变量 `SIX_DYN_CATALOG_XLSX` 指向
- `users.json`：账号文件，由部署者在本地创建，不要提交

```bash
pip install -r requirements.txt
export SIX_DYN_IMAGE_FOLDER="/path/to/character-images"
export SIX_DYN_ORIGINAL_FOLDER="/path/to/manuscript-pages"
export SIX_DYN_CATALOG_XLSX="/path/to/catalog.xlsx"
python3 "「text」4 全部字圖的主页2025年06月25日09.py" --port 5010
```

没有上述数据时，服务可以启动，检索和出图不会有结果。

## 不包含的内容

- 近百万张单字图像与原卷扫描图
- `characters.db`、`students.db`、辞书库与模型候选库
- 账号、密码、日志和标注明细
- 第三方辞书全文

# sep2ics

把国科大超星/学习通课表（`https://kb.mooc.ucas.edu.cn/res/pc/curriculum/schedule.html`）
导出为可导入任何日历客户端的 iCalendar（`.ics`）文件。原页面没有导出功能，本项目从页面
自身的教学接口抓取全学期数据，再离线转换成 `.ics`。

- **输入**：`data/lessons_all.json`（课表数据快照）
- **输出**：`output/ucas-<学年>-<学期>.ics`
- **依赖**：生成 ICS 只需要 **Python 3**（标准库），不联网、无第三方包；
  只有「抓取最新课表」这一步才需要浏览器。

## 抓取并生成

课表变动（选课 / 退课 / 调课 / 换教室）后，运行综合脚本即可完成「抓取 → 转换」：

```bash
python3 src/fetch_and_build.py
```

**前置条件**：`bsk`（[BrowserSkill CLI](https://github.com/Tencent/BrowserSkill/)），且浏览器已登录课表页面
（默认在 Agent Window 打开页面、复用现有登录态）。

脚本会：

1. 启动 bsk 会话（默认 `--no-focus`，不抢焦点）
2. 打开课表页面并等待就绪
3. 对 `week=1..maxWeek` 逐周请求 `getMyLessons`，按 `lessonConfigUuid` 去重
4. 写入 `data/lessons_all.json`（`userName` 自动脱敏为 `xxx`）
5. 生成 `output/ucas-<学年>-<学期>.ics`
6. 结束时停止会话、归还借用的标签页

常用参数：

| 参数 | 说明 |
|------|------|
| `--no-fetch` | 不抓取，直接用已有快照生成 ICS |
| `--source {agent,user-tab,auto}` | 抓取用的标签页来源：`agent`（默认，在 Agent Window 打开页面并复用登录态）、`user-tab`（借用已打开课表页面的用户标签页）、`auto`（优先借用用户标签页，找不到则回退到 Agent Window） |
| `--session ID` | 复用已存在的 bsk 会话；结束时不会停止该会话 |
| `--url URL` | 课表页面 URL（默认 `https://kb.mooc.ucas.edu.cn/res/pc/curriculum/schedule.html`） |
| `--max-week N` | 覆盖最大周次（默认取页面 `maxWeek`） |
| `--data PATH` | 快照路径（默认 `data/lessons_all.json`） |
| `--output PATH` | ICS 输出路径（默认 `output/ucas-<学年>-<学期>.ics`） |
| `--timeout N` | 逐周抓取请求的超时秒数（默认 120） |
| `--focus` | 允许 Agent Window 抢占焦点（默认后台打开、不打断当前工作） |
| `--keep-session` | 结束时保留会话 / 借用的标签页，便于排查 |
| `-v` / `--verbose` | 打印每一步的 bsk 命令与原始输出 |

示例：

```bash
python3 src/fetch_and_build.py --no-fetch           # 只重新生成 ICS
python3 src/fetch_and_build.py --source user-tab    # 借用已登录的用户标签页
python3 src/fetch_and_build.py --focus -v           # 前台运行并打印细节
```

不使用综合脚本时，页面内的抓取脚本与手动命令见
[`src/fetch_and_build.py`](src/fetch_and_build.py)。

## 校验（可选）

用 `icalendar` 解析产物，确认事件数：

```bash
python3 -m venv .venv && ./.venv/bin/pip install icalendar
./.venv/bin/python -c "from icalendar import Calendar; \
print(len(Calendar.from_ical(open('output/ucas-2026-2027-1.ics','rb').read()).walk('VEVENT')))"
# -> 95
```

## 目录结构

```
sep2ics/
├── README.md
├── src/                       # 代码
│   ├── fetch_and_build.py     # 综合脚本：抓取 + 转换
│   └── build_ics.py           # 转换脚本
├── data/                      # 中间产物（.gitignore 忽略，不入库）
│   ├── lessons_all.json       # 汇总的全学期课程数据（脚本输入）
│   └── raw/                   # 浏览器抓取的原始快照
│       ├── lessons_raw.txt
│       ├── network.txt
│       ├── observe.txt
│       ├── page.html
│       └── probe2.json
└── output/                    # 最终结果
    └── ucas-2026-2027-1.ics   # 可直接导入日历
```

## 原理与数据来源（附录）

### 接口

页面只渲染当前周，DOM 里拿不到全学期数据；真正的数据源是页面自身调用的接口
（`js/schedule.pc.js` 的 `Schedule.loadLessons()`）：

```
GET /pc/curriculum/getMyLessons?curTime=<ms>[&week=N]
```

响应结构：`{result, msg, data:{curriculum, lessonArray}}`。

- `curriculum`：学期元信息，含 `firstWeekDate`（第1周周一 00:00，Asia/Shanghai）、
  `maxWeek`、`maxLength`、`lessonTimeConfigArray`（节次时间表）。
- `lessonArray`：课程规则列表，逐条含周次与节次。
- 不带 `week` 只返回当前周，因此对 `week=1..maxWeek` 逐周请求并按
  `lessonConfigUuid` 去重，得到全学期规则。

### 字段映射（接口 → `data/lessons_all.json`）

抓取时把接口返回的原始对象精简并重命名，得到脚本使用的字段：

| 接口字段 | 快照字段 | 含义 |
|----------|----------|------|
| `lessonConfigUuid` | `uuid` | 同一条规则的稳定标识（用作 ICS UID 前缀） |
| `name` | `name` | 课程名 |
| `englishCourseName` | `english` | 英文名 |
| `teacherName` | `teacher` | 教师 |
| `weeks` | `weeks` | 上课周次列表，如 `2,3,4,7,8,9,10,11,12,13` |
| `dayOfWeek` | `dayOfWeek` | 星期，**1=周一 … 7=周日** |
| `beginNumber` / `length` | `beginNumber` / `length` | 起始节次 / 连续节数（`length=2` 即连上两小节） |
| `location` | `location` | 教室 |
| `classNo` / `courseNo` | `classNo` / `courseNo` | 班级 / 课程号 |
| `wholeDay` / `day` | 同左 | 在**所请求的那一周**里的具体日期，可用于交叉校验（转换不使用） |

## 文件说明

| 文件 | 说明 |
|------|------|
| `output/ucas-2026-2027-1.ics` | **最终产物**，可直接导入日历 |
| `src/fetch_and_build.py` | **综合脚本**：抓取课表并生成 ICS |
| `src/build_ics.py` | 转换脚本（输入 `data/lessons_all.json`，输出 `output/*.ics`） |
| `data/lessons_all.json` | 抓取的全学期课程数据快照（10 条规则 + `curriculum` 元信息） |
| `data/raw/lessons_raw.txt` | 单次接口响应的原始 `lessonArray`，用于核对字段 |
| `data/raw/page.html` | 课表页面 HTML 快照（借用的用户标签页） |
| `data/raw/observe.txt` | 页面的可访问性树快照（`bsk observe`） |
| `data/raw/probe2.json` | 页面内 JS 对象探测结果（`Schedule.*`、`dailyLessonMap`） |
| `data/raw/network.txt` | 页面网络活动记录（本次为空） |

## 局限与注意

- 数据来自页面内部接口，**接口字段若变更需要重新核对**；`data/` 中的快照随时可用
  `python3 src/fetch_and_build.py` 刷新（`curriculum.updateTime` 由服务端在每次响应时生成，
  随抓取时间变化，不代表课表变动）。
- `weekType`（本次均为 `2`）语义未进一步确认；由于 `weeks` 已给出显式周次列表，
  转换只使用 `weeks`。
- 只导出了当前课表；历史学期（2025-2026 第2/3学期）未处理。
- 事件为逐个展开，节假日/调课不会被自动调整，如有变动需重新抓取生成，或在日历端手动修改。

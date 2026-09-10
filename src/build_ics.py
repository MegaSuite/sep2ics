#!/usr/bin/env python3
"""把超星/学习通课表接口抓下来的 lesson 数据转成 iCalendar (.ics)。

输入: data/lessons_all.json  (bsk evaluate 导出的 {lessons:{...}, curriculum:{...}})
输出: output/ucas-<学年>-<学期>.ics

目录约定:
  src/           脚本
  data/          中间产物（脚本输入）
  data/raw/      浏览器抓取的原始快照
  output/        最终结果（.ics）
"""
import json
import os
import sys
import datetime

TZID = "Asia/Shanghai"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUTPUT_DIR = os.path.join(ROOT, "output")
CST = datetime.timezone(datetime.timedelta(hours=8))
WEEKDAY_CN = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def parse_hhmm(s):
    h, m = s.split(":")
    return int(h), int(m)


def build(lessons_path, out_path=None):
    raw = open(lessons_path, encoding="utf-8").read()
    data = json.loads(raw[raw.index("{"):])
    cur = data["curriculum"]
    lessons = list(data["lessons"].values())
    if out_path is None:
        out_path = os.path.join(
            OUTPUT_DIR,
            f"ucas-{cur['schoolYear']}-{int(cur['schoolYear']) + 1}-{cur['semester']}.ics",
        )

    # 学期第一周的周一 00:00
    first_week = datetime.datetime.fromtimestamp(cur["firstWeekDate"] / 1000, CST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    times = cur["lessonTimeConfigArray"]  # 第 1..N 节的 "开始-结束"
    term = f"{cur['schoolYear']}-{int(cur['schoolYear']) + 1} 第{cur['semester']}学期"
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def period_start(idx):  # idx: 1-based 节次 -> 开始时间
        return parse_hhmm(times[idx - 1].split("-")[0])

    def period_end(idx):
        return parse_hhmm(times[idx - 1].split("-")[1])

    def dtstart_for(week, day_of_week, begin):
        d = first_week + datetime.timedelta(days=(week - 1) * 7 + (day_of_week - 1))
        h, m = period_start(begin)
        return d.replace(hour=h, minute=m)

    def dtend_for(week, day_of_week, begin, length):
        d = first_week + datetime.timedelta(days=(week - 1) * 7 + (day_of_week - 1))
        h, m = period_end(begin + length - 1)
        return d.replace(hour=h, minute=m)

    def esc(s):
        return (str(s or "").replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))

    def fold(line):
        # RFC5545: 每行不超过 75 个八位组
        out, cur_line = [], line
        while len(cur_line.encode("utf-8")) > 75:
            cut = 74
            while cut > 1 and len(cur_line[:cut].encode("utf-8")) > 75:
                cut -= 1
            # 不要把 "\n" / "\," 这类转义序列切断
            while cut > 1 and cur_line[cut - 1] == "\\":
                cut -= 1
            out.append(cur_line[:cut])
            cur_line = " " + cur_line[cut:]
        out.append(cur_line)
        return out

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//moondrop-link-desktop//chaoxing schedule//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(term)} 课表",
        f"X-WR-TIMEZONE:{TZID}",
        "BEGIN:VTIMEZONE",
        f"TZID:{TZID}",
        "X-LIC-LOCATION:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]

    count = 0
    for les in sorted(lessons, key=lambda x: (x["dayOfWeek"], x["beginNumber"], x["name"])):
        weeks = [int(w) for w in str(les["weeks"]).split(",") if w.strip()]
        name = les["name"]
        loc = les["location"] or ""
        teacher = les["teacher"] or ""
        class_no = les["classNo"] or ""
        course_no = les["courseNo"] or ""
        begin, length = int(les["beginNumber"]), int(les["length"])
        dow = int(les["dayOfWeek"])
        week_str = "、".join(str(w) for w in weeks)
        desc = (f"教师: {teacher}\n班级: {class_no}\n课程号: {course_no}\n"
                f"节次: 第{begin}-{begin + length - 1}节\n"
                f"上课周次: {week_str}（共{len(weeks)}次）\n"
                f"教室: {loc}")
        for wk in weeks:
            s = dtstart_for(wk, dow, begin)
            e = dtend_for(wk, dow, begin, length)
            lines += [
                "BEGIN:VEVENT",
                f"UID:{les['uuid']}-w{wk}@chaoxing-ucas",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={TZID}:{s.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={TZID}:{e.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{esc(name)}",
                f"LOCATION:{esc(loc)}",
                f"DESCRIPTION:{esc(desc)}",
                f"CATEGORIES:{esc(term)}",
                "SEQUENCE:0",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
            count += 1

    lines.append("END:VCALENDAR")

    folded = []
    for line in lines:
        folded.extend(fold(line))
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\r\n".join(folded) + "\r\n")
    print(f"写出 {out_path}: {len(lessons)} 条课程规则 -> {count} 个 VEVENT")
    return count


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(DATA_DIR, "lessons_all.json"),
          sys.argv[2] if len(sys.argv) > 2 else None)

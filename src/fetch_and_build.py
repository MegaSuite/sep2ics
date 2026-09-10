#!/usr/bin/env python3
"""一条命令完成「抓取超星/学习通课表 → 生成 ICS」。

用法:
  python3 src/fetch_and_build.py              # 抓取最新课表并生成 ICS
  python3 src/fetch_and_build.py --no-fetch   # 跳过抓取，直接用已有快照生成

抓取依赖:
  - bsk（BrowserSkill CLI）
  - 浏览器中已登录的课表页面（Agent Window 复用现有登录态；
    也可用 --source user-tab 借用已打开的用户标签页）

转换依赖: 仅 Python 3 标准库。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import build_ics  # 同目录下的转换脚本

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(ROOT, "data", "lessons_all.json")
SCHEDULE_URL = "https://kb.mooc.ucas.edu.cn/res/pc/curriculum/schedule.html"
API_PATH = "/pc/curriculum/getMyLessons"

# 接口字段 -> 快照字段（顺序即写出顺序，与 build_ics.py 的输入约定一致）
FIELD_MAP = [
    ("uuid", "lessonConfigUuid"),
    ("name", "name"),
    ("english", "englishCourseName"),
    ("courseNo", "courseNo"),
    ("classNo", "classNo"),
    ("classId", "classId"),
    ("courseId", "courseId"),
    ("teacher", "teacherName"),
    ("weeks", "weeks"),
    ("weekType", "weekType"),
    ("dayOfWeek", "dayOfWeek"),
    ("beginNumber", "beginNumber"),
    ("length", "length"),
    ("location", "location"),
    ("wholeDay", "wholeDay"),
    ("day", "day"),
    ("updateTime", "updateTime"),
    ("extend", "extend"),
]

READY_JS = (
    "(function(){var c=window.Schedule&&Schedule.curriculum;"
    "return JSON.stringify({title:document.title,loggedIn:!!c,"
    "maxWeek:(c&&c.maxWeek)||null,href:location.href});})()"
)


def log(msg):
    print(msg, file=sys.stderr)


class BskError(RuntimeError):
    pass


def bsk(args, *, verbose=False):
    """运行 bsk 子命令，返回 stdout。"""
    if not shutil.which("bsk"):
        raise BskError("未找到 bsk 命令；抓取需要 BrowserSkill CLI（或改用 --no-fetch）")
    cmd = ["bsk", *args]
    if verbose:
        log("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BskError(
            f"bsk {' '.join(args)} 失败（exit {proc.returncode}）：{proc.stderr.strip()}"
        )
    return proc.stdout


def bsk_json(args, *, verbose=False):
    """运行 bsk 子命令并解析其 JSON 输出。"""
    out = bsk([*args, "--json"], verbose=verbose)
    if verbose:
        log(out.strip())
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise BskError(f"无法解析 bsk 输出为 JSON：{out[:500]}") from exc


def evaluate(session, tab_id, js, *, timeout, verbose=False):
    """在指定标签页执行 JS，返回表达式结果。"""
    env = bsk_json(
        ["evaluate", js, "--session", session, "--tab-id", str(tab_id),
         "--timeout", f"{timeout}s"],
        verbose=verbose,
    )
    if not env.get("ok"):
        raise BskError(f"页面内执行失败：{env}")
    return env.get("value")


def start_session(*, focus, verbose):
    args = ["session", "start"]
    if not focus:
        args.append("--no-focus")
    info = bsk_json(args, verbose=verbose)
    session = info.get("session_id") or info.get("session")
    if not session:
        raise BskError(f"无法获取会话 id：{info}")
    return session


def resolve_target(session, *, source, url, verbose):
    """定位用于抓取的标签页，返回 (tab_id, borrowed)。

    source=user-tab/auto：优先借用已打开课表页面的用户标签页；
    source=agent（默认）：在 Agent Window 中打开课表页面。
    """
    if source in ("auto", "user-tab"):
        tabs = bsk_json(
            ["tab", "list", "--scope", "user", "--session", session], verbose=verbose
        ).get("tabs", [])
        for tab in tabs:
            if "curriculum/schedule.html" in (tab.get("url") or ""):
                tab_id = tab["tab_id"]
                bsk_json(
                    ["tab", "borrow", str(tab_id), "--session", session], verbose=verbose
                )
                log(f"借用用户标签页 {tab_id}")
                return tab_id, True
        if source == "user-tab":
            raise BskError("未找到已打开的课表标签页；请打开课表页面或改用 --source agent")

    nav = bsk_json(["navigate", url, "--session", session], verbose=verbose)
    tab_id = nav["tab_id"]
    log(f"已在 Agent Window 打开课表页（tab {tab_id}）")
    return tab_id, False


def wait_ready(session, tab_id, *, timeout, verbose):
    """等待页面脚本就绪并确认已登录。"""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        value = evaluate(session, tab_id, READY_JS, timeout=15, verbose=verbose)
        last = json.loads(value) if value else None
        if last and last.get("loggedIn"):
            return last
        time.sleep(0.5)
    raise BskError(
        f"课表页未就绪或未登录（最后状态：{last}）。"
        f"请先在浏览器里登录 {SCHEDULE_URL} 后重试。"
    )


def fetch_lessons(session, tab_id, *, max_week, timeout, verbose):
    """逐周请求接口，按 lessonConfigUuid 保留首次出现，返回原始集合。"""
    max_week_expr = (
        str(max_week)
        if max_week
        else "((window.Schedule&&Schedule.curriculum&&Schedule.curriculum.maxWeek)||20)"
    )
    js = (
        "(async function(){"
        "var seen={};"
        "var cur=(window.Schedule&&Schedule.curriculum)||null;"
        "var origin=location.origin;"
        f"var maxWeek={max_week_expr};"
        "for(var w=1;w<=maxWeek;w++){"
        f"var r=await fetch(origin+'{API_PATH}?week='+w+'&curTime='+Date.now(),"
        "{credentials:'include'});"
        "var j=await r.json();"
        "if(!cur&&j.data&&j.data.curriculum)cur=j.data.curriculum;"
        "var arr=(j.data&&j.data.lessonArray)||[];"
        "for(var i=0;i<arr.length;i++){"
        "var l=arr[i];if(!seen[l.lessonConfigUuid])seen[l.lessonConfigUuid]=l;}"
        "}"
        "return JSON.stringify({lessons:seen,curriculum:cur});"
        "})()"
    )
    value = evaluate(session, tab_id, js, timeout=timeout, verbose=verbose)
    return json.loads(value)


def normalize(raw):
    """接口原始对象 -> 快照结构；并对 userName 脱敏。"""
    lessons = {}
    for uuid, item in (raw.get("lessons") or {}).items():
        norm = {out: item.get(src) for out, src in FIELD_MAP}
        norm["uuid"] = norm.get("uuid") or uuid
        lessons[norm["uuid"]] = norm
    curriculum = dict(raw.get("curriculum") or {})
    curriculum["userName"] = "xxx"
    return {"lessons": lessons, "curriculum": curriculum}


def write_snapshot(path, data):
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description="抓取超星/学习通课表并转换为 ICS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--no-fetch", action="store_true",
                    help="跳过浏览器抓取，直接用已有快照生成 ICS")
    ap.add_argument("--source", choices=["agent", "user-tab", "auto"], default="agent",
                    help="抓取用的标签页来源（默认 agent：在 Agent Window 打开）")
    ap.add_argument("--session", help="复用已存在的 bsk 会话（不会在结束时停止它）")
    ap.add_argument("--url", default=SCHEDULE_URL, help="课表页面 URL")
    ap.add_argument("--max-week", type=int, help="覆盖最大周次（默认取页面的 maxWeek）")
    ap.add_argument("--data", default=DEFAULT_DATA, help="课表快照路径（默认 data/lessons_all.json）")
    ap.add_argument("--output", help="ICS 输出路径（默认 output/ucas-<学年>-<学期>.ics）")
    ap.add_argument("--timeout", type=int, default=120, help="抓取请求的超时秒数（默认 120）")
    ap.add_argument("--focus", action="store_true", help="允许 Agent Window 抢占焦点")
    ap.add_argument("--keep-session", action="store_true", help="结束时保留会话/借用的标签页")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印 bsk 命令与输出")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    session = args.session
    owned_session = False
    tab_id = None
    borrowed = False

    try:
        if args.no_fetch:
            log(f"跳过抓取，使用已有快照 {args.data}")
        else:
            if not session:
                session = start_session(focus=args.focus, verbose=args.verbose)
                owned_session = True
                log(f"bsk 会话：{session}")
            tab_id, borrowed = resolve_target(
                session, source=args.source, url=args.url, verbose=args.verbose
            )
            state = wait_ready(session, tab_id, timeout=30, verbose=args.verbose)
            log(f"页面就绪：{state.get('title')}（maxWeek={state.get('maxWeek')}）")
            raw = fetch_lessons(
                session, tab_id, max_week=args.max_week,
                timeout=args.timeout, verbose=args.verbose,
            )
            data = normalize(raw)
            write_snapshot(args.data, data)
            log(f"已更新快照 {args.data}：{len(data['lessons'])} 条课程规则")

        count = build_ics.build(args.data, args.output)
        log(f"完成，共生成 {count} 个 VEVENT")
        return 0
    except (BskError, FileNotFoundError, json.JSONDecodeError, KeyError) as exc:
        log(f"错误：{exc}")
        return 1
    finally:
        if not args.keep_session:
            if borrowed and tab_id is not None and session:
                try:
                    bsk_json(["tab", "return", str(tab_id), "--session", session],
                             verbose=args.verbose)
                except BskError as exc:
                    log(f"警告：归还标签页失败：{exc}")
            if owned_session and session:
                try:
                    bsk(["session", "stop", session], verbose=args.verbose)
                    log(f"已停止会话 {session}")
                except BskError as exc:
                    log(f"警告：停止会话失败：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())

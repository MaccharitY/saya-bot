#!/usr/bin/env python3
"""看和删长期记忆（主人/管理员让记住的那些事）。

**为什么需要它**：长期记忆是唯一一份"机器人自己攒下来、又不会自动清"的数据。
攒进去容易，但要是不给个查看和删除的口子，它就变成只能进不能出的黑箱 ——
记错了一条，或者不想让它再提某件事，就没办法了。

用法::

    python tools/notes.py list              # 看全部（默认只看生效中的）
    python tools/notes.py list --all        # 连已停用的也看
    python tools/notes.py search 猫          # 按关键词找
    python tools/notes.py forget 3          # 停用第 3 条（不删行，只失效）
    python tools/notes.py path              # 只打印库文件路径

零第三方依赖。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "project_main" / "data" / "notebook.db"

ROLE_LABELS = {
    "owner": "主人",
    "admin": "管理员",
    "friend": "熟人",
    "member": "群友",
}


def _configure_stdout() -> None:
    # Windows 控制台默认 GBK，直接打中文会乱码
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _open(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        print(f"没有这个库：{path}")
        print("（还没有任何长期记忆，或者路径不对 —— 用 --db 指定）")
        raise SystemExit(1)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def cmd_list(db: sqlite3.Connection, *, show_all: bool) -> int:
    sql = "SELECT * FROM notes"
    if not show_all:
        sql += " WHERE active = 1"
    sql += " ORDER BY id DESC"
    rows = db.execute(sql).fetchall()

    if not rows:
        print("（没有长期记忆）")
        return 0

    for row in rows:
        mark = "" if row["active"] else "  [已停用]"
        role = ROLE_LABELS.get(str(row["subject_role"]), str(row["subject_role"]) or "?")
        print(f"#{row['id']:>3}  {row['noted_at']}  {role}({row['subject_qq']}){mark}")
        print(f"      {row['content']}")
    print(f"\n共 {len(rows)} 条。")
    return 0


def cmd_search(db: sqlite3.Connection, keyword: str) -> int:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = db.execute(
        "SELECT * FROM notes WHERE active = 1 AND content LIKE ? ESCAPE '\\' ORDER BY id DESC",
        (f"%{escaped}%",),
    ).fetchall()

    if not rows:
        print(f"没有提到「{keyword}」的记忆。")
        return 0
    for row in rows:
        role = ROLE_LABELS.get(str(row["subject_role"]), str(row["subject_role"]) or "?")
        print(f"#{row['id']:>3}  {row['noted_at']}  {role}({row['subject_qq']})")
        print(f"      {row['content']}")
    return 0


def cmd_forget(db: sqlite3.Connection, note_id: int) -> int:
    cursor = db.execute("UPDATE notes SET active = 0 WHERE id = ? AND active = 1", (note_id,))
    db.commit()
    if not cursor.rowcount:
        print(f"#{note_id} 不存在，或者已经停用了。")
        return 1
    print(f"已停用 #{note_id}。")
    print("（行还留着，只是不再注入提示词 —— 想彻底删掉就直接改库文件，但一般不必。）")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()

    parser = argparse.ArgumentParser(description="查看与删除长期记忆")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"库文件（默认 {DEFAULT_DB}）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="列出记忆").add_argument(
        "--all", action="store_true", help="连已停用的也列出来"
    )
    sub.add_parser("search", help="按关键词找").add_argument("keyword")
    sub.add_parser("forget", help="停用某一条").add_argument("id", type=int)
    sub.add_parser("path", help="只打印库文件路径")

    args = parser.parse_args(argv)
    path = Path(args.db)

    if args.command == "path":
        print(path)
        return 0

    db = _open(path)
    try:
        if args.command == "list":
            return cmd_list(db, show_all=args.all)
        if args.command == "search":
            return cmd_search(db, args.keyword)
        return cmd_forget(db, args.id)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

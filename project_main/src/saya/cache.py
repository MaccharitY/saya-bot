"""清缓存 —— 聊天记录与收到的图片。

⚠️ **只清这两样。** 长期记忆（`notebook.db`）和设置（`.env`）**一概不动** ——
前者是唯一"自己攒下来、不会被自动清理"的资产，后者是人的配置，
两者都不该被一个叫"清缓存"的动作顺手带走。

⚠️ **成员名单也不清。** 它是花名册（谁在这个群、叫什么、活跃度），
不是聊天内容。清掉之后她会重新问一遍所有人的名字，那没有好处。

用法（两个前端共用这一份）::

    cache.preview(data_dir)   # 只看，不动
    cache.purge(data_dir)     # 真清
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from saya.envfile import EnvFile

DEFAULT_DATA_DIR = "./data"
IMAGES_DIR = "images"


@dataclass(frozen=True, slots=True)
class Inventory:
    """一次清点看到的东西。`purge` 返回的也是它 —— 那是"清掉了多少"。"""

    messages: int = 0
    topics: int = 0
    images: int = 0
    files: int = 0
    size: int = 0  # 图片文件的总字节数

    @property
    def empty(self) -> bool:
        return not (self.messages or self.topics or self.images or self.files)


def data_dir_for(env_path: Path) -> Path:
    """`.env` 里的 `DATA_DIR` 在哪。

    ⚠️ 相对路径按 **`.env` 所在目录**（也就是 `project_main/`）解析，
    不是按当前工作目录 —— 机器人进程的 cwd 恰好就是那里，而面板是从
    仓库根目录跑的。两边各按各的 cwd 算，就会清到**不同的目录**。
    """
    try:
        raw = (
            EnvFile(env_path).get("SAYA_DATA_DIR", DEFAULT_DATA_DIR) or DEFAULT_DATA_DIR
        ).strip()
    except (OSError, ValueError):
        raw = DEFAULT_DATA_DIR
    path = Path(raw)
    return path if path.is_absolute() else (env_path.parent / path).resolve()


def _connect(db_path: Path) -> sqlite3.Connection | None:
    """只连**已经存在的**库。清缓存不该顺手建出一个空库来。"""
    if not db_path.is_file():
        return None
    # mode=rw：库不存在就报错，不会静默新建
    return sqlite3.connect(f"file:{db_path}?mode=rw", uri=True)


def _count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except sqlite3.DatabaseError:
        return 0


def inventory(data_dir: Path) -> Inventory:
    """数一数有多少东西会被清掉。**不改任何东西。**"""
    data_dir = Path(data_dir)
    messages = topics = images = 0
    conn = _connect(data_dir / "memory.db")
    if conn is not None:
        try:
            messages = _count(conn, "SELECT COUNT(*) FROM messages")
            topics = _count(conn, "SELECT COUNT(*) FROM topics")
            images = _count(conn, "SELECT COUNT(*) FROM images")
        finally:
            conn.close()

    files, size = _scan_images(data_dir)
    return Inventory(messages=messages, topics=topics, images=images, files=files, size=size)


def purge(data_dir: Path) -> Inventory:
    """真的清。返回**清掉了多少**。

    顺序是刻意的：**先删库里的行，再删文件**。反过来的话，中途出错会留下
    "文件没了、索引还在"的状态 —— 她去翻一张根本不存在的图，比多留几张图糟。
    """
    data_dir = Path(data_dir)
    conn = _connect(data_dir / "memory.db")
    removed = Inventory()
    if conn is not None:
        try:
            with conn:  # 事务：要么全删，要么全不删
                removed = Inventory(
                    messages=_count(conn, "SELECT COUNT(*) FROM messages"),
                    topics=_count(conn, "SELECT COUNT(*) FROM topics"),
                    images=_count(conn, "SELECT COUNT(*) FROM images"),
                )
                conn.execute("DELETE FROM messages")
                conn.execute("DELETE FROM topic_speakers")
                conn.execute("DELETE FROM topics")
                conn.execute("DELETE FROM images")
        finally:
            conn.close()

    files, size = _purge_images(data_dir)
    return Inventory(
        messages=removed.messages,
        topics=removed.topics,
        images=removed.images,
        files=files,
        size=size,
    )


def _scan_images(data_dir: Path) -> tuple[int, int]:
    folder = data_dir / IMAGES_DIR
    if not folder.is_dir():
        return 0, 0
    count = size = 0
    for item in folder.iterdir():
        if item.is_file():
            count += 1
            try:
                size += item.stat().st_size
            except OSError:
                pass
    return count, size


def _purge_images(data_dir: Path) -> tuple[int, int]:
    folder = data_dir / IMAGES_DIR
    if not folder.is_dir():
        return 0, 0
    count = size = 0
    for item in folder.iterdir():
        if not item.is_file():
            continue
        try:
            size += item.stat().st_size
        except OSError:
            pass
        try:
            item.unlink()
            count += 1
        except OSError:
            pass
    return count, size


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def describe(snap: Inventory, *, cleaned: bool = False) -> tuple[str, ...]:
    """给人看的两行。说明书体。"""
    if cleaned:
        if snap.empty:
            return ("没有可清的东西。",)
        return (
            f"已清空：{snap.messages} 条消息、{snap.topics} 个话题、"
            f"{snap.images} 条图片索引。",
            f"图片文件：删除 {snap.files} 个（{human_size(snap.size)}）。",
            "长期记忆与设置未改动。",
        )

    if snap.empty:
        return ("没有可清的东西。",)
    return (
        f"聊天记录：{snap.messages} 条消息、{snap.topics} 个话题。",
        f"收到的图片：{snap.images} 条索引、{snap.files} 个文件"
        f"（{human_size(snap.size)}）。",
        "清缓存不会动长期记忆和设置。",
    )

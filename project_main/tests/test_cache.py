"""清缓存（`saya/cache.py`）的测试。

⚠️ 全部在临时目录里做，**不碰 project_main/data** ——
那是真数据，测试跑一百遍也不该动它。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_MAIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_MAIN / "src"))

from saya import cache  # noqa: E402

SCHEMA = """
CREATE TABLE members (qq INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '');
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, qq INTEGER,
                       name TEXT, text TEXT, at TEXT, from_bot INTEGER DEFAULT 0);
CREATE TABLE topics (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, title TEXT,
                     summary TEXT, last_at TEXT);
CREATE TABLE topic_speakers (topic_id INTEGER, qq INTEGER, PRIMARY KEY (topic_id, qq));
CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT, qq INTEGER,
                     name TEXT, path TEXT, at TEXT);
"""


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.images = self.data / "images"
        self.images.mkdir()
        self.db = self.data / "memory.db"

    def seed(self, *, messages: int = 3, topics: int = 2, images: int = 4) -> None:
        conn = sqlite3.connect(self.db)
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO members (qq, name) VALUES (1001, '主人')")
        for i in range(messages):
            conn.execute(
                "INSERT INTO messages (session, qq, name, text, at) VALUES (?,?,?,?,?)",
                ("g:1", 1001, "主人", f"第{i}条", "2026-10-17T10:00:00"),
            )
        for i in range(topics):
            conn.execute(
                "INSERT INTO topics (session, title, summary, last_at) VALUES (?,?,?,?)",
                ("g:1", f"话题{i}", "", "2026-10-17T10:00:00"),
            )
            conn.execute("INSERT INTO topic_speakers (topic_id, qq) VALUES (?,?)", (i + 1, 1001))
        for i in range(images):
            path = self.images / f"pic{i}.jpg"
            path.write_bytes(b"x" * 100)
            conn.execute(
                "INSERT INTO images (session, qq, name, path, at) VALUES (?,?,?,?,?)",
                ("g:1", 1001, "某人", str(path), "2026-10-17T10:00:00"),
            )
        conn.commit()
        conn.close()


class InventoryTest(Fixture):
    def test_counts_everything(self) -> None:
        self.seed()
        snap = cache.inventory(self.data)
        self.assertEqual((snap.messages, snap.topics, snap.images, snap.files), (3, 2, 4, 4))
        self.assertEqual(snap.size, 400)

    def test_missing_database_is_not_an_error(self) -> None:
        snap = cache.inventory(self.data)
        self.assertTrue(snap.empty)

    def test_inventory_does_not_modify_anything(self) -> None:
        self.seed()
        cache.inventory(self.data)
        self.assertEqual(len(list(self.images.iterdir())), 4)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 3)
        conn.close()


class PurgeTest(Fixture):
    def test_clears_messages_topics_and_images(self) -> None:
        self.seed()
        snap = cache.purge(self.data)
        self.assertEqual((snap.messages, snap.topics, snap.images), (3, 2, 4))

        conn = sqlite3.connect(self.db)
        for table in ("messages", "topics", "topic_speakers", "images"):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, f"{table} 没清干净")
        conn.close()
        self.assertEqual(list(self.images.iterdir()), [])

    def test_keeps_the_member_roster(self) -> None:
        """花名册不是聊天内容 —— 清掉她就得重新问一遍所有人的名字。"""
        self.seed()
        cache.purge(self.data)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM members").fetchone()[0], 1)
        conn.close()

    def test_does_not_touch_long_term_memory_or_settings(self) -> None:
        self.seed()
        notebook = self.data / "notebook.db"
        notebook.write_bytes(b"not a real db")
        env = self.data / ".env"
        env.write_text("SAYA_OWNER_ID=1001\n", encoding="utf-8", newline="")

        cache.purge(self.data)

        self.assertTrue(notebook.is_file(), "长期记忆不该被清")
        self.assertEqual(
            env.read_text(encoding="utf-8").strip(), "SAYA_OWNER_ID=1001"
        )

    def test_reports_what_it_removed(self) -> None:
        self.seed()
        snap = cache.purge(self.data)
        text = "\n".join(cache.describe(snap, cleaned=True))
        self.assertIn("已清空", text)
        self.assertIn("长期记忆与设置未改动", text)

    def test_second_purge_is_a_no_op(self) -> None:
        self.seed()
        cache.purge(self.data)
        self.assertTrue(cache.purge(self.data).empty)

    def test_does_not_create_a_database(self) -> None:
        cache.purge(self.data)
        self.assertFalse(self.db.exists(), "清缓存不该顺手建出一个空库")


class DescribeTest(unittest.TestCase):
    def test_preview_says_what_is_kept(self) -> None:
        text = "\n".join(cache.describe(cache.Inventory(messages=1, topics=1, images=1, files=1)))
        self.assertIn("不会动长期记忆和设置", text)

    def test_empty_says_so(self) -> None:
        self.assertEqual(cache.describe(cache.Inventory()), ("没有可清的东西。",))

    def test_human_size(self) -> None:
        self.assertEqual(cache.human_size(512), "512 B")
        self.assertEqual(cache.human_size(2048), "2 KB")
        self.assertEqual(cache.human_size(3 * 1024 * 1024), "3.0 MB")

    def test_no_colloquialisms(self) -> None:
        for text in (
            cache.describe(cache.Inventory(messages=1, topics=1, images=1, files=1)),
            cache.describe(cache.Inventory(messages=1, files=1), cleaned=True),
            cache.describe(cache.Inventory()),
        ):
            joined = "\n".join(text)
            for bad in ("敲", "盯着", "拉起", "崩了", "闭嘴"):
                self.assertNotIn(bad, joined)


class DataDirTest(unittest.TestCase):
    def test_relative_path_resolves_against_the_env_file(self) -> None:
        """⚠️ 面板从仓库根目录跑，它算出来的 `./data` 会指到别处。"""
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("SAYA_DATA_DIR=./data\n", encoding="utf-8", newline="")
            self.assertEqual(cache.data_dir_for(env), (Path(tmp) / "data").resolve())

    def test_absolute_path_is_left_alone(self) -> None:
        # 用系统临时目录拼一个**两边都算绝对**的路径。
        # ⚠️ 别写 `/srv/saya`：Windows 上它没有盘符，`is_absolute()` 是 False。
        absolute = (Path(tempfile.gettempdir()) / "saya-elsewhere").resolve()
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(f"SAYA_DATA_DIR={absolute}\n", encoding="utf-8", newline="")
            self.assertEqual(cache.data_dir_for(env), absolute)

    def test_missing_file_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            self.assertEqual(cache.data_dir_for(env), (Path(tmp) / "data").resolve())


if __name__ == "__main__":
    unittest.main()

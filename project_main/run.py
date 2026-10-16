"""启动脚本 —— 让 `python run.py` 直接能跑，不用先 pip install。

src 布局下 `python -m saya` 需要先把 `src` 放进 sys.path，这个脚本就干这件事。
（一直保持零第三方依赖，所以不需要建 venv、不需要装任何包。）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from saya.__main__ import main  # noqa: E402
from saya.console import keep_console_from_crashing  # noqa: E402

if __name__ == "__main__":
    keep_console_from_crashing()
    raise SystemExit(main())

"""把标注表摊成可核对的单文件 HTML。

图片以缩略图形式内嵌（base64），所以预览器不认相对路径也能显示。
备注自动存进浏览器 localStorage，并可一键导出 CSV。

用法：
    python project_main/experiment/make_label_review.py \
        --labels pre/experiments/chat-real/labels.csv \
        --out pre/experiments/chat-real/labels.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
from pathlib import Path

from PIL import Image

THUMB = 360

PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title>
<style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px;background:#fafafa}}
.bar{{position:sticky;top:0;background:#fafafa;padding:8px 0;z-index:2}}
button{{padding:8px 14px;margin-right:8px;border:0;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer}}
button.secondary{{background:#6b7280}}
.card{{display:flex;gap:16px;background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0}}
.card img{{width:240px;height:240px;object-fit:contain;background:#f0f0f0;border-radius:6px}}
.meta{{font-size:13px;color:#666}}.q{{font-size:16px;font-weight:600;margin:6px 0}}
.gold{{color:#0a7}}.flag{{background:#fff3cd;border-color:#e0b400}}
textarea{{width:100%;min-height:52px;padding:6px;margin-top:8px;border:1px solid #ccc;border-radius:4px;font:inherit}}
.saved{{font-size:12px;color:#0a7;margin-left:6px}}
</style>
<div class="bar">
  <button onclick="exportCsv()">导出备注 CSV</button>
  <button class="secondary" onclick="clearNotes()">清空备注</button>
  <span id="count" class="meta"></span>
</div>
<h1>{title}</h1>
<p>逐条看：图片、类型、问题、参考答案。备注会自动保存；换浏览器或清缓存会丢，核对完请导出 CSV。</p>
{cards}
<script>
const KEY = "{key}";
let notes = JSON.parse(localStorage.getItem(KEY) || "{{}}");
function refresh() {{
  document.querySelectorAll("textarea").forEach(t => {{
    t.value = notes[t.dataset.id] || "";
    t.addEventListener("input", () => {{
      notes[t.dataset.id] = t.value;
      localStorage.setItem(KEY, JSON.stringify(notes));
      t.nextElementSibling.textContent = t.value ? "已保存" : "";
    }});
    if (t.value) t.nextElementSibling.textContent = "已保存";
  }});
  const n = Object.values(notes).filter(v => v.trim()).length;
  document.getElementById("count").textContent = "已填备注 " + n + " 条";
}}
function exportCsv() {{
  const rows = [["sample_id","note"]];
  document.querySelectorAll("textarea").forEach(t => rows.push([t.dataset.id, t.value]));
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(",")).join("\\n");
  const blob = new Blob(["\\ufeff" + csv], {{type: "text/csv;charset=utf-8"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "notes.csv";
  a.click();
}}
function clearNotes() {{
  if (!confirm("确定清空所有备注？")) return;
  notes = {{}};
  localStorage.removeItem(KEY);
  document.querySelectorAll("textarea").forEach(t => {{ t.value = ""; t.nextElementSibling.textContent = ""; }});
  refresh();
}}
refresh();
</script>
"""

CARD = """<div class="card{flag}">
<img src="data:image/jpeg;base64,{thumb}" alt="{sid}">
<div style="flex:1">
<div class="meta">{sid} · {type}</div>
<div class="q">{question}</div>
<div>参考答案：<b class="gold">{gold}</b></div>
<textarea data-id="{sid}" placeholder="备注（可选）"></textarea><span class="saved"></span>
</div></div>"""


def _thumb(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((THUMB, THUMB))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82)
    return base64.b64encode(buffer.getvalue()).decode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--title", default="标注核对")
    parser.add_argument("--key", default="label-review-notes")
    args = parser.parse_args()

    rows = list(csv.DictReader(open(args.labels, encoding="utf-8")))
    cards = []
    for row in rows:
        cards.append(
            CARD.format(
                flag=" flag" if row.get("needs_review") == "1" else "",
                thumb=_thumb(Path(row["file"])),
                sid=html.escape(row["sample_id"]),
                type=html.escape(row.get("type", "")),
                question=html.escape(row["question"]),
                gold=html.escape(row["gold"]),
            )
        )
    Path(args.out).write_text(
        PAGE.format(title=html.escape(args.title), cards="\n".join(cards), key=html.escape(args.key)), encoding="utf-8"
    )
    print(f"{len(cards)} 条 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

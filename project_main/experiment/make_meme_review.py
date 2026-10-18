"""meme 标注页：预填初标，可改下拉框，自动保存并导出 CSV。

用法：
    python project_main/experiment/make_meme_review.py \
        --labels pre/experiments/chat-real/meme-labels.csv \
        --out pre/experiments/chat-real/meme-labels.html
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
from pathlib import Path

from PIL import Image

FIELDS = {
    "has_text": ["有", "无"],
    "text_kind": ["梗文字", "界面字", "水印", "无"],
    "visual": ["动漫角色", "照片", "动物", "emoji", "物件", "其他"],
    "ocr": ["读到", "部分", "漏", "无字"],
}

HEAD = """<!doctype html><meta charset="utf-8"><title>meme 标注</title>
<style>
body{font-family:system-ui,"Microsoft YaHei",sans-serif;margin:24px;background:#fafafa}
.card{display:flex;gap:16px;background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0}
img{width:240px;height:240px;object-fit:contain;background:#f0f0f0;border-radius:6px}
.meta{font-size:13px;color:#666}.sel{margin:8px 0}label{font-size:13px;margin-right:10px}
select{margin-left:4px;padding:4px}textarea{width:100%;min-height:44px;padding:6px;border:1px solid #ccc}
button{padding:8px 14px;border:0;border-radius:6px;background:#2563eb;color:#fff;cursor:pointer;margin-bottom:10px}
</style>
<button onclick="exportCsv()">导出标注 CSV</button>
<p>预填的是我的初标；直接改下拉框即可，自动保存。导出后把 CSV 给我。</p>
"""

JS = """
<script>
const KEY = "meme-annot-v1";
let data = JSON.parse(localStorage.getItem(KEY) || "{}");
document.querySelectorAll("select,textarea").forEach(el => {
  const id = el.dataset.id, field = el.dataset.field;
  if (data[id] && data[id][field] !== undefined) el.value = data[id][field];
  const save = () => {
    data[id] = data[id] || {};
    data[id][field] = el.value;
    localStorage.setItem(KEY, JSON.stringify(data));
  };
  el.addEventListener("change", save);
  el.addEventListener("input", save);
});
function exportCsv() {
  const ids = [...new Set([...document.querySelectorAll("select")].map(e => e.dataset.id))];
  const rows = [["sample_id","has_text","text_kind","visual","ocr","note"]];
  ids.forEach(id => {
    const get = f => {
      const el = document.querySelector(`[data-id="${id}"][data-field="${f}"]`);
      return el ? el.value : "";
    };
    rows.push([id, get("has_text"), get("text_kind"), get("visual"), get("ocr"), get("note")]);
  });
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(",")).join("\\n");
  const blob = new Blob(["\\ufeff" + csv], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "meme-labels.csv";
  a.click();
}
</script>
"""


def _thumb(path: Path) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((300, 300))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def _card(row: dict) -> str:
    selects = []
    for field, options in FIELDS.items():
        choices = "".join(
            f'<option value="{html.escape(o)}"{" selected" if o == row[field] else ""}>'
            f"{html.escape(o)}</option>"
            for o in options
        )
        selects.append(
            f'<label>{field}<select data-id="{row["sample_id"]}" '
            f'data-field="{field}">{choices}</select></label>'
        )
    return (
        f'<div class="card"><img src="data:image/jpeg;base64,{_thumb(Path(row["file"]))}">'
        f'<div style="flex:1"><div class="meta">{row["sample_id"]} · OCR 字数 {row["ocr_chars"]}</div>'
        f'<div class="sel">{" ".join(selects)}</div>'
        f'<textarea data-id="{row["sample_id"]}" data-field="note" '
        f'placeholder="备注">{html.escape(row.get("note", ""))}</textarea>'
        "</div></div>"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = list(csv.DictReader(open(args.labels, encoding="utf-8")))
    Path(args.out).write_text(
        HEAD + "\n".join(_card(r) for r in rows) + JS, encoding="utf-8"
    )
    print(f"{len(rows)} 条 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

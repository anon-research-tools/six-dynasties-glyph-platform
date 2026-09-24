"""AI Pick 審核頁 — 獨立 blueprint，掛在主 Flask app 上。"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template_string, request, abort

ROOT = Path(__file__).resolve().parent
AI_PICKS_DB = ROOT / "ai_picks.db"
CHAR_DB = ROOT / "網頁部署" / "characters.db"

ai_review_bp = Blueprint("ai_review", __name__, url_prefix="/ai_review")


def _conn():
    conn = sqlite3.connect(AI_PICKS_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _char_img_url(char_id: str) -> str:
    """Look up the served URL for a char_id (uses /img/ protected route)."""
    conn = sqlite3.connect(CHAR_DB)
    cur = conn.cursor()
    cur.execute("SELECT img_url FROM characters WHERE char_id=?", (char_id,))
    row = cur.fetchone()
    conn.close()
    if not row or not row[0]:
        return ""
    url = row[0]
    if url.startswith("/static/"):
        url = "/img/" + url[len("/static/"):]
    return url


LIST_TPL = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>AI 代表字審核</title>
<style>
body{font-family:-apple-system,'PingFang TC',sans-serif;margin:24px;background:#f7f7f5}
h1{font-weight:500}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.06)}
th,td{padding:10px 14px;border-bottom:1px solid #eee;text-align:left;font-size:14px}
th{background:#fafafa;font-weight:500;color:#666}
.pending{color:#d97706}.approved{color:#059669}.overridden{color:#7c3aed}
a.row-link{color:#2563eb;text-decoration:none}a.row-link:hover{text-decoration:underline}
.muted{color:#999;font-size:12px}
</style></head><body>
<h1>AI 代表字 — 審核佇列</h1>
<p class="muted">共 {{rows|length}} 條，按創建時間降序</p>
<table><thead><tr>
<th>寫卷</th><th>字頭</th><th>候選數</th><th>整體品質</th>
<th>狀態</th><th>成本</th><th>建立時間</th><th></th>
</tr></thead><tbody>
{% for r in rows %}
<tr>
<td>{{r.manuscript}}</td><td style="font-size:18px">{{r.keyword}}</td>
<td>{{r.candidate_total}}</td><td>{{r.overall_quality or '—'}}</td>
<td class="{{r.review_status}}">{{r.review_status}}</td>
<td class="muted">${{'%.4f'|format(r.cost_usd or 0)}}</td>
<td class="muted">{{(r.created_at or '')[:19]}}</td>
<td><a class="row-link" href="/ai_review/{{r.manuscript}}/{{r.keyword}}">審核 →</a></td>
</tr>
{% endfor %}
</tbody></table>
</body></html>"""


REVIEW_TPL = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>{{rec.manuscript}} · {{rec.keyword}} — AI 代表字審核</title>
<style>
body{font-family:-apple-system,'PingFang TC',sans-serif;margin:0;padding:0;background:#f7f7f5;color:#222}
.wrap{max-width:1400px;margin:0 auto;padding:20px}
header{display:flex;align-items:baseline;gap:18px;margin-bottom:8px;flex-wrap:wrap}
h1 a{color:#666;text-decoration:none;font-weight:500;font-size:18px}
.kw{font-size:32px;font-weight:600}
.muted{color:#888;font-size:13px}
.note-bar{background:#fffbeb;border:1px solid #fde68a;padding:8px 14px;border-radius:6px;margin:8px 0 16px;font-size:14px;color:#78350f}
.legend{display:flex;gap:16px;font-size:13px;color:#666;margin-bottom:12px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:14px;height:14px;border:2px solid;border-radius:3px;display:inline-block}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.cell{position:relative;background:#fff;border:3px solid #e5e7eb;border-radius:8px;padding:6px;cursor:pointer;transition:all .12s}
.cell:hover{border-color:#3b82f6;transform:translateY(-1px);box-shadow:0 4px 10px rgba(59,130,246,.15)}
.cell.ai{border-color:#10b981;background:#ecfdf5}
.cell.gold{border-color:#fbbf24;background:#fffbeb}
.cell.final{border-color:#8b5cf6;background:#f5f3ff;box-shadow:0 0 0 3px #c4b5fd}
.cell.low{border-style:dashed;border-color:#f59e0b}
.cell.excluded{opacity:.55;cursor:not-allowed;background:#fafafa;border-color:#e5e7eb;border-style:dotted}
.cell.excluded:hover{transform:none;box-shadow:none;border-color:#e5e7eb}
.cell.excluded img{filter:grayscale(.6)}
.section-h{margin:18px 0 8px;font-weight:500;font-size:14px;color:#555;display:flex;align-items:baseline;gap:10px}
.section-h .count{color:#999;font-size:12px}
.cell .img-wrap{width:100%;height:140px;display:flex;align-items:center;justify-content:center;background:#fafafa;border-radius:4px;overflow:hidden}
.cell img{max-width:100%;max-height:100%;object-fit:contain}
.cell .tag-row{display:flex;justify-content:space-between;margin-top:5px;font-size:11px;color:#666}
.tag{background:#f3f4f6;padding:1px 6px;border-radius:3px}
.tag.ai{background:#d1fae5;color:#065f46}
.tag.gold{background:#fef3c7;color:#92400e}
.tag.final{background:#ede9fe;color:#5b21b6}
.actions{position:sticky;bottom:0;background:rgba(247,247,245,.95);backdrop-filter:blur(8px);padding:12px 0;margin-top:16px;border-top:1px solid #e5e7eb;display:flex;gap:10px;align-items:center}
.btn{padding:6px 14px;border:1px solid #d1d5db;background:#fff;border-radius:6px;cursor:pointer;font-size:13px}
.btn:hover{background:#f3f4f6}
.btn.danger{color:#dc2626}
.status{margin-left:auto;font-size:13px;color:#666}
.status b{color:#222}
</style></head><body>
<div class="wrap">

<header>
  <h1><a href="/ai_review">← 列表</a></h1>
  <div class="kw">{{rec.keyword}}</div>
  <div class="muted">{{rec.manuscript}} · 原始 {{raw_total}} → 入選 {{rec.candidate_total}} · 剔除 {{excluded|length}} · ${{'%.4f'|format(rec.cost_usd or 0)}}</div>
</header>

{% if rec.notes %}<div class="note-bar">💬 {{rec.notes}}</div>{% endif %}

<div class="legend">
  <span><span class="swatch" style="border-color:#10b981;background:#ecfdf5"></span>AI 推薦</span>
  <span><span class="swatch" style="border-color:#fbbf24;background:#fffbeb"></span>原人工首選</span>
  <span><span class="swatch" style="border-color:#8b5cf6;background:#f5f3ff"></span>當前最終</span>
  <span><span class="swatch" style="border-color:#f59e0b;border-style:dashed;background:#fff"></span>未校（CBETA 未識）</span>
  <span><span class="swatch" style="border-color:#999;border-style:dotted;background:#fafafa"></span>已剔除</span>
  <span class="muted">— 點任一張入選即採納</span>
</div>

<div class="section-h">入選候選 <span class="count">{{cells|length}} 張，進入 VLM 評選</span></div>
<div class="grid">
{% for c in cells %}
<div class="cell {% if c.is_ai %}ai{% endif %} {% if c.is_gold %}gold{% endif %} {% if c.is_final %}final{% endif %} {% if c.trust=='LOW' %}low{% endif %}"
     data-char-id="{{c.char_id}}" title="{{c.char_id}}{% if c.trust=='LOW' %} · CBETA 未識，VLM 已驗證{% endif %}">
  <div class="img-wrap">{% if c.img_url %}<img src="{{c.img_url}}">{% else %}<span class="muted">缺圖</span>{% endif %}</div>
  <div class="tag-row">
    <span class="tag">#{{c.no}}{% if c.trust=='LOW' %} ⚠{% endif %}</span>
    <span>
      {% if c.is_ai %}<span class="tag ai">AI</span>{% endif %}
      {% if c.is_gold %}<span class="tag gold">原</span>{% endif %}
      {% if c.is_final %}<span class="tag final">✓</span>{% endif %}
    </span>
  </div>
</div>
{% endfor %}
</div>

{% if excluded %}
<div class="section-h">已剔除 <span class="count">{{excluded|length}} 張，未進入 VLM</span></div>
<div class="grid">
{% for e in excluded %}
<div class="cell excluded {% if e.is_gold %}gold{% endif %}" title="{{e.char_id}} · {{e.reason}}">
  <div class="img-wrap">{% if e.img_url %}<img src="{{e.img_url}}">{% else %}<span class="muted">缺圖</span>{% endif %}</div>
  <div class="tag-row">
    <span class="tag">{{e.reason}}</span>
    {% if e.is_gold %}<span class="tag gold">原</span>{% endif %}
  </div>
</div>
{% endfor %}
</div>
{% endif %}

<div class="actions">
  <button class="btn danger" onclick="reject()">都不滿意</button>
  <button class="btn" onclick="acceptAI()">一鍵採納 AI 推薦</button>
  <span class="status">狀態 <b>{{rec.review_status}}</b>{% if rec.final_char_id %} · 最終 {{rec.final_char_id[:30]}}…{% endif %}</span>
</div>
</div>

<script>
const M='{{rec.manuscript}}', K='{{rec.keyword}}';
const aiPicks={{ai_pick_ids|tojson}};
function post(body){
  return fetch(`/ai_review/${M}/${encodeURIComponent(K)}/decide`,{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
  }).then(r=>r.json());
}
function pick(charId, status){
  post({final_char_id:charId, status:status||'approved'}).then(d=>{
    if(d.ok)location.reload(); else alert('失敗 '+(d.error||''));
  });
}
function reject(){post({final_char_id:null,status:'rejected'}).then(d=>{if(d.ok)location.reload();});}
function acceptAI(){if(aiPicks.length){pick(aiPicks[0]);}}
document.querySelectorAll('.cell:not(.excluded)').forEach(c=>{
  c.addEventListener('click',()=>{
    const cid=c.dataset.charId;
    const status = aiPicks.includes(cid)?'approved':'overridden';
    pick(cid,status);
  });
});
document.addEventListener('keydown',e=>{
  if(e.key==='Enter')acceptAI();
  if(e.key==='Escape')reject();
});
</script>
</body></html>"""


@ai_review_bp.route("/")
def list_pending():
    if not AI_PICKS_DB.exists():
        return "ai_picks.db 不存在；先跑 ai_pick.py 或 seed_simulated_pick.py", 200
    conn = _conn()
    rows = conn.execute("""
        SELECT * FROM ai_selections ORDER BY datetime(created_at) DESC
    """).fetchall()
    conn.close()
    return render_template_string(LIST_TPL, rows=rows)


@ai_review_bp.route("/<manuscript>/<keyword>")
def review_one(manuscript, keyword):
    conn = _conn()
    rec = conn.execute(
        "SELECT * FROM ai_selections WHERE manuscript=? AND keyword=?",
        (manuscript, keyword),
    ).fetchone()
    conn.close()
    if not rec:
        abort(404)

    ai_pick_ids = json.loads(rec["top5_char_ids"] or "[]")
    final_id = rec["final_char_id"]

    # Find ALL user's prior gold picks for this (manuscript, keyword)
    char_conn = sqlite3.connect(CHAR_DB)
    cur = char_conn.cursor()
    cur.execute(
        "SELECT char_id FROM characters WHERE other_info_code=? AND default_keyword=?",
        (manuscript, keyword),
    )
    gold_ids = {r[0] for r in cur.fetchall()}
    char_conn.close()
    gold_id = next(iter(gold_ids), None)   # legacy single use

    # Load full candidate list (included + excluded) for transparent review
    cand_path = ROOT / "ai_pick_runs" / f"{manuscript}_{keyword}" / "candidates.json"
    included_cells, excluded_cells = [], []
    raw_total = 0
    if cand_path.exists():
        meta = json.loads(cand_path.read_text())
        raw_total = meta.get("total_raw", meta.get("total", 0))
        trust_levels = meta.get("trust_levels", {})
        for no_str, cid in meta["no_to_char_id"].items():
            included_cells.append({
                "no": int(no_str),
                "char_id": cid,
                "img_url": _char_img_url(cid),
                "trust": trust_levels.get(no_str, ""),
                "is_ai": cid in ai_pick_ids,
                "is_gold": cid in gold_ids,
                "is_final": cid == final_id,
            })
        included_cells.sort(key=lambda c: c["no"])

        for ex in meta.get("excluded", []):
            excluded_cells.append({
                "char_id": ex["char_id"],
                "img_url": _char_img_url(ex["char_id"]),
                "decision": ex["decision"],
                "reason": ex["reason"],
                "is_gold": ex["char_id"] in gold_ids,
            })

    return render_template_string(
        REVIEW_TPL,
        rec=rec,
        cells=included_cells,
        excluded=excluded_cells,
        raw_total=raw_total,
        ai_pick_ids=ai_pick_ids,
    )


@ai_review_bp.route("/<manuscript>/<keyword>/decide", methods=["POST"])
def decide(manuscript, keyword):
    body = request.get_json() or {}
    final_char_id = body.get("final_char_id")
    status = body.get("status", "approved")
    if status not in ("approved", "overridden", "rejected"):
        return jsonify({"ok": False, "error": "bad status"}), 400

    conn = _conn()
    cur = conn.execute(
        "UPDATE ai_selections SET final_char_id=?, review_status=?, reviewed_at=? "
        "WHERE manuscript=? AND keyword=?",
        (final_char_id, status, datetime.now(timezone.utc).isoformat(),
         manuscript, keyword),
    )
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return jsonify({"ok": True, "affected": affected})


@ai_review_bp.route("/<manuscript>/<keyword>/montage.png")
def montage(manuscript, keyword):
    from flask import send_file
    p = ROOT / "ai_pick_runs" / f"{manuscript}_{keyword}" / "montage.png"
    if not p.exists():
        abort(404)
    return send_file(str(p))

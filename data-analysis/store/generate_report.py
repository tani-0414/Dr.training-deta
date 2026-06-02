#!/usr/bin/env python3
"""
store/generate_report.py — 月次実績レポート（HTML出力）

前提:
    - store/analyze_main.py 実行済み（「数字整理（統合版）」シート書き込み済み）
    - individual/analyze_main.py 実行済み（「数字整理（店舗別個人）」シート書き込み済み）

実行方法:
    python3.12 store/generate_report.py
"""

import sys
import os
import re
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from config import SHINKI_SS_ID

TARGET_STORE_LABEL = "学芸大学店（7）"
REPORTS_DIR = Path(__file__).parent.parent / "reports"

_YM = re.compile(r"^\d{6}$")


# ── ユーティリティ ──────────────────────────────────────────────

def _int(v) -> int | None:
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _float(v) -> float | None:
    s = str(v).strip()
    if not s or s in ("-", "CVR(%)", "離脱率", "来店頻度", "指名率"):
        return None
    try:
        return float(s.rstrip("%"))
    except ValueError:
        return None


def fmt_month(ym: str) -> str:
    return f"{ym[:4]}年{int(ym[4:])}月"


def fmt_diff(
    val: float | None,
    avg: float | None,
    higher_better: bool = True,
    is_rate: bool = False,
) -> tuple[str, str]:
    """(差分テキスト, CSSクラス名) を返す"""
    if val is None or avg is None:
        return "—", "neutral"
    diff = val - avg
    suffix = "pt" if is_rate else ""
    text = f"{diff:+.1f}{suffix}"
    if diff == 0:
        return text, "neutral"
    return text, "good" if (diff > 0) == higher_better else "bad"


# ── シートのパース ──────────────────────────────────────────────

def parse_store_sheet(rows: list[list]) -> dict[str, dict[str, dict]]:
    """
    「数字整理（統合版）」を解析する。
    Returns: {store_label: {ym: row_dict}}
    """
    result: dict[str, dict[str, dict]] = {}
    cur: str | None = None
    for row in rows:
        if not row:
            continue
        c0 = str(row[0]).strip()
        if c0.startswith("【") and c0.endswith("】"):
            if "月別" not in c0:
                cur = c0[1:-1]
                result[cur] = {}
            else:
                cur = None  # 月別合計セクション以降はどの店舗にも属さない
        elif cur and _YM.match(c0):
            result[cur][c0] = {
                "active":       _int(row[2])        if len(row) > 2 else None,
                "shinki":       _int(row[3])        if len(row) > 3 else None,
                "cvr":          str(row[4]).strip() if len(row) > 4 else "-",
                "shinki_cv":    _int(row[5])        if len(row) > 5 else None,
                "fukki":        _int(row[6])        if len(row) > 6 else None,
                "risseki":      _int(row[7])        if len(row) > 7 else None,
                "risseki_rate": str(row[8]).strip() if len(row) > 8 else "-",
                "zougen":       _int(row[9])        if len(row) > 9 else None,
            }
    return result


def parse_staff_sheet_all(rows: list[list]) -> dict[str, dict[str, dict[str, dict]]]:
    """
    「店舗別個人」シートを全店舗分解析する。
    Returns: {store_label: {staff_label: {ym: row_dict}}}
    """
    result: dict[str, dict] = {}
    cur_store: str | None = None
    cur_staff: str | None = None
    for row in rows:
        if not row:
            continue
        c0 = str(row[0]).strip()
        if c0.startswith("【") and c0.endswith("】"):
            cur_store = c0[1:-1]
            result[cur_store] = {}
            cur_staff = None
        elif cur_store and c0.startswith("▶ "):
            cur_staff = c0[2:].strip()
            result[cur_store][cur_staff] = {}
        elif cur_store and cur_staff and _YM.match(c0):
            result[cur_store][cur_staff][c0] = {
                "active":       _int(row[2])         if len(row) > 2  else None,
                "shinki":       _int(row[3])         if len(row) > 3  else None,
                "cvr":          str(row[4]).strip()  if len(row) > 4  else "-",
                "shinki_cv":    _int(row[5])         if len(row) > 5  else None,
                "shinki_tanto": _int(row[6])         if len(row) > 6  else None,
                "fukki_tanto":  _int(row[7])         if len(row) > 7  else None,
                "risseki":      _int(row[8])         if len(row) > 8  else None,
                "risseki_rate": str(row[9]).strip()  if len(row) > 9  else "-",
                "hikitsugi":    _int(row[10])        if len(row) > 10 else None,
                "joto":         _int(row[11])        if len(row) > 11 else None,
                "zougen":       _int(row[12])        if len(row) > 12 else None,
                "freq":         str(row[13]).strip() if len(row) > 13 else "-",
            }
    return result


def parse_staff_sheet(
    rows: list[list],
    target_store: str,
) -> dict[str, dict[str, dict]]:
    """
    「数字整理（店舗別個人）」から対象店舗のスタッフ別データを抽出する。
    Returns: {staff_label: {ym: row_dict}}
    """
    result: dict[str, dict[str, dict]] = {}
    in_target = False
    cur_staff: str | None = None
    for row in rows:
        if not row:
            continue
        c0 = str(row[0]).strip()
        if c0.startswith("【") and c0.endswith("】"):
            in_target = (c0[1:-1] == target_store)
            cur_staff = None
        elif in_target and c0.startswith("▶ "):
            cur_staff = c0[2:].strip()
            result[cur_staff] = {}
        elif in_target and cur_staff and _YM.match(c0):
            result[cur_staff][c0] = {
                "active":       _int(row[2])         if len(row) > 2  else None,
                "shinki":       _int(row[3])         if len(row) > 3  else None,
                "cvr":          str(row[4]).strip()  if len(row) > 4  else "-",
                "shinki_cv":    _int(row[5])         if len(row) > 5  else None,
                "shinki_tanto": _int(row[6])         if len(row) > 6  else None,
                "fukki_tanto":  _int(row[7])         if len(row) > 7  else None,
                "risseki":      _int(row[8])         if len(row) > 8  else None,
                "risseki_rate": str(row[9]).strip()  if len(row) > 9  else "-",
                "hikitsugi":    _int(row[10])        if len(row) > 10 else None,
                "joto":         _int(row[11])        if len(row) > 11 else None,
                "zougen":       _int(row[12])        if len(row) > 12 else None,
                "freq":         str(row[13]).strip() if len(row) > 13 else "-",
                "shimei_rate":  str(row[14]).strip() if len(row) > 14 else "-",
            }
    return result


# ── 集計 ─────────────────────────────────────────────────────

def latest_month(store_data: dict) -> str | None:
    months = {ym for d in store_data.values() for ym in d}
    return max(months) if months else None


def _avg_num(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _avg_float(rows: list[dict], key: str) -> float | None:
    vals = [_float(r[key]) for r in rows if _float(r.get(key)) is not None]
    return sum(vals) / len(vals) if vals else None


def store_averages(store_data: dict, ym: str) -> dict:
    rows = [d[ym] for d in store_data.values() if ym in d]
    if not rows:
        return {}
    return {
        **{k: _avg_num(rows, k) for k in ("active", "shinki", "shinki_cv", "fukki", "risseki", "zougen")},
        "cvr":          _avg_float(rows, "cvr"),
        "risseki_rate": _avg_float(rows, "risseki_rate"),
    }


def staff_averages(staff_data: dict, ym: str) -> dict:
    rows = [d[ym] for d in staff_data.values() if ym in d]
    if not rows:
        return {}
    num_keys = ("active", "shinki", "shinki_cv", "shinki_tanto", "fukki_tanto",
                "risseki", "hikitsugi", "joto", "zougen")
    return {
        **{k: _avg_num(rows, k) for k in num_keys},
        "cvr":          _avg_float(rows, "cvr"),
        "risseki_rate": _avg_float(rows, "risseki_rate"),
        "freq":         _avg_float(rows, "freq"),
        "shimei_rate":  _avg_float(rows, "shimei_rate"),
    }


# ── HTML 生成 ────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", sans-serif;
  background: #f0f2f5;
  color: #222;
  line-height: 1.6;
}
header {
  background: #1a1a2e;
  color: #fff;
  padding: 28px 40px;
}
header h1 { font-size: 1.6rem; font-weight: 700; letter-spacing: -0.5px; }
.subtitle { margin-top: 4px; font-size: 0.88rem; opacity: 0.65; }
main { padding: 36px 40px; max-width: 1200px; margin: 0 auto; }
section {
  background: #fff;
  border-radius: 10px;
  padding: 28px 32px;
  margin-bottom: 28px;
  box-shadow: 0 1px 6px rgba(0,0,0,.07);
}
h2 {
  font-size: 1.05rem;
  font-weight: 700;
  color: #1a1a2e;
  border-left: 4px solid #4a6fa5;
  padding-left: 12px;
  margin-bottom: 16px;
}
.note {
  font-size: 0.82rem;
  color: #777;
  margin-bottom: 14px;
}
.legend-good { color: #1a7a3c; font-weight: 600; }
.legend-bad  { color: #a02020; font-weight: 600; }

/* ── テーブル ── */
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
}
.data-table th {
  background: #2c3e50;
  color: #fff;
  padding: 10px 14px;
  text-align: right;
  white-space: nowrap;
  font-weight: 600;
}
.data-table th:first-child { text-align: center; }
.data-table th:nth-child(2) { text-align: left; }
.data-table td {
  padding: 9px 14px;
  text-align: right;
  border-bottom: 1px solid #edf0f3;
}
.data-table td:first-child { text-align: center; color: #999; font-size: 0.8rem; }
.data-table td:nth-child(2) { text-align: left; }
.data-table tbody tr:hover { background: #f7f9fc; }
.data-table tfoot td {
  background: #f0f4f8;
  font-weight: 700;
  color: #444;
  border-top: 2px solid #d0d7e0;
}

/* ── セル色 ── */
.bg-good { background: #dcf5e4; color: #1a7a3c; font-weight: 600; }
.bg-bad  { background: #fde8e8; color: #a02020; font-weight: 600; }

/* ── ターゲット行 ── */
.target-row { background: #fff8e1 !important; }
.target-row td { font-weight: 700; }
.target-label { color: #b34400 !important; }
.avg-row { font-style: italic; color: #666; }
.total-row { font-weight: 800; color: #1a1a2e; }

/* ── KPI グリッド ── */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(155px, 1fr));
  gap: 14px;
}
.kpi-card {
  border-radius: 8px;
  padding: 16px 18px;
  border-left: 4px solid #ccc;
  background: #f9f9f9;
}
.kpi-card.good { border-left-color: #28a745; background: #f0fff5; }
.kpi-card.bad  { border-left-color: #e53935; background: #fff5f5; }
.kpi-card.neutral { border-left-color: #90a4ae; }
.kpi-label { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
.kpi-value { font-size: 2rem; font-weight: 800; color: #1a1a2e; margin: 4px 0 2px; }
.kpi-avg   { font-size: 0.78rem; color: #aaa; }
.kpi-diff  { font-size: 0.92rem; font-weight: 700; margin-top: 6px; }
.kpi-diff.good    { color: #1a7a3c; }
.kpi-diff.bad     { color: #a02020; }
.kpi-diff.neutral { color: #90a4ae; }
"""


def _td_num(val: int | None, avg: float | None, higher_better: bool = True) -> str:
    if val is None:
        return "<td>—</td>"
    if avg is None:
        return f"<td>{val}</td>"
    cls = ""
    if val > avg * 1.02:
        cls = " class=\"bg-good\"" if higher_better else " class=\"bg-bad\""
    elif val < avg * 0.98:
        cls = " class=\"bg-bad\"" if higher_better else " class=\"bg-good\""
    return f"<td{cls}>{val}</td>"


def _td_rate(val_str: str, avg: float | None, higher_better: bool = True) -> str:
    val = _float(val_str)
    if val is None:
        return f"<td>{val_str}</td>"
    if avg is None:
        return f"<td>{val_str}</td>"
    cls = ""
    if avg > 0:
        if val > avg * 1.02:
            cls = " class=\"bg-good\"" if higher_better else " class=\"bg-bad\""
        elif val < avg * 0.98:
            cls = " class=\"bg-bad\"" if higher_better else " class=\"bg-good\""
    return f"<td{cls}>{val_str}</td>"


def _avg_td(val: float | None, is_rate: bool = False) -> str:
    if val is None:
        return "<td>—</td>"
    suffix = "%" if is_rate else ""
    return f"<td>{val:.1f}{suffix}</td>"


def _kpi_card(
    label: str,
    store_val_str: str,
    store_val_float: float | None,
    avg_float: float | None,
    higher_better: bool,
    is_rate: bool,
) -> str:
    diff_text, diff_cls = fmt_diff(store_val_float, avg_float, higher_better, is_rate)
    avg_disp = (f"{avg_float:.1f}{'%' if is_rate else ''}") if avg_float is not None else "—"
    return f"""
      <div class="kpi-card {diff_cls}">
        <div class="kpi-label">{label}</div>
        <div class="kpi-value">{store_val_str}</div>
        <div class="kpi-avg">全店舗平均: {avg_disp}</div>
        <div class="kpi-diff {diff_cls}">{diff_text}</div>
      </div>"""


def generate_html(
    store_data: dict,
    staff_data: dict,
    target_store: str,
    ym: str,
) -> str:
    month_label = fmt_month(ym)
    avg_s = store_averages(store_data, ym)
    avg_st = staff_averages(staff_data, ym)

    # ── セクション1: 全店舗サマリーテーブル ──────────────────────
    rows_for_ym = [(lbl, d[ym]) for lbl, d in store_data.items() if ym in d]
    rows_for_ym.sort(key=lambda x: (x[1]["zougen"] or -9999), reverse=True)

    store_tbody = []
    for rank, (lbl, row) in enumerate(rows_for_ym, 1):
        is_target = lbl == target_store
        tr_cls = ' class="target-row"' if is_target else ""
        name_cls = ' class="target-label"' if is_target else ""
        cells = (
            f"<td>{rank}</td>"
            f"<td{name_cls}>{lbl}</td>"
            + _td_num(row["active"],    avg_s.get("active"))
            + _td_num(row["shinki"],    avg_s.get("shinki"))
            + _td_num(row["shinki_cv"], avg_s.get("shinki_cv"))
            + _td_rate(row["cvr"],          avg_s.get("cvr"))
            + _td_num(row["risseki"],   avg_s.get("risseki"),   higher_better=False)
            + _td_rate(row["risseki_rate"], avg_s.get("risseki_rate"), higher_better=False)
            + _td_num(row["zougen"],    avg_s.get("zougen"))
        )
        store_tbody.append(f"    <tr{tr_cls}>{cells}</tr>")

    # 全店舗合計行
    sum_active   = sum(r["active"]    or 0 for _, r in rows_for_ym)
    sum_shinki   = sum(r["shinki"]    or 0 for _, r in rows_for_ym)
    sum_shinki_cv = sum(r["shinki_cv"] or 0 for _, r in rows_for_ym)
    sum_risseki  = sum(r["risseki"]   or 0 for _, r in rows_for_ym)
    sum_zougen   = sum(r["zougen"]    or 0 for _, r in rows_for_ym)
    total_cvr    = f"{sum_shinki_cv / sum_shinki * 100:.1f}%" if sum_shinki > 0 else "—"

    store_tfoot_total = (
        "<td>—</td>"
        '<td class="total-row">全店舗合計</td>'
        f"<td>{sum_active}</td>"
        f"<td>{sum_shinki}</td>"
        f"<td>{sum_shinki_cv}</td>"
        f"<td>{total_cvr}</td>"
        f"<td>{sum_risseki}</td>"
        "<td>—</td>"
        f"<td>{sum_zougen}</td>"
    )
    store_tfoot = (
        "<td>—</td>"
        '<td class="avg-row">全店舗平均</td>'
        + _avg_td(avg_s.get("active"))
        + _avg_td(avg_s.get("shinki"))
        + _avg_td(avg_s.get("shinki_cv"))
        + _avg_td(avg_s.get("cvr"), is_rate=True)
        + _avg_td(avg_s.get("risseki"))
        + _avg_td(avg_s.get("risseki_rate"), is_rate=True)
        + _avg_td(avg_s.get("zougen"))
    )

    section1 = f"""
  <section>
    <h2>全店舗サマリー（{month_label}）</h2>
    <p class="note">
      会員増減数の降順で表示 ／
      <span class="legend-good">■ 全店舗平均以上</span>
      <span class="legend-bad">■ 全店舗平均以下</span>　（±2%以内は無色）
    </p>
    <table class="data-table">
      <thead>
        <tr>
          <th>順位</th><th>店舗</th>
          <th>アクティブ会員数</th><th>新規数</th><th>新規獲得数</th>
          <th>CVR(%)</th><th>離脱数</th><th>離脱率</th><th>会員増減数</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(store_tbody)}
      </tbody>
      <tfoot>
        <tr>{store_tfoot_total}</tr>
        <tr>{store_tfoot}</tr>
      </tfoot>
    </table>
  </section>"""

    # ── セクション2: KPI 比較カード ───────────────────────────
    tr = store_data.get(target_store, {}).get(ym, {})

    kpi_defs = [
        ("アクティブ会員数", "active",       True,  False),
        ("新規数",           "shinki",       True,  False),
        ("新規獲得数",       "shinki_cv",    True,  False),
        ("CVR",              "cvr",          True,  True),
        ("復帰数",           "fukki",        True,  False),
        ("離脱数",           "risseki",      False, False),
        ("離脱率",           "risseki_rate", False, True),
        ("会員増減数",       "zougen",       True,  False),
    ]

    cards = []
    for label, key, higher_better, is_rate in kpi_defs:
        raw = tr.get(key)
        fval = _float(raw) if is_rate else (raw if isinstance(raw, (int, float)) else None)
        disp = str(raw) if raw is not None else "—"
        cards.append(_kpi_card(label, disp, fval, avg_s.get(key), higher_better, is_rate))

    section2 = f"""
  <section>
    <h2>{target_store} — 全店舗平均との比較</h2>
    <div class="kpi-grid">{"".join(cards)}
    </div>
  </section>"""

    # ── セクション3: スタッフ別テーブル ───────────────────────
    staff_rows = [(lbl, d[ym]) for lbl, d in staff_data.items() if ym in d]
    staff_rows.sort(key=lambda x: (x[1].get("active") or -1), reverse=True)

    staff_tbody = []
    for i, (lbl, row) in enumerate(staff_rows, 1):
        cells = (
            f"<td>{i}</td>"
            f"<td>{lbl}</td>"
            + _td_num(row.get("active"),       avg_st.get("active"))
            + _td_rate(row.get("cvr", "-"),    avg_st.get("cvr"))
            + _td_num(row.get("shinki_tanto"), avg_st.get("shinki_tanto"))
            + _td_num(row.get("fukki_tanto"),  avg_st.get("fukki_tanto"))
            + _td_num(row.get("risseki"),      avg_st.get("risseki"),      higher_better=False)
            + _td_rate(row.get("risseki_rate", "-"), avg_st.get("risseki_rate"), higher_better=False)
            + _td_rate(row.get("freq", "-"),   avg_st.get("freq"))
            + _td_num(row.get("zougen"),       avg_st.get("zougen"))
        )
        staff_tbody.append(f"    <tr>{cells}</tr>")

    staff_tfoot = (
        "<td>—</td>"
        '<td class="avg-row">店舗平均</td>'
        + _avg_td(avg_st.get("active"))
        + _avg_td(avg_st.get("cvr"), is_rate=True)
        + _avg_td(avg_st.get("shinki_tanto"))
        + _avg_td(avg_st.get("fukki_tanto"))
        + _avg_td(avg_st.get("risseki"))
        + _avg_td(avg_st.get("risseki_rate"), is_rate=True)
        + _avg_td(avg_st.get("freq"))
        + _avg_td(avg_st.get("zougen"))
    )

    staff_note = (
        f"{len(staff_rows)}名のデータあり" if staff_rows
        else "対象月のスタッフデータがありません"
    )

    section3 = f"""
  <section>
    <h2>スタッフ別実績（{target_store} / {month_label}）</h2>
    <p class="note">
      {staff_note} ／ 店舗内平均との比較 :
      <span class="legend-good">■ 店舗平均以上</span>
      <span class="legend-bad">■ 店舗平均以下</span>　（±2%以内は無色）
    </p>
    <table class="data-table">
      <thead>
        <tr>
          <th>#</th><th>スタッフ</th>
          <th>担当会員数</th><th>CVR(%)</th>
          <th>新規担当数</th><th>復帰担当数</th>
          <th>離脱数</th><th>離脱率</th>
          <th>来店頻度</th><th>会員増減数</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(staff_tbody) if staff_tbody else "        <tr><td colspan='10' style='text-align:center;color:#aaa;padding:24px'>データなし</td></tr>"}
      </tbody>
      <tfoot>
        <tr>{staff_tfoot}</tr>
      </tfoot>
    </table>
  </section>"""

    # ── 組み立て ──────────────────────────────────────────────
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{target_store} 月次実績レポート {month_label}</title>
  <style>{_CSS}</style>
</head>
<body>
  <header>
    <h1>月次実績レポート</h1>
    <p class="subtitle">{target_store}　|　{month_label}　|　生成: {now_str}</p>
  </header>
  <main>
{section1}
{section2}
{section3}
  </main>
</body>
</html>"""


# ── メイン ────────────────────────────────────────────────────

def main() -> None:
    target_ym = sys.argv[1] if len(sys.argv) > 1 else None
    if target_ym and not _YM.match(target_ym):
        print(f"エラー: 対象月は YYYYMM 形式で指定してください（例: 202603）")
        return

    print("Google Sheetsに接続中...")
    gc = gspread.oauth()
    ss = gc.open_by_key(SHINKI_SS_ID)

    print("「統合版」読み込み中...")
    ws_store = ss.worksheet("統合版")
    store_rows = ws_store.get_all_values()

    print("「店舗別個人・統合版」読み込み中...")
    ws_staff = ss.worksheet("店舗別個人・統合版")
    staff_rows = ws_staff.get_all_values()

    print("データ解析中...")
    store_data = parse_store_sheet(store_rows)
    staff_data = parse_staff_sheet(staff_rows, TARGET_STORE_LABEL)

    ym = target_ym or latest_month(store_data)
    if not ym:
        print("エラー: 店舗データが見つかりません")
        return

    if TARGET_STORE_LABEL not in store_data:
        print(f"エラー: '{TARGET_STORE_LABEL}' が「数字整理（統合版）」に見つかりません")
        return

    print(f"対象月: {fmt_month(ym)}")
    print(f"店舗数: {sum(1 for d in store_data.values() if ym in d)}")
    print(f"スタッフ数（{TARGET_STORE_LABEL}）: {sum(1 for d in staff_data.values() if ym in d)}")

    print("HTMLレポート生成中...")
    html_content = generate_html(store_data, staff_data, TARGET_STORE_LABEL, ym)

    REPORTS_DIR.mkdir(exist_ok=True)
    out_path = REPORTS_DIR / f"report_{ym}.html"
    out_path.write_text(html_content, encoding="utf-8")
    print(f"\n✅ レポート出力完了: {out_path}")


if __name__ == "__main__":
    main()

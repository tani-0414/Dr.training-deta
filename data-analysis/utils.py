"""
utils.py — 共通ヘルパー関数

すべてのスクリプトが使う汎用関数をまとめる。
分析ロジックは含めない（計算・書式・ラベル生成・行生成のみ）。
"""

from datetime import datetime
from collections import defaultdict

from config import MERGED_KEY, MERGED_NAMES, STORE_NAMES, NO_CVR


# ---- 月操作 ----

def prev_month(ym: str) -> str:
    """前月を返す（例: "202501" → "202412"）"""
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y-1:04d}12" if m == 1 else f"{y:04d}{m-1:02d}"

def next_month(ym: str) -> str:
    """翌月を返す（例: "202512" → "202601"）"""
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y+1:04d}01" if m == 12 else f"{y:04d}{m+1:02d}"


# ---- 日時パース ----

def parse_date(s: str) -> datetime | None:
    """複数フォーマットに対応した日時パース"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


# ---- 店舗・スタッフ ラベル ----

def mkey(store: str) -> str:
    """個別店舗コード → 統合キー（グループ外はそのまま返す）"""
    return MERGED_KEY.get(store, store)

def get_store_label(code: str) -> str:
    """店舗コード → 表示名（例: "中目黒店（1）"）"""
    name = STORE_NAMES.get(code, "")
    return f"{name}（{code}）" if name else f"店舗{code}"

def get_merged_label(ms: str) -> str:
    """統合キー → 表示名（例: "中目黒（統合）"）"""
    return MERGED_NAMES.get(ms, get_store_label(ms))


# ---- 書式・計算 ----

def cvr(s: int, sc: int, ym: str = "") -> str:
    """CVR文字列を返す（除外月または新規数0は "-"）"""
    if ym in NO_CVR or sc == 0:
        return "-"
    return f"{s / sc * 100:.1f}%"

def churn_rate(ri: int, prev_active: int) -> str:
    """離脱率文字列を返す（前月アクティブ0は "-"）"""
    if prev_active == 0:
        return "-"
    return f"{ri / prev_active * 100:.1f}%"

def visit_freq(total_sess: int, active_count: int) -> str:
    """来店頻度（平均セッション数）文字列を返す"""
    if active_count == 0:
        return "-"
    return f"{total_sess / active_count:.1f}"

def shimei_rate_str(shimei: int, total: int) -> str:
    """指名率文字列を返す"""
    if total == 0:
        return "-"
    return f"{shimei / total * 100:.1f}%"


# ---- グループ内移動判定 ----

def within_next_month(last_visit: datetime, visit: datetime) -> bool:
    """last_visit の翌月以内に visit があるか判定"""
    y, m = last_visit.year, last_visit.month
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return (visit.year, visit.month) <= (ny, nm)


# ---- シート行生成（店舗別・個人別共通）----

def make_sheet_rows(
    stores:        list[str],
    shinki:        dict,
    shockai:       dict,
    fukki_map:     dict,
    risseki_count: dict,
    p1_months:     list[str],
    p2_months:     list[str],
    get_label,
    active_users:    dict | None = None,
    col2_header:     str = "店舗コード",
    all_label:       str = "（全店舗合計）",
    definition_rows: list | None = None,
) -> list:
    """
    店舗別または個人別の月次集計行リストを生成する。
    store/analyze_main.py と individual/analyze_main.py の両方から呼ばれる。
    """
    rows = list(definition_rows) if definition_rows is not None else []
    header_row = [
        "年月", col2_header, "アクティブ会員数", "新規数", "CVR(%)",
        "新規獲得数", "復帰数", "離脱数", "離脱率", "会員増減数",
    ]

    ym_s:   dict[str, int] = defaultdict(int)
    ym_sc:  dict[str, int] = defaultdict(int)
    ym_s_:  dict[str, int] = defaultdict(int)
    ym_sc_: dict[str, int] = defaultdict(int)
    ym_fk:  dict[str, int] = defaultdict(int)
    ym_ri:  dict[str, int] = defaultdict(int)
    ym_act: dict[str, int] = defaultdict(int)

    def add_rows(store: str, period_months: list[str]) -> tuple:
        s_all = sc_all = s_cvr = sc_cvr = fk_all = ri_all = 0
        for ym in period_months:
            s  = len(shinki.get((store, ym), set()))
            sc = len(shockai.get((store, ym), set()))
            fk = fukki_map.get((store, ym), 0)
            ri = risseki_count.get((store, ym), 0)
            if s == 0 and sc == 0 and fk == 0 and ri == 0:
                continue
            if active_users is not None:
                act      = active_users.get((store, ym), 0)
                prev_act = active_users.get((store, prev_month(ym)), 0)
                churn_r  = churn_rate(ri, prev_act)
                ym_act[ym] += act
            else:
                act, churn_r = "", ""
            rows.append([ym, store, act, sc, cvr(s, sc, ym), s, fk, ri, churn_r, s + fk - ri])
            s_all += s; sc_all += sc; fk_all += fk; ri_all += ri
            ym_s[ym] += s; ym_sc[ym] += sc
            ym_fk[ym] += fk; ym_ri[ym] += ri
            if ym not in NO_CVR:
                s_cvr += s; sc_cvr += sc
                ym_s_[ym] += s; ym_sc_[ym] += sc
        return s_all, sc_all, s_cvr, sc_cvr, fk_all, ri_all

    for store in stores:
        rows.append([f"【{get_label(store)}】", "", "", "", "", "", "", "", "", ""])
        rows.append(header_row)
        p1_s, p1_sc, p1_s_, p1_sc_, p1_fk, p1_ri = add_rows(store, p1_months)
        rows.append(["〜2025/9 合計", "", "", p1_sc, cvr(p1_s_, p1_sc_),
                     p1_s, p1_fk, p1_ri, "", p1_s + p1_fk - p1_ri])
        rows.append([])
        p2_s, p2_sc, p2_s_, p2_sc_, p2_fk, p2_ri = add_rows(store, p2_months)
        rows.append(["2025/10〜 合計", "", "", p2_sc, cvr(p2_s_, p2_sc_),
                     p2_s, p2_fk, p2_ri, "", p2_s + p2_fk - p2_ri])
        rows.append([])
        ts = p1_s + p2_s; tsc = p1_sc + p2_sc
        tfk = p1_fk + p2_fk; tri = p1_ri + p2_ri
        rows.append(["全体合計", "", "", tsc, cvr(p1_s_ + p2_s_, p1_sc_ + p2_sc_),
                     ts, tfk, tri, "", ts + tfk - tri])
        rows.append([])

    # 月別全体合計
    rows.append(["【月別合計（全体）】", "", "", "", "", "", "", "", "", ""])
    rows.append(["年月", all_label, "アクティブ会員数", "新規数", "CVR(%)",
                 "新規獲得数", "復帰数", "離脱数", "離脱率", "会員増減数"])

    gp1_s = gp1_sc = gp1_s_ = gp1_sc_ = gp1_fk = gp1_ri = 0
    for ym in p1_months:
        act     = ym_act[ym] if active_users is not None else ""
        churn_r = churn_rate(ym_ri[ym], ym_act.get(prev_month(ym), 0)) if active_users is not None else ""
        rows.append([ym, "ALL", act, ym_sc[ym], cvr(ym_s_[ym], ym_sc_[ym], ym),
                     ym_s[ym], ym_fk[ym], ym_ri[ym], churn_r,
                     ym_s[ym] + ym_fk[ym] - ym_ri[ym]])
        gp1_s += ym_s[ym]; gp1_sc += ym_sc[ym]
        gp1_s_ += ym_s_[ym]; gp1_sc_ += ym_sc_[ym]
        gp1_fk += ym_fk[ym]; gp1_ri += ym_ri[ym]
    rows.append(["〜2025/9 合計", "ALL", "", gp1_sc, cvr(gp1_s_, gp1_sc_),
                 gp1_s, gp1_fk, gp1_ri, "", gp1_s + gp1_fk - gp1_ri])
    rows.append([])

    gp2_s = gp2_sc = gp2_s_ = gp2_sc_ = gp2_fk = gp2_ri = 0
    for ym in p2_months:
        act     = ym_act[ym] if active_users is not None else ""
        churn_r = churn_rate(ym_ri[ym], ym_act.get(prev_month(ym), 0)) if active_users is not None else ""
        rows.append([ym, "ALL", act, ym_sc[ym], cvr(ym_s_[ym], ym_sc_[ym], ym),
                     ym_s[ym], ym_fk[ym], ym_ri[ym], churn_r,
                     ym_s[ym] + ym_fk[ym] - ym_ri[ym]])
        gp2_s += ym_s[ym]; gp2_sc += ym_sc[ym]
        gp2_s_ += ym_s_[ym]; gp2_sc_ += ym_sc_[ym]
        gp2_fk += ym_fk[ym]; gp2_ri += ym_ri[ym]
    rows.append(["2025/10〜 合計", "ALL", "", gp2_sc, cvr(gp2_s_, gp2_sc_),
                 gp2_s, gp2_fk, gp2_ri, "", gp2_s + gp2_fk - gp2_ri])
    rows.append([])

    gt_s = gp1_s + gp2_s; gt_sc = gp1_sc + gp2_sc
    gt_fk = gp1_fk + gp2_fk; gt_ri = gp1_ri + gp2_ri
    rows.append(["全体合計", "ALL", "", gt_sc,
                 cvr(gp1_s_ + gp2_s_, gp1_sc_ + gp2_sc_),
                 gt_s, gt_fk, gt_ri, "", gt_s + gt_fk - gt_ri])
    return rows

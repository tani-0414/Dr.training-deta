#!/usr/bin/env python3
"""ピックアップ分析（修正前・修正後・会員増加数ベースの3バージョンを出力）
  条件1: 離脱数≤3 かつ CVR≥80% の (店舗, 月) を抽出
  条件2: 2025/10以降の合計で |新規獲得数 - 離脱数| ≤3 の店舗を抽出
  条件2（会員増加数ベース）: 2025/10以降の合計で |(新規獲得数+復帰数) - 離脱数（修正後）| ≤3
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from collections import defaultdict

from config import SHINKI_SS_ID, RISSEKI_SS_ID, NO_CVR
from utils import get_store_label as store_label, parse_date, within_next_month

PERIOD2_START = "202510"

# pickup専用のペア定義（(2+5) と (6+9+36) は別扱い）
STORE_PAIRS = [
    ("1",  "37"),
    ("8",  "34"),
    ("9",  "36"),
    ("11", "42"),
    ("14", "38"),
    ("16", "40"),
]
PAIRED_STORES = {s for pair in STORE_PAIRS for s in pair}

def fmt_ym(ym: str) -> str:
    return f"{ym[:4]}/{ym[4:]}" if len(ym) == 6 else ym


def load_shinki(gc: gspread.Client) -> tuple[
    dict[tuple[str, str], tuple[int, int]],
    dict[tuple[str, str], list[datetime]],
    dict[tuple[str, str], set[str]],
    set[tuple[str, str]],
]:
    """(store, ym) → (新規獲得数, 初回数) + ペア店舗来店日 + 全店舗来店月 + 初回あり会員セット"""
    ss = gc.open_by_key(SHINKI_SS_ID)
    ws = ss.worksheets()[0]
    print("  新規獲得数データ読み込み中...")
    rows = ws.get_all_values()
    col  = {h: i for i, h in enumerate(rows[0])}
    data = rows[1:]

    shinki:        dict[tuple[str, str], set] = defaultdict(set)
    shockai:       dict[tuple[str, str], set] = defaultdict(set)
    visits:        dict[tuple[str, str], list[datetime]] = defaultdict(list)
    member_visits: dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in data:
        try:
            if row[col["取消区分 (0:通常、1：取消)"]] == "1":
                continue
            store  = row[col["店舗コード"]]
            member = row[col["会員ID"]]
            ym     = row[col["年月"]]
            if not (store and member and ym):
                continue
            member_visits[(store, member)].add(ym)
            if store in PAIRED_STORES:
                vd = parse_date(row[col["取引日時"]])
                if vd:
                    visits[(store, member)].append(vd)
            if row[col["初回"]] == "1":
                shockai[(store, ym)].add(member)
                if row[col["CV有無"]] == "1":
                    shinki[(store, ym)].add(member)
        except IndexError:
            continue

    all_keys = shinki.keys() | shockai.keys()
    shinki_map = {k: (len(shinki.get(k, set())), len(shockai.get(k, set()))) for k in all_keys}
    has_shockai: set[tuple[str, str]] = {
        (store, member)
        for (store, _ym), members in shockai.items()
        for member in members
    }
    return shinki_map, visits, member_visits, has_shockai


def load_risseki(gc: gspread.Client) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[tuple[str, str], datetime],
    dict[tuple[str, str], set[str]],
]:
    """raw離脱者セット + 直近来店日マップ + 離脱履歴（復帰計算用）を返す"""
    ss = gc.open_by_key(RISSEKI_SS_ID)
    ws = ss.worksheets()[0]
    print("  離脱者データ読み込み中...")
    rows = ws.get_all_values()
    col  = {h: i for i, h in enumerate(rows[0])}
    data = rows[1:]

    raw:             dict[tuple[str, str], set[str]] = defaultdict(set)
    last_visit:      dict[tuple[str, str], datetime] = {}
    risseki_history: dict[tuple[str, str], set[str]] = defaultdict(set)  # (store,member)→対象月セット

    for row in data:
        try:
            if row[col["判定"]] != "離客":
                continue
            month  = row[col["対象月"]]
            store  = row[col["店舗コード"]]
            member = row[col["会員ID"]]
            lv     = parse_date(row[col["直近来店日"]])
            if month == "202605":
                continue
            if month and store and member:
                raw[(store, month)].add(member)
                risseki_history[(store, member)].add(month)
                if lv and ((store, member) not in last_visit or lv > last_visit[(store, member)]):
                    last_visit[(store, member)] = lv
        except IndexError:
            continue

    return raw, last_visit, risseki_history


def build_exclusion(last_visit_map: dict[tuple[str, str], datetime],
                    visits: dict[tuple[str, str], list[datetime]]) -> set[tuple[str, str]]:
    """翌月以内に相手店舗へ来店 → (member, store) を除外セットに追加"""
    exclusion: set[tuple[str, str]] = set()
    for store_a, store_b in STORE_PAIRS:
        for from_store, to_store in [(store_a, store_b), (store_b, store_a)]:
            for (store, member), lv in last_visit_map.items():
                if store != from_store:
                    continue
                later = [d for d in visits.get((to_store, member), []) if d > lv]
                if later and within_next_month(lv, min(later)):
                    exclusion.add((member, from_store))
    return exclusion


EXCLUDE_FUKKI = {"202504"}  # 前データなしのため復帰カウント除外

def build_fukki(risseki_history: dict[tuple[str, str], set[str]],
                member_visits:   dict[tuple[str, str], set[str]],
                has_shockai:     set[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """復帰数を計算する。
    ケース1: 離客判定後に任意店舗へ再来店した最初の月（復帰した店舗でカウント、2025/4除外）
    ケース2: データ内に初回=1がない既存会員の最初の来店月（2025/4除外）
    """
    fukki: dict[tuple[str, str], set[str]] = defaultdict(set)

    # ケース1: 離客 → 任意店舗への再来店（復帰した店舗でカウント）
    member_all_visits: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (s, m), months in member_visits.items():
        for ym in months:
            member_all_visits[m].append((ym, s))
    for m in member_all_visits:
        member_all_visits[m].sort()

    member_risseki: dict[str, set[str]] = defaultdict(set)
    for (store, member), months in risseki_history.items():
        member_risseki[member].update(months)

    for member, risseki_months in member_risseki.items():
        all_visits = member_all_visits.get(member, [])
        for rm in sorted(risseki_months):
            for vm, vs in all_visits:
                if vm > rm and vm not in EXCLUDE_FUKKI:
                    fukki[(vs, vm)].add(member)
                    break

    # ケース2: データ内に初回=1がない会員（2025/4以前から在籍）
    for (store, member), months in member_visits.items():
        if (store, member) in has_shockai:
            continue
        first_month = min(months)
        if first_month in EXCLUDE_FUKKI:
            continue
        fukki[(store, first_month)].add(member)

    return {k: len(v) for k, v in fukki.items()}


def pickup(risseki_map: dict[tuple[str, str], int],
           shinki_map:  dict[tuple[str, str], tuple[int, int]],
           fukki_map:   dict[tuple[str, str], int] | None = None,
) -> tuple[list, list, list]:
    all_keys    = set(risseki_map.keys()) | set(shinki_map.keys())
    all_stores  = sorted({s for s, _ in all_keys}, key=lambda s: int(s) if s.isdigit() else s)
    period2_months = {ym for _, ym in all_keys if ym >= PERIOD2_START and ym not in NO_CVR}

    # 条件1
    cond1 = []
    for (store, ym) in sorted(all_keys, key=lambda x: (x[1], int(x[0]) if x[0].isdigit() else x[0])):
        if ym in NO_CVR or ym == "202504":
            continue
        risseki_cnt = risseki_map.get((store, ym), 0)
        s, sc = shinki_map.get((store, ym), (0, 0))
        if sc == 0:
            continue
        rate = s / sc * 100
        if risseki_cnt <= 3 and rate >= 80.0:
            cond1.append((store, ym, risseki_cnt, s, sc, rate))

    # 条件2（新規獲得数ベース）- データがある全店舗を返す
    cond2 = []
    for store in all_stores:
        total_s = sum(shinki_map.get((store, ym), (0, 0))[0] for ym in period2_months)
        total_r = sum(risseki_map.get((store, ym), 0) for ym in period2_months)
        if total_s == 0 and total_r == 0:
            continue
        diff = total_s - total_r
        cond2.append((store, total_s, total_r, diff))

    # 条件2（会員増加数ベース）= (新規獲得数 + 復帰数) - 離脱数（修正後）
    cond2_fukki = []
    if fukki_map is not None:
        for store in all_stores:
            total_s = sum(shinki_map.get((store, ym), (0, 0))[0] for ym in period2_months)
            total_f = sum(fukki_map.get((store, ym), 0) for ym in period2_months)
            total_r = sum(risseki_map.get((store, ym), 0) for ym in period2_months)
            if total_s == 0 and total_f == 0 and total_r == 0:
                continue
            diff = (total_s + total_f) - total_r
            cond2_fukki.append((store, total_s, total_f, total_r, diff))

    return cond1, cond2, cond2_fukki


def fmt_diff(d: int) -> str:
    return f"+{d}" if d > 0 else str(d)

def cond2_section(rows: list, title: str, header: list,
                  items: list, diff_idx: int, extra_cols: int) -> None:
    """差分で3グループに分けて出力するヘルパー"""
    pad = [""] * extra_cols
    match = [x for x in items if abs(x[diff_idx]) <= 3]
    plus  = sorted([x for x in items if x[diff_idx] >= 4],  key=lambda x: -x[diff_idx])
    minus = sorted([x for x in items if x[diff_idx] <= -4], key=lambda x: x[diff_idx])

    rows.append([f"{title} ▼±3以内（{len(match)}店舗）"] + [""] * (len(header) - 1))
    rows.append(header)
    for x in match:
        rows.append(list(x[:diff_idx]) + [fmt_diff(x[diff_idx])] + pad)
    rows.append([])

    rows.append([f"{title} ▼+4以上（{len(plus)}店舗）"] + [""] * (len(header) - 1))
    rows.append(header)
    for x in plus:
        rows.append(list(x[:diff_idx]) + [fmt_diff(x[diff_idx])] + pad)
    rows.append([])

    rows.append([f"{title} ▼−4以下（{len(minus)}店舗）"] + [""] * (len(header) - 1))
    rows.append(header)
    for x in minus:
        rows.append(list(x[:diff_idx]) + [fmt_diff(x[diff_idx])] + pad)
    rows.append([])


def build_rows(cond1: list, cond2: list, label: str,
               cond2_fukki: list | None = None) -> list:
    rows = []

    # 条件1
    rows.append([f"【条件1{label}】離脱数3名以下 かつ CVR80%以上", "", "", "", "", "", ""])
    rows.append(["月", "店舗名", "離脱数", "新規獲得数", "初回数", "CVR(%)", ""])
    for store, ym, risseki, s, sc, rate in cond1:
        rows.append([fmt_ym(ym), store_label(store), risseki, s, sc, f"{rate:.1f}%", ""])
    rows.append([f"該当: {len(cond1)}件", "", "", "", "", "", ""])
    rows.append([])

    # 条件2（新規獲得数ベース）- store, total_s, total_r, diff → diff_idx=3
    cond2_labeled = [(store_label(s), t_s, t_r, d) for s, t_s, t_r, d in cond2]
    cond2_section(
        rows,
        title=f"【条件2{label}】2025/10以降 新規獲得数−離脱数",
        header=["店舗名", "新規獲得数合計", "離脱数合計", "差分（新規−離脱）", "", "", ""],
        items=cond2_labeled,
        diff_idx=3,
        extra_cols=3,
    )

    # 条件2（会員増加数ベース）
    if cond2_fukki is not None:
        cond2f_labeled = [
            (store_label(s), t_s, t_f, t_s + t_f, t_r, d)
            for s, t_s, t_f, t_r, d in cond2_fukki
        ]
        cond2_section(
            rows,
            title=f"【条件2（会員増加数ベース）{label}】2025/10以降 (新規獲得数+復帰数)−離脱数（修正後）",
            header=["店舗名", "新規獲得数合計", "復帰数合計", "会員増加数合計",
                    "離脱数合計（修正後）", "差分（会員増加数−離脱）", ""],
            items=cond2f_labeled,
            diff_idx=5,
            extra_cols=1,
        )

    return rows


def main():
    print("Google Sheetsに接続中...")
    gc = gspread.oauth()

    shinki_map, visits, member_visits, has_shockai = load_shinki(gc)
    raw_risseki, last_visit_map, risseki_history = load_risseki(gc)
    exclusion = build_exclusion(last_visit_map, visits)
    print(f"  除外対象: {len(exclusion)}名")

    # 修正前の離脱者数マップ
    risseki_before = {k: len(v) for k, v in raw_risseki.items()}

    # 修正後の離脱者数マップ（除外適用）
    corrected: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, month), members in raw_risseki.items():
        for member in members:
            if (member, store) not in exclusion:
                corrected[(store, month)].add(member)
    risseki_after = {k: len(v) for k, v in corrected.items()}

    # 復帰数マップ（新定義：離客復帰 + データ内初回なし既存会員）
    print("  復帰数計算中...")
    fukki_map = build_fukki(risseki_history, member_visits, has_shockai)

    # 各条件を算出
    cond1_before, cond2_before, _           = pickup(risseki_before, shinki_map)
    cond1_after,  cond2_after,  cond2_fukki = pickup(risseki_after,  shinki_map, fukki_map)

    # 出力行を構築
    rows_out = []
    rows_out += build_rows(cond1_before, cond2_before, "（修正前）")
    rows_out.append([])
    rows_out.append(["=" * 40, "", "", "", "", "", ""])
    rows_out.append([])
    rows_out += build_rows(cond1_after, cond2_after, "（修正後）", cond2_fukki)

    print("\n結果を書き込み中...")
    ss = gc.open_by_key(SHINKI_SS_ID)
    try:
        out_ws = ss.worksheet("ピックアップ分析")
        out_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        out_ws = ss.add_worksheet(title="ピックアップ分析", rows=500, cols=7)
    out_ws.update(rows_out, "A1")

    def cnt(lst, diff_idx):
        return (sum(1 for x in lst if abs(x[diff_idx]) <= 3),
                sum(1 for x in lst if x[diff_idx] >= 4),
                sum(1 for x in lst if x[diff_idx] <= -4))

    m, p, n = cnt(cond2_before, 3)
    print(f"\n【条件2 修正前】±3以内:{m}店 / +4以上:{p}店 / −4以下:{n}店")
    m, p, n = cnt(cond2_after, 3)
    print(f"【条件2 修正後】±3以内:{m}店 / +4以上:{p}店 / −4以下:{n}店")
    m, p, n = cnt(cond2_fukki, 4)
    print(f"【条件2 会員増加数ベース（修正後）】±3以内:{m}店 / +4以上:{p}店 / −4以下:{n}店")
    print(f"\n【条件1 修正前】{len(cond1_before)}件 → 【修正後】{len(cond1_after)}件")
    print(f"\n✅ 「ピックアップ分析」シートに書き込み完了！")


if __name__ == "__main__":
    main()

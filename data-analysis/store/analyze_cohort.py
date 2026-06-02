#!/usr/bin/env python3
"""コホート分析 - 中目黒（統合）= 中目黒店（1）+ Dr.ピラティス中目黒店（37）
入会月別に何ヶ月後に何%の会員が残っているかを集計する（統合版）
グループ内店舗移動は離脱から除外する
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from collections import defaultdict
from itertools import permutations

from config import SHINKI_SS_ID, RISSEKI_SS_ID
from utils import parse_date, within_next_month

TARGET_GROUP    = ("1", "37")   # 中目黒（統合）
GROUP_NAME      = "中目黒（統合）"
COHORT_START    = "202504"
REVISIT_WINDOWS = [3, 2]   # 比較する再来店ウィンドウ（月数）


def add_months(ym: str, n: int) -> str:
    year  = int(ym[:4])
    month = int(ym[4:]) + n
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}{month:02d}"

def months_diff(ym_from: str, ym_to: str) -> int:
    y1, m1 = int(ym_from[:4]), int(ym_from[4:])
    y2, m2 = int(ym_to[:4]),   int(ym_to[4:])
    return (y2 - y1) * 12 + (m2 - m1)

def fmt_ym(ym: str) -> str:
    return f"{ym[:4]}/{ym[4:]}"


def main():
    print("Google Sheetsに接続中...")
    gc = gspread.oauth()

    target_stores = set(TARGET_GROUP)

    # ---- 取引データ ----
    print("取引データ読み込み中...")
    ss       = gc.open_by_key(SHINKI_SS_ID)
    ws       = ss.worksheets()[0]
    all_rows = ws.get_all_values()
    col      = {h: i for i, h in enumerate(all_rows[0])}
    data     = all_rows[1:]
    print(f"  読み込み完了: {len(data)}件")

    cohort_members:      dict[str, str]                        = {}
    visits:              dict[tuple[str, str], list[datetime]] = defaultdict(list)
    member_visit_months: dict[str, set[str]]                   = defaultdict(set)
    latest_ym = "000000"

    for row in data:
        try:
            if row[col["取消区分 (0:通常、1：取消)"]] == "1":
                continue
            store  = row[col["店舗コード"]]
            member = row[col["会員ID"]]
            ym     = row[col["年月"]]
            if not (store and member and ym):
                continue
            if ym > latest_ym:
                latest_ym = ym
            member_visit_months[member].add(ym)
            if store not in target_stores:
                continue
            vd = parse_date(row[col["取引日時"]])
            if vd:
                visits[(store, member)].append(vd)
            if row[col["初回"]] == "1" and row[col["CV有無"]] == "1" and ym >= COHORT_START:
                if member not in cohort_members or ym < cohort_members[member]:
                    cohort_members[member] = ym
        except IndexError:
            continue

    print(f"  {GROUP_NAME} 入会者（{fmt_ym(COHORT_START)}〜）: {len(cohort_members)}名")
    print(f"  データ最終月: {fmt_ym(latest_ym)}")

    # ---- 離脱者データ ----
    print("離脱者データ読み込み中...")
    rss    = gc.open_by_key(RISSEKI_SS_ID)
    rws    = rss.worksheets()[0]
    r_rows = rws.get_all_values()
    r_col  = {h: i for i, h in enumerate(r_rows[0])}

    risseki_last_visit: dict[tuple[str, str], datetime]  = {}
    risseki_history:    dict[tuple[str, str], set[str]]  = defaultdict(set)

    for row in r_rows[1:]:
        try:
            if row[r_col["判定"]] != "離客":
                continue
            store  = row[r_col["店舗コード"]]
            member = row[r_col["会員ID"]]
            month  = row[r_col["対象月"]]
            lv     = parse_date(row[r_col["直近来店日"]])
            if store not in target_stores or not (member and month):
                continue
            risseki_history[(store, member)].add(month)
            if lv and ((store, member) not in risseki_last_visit
                       or lv > risseki_last_visit[(store, member)]):
                risseki_last_visit[(store, member)] = lv
        except IndexError:
            continue

    # ---- グループ内移動を除外 ----
    exclusion: set[tuple[str, str]] = set()
    for from_s, to_s in permutations(TARGET_GROUP, 2):
        for (store, member), lv in risseki_last_visit.items():
            if store != from_s:
                continue
            later = [d for d in visits.get((to_s, member), []) if d > lv]
            if later and within_next_month(lv, min(later)):
                exclusion.add((member, from_s))
    print(f"  グループ内移動（除外対象）: {len(exclusion)}名")

    # ---- コホート別に集計（ウィンドウ非依存） ----
    cohorts: dict[str, set[str]] = defaultdict(set)
    for member, ym in cohort_members.items():
        cohorts[ym].add(member)

    cohort_months = sorted(cohorts.keys())
    max_n = months_diff(cohort_months[0], latest_ym) if cohort_months else 0
    header = ["入会月", "入会人数"] + [f"{n}ヶ月後" for n in range(1, max_n + 1)]

    # ---- ウィンドウ別に誤離脱除外 → 出力行を構築 ----
    rows_out: list = []
    for revisit_window in REVISIT_WINDOWS:
        false_churn: set[tuple[str, str, str]] = set()
        for (store, member), months in risseki_history.items():
            visit_months = member_visit_months.get(member, set())
            for churn_month in months:
                window_end = add_months(churn_month, revisit_window)
                if any(churn_month < vm <= window_end for vm in visit_months):
                    false_churn.add((store, member, churn_month))
        print(f"  誤離脱除外（{revisit_window}ヶ月ウィンドウ）: {len(false_churn)}件")

        group_risseki: dict[str, str] = {}
        for (store, member), months in risseki_history.items():
            for month in sorted(months):
                if (member, store) in exclusion:
                    continue
                if (store, member, month) in false_churn:
                    continue
                if member not in group_risseki or month < group_risseki[member]:
                    group_risseki[member] = month

        section: list = [
            [f"■ コホート分析（{GROUP_NAME}）　再来店除外ウィンドウ: {revisit_window}ヶ月", ""],
            ["入会月別に、何ヶ月後に何%の会員が残っているかを示す", ""],
            ["残存率 = グループ内離脱未判定の会員数 ÷ 入会人数 × 100", ""],
            [f"対象店舗: {' + '.join(TARGET_GROUP)}（グループ内移動・{revisit_window}ヶ月以内の再来店は離脱から除外）", ""],
            [f"誤離脱除外件数: {len(false_churn)}件", ""],
            [],
            header,
        ]
        for c_ym in cohort_months:
            members = cohorts[c_ym]
            n = len(members)
            row = [fmt_ym(c_ym), n]
            for month_n in range(1, max_n + 1):
                obs_ym = add_months(c_ym, month_n)
                if obs_ym > latest_ym:
                    row.append("")
                    continue
                remaining = sum(
                    1 for m in members
                    if m not in group_risseki or group_risseki[m] > obs_ym
                )
                rate = remaining / n * 100 if n > 0 else 0
                row.append(f"{rate:.1f}%")
            section.append(row)

        if rows_out:
            rows_out.extend([[], []])
        rows_out.extend(section)

    # ---- 書き込み ----
    print("\n結果を書き込み中...")
    sheet_name = "コホート分析_中目黒統合"
    try:
        out_ws = ss.worksheet(sheet_name)
        out_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        out_ws = ss.add_worksheet(title=sheet_name, rows=100, cols=max_n + 5)
    out_ws.update(rows_out, "A1")

    print(f"✅ 「{sheet_name}」シートに書き込み完了！")
    print(f"\n  コホート: {len(cohort_months)}ヶ月分（{fmt_ym(cohort_months[0])}〜{fmt_ym(cohort_months[-1])}）")
    print(f"  追跡期間: 最大{max_n}ヶ月後まで")
    print(f"  グループ内移動除外: {len(exclusion)}名")
    print(f"  出力ウィンドウ: {REVISIT_WINDOWS}ヶ月")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""CVR × 早期離脱 相関分析（統合版）
統合店舗グループ単位で CVR と早期離脱率を集計し、両者の関係を検証する
グループ内店舗移動は離脱から除外する
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from collections import defaultdict
from itertools import permutations

from config import (
    SHINKI_SS_ID, RISSEKI_SS_ID,
    STORE_GROUPS, PAIRED_STORES, NO_CVR,
)
from utils import mkey, get_merged_label, parse_date, within_next_month

EARLY_CHURN_MONTHS = 3
REVISIT_WINDOWS    = [3, 2]   # 比較する再来店除外ウィンドウ（月数）


def add_months(ym: str, n: int) -> str:
    year  = int(ym[:4])
    month = int(ym[4:]) + n
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return f"{year:04d}{month:02d}"

def fmt_ym(ym: str) -> str:
    return f"{ym[:4]}/{ym[4:]}"

def fmt_pct(num: int, den: int) -> str:
    return f"{num / den * 100:.1f}%" if den > 0 else "-"

def pct_val(s: str) -> float:
    return float(s.replace("%", "")) if s != "-" else 0.0


def main():
    print("Google Sheetsに接続中...")
    gc = gspread.oauth()

    # ---- 取引データ ----
    print("取引データ読み込み中...")
    ss       = gc.open_by_key(SHINKI_SS_ID)
    ws       = ss.worksheets()[0]
    all_rows = ws.get_all_values()
    col      = {h: i for i, h in enumerate(all_rows[0])}
    data     = all_rows[1:]
    print(f"  読み込み完了: {len(data)}件")

    # 統合版 CVR: (merged_store, member) → 最初の初回月
    m_first_shockai: dict[tuple[str, str], str] = {}
    m_first_shinki:  dict[tuple[str, str], str] = {}

    # 入会者: (store, member) → 入会月（早期離脱追跡用）
    joined: dict[tuple[str, str], str] = {}

    # スタッフ情報
    staff_names:  dict[str, str]             = {}
    member_staff: dict[tuple[str, str], str] = {}  # (store, member) → 初回担当スタッフID

    # ペア店舗の来店日時（移動判定用）
    visits: dict[tuple[str, str], list[datetime]] = defaultdict(list)
    # 全店舗の来店月セット（誤離脱判定用）
    member_visit_months: dict[str, set[str]]          = defaultdict(set)
    # 会員×月の取引数（来店回数の代理変数）
    member_month_count:  dict[tuple[str, str], int]   = defaultdict(int)

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

            ms = mkey(store)

            member_visit_months[member].add(ym)
            member_month_count[(member, ym)] += 1
            if store in PAIRED_STORES:
                vd = parse_date(row[col["取引日時"]])
                if vd:
                    visits[(store, member)].append(vd)

            if row[col["初回"]] == "1":
                if ym not in NO_CVR:
                    k = (ms, member)
                    if k not in m_first_shockai or ym < m_first_shockai[k]:
                        m_first_shockai[k] = ym
                    if row[col["CV有無"]] == "1":
                        if k not in m_first_shinki or ym < m_first_shinki[k]:
                            m_first_shinki[k] = ym
                if row[col["CV有無"]] == "1":
                    k2 = (store, member)
                    staff_id   = row[col["スタッフID"]]
                    staff_name = row[col["スタッフ名"]]
                    if staff_id:
                        staff_names[staff_id] = staff_name or staff_id
                        member_staff[k2] = staff_id
                    if k2 not in joined or ym < joined[k2]:
                        joined[k2] = ym
        except IndexError:
            continue

    # 統合版 CVR: merged_store → (新規来店数, 新規獲得数)
    merged_shockai: dict[str, int] = defaultdict(int)
    merged_shinki:  dict[str, int] = defaultdict(int)
    for (ms, _) in m_first_shockai:
        merged_shockai[ms] += 1
    for (ms, _) in m_first_shinki:
        merged_shinki[ms] += 1

    print(f"  入会者数（全店舗）: {len(joined)}件 / データ最終月: {latest_ym[:4]}/{latest_ym[4:]}")

    # ---- 離脱者データ ----
    print("離脱者データ読み込み中...")
    rss    = gc.open_by_key(RISSEKI_SS_ID)
    rws    = rss.worksheets()[0]
    r_rows = rws.get_all_values()
    r_col  = {h: i for i, h in enumerate(r_rows[0])}

    risseki_last_visit: dict[tuple[str, str], datetime] = {}
    risseki_history:    dict[tuple[str, str], set[str]] = defaultdict(set)

    for row in r_rows[1:]:
        try:
            if row[r_col["判定"]] != "離客":
                continue
            store  = row[r_col["店舗コード"]]
            member = row[r_col["会員ID"]]
            month  = row[r_col["対象月"]]
            lv     = parse_date(row[r_col["直近来店日"]])
            if not (store and member and month):
                continue
            risseki_history[(store, member)].add(month)
            if lv and ((store, member) not in risseki_last_visit
                       or lv > risseki_last_visit[(store, member)]):
                risseki_last_visit[(store, member)] = lv
        except IndexError:
            continue

    # ---- グループ内移動を除外 ----
    exclusion: set[tuple[str, str]] = set()
    for group in STORE_GROUPS:
        for from_s, to_s in permutations(group, 2):
            for (store, member), lv in risseki_last_visit.items():
                if store != from_s:
                    continue
                later = [d for d in visits.get((to_s, member), []) if d > lv]
                if later and within_next_month(lv, min(later)):
                    exclusion.add((member, from_s))
    print(f"  グループ内移動（除外対象）: {len(exclusion)}名")

    # ---- 統合版 入会: (merged_store, member) → 最初の入会月（ウィンドウ非依存） ----
    merged_joined:       dict[tuple[str, str], str] = {}
    merged_joined_staff: dict[tuple[str, str], str] = {}
    for (store, member), ym in joined.items():
        ms = mkey(store)
        k = (ms, member)
        if k not in merged_joined or ym < merged_joined[k]:
            merged_joined[k] = ym
            merged_joined_staff[k] = member_staff.get((store, member), "")

    obs_threshold = add_months(latest_ym, -EARLY_CHURN_MONTHS)
    obs_label     = f"{obs_threshold[:4]}/{obs_threshold[4:]}"

    all_ms = sorted(
        set(merged_shockai.keys()) | set(merged_shinki.keys()),
        key=lambda s: int(s.split("+")[0]) if s.split("+")[0].isdigit() else 9999,
    )
    header = [
        "店舗（統合）", "新規来店数", "新規獲得数", "CVR(%)",
        f"早期離脱数（{EARLY_CHURN_MONTHS}ヶ月以内）", "早期離脱率",
        "観測可能入会者数", "注記",
    ]

    # 入会者数は window 非依存
    join_month_joined: dict[str, int] = defaultdict(int)
    for (ms, member), join_ym in merged_joined.items():
        if join_ym <= obs_threshold:
            join_month_joined[join_ym] += 1
    join_months_sorted = sorted(join_month_joined.keys())
    total_joined = sum(join_month_joined.values())
    print(f"\n  入会者（全店舗統合・全期間）: {len(merged_joined)}名")
    print(f"  入会者（観測対象 ≦ {obs_label}）: {total_joined}名")

    # ---- ウィンドウ別にループして出力行を構築 ----
    rows_out: list = []
    for revisit_window in REVISIT_WINDOWS:
        # 誤離脱除外
        false_churn: set[tuple[str, str, str]] = set()
        for (store, member), months in risseki_history.items():
            visit_months = member_visit_months.get(member, set())
            for churn_month in months:
                window_end = add_months(churn_month, revisit_window)
                if any(churn_month < vm <= window_end for vm in visit_months):
                    false_churn.add((store, member, churn_month))
        print(f"  誤離脱除外（{revisit_window}ヶ月ウィンドウ）: {len(false_churn)}件")

        # 統合版 離脱月
        group_risseki: dict[tuple[str, str], str] = {}
        for (store, member), months in risseki_history.items():
            ms = mkey(store)
            for month in sorted(months):
                if (member, store) in exclusion:
                    continue
                if (store, member, month) in false_churn:
                    continue
                k = (ms, member)
                if k not in group_risseki or month < group_risseki[k]:
                    group_risseki[k] = month

        # 早期離脱を集計
        early_churn: dict[str, int] = defaultdict(int)
        observable:  dict[str, int] = defaultdict(int)
        for (ms, member), join_ym in merged_joined.items():
            if join_ym > obs_threshold:
                continue
            observable[ms] += 1
            risseki_ym = group_risseki.get((ms, member))
            if risseki_ym and risseki_ym <= add_months(join_ym, EARLY_CHURN_MONTHS):
                early_churn[ms] += 1

        # 入会月別 早期離脱集計
        join_month_churn: dict[str, int] = defaultdict(int)
        for (ms, member), join_ym in merged_joined.items():
            if join_ym > obs_threshold:
                continue
            risseki_ym = group_risseki.get((ms, member))
            if risseki_ym and risseki_ym <= add_months(join_ym, EARLY_CHURN_MONTHS):
                join_month_churn[join_ym] += 1
        total_churn = sum(join_month_churn.values())
        join_month_rows = [
            [fmt_ym(ym), join_month_joined[ym], join_month_churn.get(ym, 0),
             fmt_pct(join_month_churn.get(ym, 0), join_month_joined[ym]), "", "", "", ""]
            for ym in join_months_sorted
        ]

        # トレーナー別 早期離脱集計
        staff_observable:  dict[str, int] = defaultdict(int)
        staff_churn:       dict[str, int] = defaultdict(int)
        staff_total_churn: dict[str, int] = defaultdict(int)
        for (ms, member), join_ym in merged_joined.items():
            if join_ym > obs_threshold:
                continue
            sid = merged_joined_staff.get((ms, member), "")
            if not sid:
                continue
            staff_observable[sid] += 1
            risseki_ym = group_risseki.get((ms, member))
            if risseki_ym:
                staff_total_churn[sid] += 1
                if risseki_ym <= add_months(join_ym, EARLY_CHURN_MONTHS):
                    staff_churn[sid] += 1
        staff_data_rows = []
        for sid in staff_observable:
            ob = staff_observable[sid]
            ec = staff_churn.get(sid, 0)
            tc = staff_total_churn.get(sid, 0)
            note = "※観測数5未満（参考値）" if 0 < ob < 5 else ""
            staff_data_rows.append([sid, staff_names.get(sid, sid), ob, ec, fmt_pct(ec, ob), tc, fmt_pct(ec, tc), note])
        staff_data_rows_sorted = sorted(staff_data_rows, key=lambda r: pct_val(r[4]), reverse=True)

        # 入会翌月の来店回数 × 早期離脱集計
        freq_joined: dict[int, int] = defaultdict(int)
        freq_churn:  dict[int, int] = defaultdict(int)
        for (ms, member), join_ym in merged_joined.items():
            if join_ym > obs_threshold:
                continue
            next_ym = add_months(join_ym, 1)
            count  = member_month_count.get((member, next_ym), 0)
            if count == 0:
                continue  # 翌月来店なし = その時点で離客のため除外
            bucket = min(count, 8)
            freq_joined[bucket] += 1
            risseki_ym = group_risseki.get((ms, member))
            if risseki_ym and risseki_ym <= add_months(join_ym, EARLY_CHURN_MONTHS):
                freq_churn[bucket] += 1
        freq_rows = []
        for b in range(1, 9):
            label = f"{b}回" if b < 8 else "8回以上"
            n  = freq_joined.get(b, 0)
            ec = freq_churn.get(b, 0)
            freq_rows.append([label, n, ec, fmt_pct(ec, n), "", "", "", ""])
        total_freq       = sum(freq_joined.values())
        total_freq_churn = sum(freq_churn.values())
        freq_rows.append(["合計", total_freq, total_freq_churn, fmt_pct(total_freq_churn, total_freq), "", "", "", ""])

        # 入会翌々月（M+2）の来店回数 × 早期離脱集計
        freq2_joined: dict[int, int] = defaultdict(int)
        freq2_churn:  dict[int, int] = defaultdict(int)
        for (ms, member), join_ym in merged_joined.items():
            if join_ym > obs_threshold:
                continue
            next2_ym = add_months(join_ym, 2)
            count  = member_month_count.get((member, next2_ym), 0)
            if count == 0:
                continue  # 翌々月来店なし = その時点で離客のため除外
            bucket = min(count, 8)
            freq2_joined[bucket] += 1
            risseki_ym = group_risseki.get((ms, member))
            if risseki_ym and risseki_ym <= add_months(join_ym, EARLY_CHURN_MONTHS):
                freq2_churn[bucket] += 1
        freq2_rows = []
        for b in range(1, 9):
            label = f"{b}回" if b < 8 else "8回以上"
            n  = freq2_joined.get(b, 0)
            ec = freq2_churn.get(b, 0)
            freq2_rows.append([label, n, ec, fmt_pct(ec, n), "", "", "", ""])
        total_freq2       = sum(freq2_joined.values())
        total_freq2_churn = sum(freq2_churn.values())
        freq2_rows.append(["合計", total_freq2, total_freq2_churn, fmt_pct(total_freq2_churn, total_freq2), "", "", "", ""])

        # 店舗別 data_rows
        data_rows = []
        for ms in all_ms:
            sc = merged_shockai.get(ms, 0)
            s  = merged_shinki.get(ms, 0)
            ec = early_churn.get(ms, 0)
            ob = observable.get(ms, 0)
            if sc == 0 and s == 0:
                continue
            note = "※観測数5未満（参考値）" if 0 < ob < 5 else ""
            data_rows.append([
                get_merged_label(ms), sc, s, fmt_pct(s, sc),
                ec, fmt_pct(ec, ob), ob, note,
            ])

        section = [
            [f"■ CVR × 早期離脱 相関分析（統合版）　再来店除外ウィンドウ: {revisit_window}ヶ月", "", "", "", "", "", "", ""],
            [f"CVRが高い店舗ほど早期離脱（入会後{EARLY_CHURN_MONTHS}ヶ月以内）も多いかを検証", "", "", "", "", "", "", ""],
            ["統合店舗グループ単位で集計 / グループ内店舗移動は離脱から除外", "", "", "", "", "", "", ""],
            [f"早期離脱の観測条件: 入会月 ≦ {obs_label}（{EARLY_CHURN_MONTHS}ヶ月の追跡期間確保）　誤離脱除外: {len(false_churn)}件", "", "", "", "", "", "", ""],
            [],
            ["▼ CVR 降順", "", "", "", "", "", "", ""],
            header,
            *sorted(data_rows, key=lambda r: pct_val(r[3]), reverse=True),
            [],
            ["▼ 早期離脱率 降順", "", "", "", "", "", "", ""],
            header,
            *sorted(data_rows, key=lambda r: pct_val(r[5]), reverse=True),
            [],
            [],
            ["■ 入会月別 早期離脱率（全店舗統合）", "", "", "", "", "", "", ""],
            [f"何月に入会した人の早期離脱率（{EARLY_CHURN_MONTHS}ヶ月以内）が高いかを示す", "", "", "", "", "", "", ""],
            [f"観測条件: 入会月 ≦ {obs_label}", "", "", "", "", "", "", ""],
            [],
            ["入会月", "入会者数（観測可）", "早期離脱数", "早期離脱率", "", "", "", ""],
            *join_month_rows,
            ["合計", total_joined, total_churn, fmt_pct(total_churn, total_joined), "", "", "", ""],
            [],
            [],
            ["■ トレーナー別 早期離脱率（早期離脱率降順）", "", "", "", "", "", "", ""],
            [f"初回担当トレーナーごとに早期離脱率（{EARLY_CHURN_MONTHS}ヶ月以内）を集計", "", "", "", "", "", "", ""],
            [f"観測条件: 入会月 ≦ {obs_label}", "", "", "", "", "", "", ""],
            [],
            ["スタッフID", "スタッフ名", "担当入会者数（観測可）", "早期離脱数", "早期離脱率", "離脱数（全期間）", "早期離脱割合", "注記"],
            *staff_data_rows_sorted,
            [],
            [],
            ["■ 入会翌月の来店回数 × 早期離脱率", "", "", "", "", "", "", ""],
            [f"入会月の翌月（M+1月）の来店回数別に早期離脱率（{EARLY_CHURN_MONTHS}ヶ月以内）を集計　※翌月0回（その時点で離客）は除外", "", "", "", "", "", "", ""],
            [f"観測条件: 入会月 ≦ {obs_label}", "", "", "", "", "", "", ""],
            [],
            ["入会翌月の来店回数", "人数（観測可）", "早期離脱数", "早期離脱率", "", "", "", ""],
            *freq_rows,
            [],
            [],
            ["■ 入会翌々月の来店回数 × 早期離脱率", "", "", "", "", "", "", ""],
            [f"入会月の翌々月（M+2月）の来店回数別に早期離脱率（{EARLY_CHURN_MONTHS}ヶ月以内）を集計　※翌々月0回（その時点で離客）は除外", "", "", "", "", "", "", ""],
            [f"観測条件: 入会月 ≦ {obs_label}", "", "", "", "", "", "", ""],
            [],
            ["入会翌々月の来店回数", "人数（観測可）", "早期離脱数", "早期離脱率", "", "", "", ""],
            *freq2_rows,
        ]
        if rows_out:
            rows_out.extend([[], [], [], []])
        rows_out.extend(section)

    print("\n結果を書き込み中...")
    new_title = "早期離脱分析"
    try:
        out_ws = ss.worksheet(new_title)
        out_ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        try:
            out_ws = ss.worksheet("CVR×早期離脱分析")
            out_ws.update_title(new_title)
            out_ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            out_ws = ss.add_worksheet(title=new_title, rows=600, cols=9)
    out_ws.update(rows_out, "A1")

    print(f"✅ 「{new_title}」シートに書き込み完了！")
    print(f"\n  早期離脱の観測対象: 入会月 ≦ {obs_label}")
    print(f"  グループ内移動除外: {len(exclusion)}名")
    print(f"  入会月別: {len(join_months_sorted)}ヶ月分")
    print(f"  出力ウィンドウ: {REVISIT_WINDOWS}ヶ月")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
store/analyze_main.py — 店舗別 月次集計

出力シート:
    数字整理          : 店舗別月次集計（通常版）
    数字整理（統合版） : 統合グループ別月次集計

実行方法:
    python3.12 store/analyze_main.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from collections import defaultdict
from itertools import permutations

from config import (
    CUT_MONTH, EXCLUDE_MONTHS,
    STORE_GROUPS,
)
from utils import (
    mkey, get_store_label, get_merged_label,
    within_next_month, make_sheet_rows,
)
from loader import load_transaction_data, load_risseki_data


DEFINITION_ROWS = [
    ["■ 項目定義", "", "", "", "", "", "", ""],
    ["項目", "定義", "", "", "", "", "", ""],
    ["新規数",
     "その月・店舗に初めて来店した会員数（初回=1、取消除く）",
     "", "", "", "", "", ""],
    ["CVR(%)",
     "新規獲得数 ÷ 新規数 × 100。2026/5はデータ途中のため「-」",
     "", "", "", "", "", ""],
    ["新規獲得数",
     "新規数のうち CV=1（入会・契約）した会員数",
     "", "", "", "", "", ""],
    ["復帰数",
     "①離脱者と判定された会員がどの店舗でも再来店した最初の月を、再来店した店舗でカウント（2025/4除外）"
     "　②データ期間開始前から在籍しており、データ内に初回来店記録がない会員の最初の来店月（2025/4除外）",
     "", "", "", "", "", ""],
    ["離脱数",
     "離脱者データで「離客」と判定された会員数（対象月ベース、2025/4・2026/5除外）",
     "", "", "", "", "", ""],
    ["会員増減数",
     "新規獲得数 ＋ 復帰数 － 離脱数（その月の実質的な会員数の増減）",
     "", "", "", "", "", ""],
    ["アクティブ会員数",
     "その月に1度でも来店した会員数 から 初回来店でCV無しの顧客を差し引いた数",
     "", "", "", "", "", ""],
    ["離脱率",
     "離脱数 ÷ 先月のアクティブ会員数 × 100",
     "", "", "", "", "", ""],
    [],
]


def main() -> None:
    print("Google Sheetsに接続中...")
    gc = gspread.oauth()

    # ---- データ読み込み ----
    risseki_data = load_risseki_data(gc)
    txn_data     = load_transaction_data(gc)

    risseki_history    = risseki_data["risseki_history"]
    risseki_by_sm      = risseki_data["risseki_by_sm"]
    risseki_last_visit = risseki_data["risseki_last_visit"]

    ss            = txn_data["ss"]
    shinki        = txn_data["shinki"]
    shockai       = txn_data["shockai"]
    member_visits = txn_data["member_visits"]
    paired_visits = txn_data["paired_visits"]

    # ---- 全店舗横断来店リスト ----
    member_all_visits: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for (s, m), months in member_visits.items():
        for ym in months:
            member_all_visits[m].append((ym, s))
    for m in member_all_visits:
        member_all_visits[m].sort()

    # 会員ごとの離脱月セット
    member_risseki: dict[str, set[str]] = defaultdict(set)
    for (store, member), months in risseki_history.items():
        member_risseki[member].update(months)

    # 初回=1 のある (store, member) セット
    has_shockai: set[tuple[str, str]] = {
        (store, member)
        for (store, _ym), members in shockai.items()
        for member in members
    }

    # ---- 通常版 復帰数 ----
    print("復帰数計算中（通常版）...")
    fukki: dict[tuple[str, str], set[str]] = defaultdict(set)

    # ケース1: 離客後の最初の再来店月
    for member, risseki_months in member_risseki.items():
        for rm in sorted(risseki_months):
            for vm, vs in member_all_visits.get(member, []):
                if vm > rm and vm not in EXCLUDE_MONTHS:
                    fukki[(vs, vm)].add(member)
                    break

    # ケース2: データ内に初回=1がない既存会員の最初来店月
    for (store, member), months in member_visits.items():
        if (store, member) in has_shockai:
            continue
        fm = min(months)
        if fm not in EXCLUDE_MONTHS:
            fukki[(store, fm)].add(member)

    fukki_map: dict[tuple[str, str], int]      = {k: len(v) for k, v in fukki.items()}
    risseki_count: dict[tuple[str, str], int]  = {k: len(v) for k, v in risseki_by_sm.items()}
    print("  完了")

    # ---- 統合版 計算 ----
    print("統合版計算中...")

    # グループ内店舗間移動の除外セット
    exclusion: set[tuple[str, str]] = set()
    for group in STORE_GROUPS:
        for from_s, to_s in permutations(group, 2):
            for (store, member), lv in risseki_last_visit.items():
                if store != from_s:
                    continue
                later = [d for d in paired_visits.get((to_s, member), []) if d > lv]
                if later and within_next_month(lv, min(later)):
                    exclusion.add((member, from_s))
    print(f"  統合版 除外対象: {len(exclusion)}名")

    # 統合版 新規数/新規獲得数（グループ内で最初の初回月のみ）
    m_first_shockai: dict[tuple[str, str], str] = {}
    m_first_shinki:  dict[tuple[str, str], str] = {}
    for (store, ym), members in shockai.items():
        ms = mkey(store)
        for member in members:
            k = (ms, member)
            if k not in m_first_shockai or ym < m_first_shockai[k]:
                m_first_shockai[k] = ym
    for (store, ym), members in shinki.items():
        ms = mkey(store)
        for member in members:
            k = (ms, member)
            if k not in m_first_shinki or ym < m_first_shinki[k]:
                m_first_shinki[k] = ym

    merged_shockai: dict[tuple[str, str], set[str]] = defaultdict(set)
    merged_shinki:  dict[tuple[str, str], set[str]] = defaultdict(set)
    for (ms, member), ym in m_first_shockai.items():
        merged_shockai[(ms, ym)].add(member)
    for (ms, member), ym in m_first_shinki.items():
        merged_shinki[(ms, ym)].add(member)

    # 統合版 離脱数（グループ内移動を除外）
    merged_risseki_by_sm: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, month), members in risseki_by_sm.items():
        ms = mkey(store)
        for member in members:
            if (member, store) not in exclusion:
                merged_risseki_by_sm[(ms, month)].add(member)
    merged_risseki_count: dict[tuple[str, str], int] = {
        k: len(v) for k, v in merged_risseki_by_sm.items()
    }

    # 統合版 復帰数
    merged_fukki: dict[tuple[str, str], set[str]] = defaultdict(set)

    # ケース1: 離客後の最初の再来店（統合キー単位）
    for member, risseki_months in member_risseki.items():
        for rm in sorted(risseki_months):
            for vm, vs in member_all_visits.get(member, []):
                if vm > rm and vm not in EXCLUDE_MONTHS:
                    merged_fukki[(mkey(vs), vm)].add(member)
                    break

    # ケース2: 既存会員の最初来店月（統合キー単位）
    has_shockai_merged: set[tuple[str, str]] = {
        (mkey(store), member) for (store, member) in has_shockai
    }
    merged_first_visit: dict[tuple[str, str], str] = {}
    for (store, member), months in member_visits.items():
        ms = mkey(store)
        fm = min(months)
        k  = (ms, member)
        if k not in merged_first_visit or fm < merged_first_visit[k]:
            merged_first_visit[k] = fm
    for (ms, member), fm in merged_first_visit.items():
        if (ms, member) in has_shockai_merged:
            continue
        if fm not in EXCLUDE_MONTHS:
            merged_fukki[(ms, fm)].add(member)

    merged_fukki_map: dict[tuple[str, str], int] = {
        k: len(v) for k, v in merged_fukki.items()
    }
    print("  完了")

    # ---- アクティブ会員数 ----
    store_month_visitors: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, member), months in member_visits.items():
        for ym in months:
            store_month_visitors[(store, ym)].add(member)

    active_users_raw: dict[tuple[str, str], int] = {}
    for (store, ym), visitors in store_month_visitors.items():
        non_cv_first = shockai.get((store, ym), set()) - shinki.get((store, ym), set())
        active_users_raw[(store, ym)] = len(visitors - non_cv_first)

    merged_store_month_visitors: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, member), months in member_visits.items():
        ms = mkey(store)
        for ym in months:
            merged_store_month_visitors[(ms, ym)].add(member)

    merged_active_users_raw: dict[tuple[str, str], int] = {}
    for (ms, ym), visitors in merged_store_month_visitors.items():
        non_cv_first = merged_shockai.get((ms, ym), set()) - merged_shinki.get((ms, ym), set())
        merged_active_users_raw[(ms, ym)] = len(visitors - non_cv_first)

    # ---- 月リスト ----
    all_keys   = shinki.keys() | shockai.keys()
    all_months = sorted(
        {ym for _, ym in all_keys}
        | {ym for _, ym in risseki_count}
        | {ym for _, ym in fukki_map}
    )
    p1_months = [ym for ym in all_months if ym <= CUT_MONTH and ym not in EXCLUDE_MONTHS]
    p2_months = [ym for ym in all_months if ym >  CUT_MONTH and ym not in EXCLUDE_MONTHS]

    # ---- 通常版 店舗リスト ----
    stores = sorted(
        {s for s, _ in all_keys}
        | {s for s, _ in risseki_count}
        | {s for s, _ in fukki_map},
        key=lambda s: int(s) if s.isdigit() else 9999,
    )

    # ---- 統合版 店舗リスト ----
    merged_keys = merged_shinki.keys() | merged_shockai.keys()
    merged_stores = sorted(
        {ms for ms, _ in merged_keys}
        | {ms for ms, _ in merged_risseki_count}
        | {ms for ms, _ in merged_fukki_map},
        key=lambda s: int(s.split("+")[0]) if s.split("+")[0].isdigit() else 9999,
    )

    # ---- 書き込み ----
    print("\n結果を書き込み中...")

    rows_standard = make_sheet_rows(
        stores, shinki, shockai, fukki_map, risseki_count,
        p1_months, p2_months, get_store_label,
        active_users=active_users_raw,
        definition_rows=DEFINITION_ROWS,
    )
    try:
        ws1 = ss.worksheet("数字整理")
        ws1.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws1 = ss.add_worksheet(title="数字整理", rows=3000, cols=10)
    ws1.update(rows_standard, "A1")
    ws1.format("E:E", {"horizontalAlignment": "RIGHT"})
    ws1.format("I:I", {"horizontalAlignment": "RIGHT"})
    print("  ✅ 「数字整理」シート更新完了")

    rows_merged = make_sheet_rows(
        merged_stores, merged_shinki, merged_shockai,
        merged_fukki_map, merged_risseki_count,
        p1_months, p2_months, get_merged_label,
        active_users=merged_active_users_raw,
        definition_rows=DEFINITION_ROWS,
    )
    try:
        ws2 = ss.worksheet("数字整理（統合版）")
        ws2.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws2 = ss.add_worksheet(title="数字整理（統合版）", rows=3000, cols=10)
    ws2.update(rows_merged, "A1")
    ws2.format("E:E", {"horizontalAlignment": "RIGHT"})
    ws2.format("I:I", {"horizontalAlignment": "RIGHT"})
    print("  ✅ 「数字整理（統合版）」シート更新完了")

    print(f"\n   通常版: {len(stores)}店舗 / 統合版: {len(merged_stores)}店舗")


if __name__ == "__main__":
    main()

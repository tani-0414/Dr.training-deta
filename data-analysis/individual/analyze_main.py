#!/usr/bin/env python3
"""
individual/analyze_main.py — 個人別（スタッフ別）月次集計

出力シート:
    数字整理（個人）          : スタッフ別月次集計（全店舗集計）
    数字整理（店舗別個人）     : 統合グループ×スタッフの月次集計（15列・ファクトベース）
    数字整理（店舗別個人・個別）: 個別店舗×スタッフの月次集計（15列・ファクトベース）

実行方法:
    python3.12 individual/analyze_main.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import gspread
from collections import defaultdict

from config import (
    CUT_MONTH, EXCLUDE_MONTHS, NO_CVR,
    WS4_OUT_START, WS4_OUT_END,
)
from utils import (
    mkey, get_store_label, get_merged_label,
    cvr, churn_rate, prev_month, next_month,
    visit_freq, shimei_rate_str, make_sheet_rows,
)
from loader import load_transaction_data, load_risseki_data


# ---- 定義行 ----

DEFINITION_ROWS_STAFF = [
    ["■ 項目定義（個人別）", "", "", "", "", "", "", ""],
    ["項目", "定義", "", "", "", "", "", ""],
    ["新規数",
     "初回来店（初回=1）を担当したスタッフでカウント（取消除く）",
     "", "", "", "", "", ""],
    ["CVR(%)",
     "新規獲得数 ÷ 新規数 × 100。2026/5はデータ途中のため「-」",
     "", "", "", "", "", ""],
    ["新規獲得数",
     "新規数のうち CV=1（入会・契約）した会員数を担当スタッフでカウント",
     "", "", "", "", "", ""],
    ["復帰数",
     "①離客判定後の最初の再来店セッションを担当したスタッフでカウント（2025/4除外）"
     "　②データ内に初回来店記録がない既存会員の最初来店セッションを担当したスタッフでカウント（2025/4除外）",
     "", "", "", "", "", ""],
    ["離脱数",
     "離脱者データの「直近担当者コード」でスタッフに紐付け（対象月ベース、2025/4・2026/5除外）",
     "", "", "", "", "", ""],
    ["会員増減数",
     "新規獲得数 ＋ 復帰数 － 離脱数",
     "", "", "", "", "", ""],
    [],
]

DEFINITION_ROWS_STAFF_STORE = [
    ["■ 項目定義（店舗別個人）", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ["項目", "定義", "", "", "", "", "", "", "", "", "", "", "", "", ""],
    ["担当会員数",
     "その月・その店舗でセッション回数が最多のスタッフを「担当」として集計（非CV初回者除く）",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["新規担当数",
     "CV=1の新規会員のうち、初回以外セッションで最多担当のスタッフ（初回のみ来店の場合は初回担当スタッフ）",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["復帰担当数",
     "【ファクトテーブルのみ】①先月アクティブでない&今月アクティブ&以前来店あり（ギャップ復帰）"
     "　②今月が初登場(2025/5+)&初回=1なし（既存会員）。帰属: 今月の最多担当スタッフ",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["離脱数",
     "【ファクトテーブルのみ】今月アクティブ→来月来店なし→来月の離脱者。帰属: 今月の最多担当スタッフ。出力期間: 2025/5〜2026/4",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["離脱率",
     "離脱数 ÷ 前月の担当会員数 × 100。前月担当会員数が0の場合は「-」",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["担当引き継ぎ",
     "既存会員の担当スタッフが前月から変更され、新たに担当になった数（新規担当・復帰担当除く）",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["担当譲渡",
     "既存会員の担当スタッフが前月から変更され、担当を手放した数（新規担当・復帰担当除く）",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["会員増減数",
     "新規担当数 ＋ 復帰担当数 － 離脱数 ＋ 担当引き継ぎ － 担当譲渡",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["来店頻度",
     "担当会員の月間平均セッション回数（担当会員の総セッション数 ÷ 担当会員数）",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    ["指名率",
     "そのスタッフの全セッションのうち「指名あり（指名有無=1）」の割合",
     "", "", "", "", "", "", "", "", "", "", "", ""],
    [],
]


# ---- 店舗別個人 行生成ヘルパー ----

def _add_staff_store_period(
    rows:             list,
    sid:              str,
    store:            str,
    months:           list[str],
    shinki_ss:        dict,   # 新規獲得数（CV=1の初回）
    shockai_ss:       dict,   # 新規数（全初回）
    shinki_tanto_map: dict,   # 新規担当数
    fukki_tanto_map:  dict,   # 復帰担当数
    risseki_fact_map: dict,   # 離脱数（ファクトベース）
    hikitsugi_map:    dict,   # 担当引き継ぎ
    joto_map:         dict,   # 担当譲渡
    active_ss_count:  dict,   # 担当会員数
    active_ss_sess:   dict,   # 来店頻度用セッション数
    shimei_by_staff:  dict,   # 指名セッション数
    sess_by_staff:    dict,   # 総セッション数
) -> dict:
    """月別行を rows に追加し、期間集計値を返す（15列）"""
    agg: dict[str, int] = defaultdict(int)
    for ym in months:
        sc       = len(shockai_ss.get((sid, store, ym), set()))
        s        = len(shinki_ss.get((sid, store, ym), set()))
        st       = shinki_tanto_map.get((sid, store, ym), 0)
        fk       = fukki_tanto_map.get((sid, store, ym), 0)
        ri       = risseki_fact_map.get((sid, store, ym), 0)
        hk       = hikitsugi_map.get((sid, store, ym), 0)
        jt       = joto_map.get((sid, store, ym), 0)
        act      = active_ss_count.get((sid, store, ym), 0)
        sess     = active_ss_sess.get((sid, store, ym), 0)
        shm      = shimei_by_staff.get((sid, store, ym), 0)
        tot      = sess_by_staff.get((sid, store, ym), 0)
        prev_act = active_ss_count.get((sid, store, prev_month(ym)), 0)
        churn_r  = churn_rate(ri, prev_act)
        if s == 0 and sc == 0 and st == 0 and fk == 0 and ri == 0 and act == 0:
            continue
        rows.append([
            ym, sid, act, sc, cvr(s, sc, ym), s,
            st, fk, ri, churn_r, hk, jt,
            st + fk - ri + hk - jt,
            visit_freq(sess, act), shimei_rate_str(shm, tot),
        ])
        agg["sc"] += sc; agg["s"] += s; agg["st"] += st
        agg["fk"] += fk; agg["ri"] += ri; agg["hk"] += hk; agg["jt"] += jt
        agg["shm"] += shm; agg["tot_sess"] += tot
        if ym not in NO_CVR:
            agg["s_cvr"] += s; agg["sc_cvr"] += sc
    return agg


def make_store_staff_rows(
    stores:           list[str],
    staff_by_store:   dict[str, set[str]],
    shinki_ss:        dict,
    shockai_ss:       dict,
    shinki_tanto_map: dict,
    fukki_tanto_map:  dict,
    risseki_fact_map: dict,
    hikitsugi_map:    dict,
    joto_map:         dict,
    active_ss_count:  dict,
    active_ss_sess:   dict,
    shimei_by_staff:  dict,
    sess_by_staff:    dict,
    p1_months:        list[str],
    p2_months:        list[str],
    staff_names:      dict[str, str],
    get_store_label,
) -> list:
    """店舗別個人シート（15列）のデータ行リストを生成する"""
    ncols  = 15
    header = [
        "年月", "スタッフ", "担当会員数", "新規数", "CVR(%)", "新規獲得数",
        "新規担当数", "復帰担当数", "離脱数", "離脱率", "担当引き継ぎ", "担当譲渡",
        "会員増減数", "来店頻度", "指名率",
    ]
    blank = [""] * (ncols - 1)

    rows: list = list(DEFINITION_ROWS_STAFF_STORE)

    def get_staff_label(sid: str) -> str:
        name = staff_names.get(sid, "")
        return f"{name}（{sid}）" if name else f"スタッフ{sid}"

    for store in stores:
        rows.append([f"【{get_store_label(store)}】"] + blank)
        staff_ids = sorted(
            staff_by_store.get(store, set()),
            key=lambda s: int(s) if s.isdigit() else 9999,
        )
        for sid in staff_ids:
            lbl = get_staff_label(sid)
            rows.append([f"▶ {lbl}"] + blank)
            rows.append(header)

            p1 = _add_staff_store_period(
                rows, sid, store, p1_months,
                shinki_ss, shockai_ss,
                shinki_tanto_map, fukki_tanto_map, risseki_fact_map,
                hikitsugi_map, joto_map,
                active_ss_count, active_ss_sess, shimei_by_staff, sess_by_staff,
            )
            rows.append(["〜2025/9 合計", lbl, "",
                         p1["sc"], cvr(p1["s_cvr"], p1["sc_cvr"]),
                         p1["s"], p1["st"], p1["fk"], p1["ri"], "",
                         p1["hk"], p1["jt"],
                         p1["st"] + p1["fk"] - p1["ri"] + p1["hk"] - p1["jt"],
                         "", shimei_rate_str(p1["shm"], p1["tot_sess"])])
            rows.append([])

            p2 = _add_staff_store_period(
                rows, sid, store, p2_months,
                shinki_ss, shockai_ss,
                shinki_tanto_map, fukki_tanto_map, risseki_fact_map,
                hikitsugi_map, joto_map,
                active_ss_count, active_ss_sess, shimei_by_staff, sess_by_staff,
            )
            rows.append(["2025/10〜 合計", lbl, "",
                         p2["sc"], cvr(p2["s_cvr"], p2["sc_cvr"]),
                         p2["s"], p2["st"], p2["fk"], p2["ri"], "",
                         p2["hk"], p2["jt"],
                         p2["st"] + p2["fk"] - p2["ri"] + p2["hk"] - p2["jt"],
                         "", shimei_rate_str(p2["shm"], p2["tot_sess"])])
            rows.append([])

            ts    = p1["s"]   + p2["s"]
            tsc   = p1["sc"]  + p2["sc"]
            tst   = p1["st"]  + p2["st"]
            tfk   = p1["fk"]  + p2["fk"]
            tri   = p1["ri"]  + p2["ri"]
            thk   = p1["hk"]  + p2["hk"]
            tjt   = p1["jt"]  + p2["jt"]
            tshm  = p1["shm"] + p2["shm"]
            ttot  = p1["tot_sess"] + p2["tot_sess"]
            ts_c  = p1["s_cvr"]  + p2["s_cvr"]
            tsc_c = p1["sc_cvr"] + p2["sc_cvr"]
            rows.append(["全体合計", lbl, "", tsc, cvr(ts_c, tsc_c),
                         ts, tst, tfk, tri, "",
                         thk, tjt,
                         tst + tfk - tri + thk - tjt,
                         "", shimei_rate_str(tshm, ttot)])
            rows.append([])
        rows.append([])

    return rows


# ---- メイン処理 ----

def main() -> None:
    print("Google Sheetsに接続中...")
    gc = gspread.oauth()

    # ---- データ読み込み ----
    risseki_data = load_risseki_data(gc)
    txn_data     = load_transaction_data(gc)

    risseki_history     = risseki_data["risseki_history"]
    risseki_staff_by_sm = risseki_data["risseki_staff_by_sm"]

    ss                       = txn_data["ss"]
    shinki                   = txn_data["shinki"]
    shockai                  = txn_data["shockai"]
    member_visits            = txn_data["member_visits"]
    staff_names              = txn_data["staff_names"]
    shinki_staff             = txn_data["shinki_staff"]
    shockai_staff            = txn_data["shockai_staff"]
    first_staff              = txn_data["first_staff"]
    session_count            = txn_data["session_count"]
    member_store_ym_sess     = txn_data["member_store_ym_sess"]
    shimei_by_staff          = txn_data["shimei_by_staff"]
    sess_by_staff            = txn_data["sess_by_staff"]
    shinki_staff_store       = txn_data["shinki_staff_store"]
    shockai_staff_store      = txn_data["shockai_staff_store"]
    session_count_excl_first = txn_data["session_count_excl_first"]

    # ---- 離脱数（スタッフ別・個人シート用）----
    risseki_count_staff: dict[tuple[str, str], int] = {
        k: len(v) for k, v in risseki_staff_by_sm.items()
    }

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

    # ---- 最多担当スタッフの決定（店舗単位）----
    print("最多担当スタッフ計算中...")
    _best: dict[tuple[str, str, str], tuple[int, str]] = {}
    for (member, store, ym, staff_id), cnt in session_count.items():
        k = (member, store, ym)
        prev = _best.get(k)
        if prev is None or cnt > prev[0] or (cnt == prev[0] and staff_id < prev[1]):
            _best[k] = (cnt, staff_id)
    primary_staff_store: dict[tuple[str, str, str], str] = {
        k: v[1] for k, v in _best.items()
    }

    # 担当アクティブ会員数 & 来店頻度用セッション数
    staff_store_active_set:  dict[tuple[str, str, str], set[str]] = defaultdict(set)
    staff_store_active_sess: dict[tuple[str, str, str], int]      = defaultdict(int)
    for (member, store, ym), sid in primary_staff_store.items():
        non_cv_first = shockai.get((store, ym), set()) - shinki.get((store, ym), set())
        if member not in non_cv_first:
            staff_store_active_set[(sid, store, ym)].add(member)
            staff_store_active_sess[(sid, store, ym)] += member_store_ym_sess[(member, store, ym)]
    staff_store_active_count: dict[tuple[str, str, str], int] = {
        k: len(v) for k, v in staff_store_active_set.items()
    }
    print("  完了")

    # ---- スタッフ別 復帰数（ws3用・離脱者シートベース）----
    print("復帰数計算中（個人別）...")
    fukki_staff:       dict[tuple[str, str], set[str]]       = defaultdict(set)
    fukki_staff_store: dict[tuple[str, str, str], set[str]]  = defaultdict(set)

    # ケース1: 離客→再来店（再来店時の初回担当スタッフ）
    for member, risseki_months in member_risseki.items():
        for rm in sorted(risseki_months):
            for vm, vs in member_all_visits.get(member, []):
                if vm > rm and vm not in EXCLUDE_MONTHS:
                    sid = first_staff.get((member, vm, vs), "")
                    if sid:
                        fukki_staff[(sid, vm)].add(member)
                        fukki_staff_store[(sid, vs, vm)].add(member)
                    break

    # ケース2: データ内に初回=1がない既存会員（最初来店時の初回担当スタッフ）
    for (store, member), months in member_visits.items():
        if (store, member) in has_shockai:
            continue
        fm = min(months)
        if fm not in EXCLUDE_MONTHS:
            sid = first_staff.get((member, fm, store), "")
            if sid:
                fukki_staff[(sid, fm)].add(member)
                fukki_staff_store[(sid, store, fm)].add(member)

    fukki_staff_map:       dict[tuple[str, str], int]      = {k: len(v) for k, v in fukki_staff.items()}
    fukki_staff_store_map: dict[tuple[str, str, str], int] = {k: len(v) for k, v in fukki_staff_store.items()}
    print("  完了")

    # ---- 新規担当数（CV=1の初回会員・初回以外セッション最多担当スタッフ）----
    print("新規担当数計算中...")
    _best_excl: dict[tuple[str, str, str], tuple[int, str]] = {}
    for (member, store, ym, staff_id), cnt in session_count_excl_first.items():
        k = (member, store, ym)
        prev = _best_excl.get(k)
        if prev is None or cnt > prev[0] or (cnt == prev[0] and staff_id < prev[1]):
            _best_excl[k] = (cnt, staff_id)
    primary_staff_excl_first: dict[tuple[str, str, str], str] = {
        k: v[1] for k, v in _best_excl.items()
    }

    shinki_tanto_store: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (store, ym), members in shinki.items():
        for member in members:
            # 初回以外セッションがある場合はその最多担当、なければ初回担当
            sid = primary_staff_excl_first.get((member, store, ym))
            if sid is None:
                sid = first_staff.get((member, ym, store))
            if sid:
                shinki_tanto_store[(sid, store, ym)].add(member)
    shinki_tanto_store_map: dict[tuple[str, str, str], int] = {
        k: len(v) for k, v in shinki_tanto_store.items()
    }
    print("  完了")

    # ---- 復帰担当数（復帰月の最多担当スタッフ・ws4統合版用）----
    print("復帰担当数計算中（ws4用）...")
    fukki_tanto_store: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    # ケース1: 離客→再来店（復帰月の最多担当スタッフ）
    for member, risseki_months in member_risseki.items():
        for rm in sorted(risseki_months):
            for vm, vs in member_all_visits.get(member, []):
                if vm > rm and vm not in EXCLUDE_MONTHS:
                    sid = primary_staff_store.get((member, vs, vm))
                    if sid:
                        fukki_tanto_store[(sid, vs, vm)].add(member)
                    break

    # ケース2: データ内に初回=1がない既存会員（最初来店月の最多担当スタッフ）
    for (store, member), months in member_visits.items():
        if (store, member) in has_shockai:
            continue
        fm = min(months)
        if fm not in EXCLUDE_MONTHS:
            sid = primary_staff_store.get((member, store, fm))
            if sid:
                fukki_tanto_store[(sid, store, fm)].add(member)

    fukki_tanto_store_map: dict[tuple[str, str, str], int] = {
        k: len(v) for k, v in fukki_tanto_store.items()
    }
    print("  完了")

    # ---- 担当引き継ぎ / 担当譲渡 ----
    print("担当変更計算中...")
    shinki_tanto_members: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in shinki_tanto_store.items():
        shinki_tanto_members[(store, ym)].update(members)

    fukki_tanto_members: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in fukki_tanto_store.items():
        fukki_tanto_members[(store, ym)].update(members)

    hikitsugi_store: dict[tuple[str, str, str], int] = defaultdict(int)
    joto_store:      dict[tuple[str, str, str], int] = defaultdict(int)

    for (member, store, ym), curr_sid in primary_staff_store.items():
        prev_ym  = prev_month(ym)
        prev_sid = primary_staff_store.get((member, store, prev_ym))
        if not prev_sid:
            continue
        non_cv_first      = shockai.get((store, ym),      set()) - shinki.get((store, ym),      set())
        non_cv_first_prev = shockai.get((store, prev_ym), set()) - shinki.get((store, prev_ym), set())
        if member in non_cv_first or member in non_cv_first_prev:
            continue
        # 新規担当・復帰担当は除外
        if (member in shinki_tanto_members.get((store, ym), set()) or
                member in fukki_tanto_members.get((store, ym), set())):
            continue
        if prev_sid != curr_sid:
            hikitsugi_store[(curr_sid, store, ym)] += 1
            joto_store[(prev_sid, store, ym)] += 1
    print("  完了")

    # ---- 統合版 スタッフ集計 ----
    print("統合版スタッフ集計中...")

    # 会員×統合店舗×月 のセッション数（来店頻度の分子用）
    member_ms_ym_sess: dict[tuple[str, str, str], int] = defaultdict(int)
    for (member, store, ym), sess in member_store_ym_sess.items():
        member_ms_ym_sess[(member, mkey(store), ym)] += sess

    # 統合店舗別 新規数/初回数（会員レベルでunion）
    merged_shinki_ss:  dict[tuple[str, str, str], set[str]] = defaultdict(set)
    merged_shockai_ss: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in shinki_staff_store.items():
        merged_shinki_ss[(sid, mkey(store), ym)].update(members)
    for (sid, store, ym), members in shockai_staff_store.items():
        merged_shockai_ss[(sid, mkey(store), ym)].update(members)

    # 統合店舗別 担当アクティブ会員（会員レベルでunion・重複排除）
    merged_active_ss_set: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in staff_store_active_set.items():
        merged_active_ss_set[(sid, mkey(store), ym)].update(members)
    merged_active_ss_count: dict[tuple[str, str, str], int] = {
        k: len(v) for k, v in merged_active_ss_set.items()
    }

    # 統合店舗別 担当会員の総セッション数（来店頻度用）
    merged_active_ss_sess: dict[tuple[str, str, str], int] = defaultdict(int)
    for (sid, ms, ym), members in merged_active_ss_set.items():
        for member in members:
            merged_active_ss_sess[(sid, ms, ym)] += member_ms_ym_sess[(member, ms, ym)]

    # 統合店舗別 指名・総セッション数（指名率用）
    merged_shimei_by_staff: dict[tuple[str, str, str], int] = defaultdict(int)
    merged_sess_by_staff:   dict[tuple[str, str, str], int] = defaultdict(int)
    for (sid, store, ym), cnt in shimei_by_staff.items():
        merged_shimei_by_staff[(sid, mkey(store), ym)] += cnt
    for (sid, store, ym), cnt in sess_by_staff.items():
        merged_sess_by_staff[(sid, mkey(store), ym)] += cnt

    # 統合版 新規担当数
    merged_shinki_tanto: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in shinki_tanto_store.items():
        merged_shinki_tanto[(sid, mkey(store), ym)].update(members)
    merged_shinki_tanto_map: dict[tuple[str, str, str], int] = {
        k: len(v) for k, v in merged_shinki_tanto.items()
    }

    # 統合版 担当引き継ぎ / 担当譲渡（単純合算）
    merged_hikitsugi: dict[tuple[str, str, str], int] = defaultdict(int)
    merged_joto:      dict[tuple[str, str, str], int] = defaultdict(int)
    for (sid, store, ym), cnt in hikitsugi_store.items():
        merged_hikitsugi[(sid, mkey(store), ym)] += cnt
    for (sid, store, ym), cnt in joto_store.items():
        merged_joto[(sid, mkey(store), ym)] += cnt

    # 統合版 スタッフ別店舗リスト
    staff_by_store: dict[str, set[str]] = defaultdict(set)
    for sid, store, _ in shinki_staff_store:
        staff_by_store[store].add(sid)
    for sid, store, _ in shockai_staff_store:
        staff_by_store[store].add(sid)
    for sid, store, _ in staff_store_active_set:
        staff_by_store[store].add(sid)

    merged_staff_by_store: dict[str, set[str]] = defaultdict(set)
    for store, staff_set in staff_by_store.items():
        merged_staff_by_store[mkey(store)].update(staff_set)
    merged_store_list_for_staff = sorted(
        merged_staff_by_store.keys(),
        key=lambda s: int(s.split("+")[0]) if s.split("+")[0].isdigit() else 9999,
    )

    # 個別店舗リスト（ws5用）
    store_list_for_ws5 = sorted(
        staff_by_store.keys(),
        key=lambda s: int(s) if s.isdigit() else 9999,
    )

    print("  完了")

    # ---- ws4 専用: ファクトテーブルベースの離脱・復帰（統合店舗単位）----
    print("店舗別個人 ファクトベース離脱・復帰計算中...")

    # 統合店舗単位の来店月セット (ms, member) → set of ym
    member_ms_visits: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, member), months in member_visits.items():
        member_ms_visits[(mkey(store), member)].update(months)

    # 統合店舗単位の最多担当スタッフ（primary_staff_ms）
    _merged_sc: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for (member, store, ym, sid), cnt in session_count.items():
        _merged_sc[(member, mkey(store), ym, sid)] += cnt
    _best_ms: dict[tuple[str, str, str], tuple[int, str]] = {}
    for (member, ms, ym, sid), cnt in _merged_sc.items():
        k = (member, ms, ym)
        p = _best_ms.get(k)
        if p is None or cnt > p[0] or (cnt == p[0] and sid < p[1]):
            _best_ms[k] = (cnt, sid)
    primary_staff_ms: dict[tuple[str, str, str], str] = {
        k: v[1] for k, v in _best_ms.items()
    }

    # 統合店舗・月ごとのアクティブ会員セット（担当会員ベース・非CV初回者除外済み）
    ms_month_active: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (sid, ms, ym), members in merged_active_ss_set.items():
        ms_month_active[(ms, ym)].update(members)

    # 会員ごとのアクティブ月セット（per merged store）
    member_ms_active_months: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (ms, ym), members in ms_month_active.items():
        for member in members:
            member_ms_active_months[(ms, member)].add(ym)

    # 統合店舗の has_shockai（初回=1 がある (ms, member) セット）
    has_shockai_ms: set[tuple[str, str]] = {
        (mkey(store), member) for (store, member) in has_shockai
    }

    # 離脱（ファクトベース）: 今月アクティブ → 来月来店なし → 来月の離脱者
    ws4_risseki: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (ms, ym), members in ms_month_active.items():
        next_ym = next_month(ym)
        if not (WS4_OUT_START <= next_ym <= WS4_OUT_END):
            continue
        for member in members:
            if next_ym not in member_ms_visits.get((ms, member), set()):
                sid = primary_staff_ms.get((member, ms, ym))
                if sid:
                    ws4_risseki[(sid, ms, next_ym)].add(member)

    # 復帰（ファクトベース）
    # Case1: 今月アクティブ & 先月アクティブでない & 以前(2025/4+)にアクティブ月あり
    # Case2: 今月が初登場(2025/5+) & 初回=1なし（データ開始前からの既存会員）
    ws4_fukki: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (ms, ym), members in ms_month_active.items():
        if not (WS4_OUT_START <= ym <= WS4_OUT_END):
            continue
        prev_ym = prev_month(ym)
        for member in members:
            if member in ms_month_active.get((ms, prev_ym), set()):
                continue  # 先月もアクティブ → 継続
            active_months = member_ms_active_months.get((ms, member), set())
            has_prior  = any(m < prev_ym for m in active_months)
            is_first   = (min(active_months) == ym)
            no_shockai = (ms, member) not in has_shockai_ms
            if has_prior or (is_first and no_shockai):
                sid = primary_staff_ms.get((member, ms, ym))
                if sid:
                    ws4_fukki[(sid, ms, ym)].add(member)

    ws4_risseki_map: dict[tuple[str, str, str], int] = {k: len(v) for k, v in ws4_risseki.items()}
    ws4_fukki_map:   dict[tuple[str, str, str], int] = {k: len(v) for k, v in ws4_fukki.items()}
    print("  完了")

    # ---- ws5 専用: ファクトテーブルベースの離脱・復帰（個別店舗単位）----
    print("店舗別個人（個別） ファクトベース離脱・復帰計算中...")

    # 個別店舗・月ごとのアクティブ会員セット（担当会員ベース・非CV初回者除外済み）
    store_month_active: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (sid, store, ym), members in staff_store_active_set.items():
        store_month_active[(store, ym)].update(members)

    # 会員ごとのアクティブ月セット（per individual store）
    member_store_active_months: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (store, ym), members in store_month_active.items():
        for member in members:
            member_store_active_months[(store, member)].add(ym)

    # 離脱（ファクトベース）: 今月アクティブ → 来月来店なし → 来月の離脱者（個別店舗単位）
    ws5_risseki: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (store, ym), members in store_month_active.items():
        next_ym = next_month(ym)
        if not (WS4_OUT_START <= next_ym <= WS4_OUT_END):
            continue
        for member in members:
            if next_ym not in member_visits.get((store, member), set()):
                sid = primary_staff_store.get((member, store, ym))
                if sid:
                    ws5_risseki[(sid, store, next_ym)].add(member)

    # 復帰（ファクトベース）（個別店舗単位）
    # Case1: 今月アクティブ & 先月アクティブでない & 以前にアクティブ月あり
    # Case2: 今月が初登場(2025/5+) & 初回=1なし（データ開始前からの既存会員）
    ws5_fukki: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (store, ym), members in store_month_active.items():
        if not (WS4_OUT_START <= ym <= WS4_OUT_END):
            continue
        prev_ym = prev_month(ym)
        for member in members:
            if member in store_month_active.get((store, prev_ym), set()):
                continue  # 先月もアクティブ → 継続
            active_months = member_store_active_months.get((store, member), set())
            has_prior  = any(m < prev_ym for m in active_months)
            is_first   = (min(active_months) == ym)
            no_shockai = (store, member) not in has_shockai
            if has_prior or (is_first and no_shockai):
                sid = primary_staff_store.get((member, store, ym))
                if sid:
                    ws5_fukki[(sid, store, ym)].add(member)

    ws5_risseki_map: dict[tuple[str, str, str], int] = {k: len(v) for k, v in ws5_risseki.items()}
    ws5_fukki_map:   dict[tuple[str, str, str], int] = {k: len(v) for k, v in ws5_fukki.items()}
    print("  完了")

    # ---- 月リスト ----
    all_keys   = shinki.keys() | shockai.keys()
    all_months = sorted({ym for _, ym in all_keys})
    p1_months = [ym for ym in all_months if ym <= CUT_MONTH and ym not in EXCLUDE_MONTHS]
    p2_months = [ym for ym in all_months if ym >  CUT_MONTH and ym not in EXCLUDE_MONTHS]

    ws4_p1_months = [ym for ym in all_months if WS4_OUT_START <= ym <= CUT_MONTH]
    ws4_p2_months = [ym for ym in all_months if CUT_MONTH < ym <= WS4_OUT_END]

    # ---- スタッフリスト（スタッフID昇順）----
    staff_list = sorted(
        {sid for sid, _ in shinki_staff}
        | {sid for sid, _ in shockai_staff}
        | {sid for sid, _ in fukki_staff_map}
        | {sid for sid, _ in risseki_count_staff},
        key=lambda s: int(s) if s.isdigit() else 9999,
    )

    def get_staff_label(sid: str) -> str:
        name = staff_names.get(sid, "")
        return f"{name}（{sid}）" if name else f"スタッフ{sid}"

    # ---- 書き込み ----
    print("\n結果を書き込み中...")

    rows_staff = make_sheet_rows(
        staff_list, shinki_staff, shockai_staff,
        fukki_staff_map, risseki_count_staff,
        p1_months, p2_months, get_staff_label,
        active_users=None,
        col2_header="スタッフID",
        all_label="（全スタッフ合計）",
        definition_rows=DEFINITION_ROWS_STAFF,
    )
    try:
        ws3 = ss.worksheet("数字整理（個人）")
        ws3.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws3 = ss.add_worksheet(title="数字整理（個人）", rows=5000, cols=9)
    ws3.update(rows_staff, "A1")
    ws3.format("E:E", {"horizontalAlignment": "RIGHT"})
    print("  ✅ 「数字整理（個人）」シート更新完了")

    rows_store_staff = make_store_staff_rows(
        merged_store_list_for_staff,
        merged_staff_by_store,
        merged_shinki_ss,
        merged_shockai_ss,
        merged_shinki_tanto_map,
        ws4_fukki_map,       # 復帰担当数（ファクトベース）
        ws4_risseki_map,     # 離脱数（ファクトベース）
        merged_hikitsugi,
        merged_joto,
        merged_active_ss_count,
        merged_active_ss_sess,
        merged_shimei_by_staff,
        merged_sess_by_staff,
        ws4_p1_months,
        ws4_p2_months,
        staff_names,
        get_merged_label,
    )
    try:
        ws4 = ss.worksheet("数字整理（店舗別個人）")
        ws4.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws4 = ss.add_worksheet(title="数字整理（店舗別個人）", rows=8000, cols=15)
    ws4.update(rows_store_staff, "A1")
    ws4.format("E:E", {"horizontalAlignment": "RIGHT"})
    ws4.format("J:J", {"horizontalAlignment": "RIGHT"})
    ws4.format("N:O", {"horizontalAlignment": "RIGHT"})
    print("  ✅ 「数字整理（店舗別個人）」シート更新完了")

    rows_store_staff_indiv = make_store_staff_rows(
        store_list_for_ws5,
        staff_by_store,
        shinki_staff_store,
        shockai_staff_store,
        shinki_tanto_store_map,
        ws5_fukki_map,       # 復帰担当数（ファクトベース・個別店舗）
        ws5_risseki_map,     # 離脱数（ファクトベース・個別店舗）
        hikitsugi_store,
        joto_store,
        staff_store_active_count,
        staff_store_active_sess,
        shimei_by_staff,
        sess_by_staff,
        ws4_p1_months,
        ws4_p2_months,
        staff_names,
        get_store_label,
    )
    try:
        ws5 = ss.worksheet("数字整理（店舗別個人・個別）")
        ws5.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws5 = ss.add_worksheet(title="数字整理（店舗別個人・個別）", rows=10000, cols=15)
    ws5.update(rows_store_staff_indiv, "A1")
    ws5.format("E:E", {"horizontalAlignment": "RIGHT"})
    ws5.format("J:J", {"horizontalAlignment": "RIGHT"})
    ws5.format("N:O", {"horizontalAlignment": "RIGHT"})
    print("  ✅ 「数字整理（店舗別個人・個別）」シート更新完了")

    print(f"\n   個人別: {len(staff_list)}スタッフ")
    print(f"   店舗別個人（統合）: {len(merged_store_list_for_staff)}統合店舗")
    print(f"   店舗別個人（個別）: {len(store_list_for_ws5)}店舗")
    print(f"   出力期間: {WS4_OUT_START[:4]}/{WS4_OUT_START[4:]}〜{WS4_OUT_END[:4]}/{WS4_OUT_END[4:]}")


if __name__ == "__main__":
    main()

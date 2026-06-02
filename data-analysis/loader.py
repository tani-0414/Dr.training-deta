"""
loader.py — データ読み込み共通処理

Google Sheets から取引データ・離脱者データを読み込み、
各分析スクリプトが使う共通データ構造を返す。

使い方:
    from loader import load_transaction_data, load_risseki_data
    txn = load_transaction_data(gc)
    risseki = load_risseki_data(gc)
"""

import gspread
from collections import defaultdict
from itertools import permutations
from datetime import datetime

from config import (
    SHINKI_SS_ID, RISSEKI_SS_ID,
    EXCLUDE_MONTHS, PAIRED_STORES, STORE_GROUPS,
)
from utils import parse_date, mkey, prev_month, within_next_month


def load_risseki_data(gc: gspread.Client) -> dict:
    """
    離脱者データを読み込む。

    Returns:
        {
            "risseki_history":     dict[(store, member), set[month]],
            "risseki_by_sm":       dict[(store, month), set[member]],
            "risseki_last_visit":  dict[(store, member), datetime],
            "risseki_staff_by_sm": dict[(staff, month), set[member]],
            "risseki_staff_store": dict[(staff, store, month), set[member]],
        }
    """
    print("離脱者データ読み込み中...")
    rss  = gc.open_by_key(RISSEKI_SS_ID)
    rws  = rss.worksheets()[0]
    rows = rws.get_all_values()
    col  = {h: i for i, h in enumerate(rows[0])}

    risseki_history    = defaultdict(set)
    risseki_by_sm      = defaultdict(set)
    risseki_last_visit = {}
    risseki_staff_by_sm   = defaultdict(set)
    risseki_staff_store   = defaultdict(set)

    for row in rows[1:]:
        try:
            if row[col["判定"]] != "離客":
                continue
            month      = row[col["対象月"]]
            store      = row[col["店舗コード"]]
            member     = row[col["会員ID"]]
            lv         = parse_date(row[col["直近来店日"]])
            staff_code = row[col["直近担当者コード"]]
            if month == "202605":
                continue
            if month and store and member:
                risseki_history[(store, member)].add(month)
                risseki_by_sm[(store, month)].add(member)
                if lv and ((store, member) not in risseki_last_visit
                           or lv > risseki_last_visit[(store, member)]):
                    risseki_last_visit[(store, member)] = lv
            if month and staff_code and member and month not in EXCLUDE_MONTHS:
                risseki_staff_by_sm[(staff_code, month)].add(member)
            if month and store and staff_code and member and month not in EXCLUDE_MONTHS:
                risseki_staff_store[(staff_code, store, month)].add(member)
        except IndexError:
            continue

    print(f"  完了: 離脱履歴 {len(risseki_history)}件")
    return {
        "risseki_history":    dict(risseki_history),
        "risseki_by_sm":      dict(risseki_by_sm),
        "risseki_last_visit": risseki_last_visit,
        "risseki_staff_by_sm":  {k: v for k, v in risseki_staff_by_sm.items()},
        "risseki_staff_store":  {k: v for k, v in risseki_staff_store.items()},
    }


def load_transaction_data(gc: gspread.Client) -> dict:
    """
    取引データを読み込み、分析に必要な集計データ構造を返す。

    Returns:
        {
            "shinki":              dict[(store, ym), set[member]],  # CV=1の初回会員
            "shockai":             dict[(store, ym), set[member]],  # 全初回会員
            "member_visits":       dict[(store, member), set[ym]],  # 来店月セット
            "paired_visits":       dict[(store, member), list[datetime]],
            "staff_names":         dict[staff_id, name],
            "shinki_staff":        dict[(staff, ym), set[member]],
            "shockai_staff":       dict[(staff, ym), set[member]],
            "first_staff":         dict[(member, ym, store), staff_id],
            "session_count":       dict[(member, store, ym, staff), int],
            "member_store_ym_sess":dict[(member, store, ym), int],
            "shimei_by_staff":     dict[(staff, store, ym), int],
            "sess_by_staff":       dict[(staff, store, ym), int],
            "shinki_staff_store":  dict[(staff, store, ym), set[member]],
            "shockai_staff_store": dict[(staff, store, ym), set[member]],
            "session_count_excl_first": dict[(member, store, ym, staff), int],
        }
    """
    print("取引データ読み込み中...")
    ss       = gc.open_by_key(SHINKI_SS_ID)
    ws       = ss.worksheets()[0]
    print("  全データ読み込み中... しばらくお待ちください")
    all_rows = ws.get_all_values()
    headers  = all_rows[0]
    data     = all_rows[1:]
    print(f"  読み込み完了: {len(data)}件")
    col = {h: i for i, h in enumerate(headers)}

    shinki        = defaultdict(set)
    shockai       = defaultdict(set)
    member_visits = defaultdict(set)
    paired_visits = defaultdict(list)

    staff_names   = {}
    shinki_staff  = defaultdict(set)
    shockai_staff = defaultdict(set)
    first_staff_dt = {}

    session_count             = defaultdict(int)
    member_store_ym_sess      = defaultdict(int)
    shimei_by_staff           = defaultdict(int)
    sess_by_staff             = defaultdict(int)
    shinki_staff_store        = defaultdict(set)
    shockai_staff_store       = defaultdict(set)
    session_count_excl_first  = defaultdict(int)

    for row in data:
        try:
            if row[col["取消区分 (0:通常、1：取消)"]] == "1":
                continue
            ym     = row[col["年月"]]
            store  = row[col["店舗コード"]]
            member = row[col["会員ID"]]
            if not (ym and store and member):
                continue

            staff_id   = row[col["スタッフID"]]
            staff_name = row[col["スタッフ名"]]
            if staff_id and staff_name:
                staff_names[staff_id] = staff_name

            member_store_ym_sess[(member, store, ym)] += 1
            if staff_id:
                session_count[(member, store, ym, staff_id)] += 1
                sess_by_staff[(staff_id, store, ym)] += 1
                if row[col["指名有無"]] == "1":
                    shimei_by_staff[(staff_id, store, ym)] += 1
                if row[col["初回"]] != "1":
                    session_count_excl_first[(member, store, ym, staff_id)] += 1

            member_visits[(store, member)].add(ym)

            txn_dt = parse_date(row[col["取引日時"]])
            if store in PAIRED_STORES and txn_dt:
                paired_visits[(store, member)].append(txn_dt)

            if txn_dt and staff_id:
                k = (member, ym, store)
                if k not in first_staff_dt or txn_dt < first_staff_dt[k][0]:
                    first_staff_dt[k] = (txn_dt, staff_id)

            if row[col["初回"]] == "1":
                shockai[(store, ym)].add(member)
                if staff_id:
                    shockai_staff[(staff_id, ym)].add(member)
                    shockai_staff_store[(staff_id, store, ym)].add(member)
                if row[col["CV有無"]] == "1":
                    shinki[(store, ym)].add(member)
                    if staff_id:
                        shinki_staff[(staff_id, ym)].add(member)
                        shinki_staff_store[(staff_id, store, ym)].add(member)
        except IndexError:
            continue

    first_staff = {k: v[1] for k, v in first_staff_dt.items()}

    return {
        "ss":                       ss,
        "shinki":                   dict(shinki),
        "shockai":                  dict(shockai),
        "member_visits":            dict(member_visits),
        "paired_visits":            dict(paired_visits),
        "staff_names":              staff_names,
        "shinki_staff":             dict(shinki_staff),
        "shockai_staff":            dict(shockai_staff),
        "first_staff":              first_staff,
        "session_count":            dict(session_count),
        "member_store_ym_sess":     dict(member_store_ym_sess),
        "shimei_by_staff":          dict(shimei_by_staff),
        "sess_by_staff":            dict(sess_by_staff),
        "shinki_staff_store":       dict(shinki_staff_store),
        "shockai_staff_store":      dict(shockai_staff_store),
        "session_count_excl_first": dict(session_count_excl_first),
    }

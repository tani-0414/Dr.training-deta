#!/usr/bin/env python3
"""
app.py — Dr.training 実績ダッシュボード

起動方法（ローカル）:
    streamlit run app.py

デプロイ（Streamlit Community Cloud）:
    1. このリポジトリを GitHub に push
    2. share.streamlit.io でデプロイ設定
    3. gspread 認証をサービスアカウントに切り替え（下記 load_data() を参照）
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "data-analysis"))

import re
import streamlit as st
import pandas as pd
import gspread
import plotly.express as px
from datetime import datetime

from config import SHINKI_SS_ID, MERGED_NAMES
from store.generate_report import (
    parse_store_sheet,
    parse_staff_sheet_all,
    fmt_month,
    _float,
)

# ── ページ設定 ────────────────────────────────────────────────
st.set_page_config(
    page_title="Dr.training 実績ダッシュボード",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  [data-testid="stMetricDelta"] svg { display: none; }
  .block-container { padding-top: 1.5rem; }
  div[data-testid="metric-container"] { background: #f8f9fa; border-radius: 8px; padding: 12px 16px; }
</style>
""", unsafe_allow_html=True)


# ── データ読み込み ────────────────────────────────────────────
def _gc() -> gspread.Client:
    """環境に応じて gspread クライアントを返す。
    Streamlit Cloud: st.secrets の gcp_service_account を使用
    ローカル:        ~/.config/gspread の OAuth トークンを使用
    """
    if "gcp_service_account" in st.secrets:
        return gspread.service_account_from_dict(st.secrets["gcp_service_account"])
    return gspread.oauth()


@st.cache_data(ttl=86400, show_spinner="📊 集計データを読み込み中...")
def load_data() -> tuple[dict, dict, str]:
    """Google Sheets から全データを取得（1日キャッシュ）"""
    gc = _gc()
    ss = gc.open_by_key(SHINKI_SS_ID)
    store_rows = ss.worksheet("統合版").get_all_values()
    staff_rows = ss.worksheet("店舗別個人・統合版").get_all_values()
    loaded_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return parse_store_sheet(store_rows), parse_staff_sheet_all(staff_rows), loaded_at


@st.cache_data(ttl=60, show_spinner=False)
def load_comments() -> pd.DataFrame:
    """コメントシートを読み込む（1分キャッシュ）"""
    try:
        gc = _gc()
        ss = gc.open_by_key(SHINKI_SS_ID)
        try:
            ws = ss.worksheet("コメント")
        except gspread.exceptions.WorksheetNotFound:
            return pd.DataFrame(columns=["投稿日時", "対象期間", "店舗", "スタッフ", "投稿者", "コメント"])
        rows = ws.get_all_values()
        if len(rows) <= 1:
            return pd.DataFrame(columns=["投稿日時", "対象期間", "店舗", "スタッフ", "投稿者", "コメント"])
        return pd.DataFrame(rows[1:], columns=rows[0])
    except Exception:
        return pd.DataFrame(columns=["投稿日時", "対象期間", "店舗", "スタッフ", "投稿者", "コメント"])


def post_comment(period: str, store: str, staff: str, author: str, text: str) -> bool:
    """コメントを Google Sheets のコメントシートに追記する"""
    try:
        gc = _gc()
        ss = gc.open_by_key(SHINKI_SS_ID)
        try:
            ws = ss.worksheet("コメント")
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title="コメント", rows=2000, cols=6)
            ws.append_row(["投稿日時", "対象期間", "店舗", "スタッフ", "投稿者", "コメント"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        ws.append_row([now, period, store, staff, author, text])
        return True
    except Exception:
        return False


def render_comments(
    period_key: str,
    store_label: str,
    staff_label: str = "",
) -> None:
    """コメント一覧と投稿フォームを表示する"""
    st.subheader("💬 コメント")

    comments_df = load_comments()

    # 表示対象を絞り込む
    if not comments_df.empty:
        mask = (
            (comments_df["対象期間"] == period_key) &
            (comments_df["店舗"] == store_label)
        )
        if staff_label:
            mask &= (comments_df["スタッフ"] == staff_label)
        filtered = comments_df[mask].sort_values("投稿日時", ascending=False)
    else:
        filtered = comments_df

    # コメント一覧
    if filtered.empty:
        st.caption("まだコメントはありません")
    else:
        for _, row in filtered.iterrows():
            with st.container():
                col_a, col_b = st.columns([1, 5])
                with col_a:
                    st.markdown(f"**{row['投稿者']}**  \n{row['投稿日時'][:10]}")
                with col_b:
                    st.markdown(row["コメント"])
                st.divider()

    # 投稿フォーム
    form_key = f"cf_{period_key}_{store_label}_{staff_label}"
    with st.form(key=form_key, clear_on_submit=True):
        author = st.text_input("投稿者名")
        text   = st.text_area("コメント内容", height=100)
        if st.form_submit_button("📨 送信"):
            if not author.strip():
                st.warning("投稿者名を入力してください")
            elif not text.strip():
                st.warning("コメントを入力してください")
            else:
                ok = post_comment(period_key, store_label, staff_label, author.strip(), text.strip())
                if ok:
                    load_comments.clear()
                    st.success("投稿しました！")
                    st.rerun()
                else:
                    st.error("投稿に失敗しました。再度お試しください。")


@st.cache_data(ttl=86400, show_spinner="👥 会員データを読み込み中...")
def load_member_data() -> pd.DataFrame:
    """ファクトテーブルから必要列だけ取得（1日キャッシュ）"""
    gc = _gc()
    ss = gc.open_by_key(SHINKI_SS_ID)
    ws = ss.worksheets()[0]
    all_rows = ws.get_all_values()
    headers = all_rows[0]
    col = {h: i for i, h in enumerate(headers)}

    keep = ["取引日時", "年月", "店舗コード", "会員ID", "姓", "名", "会員ランク名",
            "スタッフID", "スタッフ名", "初回", "CV有無",
            "取消区分 (0:通常、1：取消)"]
    missing = [c for c in keep if c not in col]
    if missing:
        raise ValueError(f"列が見つかりません: {missing}")

    rows = [
        {c: row[col[c]] for c in keep}
        for row in all_rows[1:]
        if row[col["取消区分 (0:通常、1：取消)"]] != "1"
    ]
    return pd.DataFrame(rows)


# ── 表示名ヘルパー ────────────────────────────────────────────

def clean_label(label: str) -> str:
    """UIの店舗名から末尾の数字コードを除去する
    例: 「学芸大学店（7）」→「学芸大学店」、「中目黒（統合）」→「中目黒（統合）」
    """
    return re.sub(r'（\d+(?:\+\d+)*）$', '', label)


# ── 店舗コード解決 ────────────────────────────────────────────

def get_store_codes(store_label: str) -> list[str]:
    """店舗ラベル → 個別店舗コードのリスト
    例: 「学芸大学店（7）」→ ["7"]  /  「中目黒（統合）」→ ["1","37"]
    """
    m = re.search(r'（(\d+)）$', store_label)
    if m:
        return [m.group(1)]
    for key, name in MERGED_NAMES.items():
        if name == store_label:
            return key.split("+")
    return []


# ── 会員一覧ビルダー ──────────────────────────────────────────

def build_member_df(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    ym_range: list[str],
    staff_id_filter: str | None = None,
) -> pd.DataFrame:
    if fact_df.empty or not store_codes:
        return pd.DataFrame()

    filtered = fact_df[
        fact_df["店舗コード"].isin(store_codes) &
        fact_df["年月"].isin(set(ym_range))
    ].copy()
    if filtered.empty:
        return pd.DataFrame()

    filtered["会員名"] = (
        filtered["姓"].fillna("") + " " + filtered["名"].fillna("")
    ).str.strip().where(
        lambda s: s != "",
        other=filtered["会員ID"],
    )

    sessions  = filtered.groupby("会員ID").size().rename("セッション数")
    months    = filtered.groupby("会員ID")["年月"].nunique().rename("来店月数")
    info      = filtered.sort_values("年月").groupby("会員ID").last()[["会員名", "会員ランク名"]]

    # 担当スタッフ（最多セッション）
    has_staff = filtered[filtered["スタッフID"].notna() & (filtered["スタッフID"] != "")]
    if not has_staff.empty:
        cnt = has_staff.groupby(["会員ID", "スタッフID", "スタッフ名"]).size().reset_index(name="n")
        primary = (
            cnt.loc[cnt.groupby("会員ID")["n"].idxmax()]
            [["会員ID", "スタッフID", "スタッフ名"]]
            .set_index("会員ID")
        )
    else:
        primary = pd.DataFrame(columns=["スタッフID", "スタッフ名"])
        primary.index.name = "会員ID"

    # ステータス
    first_cv = filtered[filtered["初回"] == "1"].groupby("会員ID")["CV有無"].max()

    def status(mid: str) -> str:
        if mid not in first_cv.index:
            return "アクティブ"
        return "新規獲得" if first_cv[mid] == "1" else "新規（未CV）"

    result = info.join(sessions).join(months).join(primary).reset_index()
    result["ステータス"] = result["会員ID"].apply(status)
    result = result.rename(columns={"会員ランク名": "ランク", "スタッフ名": "担当スタッフ"})

    if staff_id_filter and "スタッフID" in result.columns:
        result = result[result["スタッフID"] == staff_id_filter]

    cols = ["会員名", "ランク", "ステータス", "セッション数", "来店月数", "担当スタッフ"]
    result = result[[c for c in cols if c in result.columns]]
    result = result.sort_values("セッション数", ascending=False).reset_index(drop=True)
    result.index += 1
    result.index.name = "#"
    return result


def build_monthly_store_df(
    store_data: dict,
    store_label: str,
    ym_range: list[str],
) -> pd.DataFrame:
    """選択期間の店舗月別データを返す（期間モード専用）"""
    rows = []
    for ym in ym_range:
        d = store_data.get(store_label, {}).get(ym)
        if d:
            rows.append({
                "月":           fmt_month(ym),
                "アクティブ会員数": d.get("active"),
                "新規獲得数":   d.get("shinki_cv"),
                "復帰数":       d.get("fukki"),
                "離脱数":       d.get("risseki"),
                "CVR(%)":      _float(d.get("cvr")),
                "会員増減数":   d.get("zougen"),
            })
    return pd.DataFrame(rows)


def build_monthly_staff_df(
    all_staff_data: dict,
    store_label: str,
    staff_label: str,
    ym_range: list[str],
) -> pd.DataFrame:
    """選択期間のスタッフ月別データを返す（期間モード専用）"""
    rows = []
    for ym in ym_range:
        d = all_staff_data.get(store_label, {}).get(staff_label, {}).get(ym)
        if d:
            rows.append({
                "月":         fmt_month(ym),
                "担当会員数": d.get("active"),
                "新規担当数": d.get("shinki_tanto"),
                "復帰担当数": d.get("fukki_tanto"),
                "離脱数":     d.get("risseki"),
                "CVR(%)":    _float(d.get("cvr")),
                "会員増減数": d.get("zougen"),
            })
    return pd.DataFrame(rows)


def get_monthly_store_sessions(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    ym_range: list[str],
) -> dict[str, int]:
    """月別の店舗セッション数 {ym: count}"""
    mask = fact_df["店舗コード"].isin(store_codes) & fact_df["年月"].isin(set(ym_range))
    return fact_df[mask].groupby("年月").size().to_dict()


def get_monthly_staff_sessions(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    staff_id: str,
    ym_range: list[str],
) -> dict[str, int]:
    """月別のスタッフセッション数 {ym: count}"""
    mask = (
        fact_df["店舗コード"].isin(store_codes) &
        fact_df["年月"].isin(set(ym_range)) &
        (fact_df["スタッフID"] == staff_id)
    )
    return fact_df[mask].groupby("年月").size().to_dict()


def render_trend_charts(df: pd.DataFrame) -> None:
    """担当本数・CVR・離脱数の月別推移グラフを描画する"""
    if df.empty:
        st.info("グラフを表示するデータがありません")
        return

    _layout = dict(margin=dict(l=0, r=10, t=30, b=10), xaxis_title="", yaxis_title="", height=280)

    c1, c2, c3 = st.columns(3)

    with c1:
        st.caption("担当本数")
        if "担当本数" in df.columns and df["担当本数"].notna().any():
            fig = px.bar(
                df, x="月", y="担当本数",
                color_discrete_sequence=["#4a90d9"],
            )
            fig.update_layout(**_layout)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("データなし")

    with c2:
        st.caption("CVR(%)")
        if "CVR(%)" in df.columns and df["CVR(%)"].notna().any():
            fig2 = px.line(
                df, x="月", y="CVR(%)",
                markers=True,
                color_discrete_sequence=["#f0a500"],
            )
            fig2.update_traces(line_width=2.5, marker_size=8)
            fig2.update_layout(**_layout)
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("データなし")

    with c3:
        st.caption("離脱数")
        if "離脱数" in df.columns and df["離脱数"].notna().any():
            fig3 = px.bar(
                df, x="月", y="離脱数",
                color_discrete_sequence=["#e05c5c"],
            )
            fig3.update_layout(**_layout)
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("データなし")


def style_member_df(df: pd.DataFrame):
    def color_status(val: str) -> str:
        if val == "新規獲得":
            return "background-color:#d4edda;color:#155724"
        if val == "新規（未CV）":
            return "background-color:#fff3cd;color:#856404"
        return ""
    return df.style.map(color_status, subset=["ステータス"])


# ── セッション数集計 ──────────────────────────────────────────

def get_store_session_count(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    ym_range: list[str],
) -> int:
    """店舗の期間内総セッション数"""
    mask = fact_df["店舗コード"].isin(store_codes) & fact_df["年月"].isin(set(ym_range))
    return int(mask.sum())


def get_staff_session_counts(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    ym_range: list[str],
) -> dict[str, int]:
    """スタッフIDごとの期間内セッション数 {staff_id: count}"""
    mask = (
        fact_df["店舗コード"].isin(store_codes) &
        fact_df["年月"].isin(set(ym_range)) &
        fact_df["スタッフID"].notna() &
        (fact_df["スタッフID"] != "")
    )
    if not mask.any():
        return {}
    return fact_df[mask].groupby("スタッフID").size().to_dict()


# ── 週次集計 ─────────────────────────────────────────────────

def _parse_dt(fact_df: pd.DataFrame) -> pd.DataFrame:
    """取引日時を datetime に変換して週開始日（月曜）を付与する"""
    df = fact_df.copy()
    df["_dt"] = pd.to_datetime(df["取引日時"], errors="coerce")
    df = df.dropna(subset=["_dt"])
    df["_week"] = df["_dt"].dt.to_period("W-SUN").apply(lambda p: p.start_time)
    return df


def _week_label(start: "pd.Timestamp") -> str:
    end = start + pd.Timedelta(days=6)
    return f"{start.month}/{start.day}〜{end.month}/{end.day}"


def build_weekly_store_df(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    n_weeks: int = 12,
) -> pd.DataFrame:
    """店舗の週別メトリクスを返す（直近 n_weeks 週）"""
    df = _parse_dt(fact_df[fact_df["店舗コード"].isin(store_codes)])
    if df.empty:
        return pd.DataFrame()

    records = []
    for week_start, grp in df.groupby("_week"):
        shinki   = grp[grp["初回"] == "1"]["会員ID"].nunique()
        cv       = grp[(grp["初回"] == "1") & (grp["CV有無"] == "1")]["会員ID"].nunique()
        records.append({
            "_week_start": week_start,
            "週":          _week_label(week_start),
            "来店会員数":  grp["会員ID"].nunique(),
            "セッション数": len(grp),
            "新規数":      shinki,
            "新規獲得数":  cv,
            "CVR(%)":     round(cv / shinki * 100, 1) if shinki > 0 else None,
        })

    result = (
        pd.DataFrame(records)
        .sort_values("_week_start", ascending=False)
        .head(n_weeks)
        .sort_values("_week_start")
        .drop(columns=["_week_start"])
        .reset_index(drop=True)
    )
    result.index += 1
    result.index.name = "#"
    return result


def build_weekly_staff_df(
    fact_df: pd.DataFrame,
    store_codes: list[str],
    n_weeks: int = 12,
) -> pd.DataFrame:
    """スタッフ × 週のセッション数・CVRを返す（直近 n_weeks 週）"""
    df = _parse_dt(fact_df[
        fact_df["店舗コード"].isin(store_codes) &
        fact_df["スタッフID"].notna() &
        (fact_df["スタッフID"] != "")
    ])
    if df.empty:
        return pd.DataFrame()

    # 対象週を絞る
    top_weeks = sorted(df["_week"].unique(), reverse=True)[:n_weeks]
    df = df[df["_week"].isin(top_weeks)]

    records = []
    for (week_start, sid), grp in df.groupby(["_week", "スタッフID"]):
        shinki = grp[grp["初回"] == "1"]["会員ID"].nunique()
        cv     = grp[(grp["初回"] == "1") & (grp["CV有無"] == "1")]["会員ID"].nunique()
        staff_name = grp["スタッフ名"].iloc[0] if "スタッフ名" in grp.columns else sid
        records.append({
            "_week_start": week_start,
            "週":          _week_label(week_start),
            "スタッフ":    f"{staff_name}（{sid}）",
            "セッション数": len(grp),
            "来店会員数":  grp["会員ID"].nunique(),
            "新規数":      shinki,
            "新規獲得数":  cv,
            "CVR(%)":     round(cv / shinki * 100, 1) if shinki > 0 else None,
        })

    return (
        pd.DataFrame(records)
        .sort_values(["スタッフ", "_week_start"])
        .drop(columns=["_week_start"])
        .reset_index(drop=True)
    )


# ── 期間集計 ─────────────────────────────────────────────────

def _aggregate_store_months(monthly_dicts: list[dict]) -> dict:
    """複数月の店舗データを集計する（フロー指標は合計、ストック指標は平均）"""
    sum_keys = ("shinki", "shinki_cv", "fukki", "risseki", "zougen")
    sums = {k: sum(r.get(k) or 0 for r in monthly_dicts) for k in sum_keys}

    active_vals = [r["active"] for r in monthly_dicts if r.get("active") is not None]
    avg_active = round(sum(active_vals) / len(active_vals)) if active_vals else None

    cvr          = f"{sums['shinki_cv']/sums['shinki']*100:.1f}%" if sums["shinki"] > 0 else "-"
    risseki_rate = f"{sums['risseki']/avg_active*100:.1f}%" if avg_active else "-"

    return {
        "active":       avg_active,
        "shinki":       sums["shinki"],
        "shinki_cv":    sums["shinki_cv"],
        "cvr":          cvr,
        "fukki":        sums["fukki"],
        "risseki":      sums["risseki"],
        "risseki_rate": risseki_rate,
        "zougen":       sums["zougen"],
    }


def _aggregate_staff_months(monthly_dicts: list[dict]) -> dict:
    """複数月のスタッフデータを集計する"""
    sum_keys = ("shinki", "shinki_cv", "shinki_tanto", "fukki_tanto",
                "risseki", "hikitsugi", "joto", "zougen")
    sums = {k: sum(r.get(k) or 0 for r in monthly_dicts) for k in sum_keys}

    active_vals = [r["active"] for r in monthly_dicts if r.get("active") is not None]
    avg_active = round(sum(active_vals) / len(active_vals)) if active_vals else None

    cvr          = f"{sums['shinki_cv']/sums['shinki']*100:.1f}%" if sums["shinki"] > 0 else "-"
    risseki_rate = f"{sums['risseki']/avg_active*100:.1f}%" if avg_active else "-"

    freq_vals = [_float(r["freq"]) for r in monthly_dicts if _float(r.get("freq")) is not None]
    avg_freq  = f"{sum(freq_vals)/len(freq_vals):.1f}" if freq_vals else "-"

    return {
        "active":       avg_active,
        "shinki":       sums["shinki"],
        "shinki_cv":    sums["shinki_cv"],
        "cvr":          cvr,
        "shinki_tanto": sums["shinki_tanto"],
        "fukki_tanto":  sums["fukki_tanto"],
        "risseki":      sums["risseki"],
        "risseki_rate": risseki_rate,
        "hikitsugi":    sums["hikitsugi"],
        "joto":         sums["joto"],
        "zougen":       sums["zougen"],
        "freq":         avg_freq,
    }


def get_effective_store(store_data: dict, ym_range: list[str]) -> dict[str, dict]:
    """期間内の全店舗集計データを返す"""
    result = {}
    for label, d in store_data.items():
        months = [d[ym] for ym in ym_range if ym in d]
        if months:
            result[label] = _aggregate_store_months(months)
    return result


def get_effective_staff(all_staff_data: dict, store_label: str, ym_range: list[str]) -> dict[str, dict]:
    """期間内の選択店舗スタッフ集計データを返す"""
    result = {}
    for staff_lbl, d in all_staff_data.get(store_label, {}).items():
        months = [d[ym] for ym in ym_range if ym in d]
        if months:
            result[staff_lbl] = _aggregate_staff_months(months)
    return result


def _avgs(effective: dict) -> dict:
    """有効データから各指標の平均値を計算する"""
    rows = list(effective.values())
    if not rows:
        return {}
    result: dict = {}
    all_keys = {k for r in rows for k in r}
    for k in all_keys:
        num_vals = [r[k] for r in rows if isinstance(r.get(k), (int, float))]
        flt_vals = [_float(r[k]) for r in rows if isinstance(r.get(k), str) and _float(r.get(k)) is not None]
        if num_vals:
            result[k] = sum(num_vals) / len(num_vals)
        elif flt_vals:
            result[k] = sum(flt_vals) / len(flt_vals)
    return result


# ── DataFrame ビルダー ────────────────────────────────────────

def _style_col(series: pd.Series, avg: float | None, higher_better: bool) -> list[str]:
    """2段階の色分け: 10%以上乖離 → 濃い色 / 2〜10% → 薄い色"""
    styles = []
    for val in series:
        if not isinstance(val, (int, float)) or avg is None:
            styles.append("")
            continue
        if avg == 0:
            styles.append("")
            continue
        ratio = (val - avg) / abs(avg)
        good  = ratio > 0 if higher_better else ratio < 0
        gap   = abs(ratio)
        if gap < 0.02:
            styles.append("")
        elif gap < 0.10:
            styles.append("background-color:#d4edda;color:#155724" if good else "background-color:#fde8c8;color:#7a3c00")
        else:
            styles.append("background-color:#c3e6cb;color:#0d4f20;font-weight:600" if good else "background-color:#f5b7b1;color:#6b0000;font-weight:700")
    return styles


def _count_bad(r: dict, avgs: dict) -> int:
    """平均を10%以上下回っている指標の数"""
    checks = [
        ("active",       avgs.get("active"),      True),
        ("shinki_cv",    avgs.get("shinki_cv"),   True),
        ("cvr",          avgs.get("cvr"),         True),
        ("risseki_rate", avgs.get("risseki_rate"),False),
        ("zougen",       avgs.get("zougen"),      True),
    ]
    n = 0
    for key, avg, hb in checks:
        val = _float(r.get(key)) if key in ("cvr", "risseki_rate") else r.get(key)
        if not isinstance(val, (int, float)) or not avg or avg == 0:
            continue
        ratio = (val - avg) / abs(avg)
        if (hb and ratio < -0.10) or (not hb and ratio > 0.10):
            n += 1
    return n


def build_store_df(effective: dict[str, dict], avgs: dict | None = None) -> pd.DataFrame:
    records = []
    for lbl, r in effective.items():
        n_bad = _count_bad(r, avgs) if avgs else 0
        badge = f"  ⚠️×{n_bad}" if n_bad >= 2 else ("  ⚠️" if n_bad == 1 else "")
        records.append({
            "店舗":          lbl + badge,
            "_raw":          lbl,
            "アクティブ会員": r.get("active"),
            "新規数":         r.get("shinki"),
            "新規獲得数":     r.get("shinki_cv"),
            "CVR(%)":        _float(r.get("cvr")),
            "離脱数":         r.get("risseki"),
            "離脱率(%)":      _float(r.get("risseki_rate")),
            "会員増減数":     r.get("zougen"),
        })
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("会員増減数", ascending=False, na_position="last").reset_index(drop=True)
        df.index += 1
        df.index.name = "順位"
    return df


def style_store_df(df: pd.DataFrame, avgs: dict):
    col_dir = {
        "アクティブ会員": ("active",       True),
        "新規数":         ("shinki",       True),
        "新規獲得数":     ("shinki_cv",    True),
        "CVR(%)":        ("cvr",          True),
        "離脱数":         ("risseki",      False),
        "離脱率(%)":      ("risseki_rate", False),
        "会員増減数":     ("zougen",       True),
    }
    styler = df.style.format(na_rep="—", precision=1)
    for col, (key, hb) in col_dir.items():
        if col in df.columns and avgs.get(key) is not None:
            styler = styler.apply(_style_col, avg=avgs[key], higher_better=hb, subset=[col])
    return styler


def build_staff_df(
    effective: dict[str, dict],
    session_counts: dict[str, int] | None = None,
) -> pd.DataFrame:
    records = []
    for lbl, r in effective.items():
        sid_m = re.search(r'（(\d+)）$', lbl)
        sid   = sid_m.group(1) if sid_m else None
        tanto_honsu = session_counts.get(sid) if session_counts and sid else None
        records.append({
            "スタッフ":   lbl,
            "担当会員数": r.get("active"),
            "担当本数":   tanto_honsu,
            "CVR(%)":    _float(r.get("cvr")),
            "新規担当数": r.get("shinki_tanto"),
            "復帰担当数": r.get("fukki_tanto"),
            "離脱数":     r.get("risseki"),
            "離脱率(%)":  _float(r.get("risseki_rate")),
            "来店頻度":   _float(r.get("freq")),
            "会員増減数": r.get("zougen"),
        })
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.sort_values("担当会員数", ascending=False, na_position="last").reset_index(drop=True)
        df.index += 1
        df.index.name = "#"
    return df


def style_staff_df(df: pd.DataFrame, avg_st: dict):
    col_dir = {
        "担当会員数": ("active",       True),
        "CVR(%)":    ("cvr",          True),
        "新規担当数": ("shinki_tanto", True),
        "復帰担当数": ("fukki_tanto",  True),
        "離脱数":     ("risseki",      False),
        "離脱率(%)":  ("risseki_rate", False),
        "来店頻度":   ("freq",         True),
        "会員増減数": ("zougen",       True),
    }
    styler = df.style.format(na_rep="—", precision=1)
    for col, (key, hb) in col_dir.items():
        if col in df.columns and avg_st.get(key) is not None:
            styler = styler.apply(_style_col, avg=avg_st[key], higher_better=hb, subset=[col])
    return styler


# ── データ読み込み（エラーハンドリング） ──────────────────────
try:
    store_data, all_staff_data, loaded_at = load_data()
except gspread.exceptions.APIError:
    st.error("⚠️ Google Sheets の API 制限に達しました。しばらく待ってから再読み込みしてください。")
    st.stop()
except gspread.exceptions.WorksheetNotFound as e:
    st.error(f"⚠️ シートが見つかりません: {e}\n\nanalyze_main.py を実行済みか確認してください。")
    st.stop()
except Exception as e:
    st.error(f"⚠️ データの読み込みに失敗しました。再読み込みボタンを押してください。\n\n```\n{e}\n```")
    st.stop()

all_months     = sorted({ym for d in store_data.values() for ym in d}, reverse=True)
all_months_asc = list(reversed(all_months))


# ── サイドバー ────────────────────────────────────────────────
with st.sidebar:
    st.title("📊 実績ダッシュボード")
    st.caption("Dr.training")
    st.divider()

    # ── データ粒度の切り替え ──────────────────────────────────
    granularity = st.radio("データ粒度", ["📊 月次", "📅 週次"], horizontal=True)
    st.divider()

    if granularity == "📊 月次":
        # 単月 / 期間 の切り替え
        mode = st.radio("表示モード", ["単月", "期間"], horizontal=True)
        if mode == "単月":
            selected_ym  = st.selectbox("対象月", all_months, format_func=fmt_month)
            ym_range     = [selected_ym]
            period_label = fmt_month(selected_ym)
        else:
            c1, c2 = st.columns(2)
            with c1:
                start_ym = st.selectbox("開始月", all_months_asc, format_func=fmt_month, index=0)
            with c2:
                end_ym = st.selectbox("終了月", all_months_asc, format_func=fmt_month,
                                      index=len(all_months_asc) - 1)
            if start_ym > end_ym:
                st.warning("⚠️ 開始月が終了月より後になっています")
                ym_range = []
            else:
                ym_range = [ym for ym in all_months_asc if start_ym <= ym <= end_ym]
            period_label = f"{fmt_month(start_ym)} 〜 {fmt_month(end_ym)}"

        stores_available = sorted(
            {lbl for lbl, d in store_data.items() if any(ym in d for ym in ym_range)}
        )
    else:
        # 週次モードは直近N週で表示（期間選択不要）
        ym_range     = all_months       # 後続の一部処理で使われる変数をデフォルト設定
        period_label = "週次"
        stores_available = sorted(store_data.keys())

    selected_store = st.selectbox("店舗", stores_available, format_func=clean_label)

    st.divider()
    if st.button("🔄 データを再読み込み"):
        load_data.clear()
        load_member_data.clear()
        st.rerun()
    st.caption(f"最終取得: {loaded_at}")
    st.caption("データは1日1回更新（手動更新は上のボタン）")


# ── ヘッダー ──────────────────────────────────────────────────
st.title(clean_label(selected_store))

if granularity == "📊 月次":
    if not ym_range:
        st.warning("期間を正しく選択してください")
        st.stop()
    months_note = f"（{len(ym_range)}ヶ月集計）" if len(ym_range) > 1 else ""
    st.caption(f"📊 月次　|　{period_label}{months_note}")
else:
    st.caption("📅 週次")
st.divider()

# ── 月次用集計（月次モード時のみ実行）────────────────────────
if granularity == "📊 月次":
    eff_store  = get_effective_store(store_data, ym_range)
    eff_staff  = get_effective_staff(all_staff_data, selected_store, ym_range)
    avg_s      = _avgs(eff_store)
    avg_st     = _avgs(eff_staff)
    period_key = period_label

# ── タブ ──────────────────────────────────────────────────────
if granularity == "📊 月次":
    tab_summary, tab_store, tab_staff = st.tabs([
        "🏢 全店舗サマリー",
        "📈 店舗実績",
        "👤 スタッフ別",
    ])
else:
    tab_weekly_main, tab_weekly_staff = st.tabs([
        "📅 週次サマリー",
        "👤 スタッフ別（週次）",
    ])

# ── 週次モードのコンテンツ（月次コードより先に実行し st.stop() で終了）────
if granularity == "📅 週次":
    _wl_base = dict(margin=dict(l=0, r=10, t=30, b=10), xaxis_title="", yaxis_title="", height=260)

    fact_df_w = load_member_data()
    sc_w      = get_store_codes(selected_store)

    with tab_weekly_main:
        st.caption("📅 週次指標：セッション数・来店会員数・新規数・CVR")
        st.caption("⚠️ 離脱数・復帰数・会員増減数は月次のみ対応")
        st.divider()

        n_weeks   = st.select_slider("表示週数", options=[4, 8, 12, 16, 24], value=12)
        df_weekly = build_weekly_store_df(fact_df_w, sc_w, n_weeks=n_weeks)

        if df_weekly.empty:
            st.info("週次データがありません")
        else:
            if len(df_weekly) >= 2:
                latest = df_weekly.iloc[-1]
                prev   = df_weekly.iloc[-2]
                m1, m2, m3, m4 = st.columns(4)
                def _wm(col, label, key):
                    v = latest.get(key)
                    p = prev.get(key)
                    d = round(v - p, 1) if isinstance(v, (int,float)) and isinstance(p, (int,float)) else None
                    col.metric(label, str(v) if v is not None else "—",
                               delta=f"{d:+.1f}" if d is not None else None)
                _wm(m1, "来店会員数（直近週）", "来店会員数")
                _wm(m2, "セッション数（直近週）", "セッション数")
                _wm(m3, "新規獲得数（直近週）", "新規獲得数")
                _wm(m4, "CVR（直近週）", "CVR(%)")
                st.caption(f"▲▼ は前週（{prev['週']}）との比較")

            st.divider()
            r1l, r1r = st.columns(2)
            with r1l:
                st.caption("セッション数（週別）")
                fig = px.bar(df_weekly, x="週", y="セッション数",
                             color_discrete_sequence=["#4a90d9"])
                fig.update_layout(**_wl_base)
                st.plotly_chart(fig, use_container_width=True)
            with r1r:
                st.caption("来店会員数（週別）")
                fig = px.line(df_weekly, x="週", y="来店会員数",
                              markers=True, color_discrete_sequence=["#6366f1"])
                fig.update_traces(line_width=2.5, marker_size=7)
                fig.update_layout(**_wl_base)
                st.plotly_chart(fig, use_container_width=True)

            r2l, r2r = st.columns(2)
            with r2l:
                st.caption("新規数・新規獲得数（週別）")
                df_mw = df_weekly.melt(id_vars="週", value_vars=["新規数", "新規獲得数"],
                                       var_name="指標", value_name="件数")
                fig = px.bar(df_mw, x="週", y="件数", color="指標", barmode="group",
                             color_discrete_map={"新規数": "#a3c4f3", "新規獲得数": "#4a90d9"},
                             height=260)
                fig.update_layout(**_wl_base,
                                  legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
                st.plotly_chart(fig, use_container_width=True)
            with r2r:
                st.caption("CVR（週別）")
                fig = px.line(df_weekly.dropna(subset=["CVR(%)"]),
                              x="週", y="CVR(%)",
                              markers=True, color_discrete_sequence=["#f0a500"])
                fig.update_traces(line_width=2.5, marker_size=7)
                fig.update_layout(**_wl_base)
                st.plotly_chart(fig, use_container_width=True)

            st.divider()
            st.subheader("週次データ一覧")
            st.dataframe(df_weekly, use_container_width=True)

    with tab_weekly_staff:
        st.caption("📅 週次指標（スタッフ別）")
        st.divider()
        n_weeks_s  = st.select_slider("表示週数", options=[4, 8, 12, 16, 24],
                                      value=12, key="ws_slider")
        df_wstaff  = build_weekly_staff_df(fact_df_w, sc_w, n_weeks=n_weeks_s)
        if df_wstaff.empty:
            st.info("データがありません")
        else:
            staff_list_w = sorted(df_wstaff["スタッフ"].unique())
            sel_w = st.selectbox("スタッフを絞り込む", ["全員"] + staff_list_w,
                                 key="ws_staff_sel")
            show = df_wstaff if sel_w == "全員" else df_wstaff[df_wstaff["スタッフ"] == sel_w]
            st.dataframe(show.reset_index(drop=True), use_container_width=True)

    st.stop()  # 以降の月次コードは実行しない


# ── Tab 1: 全店舗サマリー ──────────────────────────────────────
with tab_summary:
    st.subheader(f"全店舗サマリー（{period_label}）")
    st.caption("全店舗平均との比較　｜　🟢 平均+2%以上　🔴 平均-2%以下")

    df_store = build_store_df(eff_store, avgs=avg_s)
    if df_store.empty:
        st.info("選択期間のデータがありません")
    else:
        df_display = df_store.drop(columns=["_raw"]).copy()
        df_display["店舗"] = df_display["店舗"].apply(
            lambda s: re.sub(r'（\d+(?:\+\d+)*）', '', s.split("  ⚠")[0])
                      + ("  " + s.split("  ")[1] if "  ⚠" in s else "")
        )
        target_badge = clean_label(selected_store)

        styled = style_store_df(df_display, avg_s)

        def highlight_target(row):
            return ["font-weight:bold; background-color:#fff8e1"] * len(row) \
                if row["店舗"].startswith(target_badge) else [""] * len(row)
        styled = styled.apply(highlight_target, axis=1)

        row_height = 35
        table_height = min(row_height * len(df_display) + 60, 800)
        st.dataframe(styled, use_container_width=True, height=table_height)

        valid         = df_store.dropna(subset=["会員増減数"])
        sum_active    = int(valid["アクティブ会員"].fillna(0).sum())
        sum_shinki    = int(valid["新規数"].fillna(0).sum())
        sum_shinki_cv = int(valid["新規獲得数"].fillna(0).sum())
        sum_risseki   = int(valid["離脱数"].fillna(0).sum())
        sum_zougen    = int(valid["会員増減数"].fillna(0).sum())
        total_cvr     = f"{sum_shinki_cv/sum_shinki*100:.1f}%" if sum_shinki > 0 else "—"

        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1: st.metric("合計 アクティブ", sum_active)
        with col2: st.metric("合計 新規数", sum_shinki)
        with col3: st.metric("合計 新規獲得数", sum_shinki_cv)
        with col4: st.metric("合計 CVR", total_cvr)
        with col5: st.metric("合計 離脱数", sum_risseki)
        with col6: st.metric("合計 会員増減数", sum_zougen)


# ── Tab 2: 店舗実績 ───────────────────────────────────────────
with tab_store:
    st.subheader(f"{selected_store}　vs　全店舗平均")

    tr = eff_store.get(selected_store, {})
    if not tr:
        st.info("選択店舗・期間のデータがありません")
    else:
        # ── 要注意指標アラート ────────────────────────────────
        alert_defs = [
            ("アクティブ会員数", "active",       True,  False, "人"),
            ("新規獲得数",       "shinki_cv",    True,  False, "人"),
            ("CVR",              "cvr",          True,  True,  "pt"),
            ("離脱率",           "risseki_rate", False, True,  "pt"),
            ("会員増減数",       "zougen",       True,  False, ""),
        ]
        alerts = []
        for label, key, hb, is_rate, unit in alert_defs:
            val = _float(tr.get(key)) if is_rate else tr.get(key)
            avg = avg_s.get(key)
            if not isinstance(val, (int, float)) or not avg or avg == 0:
                continue
            ratio = (val - avg) / abs(avg)
            bad = (hb and ratio < -0.10) or (not hb and ratio > 0.10)
            if bad:
                diff = val - avg
                sign = "+" if diff > 0 else ""
                avg_disp = f"{avg:.1f}{'%' if is_rate else ''}"
                val_disp = f"{val:.1f}{'%' if is_rate else ''}"
                diff_disp = f"{sign}{diff:.1f}{unit}"
                alerts.append(f"**{label}**　実績 {val_disp}　平均 {avg_disp}　差 {diff_disp}")

        if alerts:
            alert_md = "\n\n".join(f"🔴 {a}" for a in alerts)
            st.error(f"⚠️ **要注意指標が {len(alerts)} 項目あります**\n\n{alert_md}")
        else:
            st.success("✅ すべての指標が全店舗平均以上（±10%以内）です")

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

        cols = st.columns(4)
        for i, (label, key, higher_better, is_rate) in enumerate(kpi_defs):
            raw   = tr.get(key)
            val   = _float(raw) if is_rate else (raw if isinstance(raw, (int, float)) else None)
            avg   = avg_s.get(key)
            delta = round(val - avg, 1) if val is not None and avg is not None else None
            disp  = str(raw) if raw is not None else "—"

            with cols[i % 4]:
                st.metric(
                    label=label,
                    value=disp,
                    delta=f"{delta:+.1f}" if delta is not None else None,
                    delta_color="normal" if higher_better else "inverse",
                    help=f"全店舗平均: {avg:.1f}{'%' if is_rate else ''}" if avg else None,
                )

        st.divider()
        note = f"{len(ym_range)}ヶ月の合計（アクティブ会員数は平均）" if len(ym_range) > 1 else ""
        st.caption(f"δ = 選択店舗 − 全店舗平均　｜　ホバーで平均値を表示　{note}")

        # ── 月別推移グラフ（期間モードのみ）────────────────────
        if len(ym_range) > 1:
            st.divider()
            st.subheader(f"月別推移（{period_label}）")
            df_monthly_s = build_monthly_store_df(store_data, selected_store, ym_range)
            # 担当本数（月別セッション数）を追加
            fact_df_m    = load_member_data()
            sc_m         = get_store_codes(selected_store)
            monthly_ss   = get_monthly_store_sessions(fact_df_m, sc_m, ym_range)
            month_to_ym  = {fmt_month(ym): ym for ym in ym_range}
            df_monthly_s["担当本数"] = df_monthly_s["月"].map(
                lambda m: monthly_ss.get(month_to_ym.get(m))
            )
            render_trend_charts(df_monthly_s)

        # 担当本数（ファクトテーブルから）
        fact_df = load_member_data()
        store_codes = get_store_codes(selected_store)
        total_sessions = get_store_session_count(fact_df, store_codes, ym_range)
        st.divider()
        sc1, sc2 = st.columns(2)
        with sc1:
            st.metric("総セッション数（担当本数合計）", total_sessions)
        with sc2:
            avg_active_val = tr.get("active")
            if avg_active_val:
                freq = f"{total_sessions / avg_active_val:.1f} 回/人"
            else:
                freq = "—"
            st.metric("平均セッション数/会員", freq)

        st.divider()
        st.subheader(f"アクティブ会員一覧（{period_label}）")
        store_codes = get_store_codes(selected_store)
        df_members = build_member_df(fact_df, store_codes, ym_range)
        if df_members.empty:
            st.info("対象期間の会員データがありません")
        else:
            c1, c2, c3 = st.columns(3)
            total = len(df_members)
            n_new   = (df_members["ステータス"] == "新規獲得").sum()
            n_uncv  = (df_members["ステータス"] == "新規（未CV）").sum()
            with c1: st.metric("来店会員数", total)
            with c2: st.metric("うち 新規獲得", n_new)
            with c3: st.metric("うち 新規（未CV）", n_uncv)
            st.dataframe(style_member_df(df_members), use_container_width=True)

        st.divider()
        render_comments(period_key, selected_store)


# ── Tab 3: スタッフ別 ─────────────────────────────────────────
with tab_staff:
    store_staff_for_period = sorted(
        lbl for lbl, d in all_staff_data.get(selected_store, {}).items()
        if any(ym in d for ym in ym_range)
    )
    selected_staff = st.selectbox(
        "スタッフを選択",
        ["（全員）"] + store_staff_for_period,
        key="staff_selector",
    )

    # 担当本数（ファクトテーブルから）
    fact_df_st   = load_member_data()
    sc_st        = get_store_codes(selected_store)
    sess_counts  = get_staff_session_counts(fact_df_st, sc_st, ym_range)
    df_staff     = build_staff_df(eff_staff, session_counts=sess_counts)

    if df_staff.empty:
        st.info("選択期間のスタッフデータがありません")
    else:
        if selected_staff == "（全員）":
            st.subheader(f"スタッフ別実績（{selected_store} / {period_label}）")
            st.caption("店舗平均との比較　｜　🟢 平均+2%以上　🔴 平均-2%以下")

            styled_staff = style_staff_df(df_staff, avg_st)
            st.dataframe(styled_staff, use_container_width=True)

            # ── 棒グラフ ──────────────────────────────────────
            st.divider()
            st.subheader("スタッフ比較グラフ")

            df_chart = df_staff.reset_index().copy()
            df_chart["スタッフ名"] = df_chart["スタッフ"].apply(clean_label)

            gc1, gc2 = st.columns(2)

            with gc1:
                st.caption("担当会員数")
                df_w = df_chart.dropna(subset=["担当会員数"]).sort_values("担当会員数")
                if not df_w.empty:
                    avg_val = avg_st.get("active")
                    fig = px.bar(
                        df_w, x="担当会員数", y="スタッフ名",
                        orientation="h",
                        color_discrete_sequence=["#4a90d9"],
                        height=max(250, len(df_w) * 40),
                    )
                    if avg_val:
                        fig.add_vline(
                            x=avg_val, line_dash="dash", line_color="#e05c5c",
                            annotation_text=f"平均 {avg_val:.1f}",
                            annotation_position="top right",
                        )
                    fig.update_layout(margin=dict(l=0, r=10, t=10, b=10), xaxis_title="", yaxis_title="")
                    st.plotly_chart(fig, use_container_width=True)

            with gc2:
                st.caption("担当本数")
                df_w2 = df_chart.dropna(subset=["担当本数"]).sort_values("担当本数")
                if not df_w2.empty:
                    fig2 = px.bar(
                        df_w2, x="担当本数", y="スタッフ名",
                        orientation="h",
                        color_discrete_sequence=["#6abf69"],
                        height=max(250, len(df_w2) * 40),
                    )
                    fig2.update_layout(margin=dict(l=0, r=10, t=10, b=10), xaxis_title="", yaxis_title="")
                    st.plotly_chart(fig2, use_container_width=True)

            gc3, gc4 = st.columns(2)

            with gc3:
                st.caption("CVR(%)")
                df_w3 = df_chart.dropna(subset=["CVR(%)"]).sort_values("CVR(%)")
                if not df_w3.empty:
                    avg_cvr = avg_st.get("cvr")
                    fig3 = px.bar(
                        df_w3, x="CVR(%)", y="スタッフ名",
                        orientation="h",
                        color_discrete_sequence=["#f0a500"],
                        height=max(250, len(df_w3) * 40),
                    )
                    if avg_cvr:
                        fig3.add_vline(
                            x=avg_cvr, line_dash="dash", line_color="#e05c5c",
                            annotation_text=f"平均 {avg_cvr:.1f}%",
                            annotation_position="top right",
                        )
                    fig3.update_layout(margin=dict(l=0, r=10, t=10, b=10), xaxis_title="", yaxis_title="")
                    st.plotly_chart(fig3, use_container_width=True)

            with gc4:
                st.caption("会員増減数")
                df_w4 = df_chart.dropna(subset=["会員増減数"]).sort_values("会員増減数")
                if not df_w4.empty:
                    colors = ["#e05c5c" if v < 0 else "#4a90d9" for v in df_w4["会員増減数"]]
                    fig4 = px.bar(
                        df_w4, x="会員増減数", y="スタッフ名",
                        orientation="h",
                        height=max(250, len(df_w4) * 40),
                    )
                    fig4.update_traces(marker_color=colors)
                    fig4.add_vline(x=0, line_color="#888", line_width=1)
                    fig4.update_layout(margin=dict(l=0, r=10, t=10, b=10), xaxis_title="", yaxis_title="")
                    st.plotly_chart(fig4, use_container_width=True)

            st.divider()
            st.caption("店舗平均")
            avg_cols = st.columns(4)
            avg_items = [
                ("担当会員数平均", avg_st.get("active")),
                ("CVR平均",        avg_st.get("cvr")),
                ("離脱率平均",     avg_st.get("risseki_rate")),
                ("会員増減数平均", avg_st.get("zougen")),
            ]
            for i, (label, val) in enumerate(avg_items):
                with avg_cols[i]:
                    st.metric(label, f"{val:.1f}" if val is not None else "—")

            st.divider()
            st.subheader(f"アクティブ会員一覧（{period_label}）")
            fact_df_s = load_member_data()
            store_codes_s = get_store_codes(selected_store)
            df_mem_all = build_member_df(fact_df_s, store_codes_s, ym_range)
            if df_mem_all.empty:
                st.info("対象期間の会員データがありません")
            else:
                st.caption(f"来店会員 {len(df_mem_all)}名")
                st.dataframe(style_member_df(df_mem_all), use_container_width=True)

            st.divider()
            render_comments(period_key, selected_store)

        else:
            st.subheader(f"{selected_staff}　vs　店舗平均")

            staff_row = eff_staff.get(selected_staff, {})
            if not staff_row:
                st.info("選択スタッフ・期間のデータがありません")
            else:
                # 担当本数（個人）
                sid_m2    = re.search(r'（(\d+)）$', selected_staff)
                sid2      = sid_m2.group(1) if sid_m2 else None
                honsu_val = sess_counts.get(sid2) if sid2 else None

                kpi_staff = [
                    ("担当会員数", "active",       True,  False),
                    ("担当本数",   "_honsu",       True,  False),
                    ("CVR",        "cvr",          True,  True),
                    ("新規担当数", "shinki_tanto", True,  False),
                    ("復帰担当数", "fukki_tanto",  True,  False),
                    ("離脱数",     "risseki",      False, False),
                    ("離脱率",     "risseki_rate", False, True),
                    ("来店頻度",   "freq",         True,  False),
                    ("会員増減数", "zougen",       True,  False),
                ]

                cols2 = st.columns(4)
                for i, (label, key, higher_better, is_rate) in enumerate(kpi_staff):
                    if key == "_honsu":
                        raw, val, avg, delta = honsu_val, honsu_val, None, None
                        disp = str(honsu_val) if honsu_val is not None else "—"
                    else:
                        raw   = staff_row.get(key)
                        val   = _float(raw) if is_rate else (raw if isinstance(raw, (int, float)) else None)
                        avg   = avg_st.get(key)
                        delta = round(val - avg, 1) if val is not None and avg is not None else None
                        disp  = str(raw) if raw is not None else "—"

                    with cols2[i % 4]:
                        st.metric(
                            label=label,
                            value=disp,
                            delta=f"{delta:+.1f}" if delta is not None else None,
                            delta_color="normal" if higher_better else "inverse",
                            help=f"店舗平均: {avg:.1f}{'%' if is_rate else ''}" if avg else None,
                        )

                st.divider()
                note = f"{len(ym_range)}ヶ月の合計（アクティブ会員数・来店頻度は平均）" if len(ym_range) > 1 else ""
                st.caption(f"δ = スタッフ − 店舗平均　{note}")

                # ── 月別推移グラフ（期間モードのみ）────────────
                if len(ym_range) > 1:
                    st.divider()
                    st.subheader(f"月別推移（{period_label}）")
                    df_monthly_st = build_monthly_staff_df(
                        all_staff_data, selected_store, selected_staff, ym_range
                    )
                    # 担当本数（スタッフ別月別セッション数）を追加
                    fact_df_mt   = load_member_data()
                    sc_mt        = get_store_codes(selected_store)
                    monthly_ss_t = get_monthly_staff_sessions(fact_df_mt, sc_mt, sid2 or "", ym_range)
                    month_to_ym2 = {fmt_month(ym): ym for ym in ym_range}
                    df_monthly_st["担当本数"] = df_monthly_st["月"].map(
                        lambda m: monthly_ss_t.get(month_to_ym2.get(m))
                    )
                    render_trend_charts(df_monthly_st)

                st.subheader("全スタッフとの比較")
                styled_all = style_staff_df(df_staff, avg_st)
                st.dataframe(styled_all, use_container_width=True)

                st.divider()
                st.subheader(f"担当会員一覧（{period_label}）")
                sid_match = re.search(r'（(\d+)）$', selected_staff)
                sid_filter = sid_match.group(1) if sid_match else None
                fact_df_t  = load_member_data()
                store_codes_t = get_store_codes(selected_store)
                df_mem_staff  = build_member_df(fact_df_t, store_codes_t, ym_range, sid_filter)
                if df_mem_staff.empty:
                    st.info("担当会員のデータがありません")
                else:
                    st.caption(f"担当会員 {len(df_mem_staff)}名")
                    st.dataframe(style_member_df(df_mem_staff), use_container_width=True)

                st.divider()
                render_comments(period_key, selected_store, selected_staff)


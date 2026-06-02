# data-analysis

取引データ・会員データを Google Sheets から読み込み、集計・分析結果を書き出すプロジェクト。

---

## データソース（読み取り専用）

| 名称 | スプレッドシートID | 内容 |
|------|-----------------|------|
| 取引データ | `1HpV44-axXonKY5hD8tKeMe5dSbp8NZ_f6rL4IeVPTWQ` | セッション単位の取引履歴 |
| 離脱者データ | `1QDoUzsgHXqaOymih-ZXQrstTXRchgc9Z119tVX97CSE` | 離客判定済み会員データ |

> スプレッドシートIDの変更は `config.py` のみで行う。各スクリプトに直書きしない。

---

## フォルダ構成

```
data-analysis/
├── CLAUDE.md          ← このファイル
├── config.py          ← シートID・定数・店舗グループ定義
├── utils.py           ← 共通ヘルパー関数
├── loader.py          ← データ読み込み共通処理
├── store/             ← 店舗単位の分析（store/CLAUDE.md 参照）
└── individual/        ← 個人（スタッフ）単位の分析（individual/CLAUDE.md 参照）
```

---

## 実行方法

```bash
# 店舗別メイン集計
python3.12 store/analyze_main.py

# 個人別メイン集計
python3.12 individual/analyze_main.py

# コホート分析
python3.12 store/analyze_cohort.py

# CVR×離脱相関
python3.12 store/analyze_cvr_churn.py

# ピックアップ分析
python3.12 store/analyze_pickup.py
```

---

## 共通定数（config.py で管理）

| 定数 | 値 | 意味 |
|------|----|------|
| `CUT_MONTH` | `"202509"` | 期間分割基準月（〜2025/9 と 2025/10〜） |
| `EXCLUDE_MONTHS` | `{"202504", "202605"}` | 集計除外月（データ不完全） |
| `NO_CVR` | `{"202605"}` | CVR計算除外月 |

---

## 店舗グループ定義（統合版）

近隣ペア店舗を統合して集計する。グループ内の店舗間移動は離脱・復帰としてカウントしない。

```
1+37   : 中目黒（中目黒店 + Dr.ピラティス中目黒店）
2+5    : 恵比寿（恵比寿店 + 恵比寿２号店）
6+9+36 : 池袋・目白（目白店 + 池袋店 + Dr.ピラティス池袋店）
8+34   : 吉祥寺（吉祥寺店 + Dr.ピラティス吉祥寺店）
11+42  : 銀座（銀座店 + Dr.ピラティス銀座店）
14+38  : 三軒茶屋（三軒茶屋店 + Dr.ピラティス三軒茶屋店）
16+40  : 田園調布（田園調布店 + Dr.ピラティス田園調布店）
```

---

## コーディングルール

- スプレッドシートIDは `config.py` のみに記載
- 共通ヘルパー関数は `utils.py` に集約
- データ読み込みは `loader.py` の関数を使う
- 1ファイル = 1目的（単独で実行可能）
- 各スクリプトは `from config import ...` / `from utils import ...` で共通部品を使う
- `print()` でログ出力を残す（処理の進捗が確認できるように）

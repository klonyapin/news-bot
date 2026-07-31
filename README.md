# news-bot

Discord ニュース Bot の**二層構造**プロジェクト。

| 層 | 対象 | 遅延 | ランタイム | ディレクトリ |
|----|------|------|------------|--------------|
| **速報層 (Tier 1)** | 地震・津波 (JMA 電文) | **~1 分** | Cloudflare Workers | [`worker/`](worker/) |
| **解説層 (Tier 2)** | 国内・国際・経済ニュース | ~2 時間 | GitHub Actions | [`src/`](src/) |

- **Tier 1 (worker/)**: 気象庁 (JMA) の eqvol.xml を毎分ポーリングし、震度閾値以上または津波警報が出た瞬間に Discord へ即投稿。真の速報。詳細は [`worker/README.md`](worker/README.md)。
- **Tier 2 (src/)**: Yahoo! ニュース (国内・国際・経済) から重要な話題を Haiku で選別し、Sonnet + Web 検索で **裏付け・背景・注意点・今後の動向** を日英併記で解説。GitHub Actions で 2 時間おき自動実行。以下がこの層のドキュメント。

---

## Tier 2: 話題ニュース解説層

## 全体像

```
2h おき (JST 6-24 時)
    │
    ▼
┌─ 全カテゴリを並列取得 ────────────────────┐
│ Yahoo! News RSS (domestic/world/business) │
│ Yahoo! アクセスランキング                    │
│ Yahoo! リアルタイム検索 (トレンド語)          │
└──────────────┬──────────────────────────────┘
               ▼
    state/posted.json で dedupe (72h TTL)
               ▼
    Haiku 4.5 で全記事を一括スコアリング (0-10)
               ▼
    閾値 (>=7) 以上を最大 N 件抽出
               ▼
┌─ Sonnet 5 (+ Web 検索) で並列解析 ─┐
│ summary / evidence / background    │
│ caveats / outlook × 日英併記        │
└──────────────┬──────────────────────┘
               ▼
    as_completed で完了順に即投稿 (待たない)
               ▼
        Discord Webhook (Embed)
```

## 特徴

- **速達性**: 2 時間ごとに実行、解析完了順に即座に投稿 (待ちなし)
- **重要度フィルタ**: Haiku で速く安く全記事採点、閾値以上のみ Sonnet で詳細解析
- **4 軸解説**: 「何が起きたか」だけでなく「裏付け・背景・注意点・今後の動向」まで
- **日英併記**: 各解説を日本語 + 英訳で表示
- **重複投稿防止**: `state/posted.json` を GitHub Actions cache で run 間保存 (72h TTL)
- **フォールバック**: LLM がコケてもヒート順で代替スコアリング
- **cost 最適化**: 大半の記事は Haiku (安価) で足切り、詳細解析は本当に重要なものだけ

## セットアップ

### 1. Discord Webhook を作成

対象チャンネル → 設定 → 連携サービス → ウェブフック → 新しいウェブフック → URL をコピー。

### 2. Anthropic API キーを取得

<https://console.anthropic.com/> で発行。

### 3. ローカル動作確認

```sh
cd news-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
$EDITOR .env  # DISCORD_WEBHOOK_URL と ANTHROPIC_API_KEY を設定

# 中身を確認 (Discord には投稿されない)
python -m src.main --dry-run --skip-dedupe --threshold 5

# 実際に投稿してみる (閾値を下げてテスト)
python -m src.main --threshold 5 --skip-dedupe
```

### 4. GitHub にプッシュ & Secrets 設定

```sh
git init
git add -A
git commit -m "Initial commit"
gh repo create news-bot --private --source=. --push
```

リポジトリ設定 → Secrets and variables → Actions で以下を追加:

- `DISCORD_WEBHOOK_URL`
- `ANTHROPIC_API_KEY`

以降、`.github/workflows/breaking-news.yml` の cron で 2 時間おきに自動実行される。

## 手動実行

```sh
# ローカル
python -m src.main                    # デフォルト: threshold=7, max=5
python -m src.main --threshold 5      # もっと拾う
python -m src.main --dry-run          # 標準出力に吐くだけ
python -m src.main --skip-dedupe      # 投稿履歴を無視

# GitHub Actions
gh workflow run breaking-news.yml
gh workflow run breaking-news.yml -f threshold=5 -f dry_run=true
```

## 設定を変えたい

- **重要度閾値**: `.env` の `IMPORTANCE_THRESHOLD` (デフォルト 7)
    - 5 = そこそこ話題 (投稿頻度↑)
    - 7 = 重要ニュース (デフォルト)
    - 9 = 一大事のみ (投稿頻度↓)
- **投稿数上限**: `.env` の `MAX_ITEMS_PER_RUN` (デフォルト 5)
- **解析モデル**: `.env` の `CLAUDE_MODEL` (デフォルト `claude-sonnet-5`)
- **実行時刻**: `.github/workflows/breaking-news.yml` の `cron` を編集
    - 現在: JST 6/8/10/12/14/16/18/20/22/24 時
    - もっと頻繁にしたければ `0 */1 * * *` などに変更

## Discord 投稿の見た目

各記事は 1 Embed で以下のように表示される (概念図):

```
┌────────────────────────────────────────────────┐
│ 🟠 重要  |  🏛️ 国内・政治                       │
│ 【日銀 政策金利0.5%に据え置き】                    │
│ 📊 ランキング #1  |  🔥 日銀/政策金利            │
│                                                │
│ 📌 要約                                         │
│ 日銀は金融政策決定会合で政策金利を...             │
│ _The Bank of Japan (BoJ) kept its policy..._   │
│                                                │
│ 🔍 裏付け / Evidence                            │
│ 日銀公式サイトの決定文書 (2026-07-31 12:00)...   │
│ _According to the BoJ's official release..._   │
│                                                │
│ 🌐 背景 / Background                            │
│ 賃上げ率が物価上昇に追いつかず...                 │
│ _Wage growth has failed to..._                 │
│                                                │
│ ⚠️ 注意点 / Caveats                             │
│ 総裁会見での「上振れリスク」発言は...             │
│ _The governor's mention of "upside risks"..._  │
│                                                │
│ 🔮 今後の動向 / Outlook                          │
│ 次回 9 月会合で 0.25 ポイント利上げ観測...       │
│ _Markets are pricing in a 25bp hike..._        │
│                                                │
│ 📚 情報源 / Sources                             │
│ • 日銀「金融政策決定会合における主な意見」        │
│ • 日経「利上げ見送り 円安容認と市場」             │
│                                                │
│ Yahoo! ニュース | 重要度 8/10                    │
└────────────────────────────────────────────────┘
```

## コスト目安

10 回/日 × 平均 2 記事投稿 (閾値 7 で選別後) を想定:

| 項目 | 単価 | 使用量 | コスト |
|------|------|--------|--------|
| Haiku 4.5 重要度スコア | ~$0.001/回 | 10 回/日 | $0.01/日 |
| Sonnet 5 詳細解析 + Web 検索 | ~$0.04/記事 | 20 記事/日 | $0.80/日 |
| **合計** | | | **~$25/月** |

閾値を上げれば投稿数が減ってコスト↓。逆に 5 まで下げると倍程度になる。

## 状態管理 (dedupe)

`state/posted.json` に URL → 投稿時刻を記録。GitHub Actions では `actions/cache` で run 間永続化。72 時間経過したエントリは自動削除。

`--skip-dedupe` で強制的に再投稿できる (テスト用)。

## トラブルシューティング

**「No items passed importance threshold」で毎回終わる**
→ 閾値が高すぎる可能性。ワークフロー手動起動で `threshold=5` を指定して様子を見る。

**同じ記事が何度も来る**
→ cache が効いていない可能性。GitHub Actions → Caches タブで `news-bot-posted-*` エントリを確認。ローカルなら `state/posted.json` を確認。

**リアルタイム検索の keywords が政治・経済と無関係**
→ 現状ですべての領域から拾っているため。将来的にはカテゴリ絞りが可能だが、Yahoo! リアルタイム側の API に依存。

**Claude の応答が JSON にならない**
→ 稀に発生。エラー内容は Discord にエラー Embed として届く。ログに request_id が出ているので Anthropic Support に送れる。

## ファイル構成

```
src/
├── models.py          Pydantic: NewsItem, AnalysisResult, BilingualText
├── sources/
│   ├── yahoo_news.py       RSS + アクセスランキング (並列)
│   └── yahoo_realtime.py   トレンドキーワード抽出
├── importance.py      Haiku 4.5 で一括スコアリング
├── analyzer.py        Sonnet 5 + Web 検索で 4 軸 × 日英解析
├── discord_client.py  Embed 生成 + 送信 (rate limit 対応)
├── state.py           posted URL の JSON 永続化
└── main.py            オーケストレータ (as_completed でストリーム)
```

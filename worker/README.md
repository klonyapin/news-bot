# news-bot-quake (Cloudflare Worker)

**2 種類の速報**を毎分ポーリングして Discord に即通知する Worker。

| Tier | ソース | 対象 | 遅延 |
|------|--------|------|------|
| **Tier 1** | JMA 電文 XML | 地震・津波 (震度閾値以上、または津波警報) | **~1 分** |
| **Tier 3** | NHK RSS (主要・社会) | 非地震の緊急事態 (テロ・銃撃・崩御・大規模爆発・特別警報・弾道ミサイル発射等) | **~1-2 分** |

両方が同じ Cloudflare Worker で毎分 cron で並列実行される。Tier 3 は NHK が使う `【速報】` 等の高信号キーワードで frontend filter するため、真に緊急なイベントだけ通知される (通常日は Tier 3 は 0 posts)。

## 動作原理

```
毎分 cron
  ├─► Tier 1 (JMA)
  │    → https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml
  │    → KV (eqvol:seen-entry-ids) で dedup
  │    → 震度/津波の parse + 閾値 filter
  │    → Discord webhook に POST
  │
  └─► Tier 3 (NHK)
       → https://www3.nhk.or.jp/rss/news/cat0.xml + cat1.xml
       → KV (nhk:seen-entry-ids) で dedup
       → 高信号キーワード判定 (【速報】/【緊急】/崩御/テロ/銃撃事件/ミサイル発射等)
       → 地震関連 (【地震速報】等) は除外 (Tier 1 と重複回避)
       → Discord webhook に POST (タイトル + リンクのみ、LLM 解析なし)
```

## セットアップ

### 前提

- Node.js 20+ (npm)
- Cloudflare アカウント (無料枠で十分)
- Discord webhook URL (既存 GH Actions bot と同じでも別でも可)

### A. 初回セットアップ (自動、対話式スクリプト)

```sh
cd worker

# Cloudflare にログイン (ブラウザで OAuth 承認、または CLOUDFLARE_API_TOKEN 環境変数)
npx wrangler login

# ワンコマンドで完了 (依存インストール + KV 作成 + wrangler.jsonc 修正 + Discord secret 登録 + deploy)
./bootstrap.sh
```

スクリプトは冪等なので、途中で失敗しても何度でも再実行できます。

セットアップ完了後、`wrangler.jsonc` に KV ID が書き込まれるので commit & push:

```sh
git add wrangler.jsonc && git commit -m "wire KV ID" && git push
```

### B. 継続的デプロイ (以降のコード変更は自動)

`worker/**` を触って push すると **GitHub Actions が自動で Cloudflare にデプロイ**します (`.github/workflows/deploy-worker.yml`)。

事前に GH リポジトリの Secrets に以下を登録:

| Secret | 取得場所 |
|--------|----------|
| `CLOUDFLARE_API_TOKEN` | https://dash.cloudflare.com/profile/api-tokens → "Edit Cloudflare Workers" テンプレートで作成 |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare dashboard 右下、または `npx wrangler whoami` の出力 |

```sh
gh secret set CLOUDFLARE_API_TOKEN     # プロンプトでペースト
gh secret set CLOUDFLARE_ACCOUNT_ID    # プロンプトでペースト
```

以降の流れ:
```
コード編集 → git push → GH Actions → npm ci → tsc --noEmit → wrangler deploy
```

### C. 動作確認

```sh
# ログをリアルタイム tail
npx wrangler tail

# 手動でtickを1回発火 (デバッグ endpoint)
curl https://news-bot-quake.<account-subdomain>.workers.dev/run

# KV に保存された seen ID を確認
curl https://news-bot-quake.<account-subdomain>.workers.dev/seen
```

## 設定変更

`wrangler.jsonc` の `vars` を編集して `npx wrangler deploy` するだけ。

| 変数 | デフォルト | 意味 |
|------|------------|------|
| `MIN_SHINDO` | `"3"` | この震度以上で通知 (`"5-"` で 5弱以上) |
| `TSUNAMI_ALWAYS_POST` | `"true"` | 津波警報・注意報は震度に関係なく必ず投稿 |

例: 震度4以上だけにしたい場合は `MIN_SHINDO` を `"4"` に。

## 想定コスト

- **Cloudflare Workers 無料枠**: 100k req/day (cron 1440/day で 1.4% 消費)
- **KV 無料枠**: 100k read/day, 1k write/day (tick ごと 1 read + 変化時のみ 1 write)
- **合計コスト: $0/月**

## 通知内容

震度4以上の例:
```
⚠️ **【JMA 震度速報】**  8/1 14:23 JST

🕒 発生: 8/1 14:22 JST  ·  💥 最大震度 **4**

> 各地の震度に関する情報

-# [JMA 電文原文](https://.../.xml)
```

大津波警報の例:
```
🌊🚨 **【JMA 津波警報・注意報・予報】**  8/1 14:23 JST

📍 震源: **三陸沖**  ·  📏 M 8.5  ·  ⬇ 深さ 30km  ·  💥 最大震度 **6強**  ·  🌊 大津波警報

> 大津波警報発表 高いところで3m以上の津波が予想されます 直ちに避難を

-# [JMA 電文原文](https://.../.xml)
```

## トラブルシューティング

**cron が発火しない**  
Cloudflare dashboard → Workers → news-bot-quake → Triggers タブで cron が enabled になっているか確認。

**Discord に何も来ない**  
- `wrangler tail` でログを見る。"posted:" が出ていれば投稿成功、"skip (threshold):" なら閾値未満
- 数時間何も来ないのは正常 (震度3以上の地震は日本で1日数回程度)
- 手動テスト: `MIN_SHINDO` を `"1"` に一時的に下げてdeploy → tail 監視 → 元に戻す

**KV が肥大化する**  
`MAX_SEEN_KEEP=200` で最新 200 件だけ保持。3日 TTL も設定済。放置で OK。

**JMA feed 側の変更で parse が壊れる**  
`fetchReport` は defensive parsing だが、電文フォーマット変更時は `src/jma.ts` の Body.Earthquake 参照パスを更新。

# news-bot-quake (Cloudflare Worker)

気象庁 (JMA) の地震・津波電文を毎分ポーリングし、震度閾値以上または津波警報が出た瞬間に Discord へ速報する Worker。

**遅延**: JMA 発表から最大 60 秒 + 数秒 (post) ≈ **1 分前後**。真の速報。

## 動作原理

```
毎分 cron
  → https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml を fetch
  → KV に保存した seen-entry-ids と突合、新規エントリだけ抽出
  → 各エントリの XML 電文を fetch
  → parseで震度・震源・M・津波区分を抽出
  → 閾値 (MIN_SHINDO / TSUNAMI_ALWAYS_POST) で filter
  → 通過分を Discord webhook に POST
  → seen ID を KV に write back
```

## セットアップ

### 前提

- Node.js 20+ (npm)
- Cloudflare アカウント (無料枠で十分)
- Discord webhook URL (既存 GH Actions bot と同じでも別でも可)

### 1. 依存インストール

```sh
cd worker
npm install
```

### 2. Cloudflare にログイン

```sh
npx wrangler login
```

ブラウザで Cloudflare の OAuth 画面が開くので許可。

### 3. KV namespace を作成

```sh
npx wrangler kv namespace create SEEN
```

出力される `id` を `wrangler.jsonc` の `kv_namespaces[0].id` に貼り付ける (`REPLACE_WITH_KV_ID_AFTER_wrangler_kv_create` を差し替え)。

### 4. Discord webhook を secret として登録

```sh
npx wrangler secret put DISCORD_WEBHOOK_URL
# プロンプトで URL をペースト → Enter (履歴に残らない)
```

### 5. デプロイ

```sh
npx wrangler deploy
```

これで毎分 cron が回り始める。

### 6. 動作確認

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

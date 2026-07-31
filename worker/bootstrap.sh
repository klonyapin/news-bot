#!/usr/bin/env bash
# news-bot-quake の初回セットアップスクリプト。
# 冪等: 何度実行しても壊れない。既存のリソースは再利用する。
set -euo pipefail

cd "$(dirname "$0")"

echo "==================================================="
echo "  news-bot-quake bootstrap"
echo "==================================================="
echo ""

# 1. 依存インストール
if [ ! -d node_modules ]; then
  echo "==> npm install..."
  npm install --silent
fi

# 2. Cloudflare 認証確認
if ! npx wrangler whoami >/dev/null 2>&1; then
  echo "❌ Cloudflare にログインしていません。"
  echo ""
  echo "以下のいずれかを実行してから再度このスクリプトを回してください:"
  echo ""
  echo "  a) 対話ログイン (ブラウザが開きます):"
  echo "       npx wrangler login"
  echo ""
  echo "  b) API トークンを使う (推奨、CI と共通化しやすい):"
  echo "       1. https://dash.cloudflare.com/profile/api-tokens で"
  echo "          「Edit Cloudflare Workers」テンプレートで token 作成"
  echo "       2. export CLOUDFLARE_API_TOKEN=xxx"
  echo ""
  exit 1
fi

WHOAMI=$(npx wrangler whoami 2>/dev/null || true)
echo "✅ Cloudflare にログイン済み"
echo "$WHOAMI" | grep -E 'account|email' | head -3 | sed 's/^/   /' || true
echo ""

# 3. KV namespace
if grep -q 'REPLACE_WITH_KV_ID' wrangler.jsonc; then
  echo "==> KV namespace 'SEEN' を作成中..."
  CREATE_OUT=$(npx wrangler kv namespace create SEEN 2>&1) || {
    echo "$CREATE_OUT"
    echo "⚠️  作成失敗。既存 namespace の可能性があるので list で探します..."
  }
  echo "$CREATE_OUT" | tail -5

  # 出力から ID 抽出 (wrangler は表記が変わりやすいので複数パターン試す)
  KV_ID=$(echo "$CREATE_OUT" | grep -oE 'id\s*=\s*"[a-f0-9]{20,}"' | head -1 \
    | grep -oE '[a-f0-9]{20,}')
  [ -z "$KV_ID" ] && KV_ID=$(echo "$CREATE_OUT" | grep -oE '"id":\s*"[a-f0-9]{20,}"' | head -1 \
    | grep -oE '[a-f0-9]{20,}')

  # fallback: list から探す
  if [ -z "$KV_ID" ]; then
    echo "==> ID を抽出できず。namespace list から探します..."
    LIST_OUT=$(npx wrangler kv namespace list 2>/dev/null || echo "[]")
    KV_ID=$(echo "$LIST_OUT" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for ns in data:
    if "SEEN" in ns.get("title", ""):
        print(ns.get("id", ""))
        break
' 2>/dev/null || echo "")
  fi

  if [ -z "$KV_ID" ]; then
    echo ""
    echo "❌ KV namespace ID が特定できませんでした。手動で対応してください:"
    echo "   1. npx wrangler kv namespace list  で ID をコピー"
    echo "   2. wrangler.jsonc の REPLACE_WITH_KV_ID_AFTER_wrangler_kv_create を差し替え"
    exit 1
  fi

  echo "==> wrangler.jsonc に KV ID を注入: $KV_ID"
  sed -i.bak "s/REPLACE_WITH_KV_ID_AFTER_wrangler_kv_create/$KV_ID/" wrangler.jsonc
  rm -f wrangler.jsonc.bak
else
  echo "✅ KV namespace ID は既に wrangler.jsonc に設定済み"
fi
echo ""

# 4. Discord webhook secret
if npx wrangler secret list 2>/dev/null | grep -q 'DISCORD_WEBHOOK_URL'; then
  echo "✅ DISCORD_WEBHOOK_URL secret は既に設定済み"
else
  echo "==> DISCORD_WEBHOOK_URL を登録します。プロンプトで URL を貼ってください:"
  npx wrangler secret put DISCORD_WEBHOOK_URL
fi
echo ""

# 5. Deploy
echo "==> Cloudflare にデプロイ中..."
npx wrangler deploy
echo ""

echo "==================================================="
echo "  ✅ bootstrap 完了"
echo "==================================================="
echo ""
echo "次のステップ:"
echo ""
echo "  1. 動作確認 (ログ tail):"
echo "       npx wrangler tail"
echo ""
echo "  2. 手動発火 (debug endpoint):"
echo "       curl \"https://news-bot-quake.\$(npx wrangler whoami 2>/dev/null | grep -oE '[a-z0-9-]+\\.workers\\.dev' | head -1 || echo 'YOUR-SUBDOMAIN.workers.dev')/run\""
echo ""
echo "  3. wrangler.jsonc の KV ID を commit して push:"
echo "       git add worker/wrangler.jsonc && git commit -m 'wire KV ID' && git push"
echo ""
echo "     ↑ push すると GitHub Actions が自動で Worker を再デプロイします"
echo "     (先に CLOUDFLARE_API_TOKEN を GH secrets に登録しておく必要あり)"
echo ""

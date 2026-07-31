import { fetchFeed, fetchReport } from "./jma";
import { postToDiscord } from "./discord";
import { type Env, shindoToNumber } from "./types";

const SEEN_KEY = "eqvol:seen-entry-ids";
const SEEN_TTL_SECONDS = 60 * 60 * 24 * 3;   // 3 days
const MAX_SEEN_KEEP = 200;

type SeenState = {
  ids: string[];
  updatedAt: string;
};

async function loadSeen(env: Env): Promise<Set<string>> {
  const raw = await env.SEEN.get(SEEN_KEY);
  if (!raw) return new Set();
  try {
    const parsed = JSON.parse(raw) as SeenState;
    return new Set(parsed.ids ?? []);
  } catch {
    return new Set();
  }
}

async function saveSeen(env: Env, seen: Set<string>): Promise<void> {
  // 直近 MAX_SEEN_KEEP 件だけ保持 (KV write 上限節約 & メモリ節約)
  const ids = Array.from(seen).slice(-MAX_SEEN_KEEP);
  const payload: SeenState = { ids, updatedAt: new Date().toISOString() };
  await env.SEEN.put(SEEN_KEY, JSON.stringify(payload), {
    expirationTtl: SEEN_TTL_SECONDS,
  });
}

function shouldPost(report: Awaited<ReturnType<typeof fetchReport>>, env: Env): boolean {
  if (env.TSUNAMI_ALWAYS_POST === "true" && report.tsunami && report.tsunami !== "none") {
    return true;
  }
  const minShindo = parseFloat(env.MIN_SHINDO || "3");
  const observed = shindoToNumber(report.maxShindo);
  if (observed < 0) {
    // 震源のみの一報 (震度未確定) は初回だけ通知する方針として false にしておく
    // 震度速報が後から出るのでそちらでカバーされる
    return false;
  }
  return observed >= minShindo;
}

async function tick(env: Env, ctx: ExecutionContext): Promise<void> {
  const entries = await fetchFeed();
  if (entries.length === 0) {
    console.log("no entries returned from feed");
    return;
  }

  const seen = await loadSeen(env);
  const fresh = entries.filter((e) => !seen.has(e.id));

  if (fresh.length === 0) {
    console.log(`no fresh entries (feed has ${entries.length}, all already seen)`);
    return;
  }

  console.log(`processing ${fresh.length} fresh entries (feed=${entries.length}, seen=${seen.size})`);

  // 古い順に処理 (Atom は新しい順に並ぶので reverse)
  const oldestFirst = [...fresh].reverse();

  let posted = 0;
  for (const entry of oldestFirst) {
    seen.add(entry.id); // 失敗しても再試行しない (次の tick で拾い直すのを避ける)
    try {
      const report = await fetchReport(entry);
      if (!shouldPost(report, env)) {
        console.log(`skip (threshold): ${entry.category} shindo=${report.maxShindo} tsunami=${report.tsunami}`);
        continue;
      }
      await postToDiscord(env.DISCORD_WEBHOOK_URL, report);
      posted++;
      console.log(`posted: ${entry.category} shindo=${report.maxShindo} tsunami=${report.tsunami}`);
    } catch (e) {
      console.error(`failed to process ${entry.id}: ${(e as Error).message}`);
    }
  }

  await saveSeen(env, seen);
  console.log(`tick done: posted ${posted}/${fresh.length}`);
}

export default {
  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(tick(env, ctx));
  },

  /**
   * ブラウザから叩いて手動発火するためのデバッグ endpoint。
   * GET /run で 1 回だけ tick を実行する。デバッグ用なので本番では叩かない想定。
   */
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/run") {
      const result = await tick(env, ctx).then(
        () => ({ ok: true }),
        (e) => ({ ok: false, error: String(e) }),
      );
      return Response.json(result);
    }
    if (url.pathname === "/seen") {
      const raw = await env.SEEN.get(SEEN_KEY);
      return new Response(raw ?? "(empty)", { headers: { "Content-Type": "application/json" } });
    }
    return new Response("news-bot-quake\n\nGET /run  - trigger one tick manually\nGET /seen - dump seen state", {
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  },
} satisfies ExportedHandler<Env>;

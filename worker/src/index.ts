import { fetchFeed, fetchReport } from "./jma";
import { fetchUrgentAlerts } from "./nhk";
import { postToDiscord, postNhkAlert } from "./discord";
import { type Env, shindoToNumber } from "./types";

const SEEN_KEY = "eqvol:seen-entry-ids";
const NHK_SEEN_KEY = "nhk:seen-entry-ids";
const SEEN_TTL_SECONDS = 60 * 60 * 24 * 3;   // 3 days
const MAX_SEEN_KEEP = 200;

type SeenState = {
  ids: string[];
  updatedAt: string;
};

async function loadSeen(env: Env, key: string = SEEN_KEY): Promise<Set<string>> {
  const raw = await env.SEEN.get(key);
  if (!raw) return new Set();
  try {
    const parsed = JSON.parse(raw) as SeenState;
    return new Set(parsed.ids ?? []);
  } catch {
    return new Set();
  }
}

async function saveSeen(env: Env, seen: Set<string>, key: string = SEEN_KEY): Promise<void> {
  // 直近 MAX_SEEN_KEEP 件だけ保持 (KV write 上限節約 & メモリ節約)
  const ids = Array.from(seen).slice(-MAX_SEEN_KEEP);
  const payload: SeenState = { ids, updatedAt: new Date().toISOString() };
  await env.SEEN.put(key, JSON.stringify(payload), {
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

  await saveSeen(env, seen, SEEN_KEY);
  console.log(`JMA tick done: posted ${posted}/${fresh.length}`);
}

async function nhkTick(env: Env, _ctx: ExecutionContext): Promise<void> {
  const alerts = await fetchUrgentAlerts();
  if (alerts.length === 0) {
    console.log("NHK: no urgent items in feed");
    return;
  }
  const seen = await loadSeen(env, NHK_SEEN_KEY);
  const fresh = alerts.filter((a) => !seen.has(a.id));
  if (fresh.length === 0) {
    console.log(`NHK: ${alerts.length} urgent items but all already seen`);
    return;
  }

  console.log(`NHK: processing ${fresh.length} fresh urgent items`);
  let posted = 0;
  for (const alert of fresh) {
    seen.add(alert.id);
    try {
      await postNhkAlert(env.DISCORD_WEBHOOK_URL, alert);
      posted++;
      console.log(`NHK posted: [${alert.matchedKeyword}] ${alert.title.slice(0, 60)}`);
    } catch (e) {
      console.error(`NHK failed for ${alert.id}: ${(e as Error).message}`);
    }
  }
  await saveSeen(env, seen, NHK_SEEN_KEY);
  console.log(`NHK tick done: posted ${posted}/${fresh.length}`);
}

export default {
  async scheduled(_event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    // JMA (Tier 1) と NHK (Tier 3) を並列で回す。同じ Worker の同じ cron 発火で両方処理する
    ctx.waitUntil(
      Promise.allSettled([tick(env, ctx), nhkTick(env, ctx)]).then(() => undefined),
    );
  },

  /**
   * デバッグ endpoint。GET /run=jma または /run=nhk で個別発火。
   */
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/run") {
      const which = url.searchParams.get("which") ?? "both";
      const jobs: Promise<unknown>[] = [];
      if (which === "jma" || which === "both") jobs.push(tick(env, ctx));
      if (which === "nhk" || which === "both") jobs.push(nhkTick(env, ctx));
      const results = await Promise.allSettled(jobs);
      return Response.json({
        ok: results.every((r) => r.status === "fulfilled"),
        results: results.map((r) => ({
          status: r.status,
          error: r.status === "rejected" ? String(r.reason) : undefined,
        })),
      });
    }
    if (url.pathname === "/seen") {
      const key = url.searchParams.get("key") ?? SEEN_KEY;
      const raw = await env.SEEN.get(key);
      return new Response(raw ?? "(empty)", { headers: { "Content-Type": "application/json" } });
    }
    return new Response(
      "news-bot-quake\n\n" +
        "GET /run              - run both JMA and NHK ticks\n" +
        "GET /run?which=jma    - JMA only\n" +
        "GET /run?which=nhk    - NHK only\n" +
        "GET /seen             - JMA seen state\n" +
        "GET /seen?key=nhk:seen-entry-ids - NHK seen state\n",
      { headers: { "Content-Type": "text/plain; charset=utf-8" } },
    );
  },
} satisfies ExportedHandler<Env>;

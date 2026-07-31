import type { QuakeReport } from "./types";

const COLOR_TSUNAMI_MAJOR = 0x8b0000;   // 暗い赤
const COLOR_TSUNAMI_WARN = 0xdc143c;    // 深紅
const COLOR_TSUNAMI_ADV = 0xff8c00;     // オレンジ
const COLOR_QUAKE_STRONG = 0xff4500;    // 震度6弱以上
const COLOR_QUAKE_MODERATE = 0xf7b500;  // 震度4-5
const COLOR_QUAKE_MINOR = 0x87ceeb;     // 震度3以下

function pickColor(report: QuakeReport): number {
  if (report.tsunami === "major_warning") return COLOR_TSUNAMI_MAJOR;
  if (report.tsunami === "warning") return COLOR_TSUNAMI_WARN;
  if (report.tsunami === "advisory") return COLOR_TSUNAMI_ADV;

  const s = report.maxShindo ?? "";
  if (/[67]/.test(s)) return COLOR_QUAKE_STRONG;
  if (/[45]/.test(s)) return COLOR_QUAKE_MODERATE;
  return COLOR_QUAKE_MINOR;
}

function tsunamiLabel(t: QuakeReport["tsunami"]): string {
  switch (t) {
    case "major_warning": return "🌊 大津波警報";
    case "warning": return "🌊 津波警報";
    case "advisory": return "🌊 津波注意報";
    default: return "";
  }
}

function headerEmoji(report: QuakeReport): string {
  if (report.tsunami && report.tsunami !== "none") return "🌊🚨";
  const s = report.maxShindo ?? "";
  if (/[67]/.test(s)) return "🚨🚨";
  if (/[45]/.test(s)) return "⚠️";
  return "📡";
}

/** メイン message content (見やすい大きい表示部分)。 */
function buildContent(report: QuakeReport): string {
  const emoji = headerEmoji(report);
  const lines: string[] = [];

  lines.push(`${emoji} **【JMA ${report.title}】**  ${formatJst(report.reportDateTime)}`);
  lines.push("");

  const bullet: string[] = [];
  if (report.originTime) bullet.push(`🕒 発生: ${formatJst(report.originTime)}`);
  if (report.hypocenter) bullet.push(`📍 震源: **${report.hypocenter}**`);
  if (report.magnitude) bullet.push(`📏 M ${report.magnitude}`);
  if (report.depth) bullet.push(`⬇ 深さ ${report.depth}`);
  if (report.maxShindo) bullet.push(`💥 最大震度 **${report.maxShindo}**`);
  const t = tsunamiLabel(report.tsunami);
  if (t) bullet.push(t);

  lines.push(bullet.join("  ·  "));

  if (report.headlineText) {
    lines.push("");
    lines.push(`> ${truncate(report.headlineText.replace(/\n/g, " "), 500)}`);
  }

  lines.push("");
  lines.push(`-# [JMA 電文原文](<${report.sourceUrl}>)`);

  return truncate(lines.join("\n"), 2000);
}

/** Discord webhook にポスト。429 は Retry-After に従って最大 3 回リトライ。 */
export async function postToDiscord(
  webhookUrl: string,
  report: QuakeReport,
): Promise<void> {
  const payload = {
    content: buildContent(report),
    embeds: [
      {
        color: pickColor(report),
        footer: { text: `気象庁 XML 電文 (${report.title})` },
      },
    ],
  };

  for (let attempt = 0; attempt < 3; attempt++) {
    const res = await fetch(webhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 429) {
      const wait = parseFloat(res.headers.get("Retry-After") ?? "1");
      await sleep(wait * 1000);
      continue;
    }
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Discord post failed ${res.status}: ${body.slice(0, 200)}`);
    }
    return;
  }
  throw new Error("Discord post failed after retries");
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function truncate(s: string, limit: number): string {
  if (s.length <= limit) return s;
  return s.slice(0, limit - 1) + "…";
}

/** ISO8601 (+09:00) → "8/1 14:23 JST" */
function formatJst(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const jst = new Date(d.getTime() + 9 * 3600 * 1000);
  const mm = jst.getUTCMonth() + 1;
  const dd = jst.getUTCDate();
  const hh = String(jst.getUTCHours()).padStart(2, "0");
  const min = String(jst.getUTCMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${min} JST`;
}

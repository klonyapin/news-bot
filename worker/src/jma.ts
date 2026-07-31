import { XMLParser } from "fast-xml-parser";
import type { FeedEntry, QuakeReport } from "./types";

const EQVOL_FEED = "https://www.data.jma.go.jp/developer/xml/feed/eqvol.xml";

const USER_AGENT = "news-bot-quake/0.1 (+https://github.com/klonyapin/news-bot)";

/** 地震関連の Atom entry title。火山系 (降灰予報, 火山の状況…) は除外。 */
const QUAKE_TITLES = new Set([
  "震度速報",
  "震源に関する情報",
  "震源・震度に関する情報",
  "遠地地震に関する情報",
  "津波警報・注意報・予報",
  "津波情報",
  "沖合の津波観測に関する情報",
  "津波観測に関する情報",
  "地震回数に関する情報",
  "顕著な地震の震源要素更新のお知らせ",
]);

const feedParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  isArray: (name) => name === "entry",
});

const reportParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  parseTagValue: false,
  parseAttributeValue: false,
  removeNSPrefix: true,
});

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} for ${url}`);
  }
  return await res.text();
}

/** Atom フィードから地震・津波関連エントリだけを新しい順で返す。 */
export async function fetchFeed(): Promise<FeedEntry[]> {
  const xml = await fetchText(EQVOL_FEED);
  const parsed = feedParser.parse(xml);
  const feed = parsed?.feed;
  if (!feed) return [];
  const entries = Array.isArray(feed.entry) ? feed.entry : [];
  const result: FeedEntry[] = [];
  for (const e of entries) {
    const title = String(e?.title ?? "").trim();
    if (!QUAKE_TITLES.has(title)) continue;
    const link = e?.link?.["@_href"] ?? e?.link ?? "";
    result.push({
      id: String(e.id ?? ""),
      updated: String(e.updated ?? ""),
      title,
      link: String(link),
      category: title,
    });
  }
  return result;
}

/** 個別電文 XML を fetch して要点を抽出。 */
export async function fetchReport(entry: FeedEntry): Promise<QuakeReport> {
  const xml = await fetchText(entry.link);
  const doc = reportParser.parse(xml);
  const report = doc?.Report;
  const head = report?.Head ?? {};
  const body = report?.Body ?? {};

  const originTime = pickText(body?.Earthquake?.OriginTime);
  const hypocenter = pickText(body?.Earthquake?.Hypocenter?.Area?.Name);
  const magnitude = formatMagnitude(body?.Earthquake?.Magnitude);
  const depth = extractDepth(body?.Earthquake?.Hypocenter?.Area?.Coordinate);
  const maxShindo = pickText(body?.Intensity?.Observation?.MaxInt);

  const tsunami = detectTsunami(entry.title, body);

  const headlineText = pickText(head?.Headline?.Text);

  return {
    reportDateTime: String(head?.ReportDateTime ?? ""),
    title: entry.title || String(head?.Title ?? ""),
    originTime,
    hypocenter,
    magnitude,
    depth,
    maxShindo,
    tsunami,
    sourceUrl: entry.link,
    headlineText,
  };
}

function pickText(v: unknown): string | undefined {
  if (v == null) return undefined;
  if (typeof v === "string") return v.trim() || undefined;
  if (typeof v === "number") return String(v);
  if (typeof v === "object") {
    const anyV = v as Record<string, unknown>;
    if (typeof anyV["#text"] === "string") return String(anyV["#text"]).trim() || undefined;
  }
  return undefined;
}

/** Magnitude 要素 (テキスト or 属性) から表示用文字列を組む。 */
function formatMagnitude(v: unknown): string | undefined {
  if (v == null) return undefined;
  if (typeof v === "string") return v.trim() || undefined;
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    // "@_description" 例: "Ｍ６．５"、"#text" 例: "6.5"
    const desc = o["@_description"];
    if (typeof desc === "string" && desc.trim()) return desc.trim();
    const text = o["#text"];
    if (typeof text === "string" && text.trim()) return `M${text.trim()}`;
  }
  return undefined;
}

/**
 * Coordinate の @_description から深さを抜き出す。
 * 例: "北緯３２．７度　東経１３０．７度　深さ　１０ｋｍ" → "10km"
 */
function extractDepth(coord: unknown): string | undefined {
  if (!coord || typeof coord !== "object") return undefined;
  const desc = (coord as Record<string, unknown>)["@_description"];
  if (typeof desc !== "string") return undefined;
  const normalized = zenkakuDigitsToHankaku(desc)
    .replace(/[ｋＫ][ｍＭ]/g, "km")
    .replace(/\s+/g, " ");
  const m = normalized.match(/深さ\s*(?:約\s*)?([0-9]+(?:\.[0-9]+)?)\s*km/);
  if (m) return `${m[1]}km`;
  const highM = normalized.match(/深さ\s*(?:ごく)?浅い/);
  if (highM) return "ごく浅い";
  return undefined;
}

/** 全角数字と全角ドットを半角化。 */
function zenkakuDigitsToHankaku(s: string): string {
  return s
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xff10 + 0x30))
    .replace(/．/g, ".");
}

function detectTsunami(title: string, body: unknown): QuakeReport["tsunami"] {
  const isTsunamiCategory = title.includes("津波");
  if (!isTsunamiCategory) {
    // 通常の地震情報でも「津波の心配はありません」等のコメントが含まれるが none 扱い
    return "none";
  }
  const text = JSON.stringify(body ?? {});
  if (text.includes("大津波警報")) return "major_warning";
  if (text.includes("津波警報")) return "warning";
  if (text.includes("津波注意報")) return "advisory";
  return "advisory";
}

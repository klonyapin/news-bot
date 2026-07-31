import { XMLParser } from "fast-xml-parser";

const NHK_FEEDS = [
  "https://www3.nhk.or.jp/rss/news/cat0.xml", // 主要ニュース
  "https://www3.nhk.or.jp/rss/news/cat1.xml", // 社会 (速報タグの主戦場)
];

const USER_AGENT = "news-bot-quake/0.1 (+https://github.com/klonyapin/news-bot)";

/**
 * 「本当に緊急」と判断できる高信号キーワード。
 * 曖昧なもの (「死亡」単体など) は誤検知が多いため入れない。
 */
const URGENT_KEYWORDS = [
  "【速報】",         // NHK 汎用速報タグ
  "【緊急】",
  "【号外】",
  "崩御",             // 天皇・皇族の逝去
  "テロ",             // テロ事件
  "銃撃事件",         // 銃撃事件
  "拉致事件",         // 拉致事件
  "特別警報",         // 気象特別警報
  "緊急事態宣言",     // 政府の緊急事態宣言
  "戦争勃発",
  "ミサイル発射",     // 弾道ミサイル発射 (北朝鮮等)
  "弾道ミサイル",
  "大規模爆発",
  "航空機事故",
  "新幹線事故",
  "首相辞任",         // 政変級
  "内閣総辞職",
];

/**
 * Tier 1 (JMA) が既に扱っているため除外するキーワード。
 * 地震・津波は JMA XML 電文で ~1 分以内に別途通知される。
 */
const EXCLUDE_KEYWORDS = [
  "【地震速報】",
  "【地震】",
  "【津波警報】",
  "【津波注意報】",
];

export type NhkAlert = {
  id: string;
  title: string;
  link: string;
  published: string;
  matchedKeyword: string;
};

const parser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  isArray: (name) => name === "item",
});

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url, { headers: { "User-Agent": USER_AGENT } });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return await res.text();
}

export function classifyTitle(title: string): string | null {
  if (EXCLUDE_KEYWORDS.some((k) => title.includes(k))) return null;
  const matched = URGENT_KEYWORDS.find((k) => title.includes(k));
  return matched ?? null;
}

async function fetchOne(feedUrl: string): Promise<NhkAlert[]> {
  const xml = await fetchText(feedUrl);
  const doc = parser.parse(xml);
  // NHK は RDF/RSS 1.0 形式 (rdf:RDF > item[])
  const rdf = doc?.["rdf:RDF"] ?? doc?.rss?.channel ?? doc;
  const items = Array.isArray(rdf?.item) ? rdf.item : [];

  const alerts: NhkAlert[] = [];
  for (const item of items) {
    const title = String(item?.title ?? "").trim();
    const link = String(item?.link ?? "").trim();
    if (!title || !link) continue;

    const matched = classifyTitle(title);
    if (!matched) continue;

    alerts.push({
      id: link, // NHK は URL がユニーク ID として機能する
      title,
      link,
      published: String(item?.["dc:date"] ?? item?.pubDate ?? "").trim(),
      matchedKeyword: matched,
    });
  }
  return alerts;
}

export async function fetchUrgentAlerts(): Promise<NhkAlert[]> {
  const settled = await Promise.allSettled(NHK_FEEDS.map(fetchOne));
  const merged: NhkAlert[] = [];
  const seen = new Set<string>();
  for (const r of settled) {
    if (r.status !== "fulfilled") {
      console.warn("NHK feed failed:", (r.reason as Error)?.message);
      continue;
    }
    for (const alert of r.value) {
      if (seen.has(alert.id)) continue;
      seen.add(alert.id);
      merged.push(alert);
    }
  }
  return merged;
}

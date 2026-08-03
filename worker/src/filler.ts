import { XMLParser } from "fast-xml-parser";

const CAT_API = "https://api.thecatapi.com/v1/images/search?limit=1&mime_types=jpg,png,gif";

const USER_AGENT = "news-bot-quake/0.1 (+https://github.com/klonyapin/news-bot)";

type LightFeed = { name: string; url: string };

const LIGHT_FEEDS: LightFeed[] = [
  { name: "Yahoo 話題",        url: "https://news.yahoo.co.jp/rss/categories/life.xml" },
  { name: "livedoor エンタメ",  url: "https://news.livedoor.com/topics/rss/ent.xml" },
  { name: "Yahoo エンタメ",     url: "https://news.yahoo.co.jp/rss/topics/entertainment.xml" },
  { name: "Yahoo IT",          url: "https://news.yahoo.co.jp/rss/topics/it.xml" },
];

const rssParser = new XMLParser({
  ignoreAttributes: false,
  attributeNamePrefix: "@_",
  isArray: (name) => name === "item" || name === "entry",
});

type LightNews = { source: string; title: string; link: string };

async function fetchCatImage(): Promise<string | null> {
  try {
    const res = await fetch(CAT_API, { headers: { "User-Agent": USER_AGENT } });
    if (!res.ok) {
      console.warn(`Cat API returned ${res.status}`);
      return null;
    }
    const data = (await res.json()) as Array<{ url?: string }>;
    return data?.[0]?.url ?? null;
  } catch (e) {
    console.warn("Cat API failed:", (e as Error).message);
    return null;
  }
}

async function fetchLightNews(): Promise<LightNews | null> {
  // ランダム順で試して最初に成功した feed から 1 記事ピック
  const shuffled = [...LIGHT_FEEDS].sort(() => Math.random() - 0.5);
  for (const feed of shuffled) {
    try {
      const res = await fetch(feed.url, { headers: { "User-Agent": USER_AGENT } });
      if (!res.ok) continue;
      const xml = await res.text();
      const doc = rssParser.parse(xml);
      const items = (doc?.rss?.channel?.item ?? doc?.["rdf:RDF"]?.item ?? []) as Array<{ title?: string; link?: string }>;
      if (!items.length) continue;
      const top = items.slice(0, 20);
      const pick = top[Math.floor(Math.random() * top.length)];
      const title = String(pick?.title ?? "").trim();
      const link = String(pick?.link ?? "").trim();
      if (title && link) {
        return { source: feed.name, title, link };
      }
    } catch (e) {
      console.warn(`Light feed ${feed.name} failed:`, (e as Error).message);
    }
  }
  return null;
}

function formatJstNow(): string {
  const jst = new Date(Date.now() + 9 * 3600 * 1000);
  const mm = jst.getUTCMonth() + 1;
  const dd = jst.getUTCDate();
  const hh = String(jst.getUTCHours()).padStart(2, "0");
  const min = String(jst.getUTCMinutes()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${min} JST`;
}

function truncate(s: string, limit: number): string {
  return s.length <= limit ? s : s.slice(0, limit - 1) + "…";
}

export async function postFiller(webhookUrl: string): Promise<void> {
  const [catUrl, news] = await Promise.all([fetchCatImage(), fetchLightNews()]);

  if (!catUrl && !news) {
    console.warn("Both cat and news fetch failed, skipping filler post");
    return;
  }

  const embed: Record<string, unknown> = { color: 0xffa07a };
  if (catUrl) embed.image = { url: catUrl };
  if (news) {
    embed.title = truncate(news.title, 250);
    embed.url = news.link;
    embed.footer = { text: news.source };
    embed.description = "📰 息抜きに 1 本";
  }

  const payload = {
    content: `🐈 **今日の休憩**  ·  ${formatJstNow()}`,
    embeds: [embed],
  };

  const res = await fetch(webhookUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Discord filler post failed ${res.status}: ${body.slice(0, 200)}`);
  }
  console.log(`Filler posted: cat=${!!catUrl}, news=${news?.title.slice(0, 40) ?? "none"}`);
}

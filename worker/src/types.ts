export type Env = {
  SEEN: KVNamespace;
  DISCORD_WEBHOOK_URL: string;
  MIN_SHINDO: string;
  TSUNAMI_ALWAYS_POST: string;
};

/** Atom フィードの 1 エントリ (要点だけ) */
export type FeedEntry = {
  id: string;
  updated: string;
  title: string;
  link: string;
  /** JMA 分類 (震度速報 / 震源に関する情報 / 震源・震度に関する情報 / 津波警報・注意報・予報 など) */
  category: string;
};

/** 個別 XML 電文を parse した結果 */
export type QuakeReport = {
  /** JMA が発表した時刻 */
  reportDateTime: string;
  /** 電文種別 (震度速報 / 震源・震度に関する情報 / 津波警報・注意報・予報 など) */
  title: string;
  /** 発生時刻 (震源に関する情報が来て初めて確定) */
  originTime?: string;
  /** 震央地名 (例: "宮城県沖") */
  hypocenter?: string;
  /** マグニチュード (例: "6.5") */
  magnitude?: string;
  /** 深さ (例: "60km") */
  depth?: string;
  /** 最大震度 (例: "5強") */
  maxShindo?: string;
  /** 津波区分 (None | 注意報 | 警報 | 大津波警報) */
  tsunami?: "none" | "advisory" | "warning" | "major_warning";
  /** ソース URL (Atom entry の link) */
  sourceUrl: string;
  /** 本文全体 (Description の Text 要素からの引用) */
  headlineText?: string;
};

/** 震度文字列 → 数値化 (0-9)。5弱=5.0, 5強=5.5, 6弱=6.0, 6強=6.5, 7=7.0 */
export function shindoToNumber(s: string | undefined): number {
  if (!s) return -1;
  const map: Record<string, number> = {
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
    "5-": 4.5, "5弱": 5.0,
    "5+": 5.5, "5強": 5.5,
    "6-": 6.0, "6弱": 6.0,
    "6+": 6.5, "6強": 6.5,
    "7": 7.0,
  };
  return map[s.trim()] ?? -1;
}

/** MIN_SHINDO env var のパース。"5強" や "5.5" のような両フォーマットを受ける。 */
export function parseMinShindo(raw: string | undefined, fallback: number = 3): number {
  if (!raw) return fallback;
  const trimmed = raw.trim();
  // 震度表記 (5強 等) を優先
  const asShindo = shindoToNumber(trimmed);
  if (asShindo >= 0) return asShindo;
  // 数値文字列 (5.5, 3 等) にフォールバック
  const asFloat = parseFloat(trimmed);
  if (!isNaN(asFloat)) return asFloat;
  return fallback;
}

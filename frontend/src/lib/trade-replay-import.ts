import type { PriceBar } from "@/lib/api";
import { EXIT_PERIODS, type ReplayCase } from "@/lib/trade-replay";

const ADJUSTMENTS = ["raw", "split", "split_dividend", "split_dividend_additive"] as const;
type Adjustment = (typeof ADJUSTMENTS)[number];

export type ImportedReplayCase = ReplayCase & {
  symbol: string;
  source: string;
  snapshotId: string;
  adjustment: Adjustment;
  currency: "USD";
  decisionDate: string;
};

const errorMessages = {
  invalidJson: () => "The file must contain valid JSON.",
  invalidSnapshot: () => "The OHLCV snapshot must be a JSON object.",
  missingField: (field: string) => `Missing required field: ${field}.`,
  invalidText: (field: string) => `${field} must be non-empty text.`,
  unknownAdjustment: () => `Specify a price adjustment basis: ${ADJUSTMENTS.join(", ")}.`,
  unsupportedCurrency: () => "Replay currently supports USD prices. Set currency to USD for a USD snapshot.",
  invalidDate: (field: string) => `${field} must be a real calendar date in YYYY-MM-DD format.`,
  invalidPrice: (field: string) => `${field} must round to a positive price within the supported USD cent range.`,
  invalidVolume: (field: string) => `${field} must be a finite, non-negative number.`,
  invalidBars: () => "bars must be a non-empty array of daily OHLCV rows.",
  invalidBar: (field: string) => `${field} must be a daily OHLCV object.`,
  duplicateDate: (date: string) => `Duplicate trading date: ${date}.`,
  invalidOhlc: (date: string) => `On ${date}, low and high must contain both open and close.`,
  missingCutoff: (date: string) => `No daily bar matches the decision date ${date}.`,
  insufficientHistory: (count: string) => `Include at least ${count} daily bars through the decision date for the SMA comparison.`,
  missingFuture: (date: string) => `Include at least one daily bar after the decision date ${date}.`,
};

export class ReplayImportError extends Error {
  constructor(public readonly code: keyof typeof errorMessages, public readonly detail = "") {
    super(errorMessages[code](detail));
    this.name = "ReplayImportError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function required(record: Record<string, unknown>, key: string, field = key): unknown {
  if (!Object.prototype.hasOwnProperty.call(record, key)) {
    throw new ReplayImportError("missingField", field);
  }
  return record[key];
}

function readText(record: Record<string, unknown>, field: string): string {
  const value = required(record, field);
  if (typeof value !== "string" || !value.trim()) {
    throw new ReplayImportError("invalidText", field);
  }
  return value.trim();
}

function readDate(value: unknown, field: string): string {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const date = new Date(`${value}T00:00:00.000Z`);
    if (Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value) return value;
  }
  throw new ReplayImportError("invalidDate", field);
}

function readPrice(value: unknown, field: string): number {
  if (typeof value === "number") {
    const cents = Math.round(value * 100);
    if (Number.isSafeInteger(cents) && cents > 0) return value;
  }
  throw new ReplayImportError("invalidPrice", field);
}

function readBar(value: unknown, index: number): PriceBar {
  const row = `bars[${index}]`;
  if (!isRecord(value)) throw new ReplayImportError("invalidBar", row);

  const price = (key: "open" | "high" | "low" | "close") => {
    const field = `${row}.${key}`;
    return readPrice(required(value, key, field), field);
  };
  const time = readDate(required(value, "time", `${row}.time`), `${row}.time`);
  const open = price("open");
  const high = price("high");
  const low = price("low");
  const close = price("close");
  if (low > Math.min(open, close) || high < Math.max(open, close)) {
    throw new ReplayImportError("invalidOhlc", time);
  }

  const volumeField = `${row}.volume`;
  const volume = required(value, "volume", volumeField);
  if (typeof volume !== "number" || !Number.isFinite(volume) || volume < 0) {
    throw new ReplayImportError("invalidVolume", volumeField);
  }
  return { time, open, high, low, close, volume };
}

export function parseReplaySnapshot(text: string): ImportedReplayCase {
  let snapshot: unknown;
  try {
    snapshot = JSON.parse(text.replace(/^\uFEFF/, ""));
  } catch {
    throw new ReplayImportError("invalidJson");
  }
  if (!isRecord(snapshot)) throw new ReplayImportError("invalidSnapshot");

  const symbol = readText(snapshot, "symbol");
  const source = readText(snapshot, "source");
  const snapshotId = readText(snapshot, "snapshotId");
  const adjustmentField = required(snapshot, "adjustment");
  const adjustment = ADJUSTMENTS.find((basis) => basis === adjustmentField);
  if (!adjustment) throw new ReplayImportError("unknownAdjustment", "adjustment");

  const currency = required(snapshot, "currency");
  if (currency !== "USD") throw new ReplayImportError("unsupportedCurrency", "currency");
  const decisionDate = readDate(required(snapshot, "decisionDate"), "decisionDate");
  const stop = readPrice(required(snapshot, "stop"), "stop");
  const rows = required(snapshot, "bars");
  if (!Array.isArray(rows) || !rows.length) throw new ReplayImportError("invalidBars", "bars");

  const dates = new Set<string>();
  const bars = rows.map((value, index) => {
    const bar = readBar(value, index);
    if (dates.has(bar.time)) throw new ReplayImportError("duplicateDate", bar.time);
    dates.add(bar.time);
    return bar;
  });
  bars.sort((a, b) => (a.time < b.time ? -1 : 1));

  const decisionIndex = bars.findIndex((bar) => bar.time === decisionDate);
  if (decisionIndex < 0) throw new ReplayImportError("missingCutoff", decisionDate);
  const minimumHistory = Math.max(...EXIT_PERIODS);
  if (decisionIndex + 1 < minimumHistory) {
    throw new ReplayImportError("insufficientHistory", String(minimumHistory));
  }
  if (decisionIndex === bars.length - 1) throw new ReplayImportError("missingFuture", decisionDate);

  return { id: symbol, symbol, source, snapshotId, adjustment, currency, decisionDate, decisionIndex, stop, bars };
}

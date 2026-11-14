import type { PriceBar } from "@/lib/api";
import { calcMA } from "@/lib/indicators";

export const REPLAY_CASH = 10_000;
export const REPLAY_RISK = 100;
export const DECISION_BAR = 27;
export const EXIT_PERIODS = [10, 20] as const;

export type ReplayCase = {
  id: string;
  stop: number;
  bars: PriceBar[];
  decisionIndex: number;
};

function demoBars(level: number, continuation: number[]): PriceBar[] {
  const closes = [
    38.8, 39.3, 38.9, 39.7, 40.2, 39.8, 40.6, 41, 40.5, 41.2,
    41.6, 42.1, 41.5, 41.3, 42.3, 42.7, 42.1, 42.9, 43.2, 42.7,
    43.5, 43.3, 43.8, 43.6, 44, 43.8, 44.1, 45.8, ...continuation,
  ];
  const date = new Date(Date.UTC(2025, 1, 3));
  const prices = (value: number) => Math.round(value * 100) / 100;
  return closes.map((value, index) => {
    const close = prices(value + level);
    const prior = closes[index - 1] ?? value;
    const open = prices(prior + level + [0.12, -0.18, 0.22, -0.1, 0.05][index % 5]);
    const bar = {
      time: date.toISOString().slice(0, 10),
      open,
      close,
      high: prices(Math.max(open, close) + [0.37, 0.22, 0.48][index % 3]),
      low: prices(Math.min(open, close) - [0.28, 0.4, 0.32][index % 3]),
      volume: 145_000 + (index * 71_339) % 220_000 + (index === DECISION_BAR ? 420_000 : 0),
    };
    do {
      date.setUTCDate(date.getUTCDate() + 1);
    } while (date.getUTCDay() === 0 || date.getUTCDay() === 6);
    return bar;
  });
}

export const REPLAY_CASES: readonly ReplayCase[] = [
  {
    id: "DEMO-A",
    stop: 43.5,
    decisionIndex: DECISION_BAR,
    bars: demoBars(0, [
      46.5, 47.3, 46.7, 48.2, 49.1, 48.5, 50, 50.8, 51.3, 50.7, 52,
      53, 52.7, 54.1, 55, 54.3, 53.3, 52.6, 51.9, 51.2, 50.2, 51,
    ]),
  },
  {
    id: "DEMO-B",
    stop: 67.5,
    decisionIndex: DECISION_BAR,
    bars: demoBars(24, [
      46.1, 45.6, 46.7, 45.9, 44.6, 43.1, 42.2, 42.8, 43.5, 43,
      42.5, 43.2, 42.9, 44, 44.9, 44.4, 45.2, 46, 45.6, 46.4, 45.8, 46.2,
    ]),
  },
];

export type Fill = { index: number; price: number; shares: number };
export type Exit = Fill & { reason: "manual" | "stop" | "sma" };
type Decision = "long" | "pass";

export type ReplayState = {
  index: number;
  decision: Decision | null;
  pending: "buy" | "sell" | null;
  entry: Fill | null;
  exit: Exit | null;
  rejected: boolean;
};

export function newReplay(sample: ReplayCase): ReplayState {
  return { index: sample.decisionIndex, decision: null, pending: null, entry: null, exit: null, rejected: false };
}

export function decide(state: ReplayState, decision: Decision): ReplayState {
  if (state.decision) return state;
  return { ...state, decision, pending: decision === "long" ? "buy" : null };
}

export function queueReplayExit(state: ReplayState): ReplayState {
  if (!state.entry || state.exit || state.pending) return state;
  return { ...state, pending: "sell" };
}

function cents(price: number): number {
  return Math.round(price * 100);
}

function stopFill(bar: PriceBar, stop: number): number | null {
  if (bar.open <= stop) return bar.open;
  return bar.low <= stop ? stop : null;
}

export function advanceReplay(state: ReplayState, sample: ReplayCase): ReplayState {
  if (!state.decision || state.index >= sample.bars.length - 1) return state;
  const index = state.index + 1;
  const bar = sample.bars[index];
  const next: ReplayState = { ...state, index, pending: null };
  if (state.pending === "buy") {
    const risk = cents(bar.open) - cents(sample.stop);
    const shares = risk > 0
      ? Math.min(Math.floor(cents(REPLAY_RISK) / risk), Math.floor(cents(REPLAY_CASH) / cents(bar.open)))
      : 0;
    if (!shares) return { ...next, rejected: true };
    next.entry = { index, price: bar.open, shares };
  }
  if (!next.entry || next.exit) return next;

  const shares = next.entry.shares;
  if (state.pending === "sell" && bar.open > sample.stop) {
    next.exit = { index, price: bar.open, shares, reason: "manual" };
  } else {
    const price = stopFill(bar, sample.stop);
    if (price !== null) next.exit = { index, price, shares, reason: "stop" };
  }
  return next;
}

export function finishReplay(state: ReplayState, sample: ReplayCase): ReplayState {
  let next = state;
  while (next.decision && next.index < sample.bars.length - 1) next = advanceReplay(next, sample);
  return next;
}

export function replayAccount(state: ReplayState, bar: PriceBar) {
  const spent = state.entry ? cents(state.entry.price) * state.entry.shares : 0;
  const proceeds = state.exit ? cents(state.exit.price) * state.exit.shares : 0;
  const shares = state.exit ? 0 : (state.entry?.shares ?? 0);
  const cash = cents(REPLAY_CASH) - spent + proceeds;
  const equity = cash + cents(bar.close) * shares;
  return { shares, cash: cash / 100, equity: equity / 100, pnl: (equity - cents(REPLAY_CASH)) / 100 };
}

export function compareReplayExits(sample: ReplayCase, entry: Fill, visibleIndex: number) {
  const bars = sample.bars.slice(0, visibleIndex + 1);
  const risk = cents(entry.price) - cents(sample.stop);
  return EXIT_PERIODS.map((period) => {
    const averages = calcMA(bars.map((bar) => bar.close), period);
    let exit: Exit | null = null;
    let sellAtOpen = false;
    let high = entry.price;
    let low = entry.price;
    for (let index = entry.index; index < bars.length; index += 1) {
      const bar = bars[index];
      const stop = stopFill(bar, sample.stop);
      const price = sellAtOpen ? bar.open : stop;
      if (price !== null) {
        const reason = sellAtOpen && bar.open > sample.stop ? "sma" : "stop";
        exit = { index, price, shares: entry.shares, reason };
        // With only a daily bar, the order of intraday extremes is unknown.
        high = Math.max(high, price);
        low = Math.min(low, price);
        break;
      }
      high = Math.max(high, bar.high);
      low = Math.min(low, bar.low);
      const average = averages[index];
      sellAtOpen = average !== null && bar.close < average;
    }
    const mark = exit?.price ?? bars[bars.length - 1].close;
    const move = cents(mark) - cents(entry.price);
    return {
      period,
      exit,
      mark,
      gain: move / cents(entry.price),
      r: move / risk,
      holding: (exit?.index ?? visibleIndex) - entry.index,
      mae: (cents(low) - cents(entry.price)) / cents(entry.price),
      mfe: (cents(high) - cents(entry.price)) / cents(entry.price),
    };
  });
}

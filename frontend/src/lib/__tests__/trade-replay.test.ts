import {
  advanceReplay, compareReplayExits, DECISION_BAR, decide, finishReplay, newReplay,
  queueReplayExit, REPLAY_CASES, replayAccount, type ReplayCase,
} from "../trade-replay";

const [trend, failure] = REPLAY_CASES;
const next = DECISION_BAR + 1;

test("requires a decision and only reveals one session per step", () => {
  const initial = newReplay(trend);
  expect(advanceReplay(initial, trend)).toBe(initial);
  expect(finishReplay(initial, trend)).toBe(initial);
  const passed = decide(initial, "pass");
  expect(decide(passed, "long")).toBe(passed);
  expect(advanceReplay(passed, trend).index).toBe(next);
  expect(replayAccount(finishReplay(passed, trend), trend.bars.at(-1)!)).toEqual({
    cash: 10_000, equity: 10_000, pnl: 0, shares: 0,
  });
});

test("fills at next open, limits risk and uses actual shares and cents", () => {
  const ordered = decide(newReplay(trend), "long");
  expect(ordered.entry).toBeNull();
  const opened = advanceReplay(ordered, trend);
  expect(opened.entry).toEqual({ index: next, price: 45.7, shares: 45 });
  expect(replayAccount(opened, trend.bars[next])).toEqual({
    cash: 7943.5, equity: 10036, pnl: 36, shares: 45,
  });
  const queued = queueReplayExit(opened);
  expect(queued.exit).toBeNull();
  const exited = advanceReplay(queued, trend);
  expect(exited.exit).toEqual({ index: next + 1, price: 46.55, shares: 45, reason: "manual" });
  expect(replayAccount(exited, trend.bars[next + 1])).toEqual({
    cash: 10038.25, equity: 10038.25, pnl: 38.25, shares: 0,
  });
});

test("uses the stop for a touch, the open for a gap, and skips an invalid entry", () => {
  const entered = finishReplay(decide(newReplay(failure), "long"), failure);
  expect(entered.exit).toMatchObject({ price: failure.stop, reason: "stop" });
  const gap: ReplayCase = { ...trend, bars: trend.bars.map((bar, i) =>
    i === next + 1 ? { ...bar, open: 42, low: 41, close: 42.5 } : bar) };
  const opened = advanceReplay(decide(newReplay(gap), "long"), gap);
  expect(advanceReplay(opened, gap).exit).toMatchObject({ price: 42, reason: "stop" });
  const invalid = advanceReplay(decide(newReplay(trend), "long"), { ...trend, stop: 46 });
  expect(invalid).toMatchObject({ rejected: true, pending: null, entry: null });
});

test("SMA comparison waits for a later open and uses only bars revealed so far", () => {
  const opened = advanceReplay(decide(newReplay(trend), "long"), trend);
  const entry = opened.entry!;
  const initial = compareReplayExits(trend, entry, next);
  expect(initial.map((rule) => [rule.exit, rule.mark])).toEqual([[null, 46.5], [null, 46.5]]);
  const [fast, slow] = compareReplayExits(trend, entry, trend.bars.length - 1);
  expect(fast.exit?.reason).toBe("sma");
  expect(slow.exit?.reason).toBe("sma");
  expect(fast.exit!.index).toBeLessThan(slow.exit!.index);
  for (const result of [fast, slow]) {
    const index = result.exit!.index;
    const signal = trend.bars.slice(index - result.period, index);
    const average = signal.reduce((sum, bar) => sum + bar.close, 0) / result.period;
    expect(signal.at(-1)!.close).toBeLessThan(average);
    expect(result.mark).toBe(trend.bars[index].open);
    expect(result.r).toBeCloseTo((result.mark - entry.price) / (entry.price - trend.stop));
    expect(result.mae).toBeLessThanOrEqual(0);
    expect(result.mfe).toBeGreaterThan(0);
    expect(compareReplayExits(trend, entry, index - 1).find((rule) => rule.period === result.period)?.exit).toBeNull();
  }
});

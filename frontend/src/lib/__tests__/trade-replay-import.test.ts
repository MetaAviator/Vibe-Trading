import { parseReplaySnapshot, ReplayImportError } from "../trade-replay-import";

function snapshot() {
  return {
    symbol: "LOCAL-TEST",
    source: "Synthetic parser fixture",
    snapshotId: "case-import-test-v1",
    adjustment: "raw",
    currency: "USD",
    decisionDate: "2025-01-20",
    stop: 9,
    bars: Array.from({ length: 22 }, (_, index) => ({
      time: `2025-01-${String(index + 1).padStart(2, "0")}`,
      open: 10,
      high: 12,
      low: 9.5,
      close: 11,
      volume: 100,
    })),
  };
}

const parse = (value: unknown) => parseReplaySnapshot(JSON.stringify(value));

test("preserves snapshot provenance and applies the exact date cutoff after ordering bars", () => {
  const input = snapshot();
  const bars = [...input.bars];
  const imported = parse({ ...input, bars: [...bars].reverse() });
  expect(imported).toMatchObject({ ...input, id: input.symbol, decisionIndex: 19 });
  expect(imported.bars[imported.decisionIndex + 1]).toEqual(bars[20]);
});

test("reports the specific missing field on the snapshot or a daily row", () => {
  const input = snapshot();
  const { source: _source, ...missingSource } = input;
  const { volume: _volume, ...missingVolume } = input.bars[0];
  expect(() => parse(missingSource)).toThrow(new ReplayImportError("missingField", "source"));
  expect(() => parse({ ...input, bars: [missingVolume, ...input.bars.slice(1)] }))
    .toThrow(new ReplayImportError("missingField", "bars[0].volume"));
});

test("reports a repeated trading date even when rows arrive out of order", () => {
  const input = snapshot();
  expect(() => parse({ ...input, bars: [input.bars[2], ...input.bars] }))
    .toThrow(expect.objectContaining({ code: "duplicateDate", detail: "2025-01-03" }));
});

test("requires a declared adjustment basis compatible with the snapshot prices", () => {
  const input = snapshot();
  const { adjustment: _adjustment, ...missingAdjustment } = input;
  expect(() => parse(missingAdjustment)).toThrow(new ReplayImportError("missingField", "adjustment"));
  expect(() => parse({ ...input, adjustment: "unknown" }))
    .toThrow(new ReplayImportError("unknownAdjustment", "adjustment"));
  expect(parse({ ...input, adjustment: "split_dividend" }).adjustment).toBe("split_dividend");
});

test.each([
  [{ decisionDate: "2025-02-29" }, "invalidDate", "decisionDate"],
  [{ decisionDate: "2025-01-20T16:00:00-05:00" }, "invalidDate", "decisionDate"],
  [{ decisionDate: "2025-01-23" }, "missingCutoff", "2025-01-23"],
  [{ decisionDate: "2025-01-19" }, "insufficientHistory", "20"],
  [{ decisionDate: "2025-01-22" }, "missingFuture", "2025-01-22"],
  [{ stop: 0.001 }, "invalidPrice", "stop"],
  [{ currency: "TWD" }, "unsupportedCurrency", "currency"],
] as const)("rejects invalid snapshot values %j", (values, code, detail) => {
  expect(() => parse({ ...snapshot(), ...values })).toThrow(new ReplayImportError(code, detail));
});

test.each([
  [{ time: "2025-02-29" }, "invalidDate", "bars[0].time"],
  [{ open: "10" }, "invalidPrice", "bars[0].open"],
  [{ low: 11 }, "invalidOhlc", "2025-01-01"],
  [{ high: 10.5 }, "invalidOhlc", "2025-01-01"],
  [{ volume: -1 }, "invalidVolume", "bars[0].volume"],
] as const)("rejects invalid bar values %j", (values, code, detail) => {
  const input = snapshot();
  expect(() => parse({ ...input, bars: [{ ...input.bars[0], ...values }, ...input.bars.slice(1)] }))
    .toThrow(new ReplayImportError(code, detail));
});

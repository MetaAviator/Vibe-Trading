import { useMemo, useRef, useState, type ChangeEvent } from "react";
import { ArrowRight, ChartCandlestick, ChevronRight, LockKeyhole, RotateCcw } from "lucide-react";
import { Link } from "react-router";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import type { TradeMarker } from "@/lib/api";
import {
  advanceReplay, compareReplayExits, decide, finishReplay, newReplay,
  queueReplayExit, REPLAY_CASES, REPLAY_RISK, replayAccount, type ReplayCase,
} from "@/lib/trade-replay";
import { useReplayCopy } from "@/lib/trade-replay-copy";
import { parseReplaySnapshot, ReplayImportError, type ImportedReplayCase } from "@/lib/trade-replay-import";

const panel = "rounded-2xl border bg-card";
const control = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border px-3 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 disabled:cursor-not-allowed disabled:opacity-40";
const primary = `${control} border-primary bg-primary text-primary-foreground hover:bg-primary/90`;
const metric = "mt-1 font-mono text-lg font-semibold tabular-nums";
const decimal = new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const money = (amount: number) => decimal.format(amount);
const signed = (value: number, suffix = "") => `${value > 0 ? "+" : ""}${money(value)}${suffix}`;

export function TradeReplay() {
  const copy = useReplayCopy();
  const [caseId, setCaseId] = useState(REPLAY_CASES[0].id);
  const [imported, setImported] = useState<{ sample: ImportedReplayCase; revision: number }>();
  const [importError, setImportError] = useState<ReplayImportError | "read">();
  const importRevision = useRef(0);
  const activeImport = caseId === "local" ? imported : undefined;
  const sample = activeImport?.sample ?? REPLAY_CASES.find((item) => item.id === caseId) ?? REPLAY_CASES[0];

  async function importSnapshot(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    event.currentTarget.value = "";
    const revision = ++importRevision.current;
    setImportError(undefined);

    try {
      const parsed = parseReplaySnapshot(await file.text());
      if (revision !== importRevision.current) return;
      setImported({ sample: parsed, revision });
      setCaseId("local");
    } catch (error) {
      if (revision === importRevision.current) {
        setImportError(error instanceof ReplayImportError ? error : "read");
      }
    }
  }

  return (
    <div className="mx-auto max-w-[1460px] space-y-5 p-4 sm:p-6 xl:p-8">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b pb-5">
        <div>
          <Link to="/paper-trading" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            {copy.accounts} <ChevronRight size={13} aria-hidden="true" />
          </Link>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight sm:text-4xl">{copy.title}</h1>
          <p className="mt-2 text-sm text-muted-foreground">{copy.description}</p>
        </div>
        <label className="flex items-center gap-3 text-sm font-medium">
          {copy.case}
          <select className="h-10 max-w-56 rounded-lg border bg-card px-3 font-mono" value={caseId}
            onChange={(event) => setCaseId(event.target.value)}>
            {REPLAY_CASES.map((item) => <option key={item.id}>{item.id}</option>)}
            {imported && <option value="local">{imported.sample.symbol} ({copy.local})</option>}
          </select>
        </label>
      </header>
      <div className={`${panel} px-4 py-3`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <label htmlFor="replay-file" className="text-sm font-medium">{copy.importFile}</label>
            <p id="replay-import-help" className="mt-1 text-xs text-muted-foreground">{copy.importHelp}</p>
          </div>
          <input id="replay-file" type="file" accept=".json,application/json" onChange={importSnapshot}
            aria-describedby={importError ? "replay-import-help replay-import-error" : "replay-import-help"}
            className="max-w-full text-sm text-muted-foreground file:me-3 file:min-h-10 file:rounded-lg file:border file:border-border file:bg-background file:px-3 file:font-medium file:text-foreground hover:file:bg-muted" />
        </div>
        {importError && (
          <p id="replay-import-error" role="alert" className="mt-2 text-sm text-destructive">
            {importError === "read" ? copy.readError : copy.importError(importError)}
          </p>
        )}
        {activeImport && (
          <div className="mt-3 border-t pt-3">
            <h2 className="text-sm font-medium">{copy.snapshotDetails}</h2>
            <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
              {([
                [copy.dataSource, activeImport.sample.source],
                [copy.snapshotId, activeImport.sample.snapshotId],
                [copy.adjustment, activeImport.sample.adjustment],
                [copy.cutoff, activeImport.sample.decisionDate],
              ] as const).map(([label, value]) => (
                <div key={label} className="min-w-0">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="mt-0.5 break-words font-mono">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </div>
      <Replay key={activeImport ? `local-${activeImport.revision}` : sample.id} sample={sample} imported={!!activeImport} />
    </div>
  );
}

function Replay({ sample, imported }: { sample: ReplayCase; imported: boolean }) {
  const copy = useReplayCopy();
  const [state, setState] = useState(() => newReplay(sample));
  const bars = useMemo(() => sample.bars.slice(0, state.index + 1), [sample, state.index]);
  const bar = bars[bars.length - 1];
  const complete = state.index === sample.bars.length - 1;
  const account = replayAccount(state, bar);
  const markers = useMemo<TradeMarker[]>(() => {
    const fills: TradeMarker[] = [];
    if (state.entry) fills.push({ time: sample.bars[state.entry.index].time, side: "BUY", price: state.entry.price, qty: state.entry.shares });
    if (state.exit) fills.push({ time: sample.bars[state.exit.index].time, side: "SELL", price: state.exit.price, qty: state.exit.shares });
    return fills;
  }, [sample, state.entry, state.exit]);

  const entry = complete && state.decision === "pass"
    ? advanceReplay(decide(newReplay(sample), "long"), sample).entry
    : state.entry;
  const comparisons = complete && entry ? compareReplayExits(sample, entry, state.index) : [];
  const exitLabel = { manual: copy.manual, stop: copy.stopExit, sma: copy.smaExit };
  const canExit = !complete && account.shares > 0 && !state.pending;
  const decisionStatus = state.pending === "buy" ? copy.pendingBuy
    : state.pending === "sell" ? copy.pendingSell
    : state.decision === "pass" ? copy.passed
    : state.rejected ? copy.rejected
    : state.exit ? copy.closed
    : state.entry ? copy.holding : copy.choose;

  return (
    <>
      <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <section className={`${panel} min-w-0 overflow-hidden`} aria-labelledby="replay-chart-title">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
            <div className="flex items-center gap-3">
              <span className="rounded-lg bg-primary/10 p-2 text-primary"><ChartCandlestick size={18} aria-hidden="true" /></span>
              <div>
                <h2 id="replay-chart-title" className="font-semibold">{sample.id} <span className="ms-1 text-sm font-normal text-muted-foreground">{copy.chart}</span></h2>
                <p className="mt-0.5 font-mono text-xs text-muted-foreground">{bar.time} · USD</p>
              </div>
            </div>
            <span className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground">{imported ? copy.local : copy.synthetic}</span>
          </div>
          <dl className="grid grid-cols-4 gap-2 px-4 py-3 text-xs sm:max-w-lg">
            {([[copy.open, bar.open], [copy.high, bar.high], [copy.low, bar.low], [copy.close, bar.close]] as const).map(([label, value]) => (
              <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 font-mono text-sm">{money(value)}</dd></div>
            ))}
          </dl>
          <div className="px-2 sm:px-4">
            <CandlestickChart data={bars} markers={markers} height={340} initialOverlays={["ma10", "ma20"]} />
          </div>
          <div className="mt-2 space-y-3 border-t px-4 py-3">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <progress className="h-1.5 min-w-0 flex-1 accent-primary" value={state.index - sample.decisionIndex} max={sample.bars.length - 1 - sample.decisionIndex} aria-label={copy.shown} />
              <span className="font-mono tabular-nums">{bars.length}/{sample.bars.length}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" className={primary} disabled={!state.decision || complete} onClick={() => setState((current) => advanceReplay(current, sample))}>
                {copy.next} <ArrowRight size={15} aria-hidden="true" />
              </button>
              <button type="button" className={control} disabled={!state.decision || complete} onClick={() => setState((current) => finishReplay(current, sample))}>
                {copy.finish}
              </button>
              <button type="button" className={`${control} ms-auto`} onClick={() => setState(newReplay(sample))}>
                <RotateCcw size={14} aria-hidden="true" /> {copy.reset}
              </button>
            </div>
            {complete && <p role="status" className="text-sm font-medium">{copy.complete}</p>}
          </div>
        </section>

        <section className={`${panel} min-w-0 divide-y`} aria-labelledby="decision-title">
          <div className="space-y-3 p-4">
            <h2 id="decision-title" className="font-semibold">{copy.decision}</h2>
            <div className="grid grid-cols-2 gap-2">
              <button type="button" className={primary} disabled={!!state.decision} onClick={() => setState((current) => decide(current, "long"))}>{copy.buy}</button>
              <button type="button" className={control} disabled={!!state.decision} onClick={() => setState((current) => decide(current, "pass"))}>{copy.pass}</button>
            </div>
            <p aria-live="polite" className="min-h-10 text-sm text-muted-foreground">{decisionStatus}</p>
            <dl className="grid grid-cols-2 gap-3 text-xs text-muted-foreground">
              <div><dt>{copy.stop}</dt><dd className="mt-1 font-mono text-sm text-foreground">{money(sample.stop)}</dd></div>
              <div><dt>{copy.risk}</dt><dd className="mt-1 font-mono text-sm text-foreground">{money(REPLAY_RISK)} USD</dd></div>
            </dl>
          </div>
          <div className="p-4">
            <dl className="grid grid-cols-2 gap-x-3 gap-y-4 text-xs">
              <div><dt className="text-muted-foreground">{copy.equity} · USD</dt><dd className={metric}>{money(account.equity)}</dd></div>
              <div><dt className="text-muted-foreground">{copy.pnl} · USD</dt><dd className={metric}>{signed(account.pnl)}</dd></div>
              <div><dt className="text-muted-foreground">{copy.cash}</dt><dd className="mt-1 font-mono">{money(account.cash)}</dd></div>
              <div><dt className="text-muted-foreground">{copy.shares}</dt><dd className="mt-1 font-mono">{account.shares}</dd></div>
            </dl>
            <button type="button" className={`${control} mt-4 w-full`} disabled={!canExit} onClick={() => setState(queueReplayExit)}>{copy.sell}</button>
          </div>
          <div className="p-4">
            <h3 className="mb-2 text-sm font-medium">{copy.events}</h3>
            {markers.length === 0 ? <p className="text-sm text-muted-foreground">{copy.noFills}</p> : (
              <ul className="space-y-2 text-xs">
                {markers.map((fill) => (
                  <li className="grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5" key={fill.side}>
                    <span className="font-medium">{fill.side === "BUY" ? copy.buyFill : exitLabel[state.exit!.reason]}</span>
                    <span className="font-mono">{fill.qty} × {money(fill.price)}</span>
                    <time className="font-mono text-muted-foreground">{fill.time}</time>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <section className={`${panel} p-4 sm:p-5`} aria-labelledby="comparison-title">
        <h2 id="comparison-title" className="text-lg font-semibold">{copy.comparison}</h2>
        {!complete ? (
          <p className="mt-2 flex items-center gap-2 text-sm text-muted-foreground"><LockKeyhole size={14} aria-hidden="true" /> {copy.sealed}</p>
        ) : !entry ? (
          <p className="mt-2 text-sm text-muted-foreground">{copy.noEntry}</p>
        ) : (
          <>
            <p className="mt-2 text-sm text-muted-foreground">
              {state.decision === "pass" ? copy.hypothetical : copy.entry} · {sample.bars[entry.index].time} · {money(entry.price)} USD
            </p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {comparisons.map((result) => (
                <article key={result.period} className="rounded-xl border bg-background p-4">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="font-medium">SMA {result.period}</h3>
                    <span className="font-mono text-2xl font-semibold">{signed(result.r, "R")}</span>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {result.exit ? `${exitLabel[result.exit.reason]} · ${sample.bars[result.exit.index].time}` : copy.openMark} · {money(result.mark)} USD
                  </p>
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t pt-3 text-xs sm:grid-cols-4">
                    {([[copy.gain, signed(result.gain * 100, "%")], [copy.duration, result.holding], ["MAE", signed(result.mae * 100, "%")], ["MFE", signed(result.mfe * 100, "%")]] as const).map(([label, value]) => (
                      <div key={label}><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 font-mono text-sm">{value}</dd></div>
                    ))}
                  </dl>
                </article>
              ))}
            </div>
          </>
        )}
      </section>
      <details className="rounded-xl border bg-card px-4 py-3 text-sm">
        <summary className="cursor-pointer font-medium">{copy.rules}</summary>
        <div className="mt-3 max-w-3xl space-y-2 leading-relaxed text-muted-foreground">
          <p>{imported ? copy.importedRules : copy.source}</p><p>{copy.sizing}</p><p>{copy.model}</p><p>{copy.metrics}</p>
        </div>
      </details>
    </>
  );
}

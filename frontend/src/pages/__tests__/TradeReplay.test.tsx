import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { PriceBar } from "@/lib/api";
import { REPLAY_CASES } from "@/lib/trade-replay";
import { TradeReplay } from "../TradeReplay";

vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: ({ data }: { data: PriceBar[] }) => <ol aria-label="Candles">
    {data.map((bar) => <li key={bar.time}>{bar.time}</li>)}
  </ol>,
}));

test("reveals no future bars before the decision and separates comparison from fills", () => {
  render(<TradeReplay />, { wrapper: MemoryRouter });
  const last = REPLAY_CASES[0].bars.at(-1)!.time;
  const next = screen.getByRole("button", { name: "Next session" });
  expect(next).toBeDisabled();
  expect(screen.queryByText(last)).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "SMA 10" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Pass" }));
  fireEvent.click(next);
  expect(screen.getAllByText(REPLAY_CASES[0].bars[28].time, { exact: true })).toHaveLength(1);
  expect(screen.queryByText(last)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Reveal to end" }));
  expect(screen.getByText("Hypothetical entry after passing", { exact: false })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "SMA 20" })).toBeInTheDocument();
  expect(screen.getByText("No fills yet.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Practice case"), { target: { value: "DEMO-B" } });
  expect(screen.getByRole("heading", { name: /DEMO-B/ })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "SMA 20" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Next session" })).toBeDisabled();
});

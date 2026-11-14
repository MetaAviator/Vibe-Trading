"use strict";
(() => {
  const model = JSON.parse(document.getElementById("prices-data").textContent);
  const select = document.getElementById("ticker");
  const body = document.getElementById("price-rows");
  const svg = document.getElementById("chart");
  const plot = document.getElementById("plot");
  const previous = document.getElementById("previous");
  const next = document.getElementById("next");
  const pageSize = 50;
  let page = 0;
  let chartWidth = 0;

  function svgNode(tag, attrs, text) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text !== undefined) node.textContent = text;
    plot.appendChild(node);
    return node;
  }

  function chart(rows) {
    // Use viewport-sized SVG units so axis labels stay readable on narrow screens.
    chartWidth = Math.round(svg.getBoundingClientRect().width);
    const width = chartWidth || 880;
    const height = Math.max(200, Math.round(width * 290 / 880));
    const left = 100, right = width - 18, top = 30, bottom = height - 52;
    const dateY = height - 20;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    plot.replaceChildren();
    document.getElementById("chart-title").textContent = select.value + " · 观测价格";
    const values = rows.map(row => Number(row[1]));
    const note = document.getElementById("chart-note");
    if (values.some(value => !Number.isFinite(value) || value <= 0)) {
      note.textContent = "价格超出近似绘图范围，请查看下方原始价格表格。";
      return;
    }
    note.textContent = rows.length === 1 ? "仅有一个观测点，无法显示价格趋势。" : "";
    let low = values[0], high = values[0];
    for (const value of values) { low = Math.min(low, value); high = Math.max(high, value); }
    const first = Date.parse(rows[0][0] + "T00:00:00Z");
    const last = Date.parse(rows[rows.length - 1][0] + "T00:00:00Z");
    for (const [y, label] of [[top, high], [bottom, low]]) {
      svgNode("line", {x1:left, x2:right, y1:y, y2:y, class:"grid-line"});
      svgNode("text", {x:left - 10, y:y + 4, "text-anchor":"end"}, label.toPrecision(6));
    }
    const points = rows.map((row, index) => {
      const day = Date.parse(row[0] + "T00:00:00Z");
      const x = last === first ? (left + right) / 2 : left + (day - first) / (last - first) * (right - left);
      const y = high === low ? (top + bottom) / 2 : bottom - (values[index] - low) / (high - low) * (bottom - top);
      return [x.toFixed(2), y.toFixed(2)];
    });
    svgNode("polyline", {points:points.map(p => p.join(",")).join(" "), class:"price-line"});
    if (rows.length === 1) {
      svgNode("circle", {cx:points[0][0], cy:points[0][1], r:4, class:"price-dot"});
      svgNode("text", {x:points[0][0], y:dateY, "text-anchor":"middle"}, rows[0][0]);
    } else {
      svgNode("text", {x:left, y:dateY}, rows[0][0]);
      svgNode("text", {x:right, y:dateY, "text-anchor":"end"}, rows[rows.length - 1][0]);
    }
  }

  function table() {
    const rows = model.series[select.value];
    body.replaceChildren();
    for (const values of rows.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = document.createElement("tr");
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
    const count = Math.ceil(rows.length / pageSize);
    document.getElementById("page").textContent = "第 " + (page + 1) + " / " + count + " 页 · 共 " + rows.length + " 行";
    previous.disabled = page === 0;
    next.disabled = page + 1 === count;
  }

  function changeTicker() {
    page = 0;
    const rows = model.series[select.value];
    document.getElementById("selection").textContent = select.value + " · " + rows[0][0] + " → " + rows[rows.length - 1][0] + " · " + rows.length + " 行";
    chart(rows);
    table();
  }
  previous.addEventListener("click", () => { if (page > 0) { page -= 1; table(); } });
  next.addEventListener("click", () => {
    if ((page + 1) * pageSize < model.series[select.value].length) { page += 1; table(); }
  });
  select.addEventListener("change", changeTicker);
  changeTicker();
  new ResizeObserver(([entry]) => {
    if (Math.round(entry.contentRect.width) !== chartWidth) chart(model.series[select.value]);
  }).observe(svg);
})();

// GET /api/intraday?t=STLA — one session of five-minute closes.
//
// Fetched when a reader asks for it rather than polled: intraday data for seven
// thousand symbols would be gigabytes a day of points almost nobody opens, and
// it goes stale in minutes. On demand it costs nothing to store and is current
// by construction.
//
// It also has to be a proxy rather than a direct call from the page: Yahoo
// sends no CORS headers, so the browser cannot fetch this itself. Yahoo also
// fingerprints the user agent — a full Chrome string is refused where the bare
// one is served, which is the same quirk the poller hit.
const SPARK = "https://query1.finance.yahoo.com/v7/finance/spark" +
              "?symbols={t}&range=1d&interval=5m";

module.exports = async function handler(req, res) {
  const t = String((req.query && req.query.t) || "").toUpperCase()
    .replace(/[^A-Z0-9.\-]/g, "").slice(0, 12);
  if (!t) return res.status(400).json({ error: "ticker required" });

  try {
    const r = await fetch(SPARK.replace("{t}", encodeURIComponent(t)), {
      headers: { "User-Agent": "Mozilla/5.0" },
    });
    if (!r.ok) throw new Error(`upstream ${r.status}`);
    const body = await r.json();
    const resp = body?.spark?.result?.[0]?.response?.[0];
    const ts = resp?.timestamp || [];
    const close = resp?.indicators?.quote?.[0]?.close || [];

    const series = [];
    for (let i = 0; i < ts.length; i++) {
      if (close[i] != null) series.push([ts[i] * 1000, Math.round(close[i] * 1e4) / 1e4]);
    }
    if (series.length < 2) return res.status(404).json({ error: "no session data" });

    // A day's move is measured from the prior close, not from the first print —
    // otherwise an opening gap silently disappears from the chart's own number.
    res.setHeader("Cache-Control", "public, max-age=60, s-maxage=60");
    return res.status(200).json({
      ticker: t,
      series,
      prev_close: resp?.meta?.previousClose ?? null,
      exchange: resp?.meta?.exchangeName ?? null,
      source: "Yahoo Finance spark, 5-minute intervals",
    });
  } catch (e) {
    return res.status(502).json({ error: "upstream unavailable" });
  }
};

(() => {
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  })[ch]);

  const fmtYield = value => {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(2)}%` : "—";
  };

  const fmtBps = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n > 0 ? "+" : ""}${n.toFixed(1)} bps`;
  };

  const fmtDate = value => {
    if (!value) return "—";
    const d = new Date(value.includes("T") ? value : `${value}T12:00:00`);
    return Number.isNaN(d.getTime()) ? esc(value) : d.toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"});
  };

  function policyRange(fed){
    const low = Number(fed?.targetLow);
    const high = Number(fed?.targetHigh);
    return Number.isFinite(low) && Number.isFinite(high) ? `${low.toFixed(2)}–${high.toFixed(2)}%` : "—";
  }

  function moveClass(value){
    const n = Number(value);
    if (!Number.isFinite(n) || n === 0) return "";
    return n > 0 ? "neg" : "pos";
  }

  function renderSummary(rates){
    const el = document.getElementById("rateSummary");
    if (!el) return;
    const treasury = rates.treasury || {};
    const yields = treasury.yields || {};
    const changes = treasury.changesBps || {};
    const fed = rates.fed || {};

    const cards = [
      {label:"FED TARGET RANGE", value:policyRange(fed), detail:"The overnight policy range set by the FOMC.", meta:fed.lastDecision?.date ? `Last decision: ${fmtDate(fed.lastDecision.date)}` : "Latest FOMC policy setting"},
      {label:"2-YEAR TREASURY", value:fmtYield(yields["2Y"]), detail:"Often reacts most to expectations for what the Fed will do next.", meta:`${fmtBps(changes["2Y"])} vs. prior Treasury day`, cls:moveClass(changes["2Y"])},
      {label:"10-YEAR TREASURY", value:fmtYield(yields["10Y"]), detail:"A key benchmark for mortgages, valuation and long-term borrowing costs.", meta:`${fmtBps(changes["10Y"])} vs. prior Treasury day`, cls:moveClass(changes["10Y"])},
      {label:"30-YEAR TREASURY", value:fmtYield(yields["30Y"]), detail:"Shows the market's longer-run view of inflation, growth and term risk.", meta:`${fmtBps(changes["30Y"])} vs. prior Treasury day`, cls:moveClass(changes["30Y"])},
    ];

    el.innerHTML = cards.map(card => `<article class="rateCard">
      <label>${esc(card.label)}</label>
      <strong>${esc(card.value)}</strong>
      <span>${esc(card.detail)}</span>
      <small class="${card.cls || ""}">${esc(card.meta)}</small>
    </article>`).join("");
  }

  function renderCurve(rates){
    const el = document.getElementById("yieldCurve");
    if (!el) return;
    const treasury = rates.treasury || {};
    const yields = treasury.yields || {};
    const changes = treasury.changesBps || {};
    const order = ["3M","6M","1Y","2Y","3Y","5Y","10Y","30Y"];

    el.innerHTML = order.map(term => `<div class="curvePoint">
      <small>${esc(term)}</small>
      <strong>${fmtYield(yields[term])}</strong>
      <span class="${moveClass(changes[term])}">${fmtBps(changes[term])}</span>
    </div>`).join("");
  }

  function curveMessage(treasury){
    const spread = Number(treasury?.twoTenSpreadBps);
    if (!Number.isFinite(spread)) return "Waiting for enough Treasury data to read the curve.";
    if (spread < -10) return `The 2-year yield is ${Math.abs(spread).toFixed(0)} bps above the 10-year. That is an inverted curve, usually a sign that investors expect slower growth or easier policy later.`;
    if (spread > 10) return `The 10-year yield is ${spread.toFixed(0)} bps above the 2-year. The curve is positively sloped, which generally signals more normal compensation for holding longer-term bonds.`;
    return `The 2-year and 10-year yields are only ${Math.abs(spread).toFixed(0)} bps apart. The curve is nearly flat, so markets are not pricing a large gap between near-term and longer-term rates.`;
  }

  function dailyMoveMessage(treasury){
    const changes = treasury?.changesBps || {};
    const two = Number(changes["2Y"]);
    const ten = Number(changes["10Y"]);
    if (!Number.isFinite(two) && !Number.isFinite(ten)) return "Waiting for a prior Treasury day to calculate daily rate moves.";

    const parts = [];
    if (Number.isFinite(two)) parts.push(`2-year ${two >= 0 ? "rose" : "fell"} ${Math.abs(two).toFixed(1)} bps`);
    if (Number.isFinite(ten)) parts.push(`10-year ${ten >= 0 ? "rose" : "fell"} ${Math.abs(ten).toFixed(1)} bps`);
    let meaning = "Small daily moves are normal.";
    if (Number.isFinite(ten) && ten >= 5) meaning = "Higher long-term yields can make mortgages and corporate borrowing more expensive and can pressure expensive growth-stock valuations.";
    if (Number.isFinite(ten) && ten <= -5) meaning = "Falling long-term yields can ease financial conditions and often help rate-sensitive and growth-oriented assets.";
    if (Number.isFinite(two) && Math.abs(two) > Math.abs(ten || 0) + 4) meaning = "The front end moved more than the 10-year, which often means investors are repricing the expected path of Fed policy.";
    return `${parts.join("; ")}. ${meaning}`;
  }

  function renderRead(rates){
    const el = document.getElementById("rateRead");
    if (!el) return;
    const treasury = rates.treasury || {};
    const fed = rates.fed || {};
    const decision = fed.lastDecision || {};

    el.innerHTML = `
      <article><label>CURVE SIGNAL</label><h4>What the 2Y–10Y spread is saying</h4><p>${esc(curveMessage(treasury))}</p></article>
      <article><label>TODAY'S RATE MOVE</label><h4>What changed in yields</h4><p>${esc(dailyMoveMessage(treasury))}</p></article>
      <article><label>LAST FED DECISION</label><h4>${esc(decision.headline || "Waiting for the latest FOMC statement")}</h4><p>${policyRange(fed) !== "—" ? `The current target range is ${policyRange(fed)}. ` : ""}Market Espresso will refresh this automatically when the Fed publishes a new policy statement.</p>${decision.url ? `<a class="decisionLink" href="${esc(decision.url)}" target="_blank" rel="noopener">Read the Fed statement ↗</a>` : ""}</article>
    `;
  }

  function renderHeadlines(rates){
    const el = document.getElementById("ratesHeadlines");
    if (!el) return;
    const headlines = rates.headlines || [];
    el.innerHTML = headlines.slice(0,10).map(item => `<div class="headline">
      <a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.headline)}</a>
      <small>${esc(item.source || item.label || "Rates")} ${item.date ? `• ${esc(fmtDate(item.date))}` : ""}</small>
    </div>`).join("") || "<p>No Fed or rates headlines are available in the latest edition.</p>";
  }

  function renderSources(rates){
    const el = document.getElementById("ratesSource");
    if (!el) return;
    const treasury = rates.treasury || {};
    const fed = rates.fed || {};
    const dateText = treasury.date ? ` Treasury yields are the latest official daily par yield curve published for ${fmtDate(treasury.date)}.` : "";
    el.innerHTML = `Sources: <a href="${esc(treasury.sourceUrl || "https://home.treasury.gov/resource-center/data-chart-center/interest-rates")}" target="_blank" rel="noopener">U.S. Treasury</a> for the yield curve and <a href="${esc(fed.sourceUrl || "https://www.federalreserve.gov/monetarypolicy.htm")}" target="_blank" rel="noopener">Federal Reserve</a> for monetary-policy releases.${esc(dateText)}`;
  }

  async function hydrateRates(){
    try{
      const response = await fetch(`./data/edition.json?rates=${Date.now()}`, {cache:"no-store"});
      if(!response.ok) throw new Error(`edition.json returned ${response.status}`);
      const edition = await response.json();
      const rates = edition.rates || {};
      renderSummary(rates);
      renderCurve(rates);
      renderRead(rates);
      renderHeadlines(rates);
      renderSources(rates);
    }catch(error){
      const el = document.getElementById("rateSummary");
      if(el) el.innerHTML = `<p>Live Fed and Treasury data could not be loaded in this edition.</p>`;
      console.warn("Rates dashboard could not load:", error);
    }
  }

  hydrateRates();
})();

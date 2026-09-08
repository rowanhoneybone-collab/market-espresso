(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"})[ch]);
  const num = (value, digits=2) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
  const pct = value => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(1)}%`;
  const ratio = value => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(2);
  const dateTime = value => {
    if(!value) return "—";
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString(undefined,{month:"short",day:"numeric",year:"numeric",hour:"numeric",minute:"2-digit"});
  };

  function criterion(label, value, note){
    return `<article class="formulaCard"><label>${esc(label)}</label><strong>${esc(value)}</strong><p>${esc(note)}</p></article>`;
  }

  function passDot(ok, text){
    return `<span class="ruleDot ${ok ? "pass" : "fail"}">${ok ? "✓" : "×"} ${esc(text)}</span>`;
  }

  function row(record, near=false){
    const p = record.passes || {};
    const failed = Object.entries(p).filter(([,ok]) => !ok).map(([key]) => key);
    const failLabel = {revenue:"Revenue streak",roa:"ROA",dividend:"Dividend streak",debt:"Net debt/EBITDA",pe:"P/E"};
    return `<tr>
      <td><b>${esc(record.symbol)}</b><small>${esc(record.name || "")}</small></td>
      <td>${near ? `<span class="badge waiting">4 / 5</span><small>${esc(failed.map(k=>failLabel[k]||k).join(", "))}</small>` : `<span class="badge confirmed">5 / 5</span>`}</td>
      <td>${record.consecutiveRevenueGrowthYears == null ? "—" : `${record.consecutiveRevenueGrowthYears} yrs`}</td>
      <td>${pct(record.roaPct)}</td>
      <td>${record.dividendGrowthYears == null ? "—" : `${Math.round(record.dividendGrowthYears)} yrs`}</td>
      <td>${ratio(record.netDebtToEbitda)}</td>
      <td>${num(record.pe)}</td>
      <td><div class="ruleStrip">${passDot(p.revenue,"Rev")}${passDot(p.roa,"ROA")}${passDot(p.dividend,"Div")}${passDot(p.debt,"Debt")}${passDot(p.pe,"P/E")}</div></td>
    </tr>`;
  }

  function render(data){
    const c = data.criteria || {};
    $("formulaCriteria").innerHTML = [
      criterion("TOP-LINE GROWTH", `${c.minConsecutiveRevenueYears ?? 3}+ years`, "Consecutive completed fiscal years with higher revenue."),
      criterion("RETURN ON ASSETS", `≥ ${c.minRoaPct ?? 10}%`, "How efficiently the business turns its assets into profit."),
      criterion("DIVIDEND GROWTH", `${c.minDividendGrowthYears ?? 10}+ years`, "Consecutive years of dividend increases."),
      criterion("NET DEBT / EBITDA", `< ${c.maxNetDebtToEbitda ?? 4}`, "Lower leverage gives the company more financial breathing room."),
      criterion("P/E RATIO", `< ${c.maxPe ?? 25}`, "Avoid paying an extreme price even for a high-quality business."),
    ].join("");

    $("opportunityMeta").innerHTML = `
      <article><label>UNIVERSE</label><strong>${data.universeCount ?? "—"}</strong><span>10+ year U.S. dividend growers</span></article>
      <article><label>FULL FORMULA PASSES</label><strong>${data.winnerCount ?? 0}</strong><span>Stocks meeting all five rules</span></article>
      <article><label>LAST FULL SCREEN</label><strong class="metaDate">${dateTime(data.generatedAt)}</strong><span>Fundamentals change slowly; screen refreshes weekly</span></article>`;

    const winners = data.winners || [];
    $("opportunityWinners").innerHTML = winners.length ? `
      <div class="tableWrap"><table class="opTable"><thead><tr><th>Stock</th><th>Score</th><th>Revenue Growth</th><th>ROA</th><th>Dividend Growth</th><th>Net Debt/EBITDA</th><th>P/E</th><th>Rules</th></tr></thead>
      <tbody>${winners.map(r => row(r,false)).join("")}</tbody></table></div>`
      : `<div class="emptyState"><b>No stocks currently clear all five filters.</b><p>That is a valid screen result—not a signal to weaken the rules. Near misses are shown below.</p></div>`;

    const near = data.nearMisses || [];
    $("opportunityNearMisses").innerHTML = near.length ? `
      <div class="tableWrap"><table class="opTable"><thead><tr><th>Stock</th><th>Score</th><th>Revenue Growth</th><th>ROA</th><th>Dividend Growth</th><th>Net Debt/EBITDA</th><th>P/E</th><th>Rules</th></tr></thead>
      <tbody>${near.map(r => row(r,true)).join("")}</tbody></table></div>`
      : `<div class="emptyState"><b>No 4-of-5 near misses loaded.</b></div>`;

    $("opportunitySource").textContent = `Universe: ${data.universe || "10+ year dividend growers"}. Net debt/EBITDA is calculated as (total debt − cash) ÷ EBITDA. This is a research screen, not a buy list.`;
  }

  async function loadOpportunities(){
    try{
      const r = await fetch(`./data/opportunities.json?ts=${Date.now()}`, {cache:"no-store"});
      if(!r.ok) throw new Error(`opportunities.json returned ${r.status}`);
      render(await r.json());
    }catch(err){
      $("formulaCriteria").innerHTML = [
        criterion("TOP-LINE GROWTH","3+ years","Consecutive completed fiscal years with higher revenue."),
        criterion("RETURN ON ASSETS","≥ 10%","Doc's profitability threshold."),
        criterion("DIVIDEND GROWTH","10+ years","Consecutive annual dividend increases."),
        criterion("NET DEBT / EBITDA","< 4","Doc's leverage ceiling."),
        criterion("P/E RATIO","< 25","Doc's valuation ceiling."),
      ].join("");
      $("opportunityMeta").innerHTML = `<article><label>SCREEN STATUS</label><strong>Building</strong><span>The first full market screen has not published yet.</span></article>`;
      $("opportunityWinners").innerHTML = `<div class="emptyState"><b>Formula is configured; opportunity data is not published yet.</b><p>The screen will populate from the next successful opportunity refresh.</p></div>`;
      $("opportunityNearMisses").innerHTML = "";
      $("opportunitySource").textContent = "Doc's five rules are saved in Market Espresso and will remain visible even if the data source is temporarily unavailable.";
    }
  }

  loadOpportunities();
})();

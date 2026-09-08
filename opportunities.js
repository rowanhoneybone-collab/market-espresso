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
  const dateOnly = value => {
    if(!value) return "—";
    const d = new Date(`${value}T12:00:00`);
    return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined,{month:"short",day:"numeric"});
  };

  function criterion(label, value, note){
    return `<article class="formulaCard"><label>${esc(label)}</label><strong>${esc(value)}</strong><p>${esc(note)}</p></article>`;
  }

  function renderCriteria(c={}){
    $("formulaCriteria").innerHTML = [
      criterion("TOP-LINE GROWTH", `${c.minConsecutiveRevenueYears ?? 3}+ years`, "Consecutive completed fiscal years with higher revenue."),
      criterion("RETURN ON ASSETS", `≥ ${c.minRoaPct ?? 10}%`, "How efficiently the business turns its assets into profit."),
      criterion("DIVIDEND GROWTH", `${c.minDividendGrowthYears ?? 10}+ years`, "Consecutive years of dividend increases."),
      criterion("NET DEBT / EBITDA", `< ${c.maxNetDebtToEbitda ?? 4}`, "Lower leverage gives the company more financial breathing room."),
      criterion("P/E RATIO", `< ${c.maxPe ?? 25}`, "Avoid paying an extreme price even for a high-quality business."),
    ].join("");
  }

  function passDot(ok, text){
    return `<span class="ruleDot ${ok ? "pass" : "fail"}">${ok ? "✓" : "×"} ${esc(text)}</span>`;
  }

  function incomeCell(record){
    const yieldText = pct(record.dividendYieldPct);
    const exText = record.nextExDate ? dateOnly(record.nextExDate) : "Awaiting date";
    const amount = record.nextDividendAmount == null ? "" : ` · $${num(record.nextDividendAmount,2)}`;
    return `<b>${yieldText}</b><small>Ex-div: ${esc(exText)}${amount}</small>`;
  }

  function row(record, near=false){
    const p = record.passes || {};
    const failed = Object.entries(p).filter(([,ok]) => !ok).map(([key]) => key);
    const failLabel = {revenue:"Revenue streak",roa:"ROA",dividend:"Dividend streak",debt:"Net debt/EBITDA",pe:"P/E"};
    const divYears = record.dividendGrowthYears == null ? "—" : `${Math.round(record.dividendGrowthYears)}${record.dividendGrowthYearsIsFloor ? "+" : ""} yrs`;
    return `<tr>
      <td><b>${esc(record.symbol)}</b><small>${esc(record.name || "")}</small></td>
      <td>${near ? `<span class="badge waiting">4 / 5</span><small>${esc(failed.map(k=>failLabel[k]||k).join(", "))}</small>` : `<span class="badge confirmed">5 / 5</span>`}</td>
      <td>${record.consecutiveRevenueGrowthYears == null ? "—" : `${record.consecutiveRevenueGrowthYears} yrs`}</td>
      <td>${pct(record.roaPct)}</td>
      <td>${esc(divYears)}</td>
      <td>${ratio(record.netDebtToEbitda)}</td>
      <td>${num(record.pe)}</td>
      <td>${incomeCell(record)}</td>
      <td><div class="ruleStrip">${passDot(p.revenue,"Rev")}${passDot(p.roa,"ROA")}${passDot(p.dividend,"Div")}${passDot(p.debt,"Debt")}${passDot(p.pe,"P/E")}</div></td>
    </tr>`;
  }

  function renderUnavailable(data={}){
    renderCriteria(data.criteria || {});
    $("opportunityMeta").innerHTML = `
      <article><label>SCREEN STATUS</label><strong>Unavailable</strong><span>The latest automated screen could not be completed.</span></article>
      <article><label>FORMULA</label><strong>5 rules saved</strong><span>The thresholds remain active and unchanged.</span></article>
      <article><label>LAST ATTEMPT</label><strong class="metaDate">${dateTime(data.generatedAt)}</strong><span>Market Espresso will retry on the next scheduled run.</span></article>`;
    $("opportunityWinners").innerHTML = `<div class="emptyState"><b>Do not interpret this as “zero qualifying stocks.”</b><p>The outside screening source did not return a usable dataset on this run, so Market Espresso is withholding results rather than showing a false zero.</p></div>`;
    $("opportunityNearMisses").innerHTML = "";
    $("opportunitySource").textContent = data.error ? `Latest data-source message: ${data.error}` : "The latest opportunity screen was unavailable.";
  }

  function render(data){
    if(data?.status && data.status !== "ok"){
      renderUnavailable(data);
      return;
    }
    const c = data.criteria || {};
    renderCriteria(c);

    $("opportunityMeta").innerHTML = `
      <article><label>UNIVERSE</label><strong>${data.universeCount ?? "—"}</strong><span>10+ year U.S. dividend growers</span></article>
      <article><label>FULL FORMULA PASSES</label><strong>${data.winnerCount ?? 0}</strong><span>Stocks meeting all five rules</span></article>
      <article><label>LAST FULL SCREEN</label><strong class="metaDate">${dateTime(data.generatedAt)}</strong><span>Fundamentals change slowly; screen refreshes weekly</span></article>`;

    const winners = data.winners || [];
    $("opportunityWinners").innerHTML = winners.length ? `
      <div class="tableWrap"><table class="opTable"><thead><tr><th>Stock</th><th>Score</th><th>Revenue Growth</th><th>ROA</th><th>Dividend Growth</th><th>Net Debt/EBITDA</th><th>P/E</th><th>Income</th><th>Rules</th></tr></thead>
      <tbody>${winners.map(r => row(r,false)).join("")}</tbody></table></div>`
      : `<div class="emptyState"><b>No stocks currently clear all five filters.</b><p>That is a valid screen result—not a signal to weaken the rules. Near misses are shown below.</p></div>`;

    const near = data.nearMisses || [];
    $("opportunityNearMisses").innerHTML = near.length ? `
      <div class="tableWrap"><table class="opTable"><thead><tr><th>Stock</th><th>Score</th><th>Revenue Growth</th><th>ROA</th><th>Dividend Growth</th><th>Net Debt/EBITDA</th><th>P/E</th><th>Income</th><th>Rules</th></tr></thead>
      <tbody>${near.map(r => row(r,true)).join("")}</tbody></table></div>`
      : `<div class="emptyState"><b>No 4-of-5 near misses loaded.</b></div>`;

    $("opportunitySource").textContent = `Universe: ${data.universe || "10+ year dividend growers"}. Net debt/EBITDA is calculated as net debt ÷ trailing EBITDA. The Income column shows the current indicated dividend yield and a confirmed upcoming ex-date when the data feed has one. This is a research screen, not a buy list.`;
  }

  async function loadOpportunities(){
    try{
      const r = await fetch(`./data/opportunities.json?ts=${Date.now()}`, {cache:"no-store"});
      if(!r.ok) throw new Error(`opportunities.json returned ${r.status}`);
      render(await r.json());
    }catch(err){
      renderUnavailable({error:"The first full opportunity dataset has not published yet."});
    }
  }

  loadOpportunities();
})();

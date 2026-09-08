(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#039;"})[ch]);
  const num = (value, digits=2) => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits);
  const pct = value => value == null || !Number.isFinite(Number(value)) ? "—" : `${Number(value).toFixed(1)}%`;
  const ratio = value => value == null || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(2);
  const money = value => value == null || !Number.isFinite(Number(value)) ? "—" : new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value));
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

  let currentData = null;
  let activeSector = "All";
  let searchQuery = "";

  const failLabel = {revenue:"Revenue streak",roa:"ROA",dividend:"Dividend streak",debt:"Net debt/EBITDA",pe:"P/E"};

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

  function scoreBadge(record, near=false){
    const p = record.passes || {};
    const failed = Object.entries(p).filter(([,ok]) => !ok).map(([key]) => key);
    if(record.passCount === 5 || (!near && record.meetsFormula)) return `<span class="badge confirmed">5 / 5</span>`;
    if(record.passCount === 4 || near) return `<span class="badge waiting">4 / 5</span><small>${esc(failed.map(k=>failLabel[k]||k).join(", "))}</small>`;
    return `<span class="scorePill">${Number(record.passCount ?? 0)} / 5</span><small>${esc(failed.map(k=>failLabel[k]||k).join(", "))}</small>`;
  }

  function row(record, near=false){
    const p = record.passes || {};
    const divYears = record.dividendGrowthYears == null ? "—" : `${Math.round(record.dividendGrowthYears)}${record.dividendGrowthYearsIsFloor ? "+" : ""} yrs`;
    return `<tr>
      <td><b>${esc(record.symbol)}</b><small>${esc(record.name || "")}</small></td>
      <td class="priceCell"><b>${money(record.currentPrice)}</b></td>
      <td><span class="sectorTag">${esc(record.sector || "Other")}</span></td>
      <td>${scoreBadge(record,near)}</td>
      <td>${record.consecutiveRevenueGrowthYears == null ? "—" : `${record.consecutiveRevenueGrowthYears} yrs`}</td>
      <td>${pct(record.roaPct)}</td>
      <td>${esc(divYears)}</td>
      <td>${ratio(record.netDebtToEbitda)}</td>
      <td>${num(record.pe)}</td>
      <td>${incomeCell(record)}</td>
      <td><div class="ruleStrip">${passDot(p.revenue,"Rev")}${passDot(p.roa,"ROA")}${passDot(p.dividend,"Div")}${passDot(p.debt,"Debt")}${passDot(p.pe,"P/E")}</div></td>
    </tr>`;
  }

  function table(records, near=false){
    return `<div class="tableWrap"><table class="opTable"><thead><tr><th>Stock</th><th>Price</th><th>Sector</th><th>Score</th><th>Revenue Growth</th><th>ROA</th><th>Dividend Growth</th><th>Net Debt/EBITDA</th><th>P/E</th><th>Income</th><th>Rules</th></tr></thead><tbody>${records.map(r=>row(r,near)).join("")}</tbody></table></div>`;
  }

  function recordMatches(record){
    const sectorOk = activeSector === "All" || (record.sector || "Other") === activeSector;
    if(!sectorOk) return false;
    const q = searchQuery.trim().toLowerCase();
    if(!q) return true;
    return String(record.symbol || "").toLowerCase().includes(q) || String(record.name || "").toLowerCase().includes(q);
  }

  function sectorGroups(records){
    const groups = {};
    records.forEach(record => {
      const sector = record.sector || "Other";
      (groups[sector] ||= []).push(record);
    });
    return Object.entries(groups).sort((a,b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  }

  function renderSectorBreakdown(data){
    const winners = data.winners || [];
    const counts = {};
    winners.forEach(record => {
      const sector = record.sector || "Other";
      counts[sector] = (counts[sector] || 0) + 1;
    });
    const buttons = [["All",winners.length], ...Object.entries(counts).sort((a,b)=>b[1]-a[1] || a[0].localeCompare(b[0]))];
    $("opportunitySectors").innerHTML = buttons.map(([sector,count]) => `<button class="sectorChip ${activeSector === sector ? "active" : ""}" data-sector="${esc(sector)}"><b>${esc(sector)}</b><span>${count}</span></button>`).join("");
    $("opportunitySectors").querySelectorAll(".sectorChip").forEach(button => {
      button.addEventListener("click", () => {
        activeSector = button.dataset.sector || "All";
        renderSectorBreakdown(data);
        renderTables(data);
      });
    });
  }

  function searchResultCard(record){
    const p = record.passes || {};
    const failed = Object.entries(p).filter(([,ok])=>!ok).map(([key])=>failLabel[key]||key);
    return `<article class="stockSearchHit">
      <div class="searchHitTop"><div><b>${esc(record.symbol)}</b><span>${esc(record.name || "")}</span></div><strong>${money(record.currentPrice)}</strong></div>
      <div class="searchHitMeta"><span>${esc(record.sector || "Other")}</span><span>${Number(record.passCount ?? 0)} / 5 rules</span><span>Yield ${pct(record.dividendYieldPct)}</span></div>
      <div class="ruleStrip">${passDot(p.revenue,"Rev")}${passDot(p.roa,"ROA")}${passDot(p.dividend,"Div")}${passDot(p.debt,"Debt")}${passDot(p.pe,"P/E")}</div>
      <small>${failed.length ? `Misses: ${esc(failed.join(", "))}` : "Clears all five requirements."}</small>
    </article>`;
  }

  function renderSearch(data){
    const holder = $("stockSearchResults");
    if(!holder) return;
    const q = searchQuery.trim().toLowerCase();
    if(!q){
      holder.innerHTML = "";
      return;
    }
    const universe = data.screenedStocks || [...(data.winners || []), ...(data.nearMisses || [])];
    const matches = universe.filter(record => String(record.symbol || "").toLowerCase().includes(q) || String(record.name || "").toLowerCase().includes(q)).sort((a,b) => {
      const aExact = String(a.symbol || "").toLowerCase() === q ? 1 : 0;
      const bExact = String(b.symbol || "").toLowerCase() === q ? 1 : 0;
      return bExact - aExact || Number(b.passCount || 0) - Number(a.passCount || 0) || String(a.symbol).localeCompare(String(b.symbol));
    }).slice(0,8);
    holder.innerHTML = matches.length ? matches.map(searchResultCard).join("") : `<div class="searchEmpty">No screened dividend grower matched “${esc(searchQuery)}”.</div>`;
  }

  function renderTables(data){
    const winners = (data.winners || []).filter(recordMatches);
    if(winners.length){
      $("opportunityWinners").innerHTML = sectorGroups(winners).map(([sector,records]) => `<section class="sectorGroup"><div class="sectorGroupHead"><h4>${esc(sector)}</h4><span>${records.length} match${records.length === 1 ? "" : "es"}</span></div>${table(records,false)}</section>`).join("");
    }else{
      const filterText = searchQuery || activeSector !== "All" ? "No 5/5 matches fit the current search/sector filter." : "No stocks currently clear all five filters.";
      $("opportunityWinners").innerHTML = `<div class="emptyState"><b>${esc(filterText)}</b><p>${searchQuery || activeSector !== "All" ? "Clear the search or choose All sectors to restore the full list." : "That is a valid screen result—not a signal to weaken the rules. Near misses are shown below."}</p></div>`;
    }

    const near = (data.nearMisses || []).filter(recordMatches);
    $("opportunityNearMisses").innerHTML = near.length ? table(near,true) : `<div class="emptyState"><b>No 4-of-5 near misses fit the current filter.</b></div>`;
  }

  function wireControls(data){
    const input = $("opportunitySearch");
    if(input){
      input.value = searchQuery;
      input.oninput = event => {
        searchQuery = event.target.value || "";
        renderSearch(data);
        renderTables(data);
      };
    }
    renderSectorBreakdown(data);
    renderSearch(data);
  }

  function renderUnavailable(data={}){
    renderCriteria(data.criteria || {});
    $("opportunityMeta").innerHTML = `
      <article><label>SCREEN STATUS</label><strong>Unavailable</strong><span>The latest automated screen could not be completed.</span></article>
      <article><label>FORMULA</label><strong>5 rules saved</strong><span>The thresholds remain active and unchanged.</span></article>
      <article><label>LAST ATTEMPT</label><strong class="metaDate">${dateTime(data.generatedAt)}</strong><span>Market Espresso will retry on the next scheduled run.</span></article>`;
    $("opportunityWinners").innerHTML = `<div class="emptyState"><b>Do not interpret this as “zero qualifying stocks.”</b><p>The outside screening source did not return a usable dataset on this run, so Market Espresso is withholding results rather than showing a false zero.</p></div>`;
    $("opportunityNearMisses").innerHTML = "";
    if($("opportunitySectors")) $("opportunitySectors").innerHTML = "";
    if($("stockSearchResults")) $("stockSearchResults").innerHTML = "";
    $("opportunitySource").textContent = data.error ? `Latest data-source message: ${data.error}` : "The latest opportunity screen was unavailable.";
  }

  function render(data){
    currentData = data;
    if(data?.status && data.status !== "ok"){
      renderUnavailable(data);
      return;
    }
    const c = data.criteria || {};
    renderCriteria(c);

    $("opportunityMeta").innerHTML = `
      <article><label>UNIVERSE</label><strong>${data.universeCount ?? "—"}</strong><span>10+ year U.S. dividend growers</span></article>
      <article><label>FULL FORMULA PASSES</label><strong>${data.winnerCount ?? 0}</strong><span>Stocks meeting all five rules</span></article>
      <article><label>LAST FULL SCREEN</label><strong class="metaDate">${dateTime(data.generatedAt)}</strong><span>Prices and fundamentals refresh after market close on weekdays</span></article>`;

    wireControls(data);
    renderTables(data);

    $("opportunitySource").textContent = `Universe: ${data.universe || "10+ year dividend growers"}. Price is the latest share price captured by the screener refresh. Net debt/EBITDA is calculated as net debt ÷ trailing EBITDA. The Income column shows the current indicated dividend yield and a confirmed upcoming ex-date when the data feed has one. This is a research screen, not a buy list.`;
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

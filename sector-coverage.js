(() => {
  const fmtPct = value => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
  };

  const fmtPrice = value => {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "—";
    return n >= 1000
      ? n.toLocaleString(undefined, {maximumFractionDigits: 0})
      : n.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  };

  const esc = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"
  })[ch]);

  const dedupeBySymbol = items => {
    const seen = new Set();
    return (items || []).filter(item => {
      if (!item?.symbol || seen.has(item.symbol)) return false;
      seen.add(item.symbol);
      return true;
    });
  };

  function renderCards(containerId, companies, market, holdings) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const quoteMap = Object.fromEntries((market || []).map(item => [item.symbol, item]));
    const holdingSymbols = new Set((holdings || []).map(item => item.symbol));

    container.innerHTML = dedupeBySymbol(companies).map(item => {
      const quote = quoteMap[item.symbol] || {};
      const change = Number(quote.changePct);
      const moveClass = Number.isFinite(change) ? (change >= 0 ? "pos" : "neg") : "";
      const owned = holdingSymbols.has(item.symbol);

      return `<article class="sectorCard">
        <div class="sectorCardTop">
          <div>
            <label>${esc(item.group || "Company")}</label>
            <h3>${esc(item.name || item.symbol)}</h3>
            <small>${esc(item.symbol)}</small>
          </div>
          ${owned ? '<span class="ownedBadge">IN YOUR PORTFOLIO</span>' : ""}
        </div>
        <div class="sectorQuote">
          <strong>${fmtPrice(quote.price)}</strong>
          <b class="${moveClass}">${fmtPct(quote.changePct)}</b>
        </div>
        <p>${esc(item.role || "")}</p>
        <div class="sectorWatch"><b>What to watch</b><span>${esc(item.watch || "Business execution, demand, margins and valuation.")}</span></div>
      </article>`;
    }).join("") || "<p>No broader sector companies are configured yet.</p>";
  }

  function renderHeadlines(containerId, companies, companyNews) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const symbols = new Set((companies || []).map(item => item.symbol));
    const relevant = (companyNews || [])
      .filter(item => symbols.has(item.label))
      .sort((a, b) => Number(b.datetime || 0) - Number(a.datetime || 0));

    container.innerHTML = relevant.slice(0, 8).map(item => `
      <div class="headline">
        <a href="${esc(item.url)}" target="_blank" rel="noopener">${esc(item.headline)}</a>
        <small>${esc(item.label)} • ${esc(item.source || "News")}</small>
      </div>`
    ).join("") || "<p>No new broader-sector headlines in the latest edition.</p>";
  }

  async function hydrateSectorRadar() {
    try {
      const response = await fetch(`./data/edition.json?sector=${Date.now()}`, {cache: "no-store"});
      if (!response.ok) return;
      const edition = await response.json();
      const config = edition.config || {};
      const sectors = config.sectorCoverage || {};
      const holdings = config.holdings || [];
      const companyNews = edition.portfolioNews || [];

      renderCards("energyCompanyCards", sectors.energy || [], edition.market || [], holdings);
      renderCards("aiHealthCompanyCards", sectors.aiHealth || [], edition.market || [], holdings);

      // Run after the base app renders so these broader-sector feeds replace
      // the old holdings-only headline lists.
      renderHeadlines("energyNews", sectors.energy || [], companyNews);
      renderHeadlines("aiHealthNews", sectors.aiHealth || [], companyNews);

      const energyCount = document.getElementById("energyCompanyCount");
      const aiCount = document.getElementById("aiHealthCompanyCount");
      if (energyCount) energyCount.textContent = `${dedupeBySymbol(sectors.energy || []).length} companies`;
      if (aiCount) aiCount.textContent = `${dedupeBySymbol(sectors.aiHealth || []).length} companies`;
    } catch (error) {
      console.warn("Sector Radar could not load:", error);
    }
  }

  function runAfterBaseEdition() {
    const status = document.getElementById("dataStatus");
    if (!status) {
      hydrateSectorRadar();
      return;
    }
    if (status.classList.contains("good") || status.classList.contains("error")) {
      hydrateSectorRadar();
      return;
    }
    const observer = new MutationObserver(() => {
      if (status.classList.contains("good") || status.classList.contains("error")) {
        observer.disconnect();
        hydrateSectorRadar();
      }
    });
    observer.observe(status, {attributes: true, attributeFilter: ["class"]});
  }

  runAfterBaseEdition();
})();

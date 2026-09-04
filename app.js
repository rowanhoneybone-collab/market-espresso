const HOLDINGS = {
  VOO: "Broad U.S. market exposure. A simple long-term anchor.",
  TEM: "Healthcare + AI growth. Watch revenue growth and the path to profit.",
  ITW: "High-quality industrial compounder. Watch margins, cash flow and dividend growth.",
  BP: "Energy income + oil exposure. Watch debt, operations and commodity prices.",
  TTE: "Integrated energy + dividends. Watch oil/LNG and shareholder returns.",
  SONY: "Gaming, entertainment and technology. Watch PlayStation, media and buybacks.",
  AIQ: "Diversified AI exposure. Watch AI spending and interest rates."
};

let edition = { market: [], news: [], portfolioNews: [], generatedAt: null };

function fmtPct(v){
  if(v == null || !Number.isFinite(Number(v))) return "—";
  v = Number(v);
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
function fmtPrice(v){
  if(v == null || !Number.isFinite(Number(v))) return "—";
  v = Number(v);
  return v >= 1000
    ? v.toLocaleString(undefined,{maximumFractionDigits:0})
    : v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
}
function setStatus(kind,title,text){
  const box = document.getElementById("dataStatus");
  box.className = `data-status ${kind}`;
  box.querySelector("b").textContent = title;
  document.getElementById("dataStatusText").textContent = text;
}
function get(name){ return (edition.market || []).find(x => x.name === name); }

async function load(){
  setStatus("loading","Loading latest edition…","Checking the newest data published by GitHub Actions.");
  try{
    const r = await fetch(`./data/edition.json?ts=${Date.now()}`, {cache:"no-store"});
    if(!r.ok) throw new Error(`edition.json returned ${r.status}`);
    edition = await r.json();
    render();
    const dt = edition.generatedAt ? new Date(edition.generatedAt) : null;
    setStatus("good","Latest edition loaded.",dt ? `Published ${dt.toLocaleString()}` : "Published by GitHub Actions.");
    document.getElementById("stamp").textContent = dt
      ? `Published ${dt.toLocaleTimeString([], {hour:"numeric",minute:"2-digit"})}`
      : "Published recently";
  }catch(e){
    setStatus("error","No market edition is published yet.","Run the GitHub Action once, then refresh this page.");
    render();
  }
}

function render(){
  document.getElementById("date").textContent = new Date().toLocaleDateString(undefined,{weekday:"long",year:"numeric",month:"long",day:"numeric"});

  const top = ["S&P 500","Nasdaq","Dow","VOO","TEM","BP","TTE"];
  document.getElementById("ticker").innerHTML = top.map(n => {
    const x = get(n) || {};
    const cls = Number(x.changePct || 0) >= 0 ? "pos" : "neg";
    return `<div class="tick"><span>${n}</span><b class="${cls}">${fmtPrice(x.price)} ${fmtPct(x.changePct)}</b></div>`;
  }).join("");

  const values = ["S&P 500","Nasdaq","Dow"].map(n => Number(get(n)?.changePct)).filter(Number.isFinite);
  const avg = values.length ? values.reduce((a,b)=>a+b,0)/values.length : 0;
  document.getElementById("mood").textContent =
    !values.length ? "⚪ Waiting for data" :
    avg > .6 ? "🟢 Risk-On" :
    avg > .05 ? "🟡 Positive" :
    avg < -.6 ? "🔴 Risk-Off" :
    avg < -.05 ? "🟠 Cautious" : "🟡 Mixed";

  document.getElementById("pulseTitle").textContent = !values.length
    ? "Waiting for market data."
    : avg >= 0 ? "Stocks are leaning higher." : "Stocks are under pressure.";

  document.getElementById("pulseText").textContent = values.length
    ? `S&P proxy ${fmtPct(get("S&P 500")?.changePct)}, Nasdaq proxy ${fmtPct(get("Nasdaq")?.changePct)}, Dow proxy ${fmtPct(get("Dow")?.changePct)}.`
    : "The next GitHub Action run will publish fresh market data here.";

  renderHeadlines();
  renderPortfolio();
}

function renderHeadlines(){
  const markets = edition.news || [];
  const pnews = edition.portfolioNews || [];
  const lead = markets[0] || pnews[0];

  if(lead){
    document.getElementById("leadTitle").textContent = lead.headline;
    document.getElementById("leadText").textContent = lead.summary || "A fresh market headline worth checking today.";
    document.getElementById("leadWhy").textContent = "Ask whether this changes interest rates, energy prices, company earnings, or the long-term story for one of your holdings.";
  } else {
    document.getElementById("leadTitle").textContent = "Waiting for today's published headlines.";
    document.getElementById("leadText").textContent = "Run the GitHub Action once to create the first updated edition.";
  }

  document.getElementById("headlineList").innerHTML = markets.slice(0,4).map(h => `
    <div class="headline">
      <a href="${h.url}" target="_blank" rel="noopener">${h.headline}</a>
      <small>${h.source || "News"}</small>
    </div>`).join("") || "<p>No published headlines yet.</p>";

  const energy = pnews.filter(h => ["BP","TTE"].includes(h.label));
  const aiHealth = pnews.filter(h => ["TEM","AIQ"].includes(h.label));

  document.getElementById("energyNews").innerHTML = energy.slice(0,6).map(h => `
    <div class="headline"><a href="${h.url}" target="_blank" rel="noopener">${h.headline}</a><small>${h.label} • ${h.source || ""}</small></div>`
  ).join("") || "<p>No new BP/TTE headlines in the latest edition.</p>";

  document.getElementById("aiHealthNews").innerHTML = aiHealth.slice(0,6).map(h => `
    <div class="headline"><a href="${h.url}" target="_blank" rel="noopener">${h.headline}</a><small>${h.label} • ${h.source || ""}</small></div>`
  ).join("") || "<p>No new TEM/AIQ headlines in the latest edition.</p>";

  const movers = (edition.market || [])
    .filter(x => ["VOO","TEM","ITW","BP","TTE","SONY","AIQ"].includes(x.name) && Number.isFinite(Number(x.changePct)))
    .sort((a,b) => Math.abs(Number(b.changePct)) - Math.abs(Number(a.changePct)));

  if(movers[0]){
    document.getElementById("portfolioTitle").textContent = `${movers[0].name} is your biggest move.`;
    document.getElementById("portfolioText").textContent = `${movers[0].name} is ${fmtPct(movers[0].changePct)} in the latest data. A price move matters less than whether the long-term business story changed.`;
  } else {
    document.getElementById("portfolioTitle").textContent = "Waiting for portfolio quotes.";
    document.getElementById("portfolioText").textContent = "Your holdings will appear after the first successful refresh.";
  }
}

function renderPortfolio(){
  document.getElementById("portfolioCards").innerHTML = ["VOO","TEM","ITW","BP","TTE","SONY","AIQ"].map(n => {
    const x = get(n) || {};
    const cls = Number(x.changePct || 0) >= 0 ? "pos" : "neg";
    return `<article class="card">
      <label>${n}</label><h3>${n}</h3>
      <div class="price">${fmtPrice(x.price)}</div>
      <b class="${cls}">${fmtPct(x.changePct)}</b>
      <p>${HOLDINGS[n]}</p>
      <small>${x.price ? "Latest published quote" : "Waiting for quote"}</small>
    </article>`;
  }).join("");
}

document.querySelectorAll("nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("nav button").forEach(x => x.classList.remove("active"));
  b.classList.add("active");
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
  document.getElementById(b.dataset.tab).classList.add("active");
});

document.getElementById("refresh").onclick = () => location.reload();
load();

"""Judge-facing India hazard-priority atlas for the VeriSwarm console.

The interactive geometry is deliberately labelled as an illustrative planning
overlay.  It is not a replacement for authoritative GIS data.  The page links
to the Indian government portals from which an operational reference bank
would be requested or downloaded.
"""

from __future__ import annotations

import html
from pathlib import Path


HAZARD_ASSETS: dict[str, tuple[str, str]] = {
    "flood-affected-reference.png": ("flood-affected-reference.png", "image/png"),
    "seismic-zones-reference.png": ("seismic-zones-reference.png", "image/png"),
    "landslide-prone-reference.png": ("landslide-prone-reference.png", "image/png"),
    "multi-hazard-risk-reference.jpg": ("multi-hazard-risk-reference.jpg", "image/jpeg"),
}


def hazard_asset(asset_root: Path, name: str) -> tuple[Path, str]:
    """Resolve one allow-listed reference image without permitting traversal."""

    item = HAZARD_ASSETS.get(name)
    if item is None:
        raise ValueError("hazard reference asset not found")
    root = asset_root.resolve(strict=True)
    path = (root / item[0]).resolve(strict=True)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError("hazard asset escapes configured root") from error
    return path, item[1]


def hazard_atlas_html(asset_root: Path) -> bytes:
    available = {
        name
        for name in HAZARD_ASSETS
        if (asset_root / HAZARD_ASSETS[name][0]).is_file()
    }
    cards = []
    references = [
        (
            "flood-affected-reference.png",
            "Flood-affected areas reference",
            "Historical summary image supplied for presentation context. Use NRSC/Bhuvan GIS layers for operations.",
        ),
        (
            "seismic-zones-reference.png",
            "India seismic-zone reference",
            "Static zonation reference. Operational planning must use the applicable official BIS/NDMA source.",
        ),
        (
            "landslide-prone-reference.png",
            "Landslide-prone belts reference",
            "Static map attributed to Geological Survey of India in the supplied source material.",
        ),
        (
            "multi-hazard-risk-reference.jpg",
            "Risk, exposure and vulnerability reference",
            "A contextual screenshot, not a machine-readable or live government layer.",
        ),
    ]
    for filename, title, caption in references:
        if filename not in available:
            continue
        cards.append(
            '<article class="reference-card">'
            f'<img src="/assets/hazards/{html.escape(filename)}" alt="{html.escape(title)}">'
            f'<div><h3>{html.escape(title)}</h3><p>{html.escape(caption)}</p></div></article>'
        )
    return HAZARD_TEMPLATE.replace("__REFERENCE_CARDS__", "".join(cards)).encode("utf-8")


HAZARD_TEMPLATE = r'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VeriSwarm India Rescue Coverage Atlas</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#06100f;--panel:#0c1817;--panel2:#102321;--line:#243b38;--text:#f0f9f6;--muted:#8da9a3;--mint:#4de8ad;--blue:#55b9ff;--red:#ff6b6b;--amber:#ffba57;--magenta:#dd73ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% -10%,#183e34 0,transparent 34%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}.shell{max-width:1500px;margin:auto;padding:22px 28px 64px}header{display:flex;align-items:center;gap:14px;border-bottom:1px solid var(--line);padding:8px 0 22px}.brand{font-size:20px;font-weight:800;letter-spacing:-.03em}.brand b{color:var(--mint)}nav{display:flex;gap:6px;margin-left:auto}nav a{padding:7px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted);text-decoration:none;font:11px JetBrains Mono,monospace}nav a.active{color:var(--mint);border-color:#297d60;background:#0b201b}.hero{display:grid;grid-template-columns:1.5fr .7fr;gap:20px;align-items:end;padding:38px 0 24px}.eyebrow{color:var(--mint);font:600 11px JetBrains Mono,monospace;letter-spacing:.13em}.hero h1{font-size:clamp(34px,4.3vw,64px);line-height:1.02;letter-spacing:-.055em;margin:12px 0 14px;max-width:900px}.hero p{color:var(--muted);font-size:17px;max-width:800px;margin:0}.hero-stat{border:1px solid var(--line);background:linear-gradient(140deg,#10231f,#091413);padding:18px;border-radius:12px}.hero-stat .number{font:700 34px JetBrains Mono,monospace;color:var(--mint)}.hero-stat small{display:block;color:var(--muted)}.warning{border:1px solid #78562c;background:#21180d;color:#f0d1a1;border-radius:9px;padding:12px 15px;margin-bottom:18px}.warning b{color:var(--amber)}.workspace{display:grid;grid-template-columns:310px minmax(580px,1fr) 330px;gap:14px}.panel{border:1px solid var(--line);background:linear-gradient(155deg,rgba(16,34,31,.97),rgba(8,18,17,.97));border-radius:11px;overflow:hidden}.panel-head{display:flex;align-items:center;gap:9px;padding:13px 15px;border-bottom:1px solid var(--line);font:600 11px JetBrains Mono,monospace;color:var(--muted);letter-spacing:.08em;text-transform:uppercase}.dot{width:7px;height:7px;border-radius:50%;background:var(--mint);box-shadow:0 0 12px var(--mint)}.layers{padding:12px}.layer{display:grid;grid-template-columns:32px 1fr auto;gap:10px;align-items:center;padding:10px;border:1px solid transparent;border-radius:8px;cursor:pointer}.layer:hover{background:#122421;border-color:var(--line)}.swatch{width:27px;height:7px;border-radius:5px}.layer strong{display:block;font-size:13px}.layer small{color:var(--muted)}.switch{width:34px;height:19px;background:#213531;border-radius:20px;position:relative}.switch:after{content:"";position:absolute;left:3px;top:3px;width:13px;height:13px;background:#76918b;border-radius:50%;transition:.15s}.layer.on .switch{background:#226c53}.layer.on .switch:after{left:18px;background:var(--mint)}.base-row{display:flex;gap:7px;padding:0 12px 12px}.base-row button,.action{border:1px solid var(--line);background:#10231f;color:var(--text);padding:8px 10px;border-radius:6px;font-weight:600;cursor:pointer}.base-row button.active{border-color:var(--mint);color:var(--mint)}#map{height:650px;background:#08110f}.leaflet-container{font-family:Inter,sans-serif}.leaflet-popup-content-wrapper,.leaflet-popup-tip{background:#10201e;color:var(--text)}.leaflet-popup-content{min-width:220px}.leaflet-control-layers{background:#0d1b19!important;color:var(--text)!important;border-color:var(--line)!important}.region{padding:15px}.tier{display:inline-flex;padding:4px 7px;border:1px solid #7c5d31;color:var(--amber);border-radius:4px;font:600 10px JetBrains Mono,monospace}.region h2{font-size:23px;line-height:1.1;margin:11px 0 5px}.region .place{color:var(--muted);font:11px JetBrains Mono,monospace}.region p{color:#b9cec9}.checklist{padding:0;margin:12px 0 0;list-style:none}.checklist li{border-top:1px solid var(--line);padding:8px 0;color:#c5d7d3}.checklist li:before{content:"+";color:var(--mint);margin-right:8px;font:700 12px JetBrains Mono,monospace}.acquire{margin:15px;width:calc(100% - 30px);background:var(--mint);border-color:var(--mint);color:#04110d}.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}.metric{border:1px solid var(--line);border-radius:8px;background:#0b1716;padding:13px}.metric label{display:block;color:var(--muted);font:10px JetBrains Mono,monospace;text-transform:uppercase}.metric strong{display:block;font:700 20px JetBrains Mono,monospace;margin-top:5px}.explain{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:28px}.step{border:1px solid var(--line);border-radius:10px;padding:18px;background:#0b1716}.step .n{font:700 12px JetBrains Mono,monospace;color:var(--mint)}.step h3{margin:10px 0 7px}.step p{color:var(--muted);margin:0}.sources{margin-top:30px}.sources h2{font-size:28px;letter-spacing:-.03em;margin-bottom:7px}.sources>p{color:var(--muted);max-width:900px}.source-links{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:17px 0}.source-link{border:1px solid var(--line);background:#0b1716;padding:14px;border-radius:8px;text-decoration:none;color:var(--text)}.source-link b{display:block}.source-link span{display:block;color:var(--mint);font:10px JetBrains Mono,monospace;margin-top:7px}.references{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}.reference-card{display:grid;grid-template-columns:180px 1fr;gap:13px;border:1px solid var(--line);background:#0b1716;border-radius:9px;overflow:hidden}.reference-card img{width:180px;height:150px;object-fit:cover;background:#fff}.reference-card div{padding:12px 12px 12px 0}.reference-card h3{margin:0 0 7px;font-size:14px}.reference-card p{margin:0;color:var(--muted);font-size:12px}.footer-note{margin-top:20px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:15px}a{color:var(--mint)}@media(max-width:1120px){.workspace{grid-template-columns:270px 1fr}.detail-panel{grid-column:1/-1}.hero{grid-template-columns:1fr}.source-links{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){.shell{padding:15px}.workspace{grid-template-columns:1fr}.map-panel{grid-row:1}.layer-panel{grid-row:2}#map{height:500px}.metrics,.explain,.references,.source-links{grid-template-columns:1fr}.reference-card{grid-template-columns:130px 1fr}.reference-card img{width:130px}.hero h1{font-size:40px}header{align-items:flex-start;flex-wrap:wrap}nav{margin-left:0}}
</style></head><body><div class="shell"><header><div class="brand"><b>VERI</b>SWARM / RESCUE INTELLIGENCE</div><nav><a href="/rescue">Survivor video</a><a href="/">Visual geolocation</a><a class="active" href="/hazards">India hazard atlas</a></nav></header>
<section class="hero"><div><div class="eyebrow">NATIONAL CROSS-VIEW REFERENCE-BANK PLAN</div><h1>Map danger first.<br>Find survivors faster.</h1><p>An interactive planning atlas for deciding where India-specific satellite, drone-view and seasonal reference imagery should be acquired first.</p></div><div class="hero-stat"><div class="number">12</div><b>illustrative priority corridors</b><small>Flood · earthquake · landslide · multi-hazard intersections</small></div></section>
<div class="warning"><b>Presentation layer — not an operational hazard product.</b> The colored regions below are illustrative acquisition-priority overlays. Replace them with authoritative government GIS layers before field deployment.</div>
<section class="workspace"><aside class="panel layer-panel"><div class="panel-head"><span class="dot"></span>Planning overlays</div><div class="layers" id="layers"></div><div class="panel-head">Basemap</div><div class="base-row"><button class="active" data-base="street">Street</button><button data-base="satellite">Satellite</button></div></aside><section class="panel map-panel"><div class="panel-head"><span class="dot"></span>India disaster-imagery acquisition priority</div><div id="map"></div></section><aside class="panel detail-panel"><div class="panel-head"><span class="dot"></span>Selected corridor</div><div class="region"><span class="tier" id="tier">SELECT A REGION</span><h2 id="regionName">Explore the atlas</h2><div class="place" id="regionPlace">CLICK ANY COLORED OVERLAY</div><p id="regionWhy">The selection will show the reference imagery needed to make cross-view geolocation useful during a real disaster.</p><ul class="checklist" id="checklist"><li>Pre-event satellite reference</li><li>Post-event change imagery</li><li>Drone approach views</li><li>VIO/terrain confirmation</li></ul></div><button class="action acquire" id="brief">Download acquisition brief</button></aside></section>
<div class="metrics"><div class="metric"><label>Hazard families</label><strong>03</strong></div><div class="metric"><label>Priority corridors</label><strong>12</strong></div><div class="metric"><label>Operational GIS onboarded</label><strong>00</strong></div><div class="metric"><label>Control policy</label><strong>CANDIDATE</strong></div></div>
<section class="explain"><article class="step"><div class="n">01 / REFERENCE BANK</div><h3>Acquire official imagery</h3><p>Collect satellite tiles, elevation, hazard history and seasonal views for selected corridors.</p></article><article class="step"><div class="n">02 / CROSS VIEW</div><h3>Pair drone approaches</h3><p>Train the University-1652-style encoder on aerial approaches to those same geo-tagged cells.</p></article><article class="step"><div class="n">03 / SAFE USE</div><h3>Confirm, never blindly control</h3><p>Visual retrieval proposes a coordinate candidate; VIO, IMU and temporal consistency decide whether it is usable.</p></article></section>
<section class="sources"><h2>Authoritative data path</h2><p>For an operational build, replace every illustrative shape with machine-readable layers and imagery obtained through these primary Indian government portals.</p><div class="source-links"><a class="source-link" target="_blank" rel="noopener" href="https://www.nrsc.gov.in/nrscnew/Apps_DMS.php?lang_code=en"><b>NRSC Disaster Management</b><span>FLOOD / SATELLITE PRODUCTS ↗</span></a><a class="source-link" target="_blank" rel="noopener" href="https://bhuvan-app1.nrsc.gov.in/2dresources/bhuvanstore.php"><b>Bhuvan Store</b><span>IMAGERY / WMS / DEM ↗</span></a><a class="source-link" target="_blank" rel="noopener" href="https://bhusanket.gsi.gov.in/NLSM_10K_Map.html"><b>GSI Bhusanket</b><span>LANDSLIDE SUSCEPTIBILITY ↗</span></a><a class="source-link" target="_blank" rel="noopener" href="https://bhukosh.gsi.gov.in/Bhukosh/Public"><b>GSI Bhukosh</b><span>DOWNLOADABLE GEOSCIENCE ↗</span></a><a class="source-link" target="_blank" rel="noopener" href="https://www.bis.gov.in/wp-content/uploads/2022/11/Simplified_Guidelines_Sep_20211-1.pdf"><b>BIS / NDMA seismic guide</b><span>OFFICIAL SEISMIC ZONATION ↗</span></a><a class="source-link" target="_blank" rel="noopener" href="https://www.cwc.gov.in/en/publications"><b>Central Water Commission</b><span>FLOOD PUBLICATIONS / DATA ↗</span></a></div><h2>Reference material used for this pitch</h2><p>These static images provide presentation context only. They are shown alongside—not converted into—our interactive GIS overlay.</p><div class="references">__REFERENCE_CARDS__</div></section>
<div class="footer-note">VeriSwarm planning principle: prioritize survivor reporting from any credible view; use consensus to authorize movement, not to suppress a detection. All map candidates remain advisory until independent navigation checks agree.</div>
</div><script>
const colors={flood:'#55b9ff',earthquake:'#ff6b6b',landslide:'#ffba57',priority:'#dd73ff'};
const layerInfo={flood:['Flood corridors','Historical inundation + SAR reference'],earthquake:['Seismic corridors','Terrain + built-environment reference'],landslide:['Landslide belts','Slope + DEM + monsoon reference'],priority:['Multi-hazard intersections','Highest acquisition priority']};
const regions=[
{id:'brahmaputra',name:'Brahmaputra Valley',place:'Assam · Northeast India',type:'flood',tier:'TIER 1 · FLOOD',why:'Persistent flood exposure and cloud cover make seasonal optical plus SAR reference imagery essential.',shape:'polygon',coords:[[27.7,90.7],[27.3,96.2],[25.5,95.8],[24.9,91.0]],need:['Pre/post-monsoon optical tiles','Flood-season SAR acquisitions','Riverbank and settlement masks','Low-altitude drone approaches']},
{id:'ganga',name:'Middle Ganga Plain',place:'Eastern Uttar Pradesh · Bihar',type:'flood',tier:'TIER 1 · FLOOD',why:'Dense settlement and a wide historical flood footprint justify a high-resolution, season-aware reference bank.',shape:'polygon',coords:[[27.2,80.7],[26.8,88.0],[24.3,87.8],[24.7,81.3]],need:['Seasonal satellite mosaics','Flood annual/hazard layers','Settlement and access-road labels','Drone oblique views at multiple heights']},
{id:'delta',name:'Ganga–Brahmaputra Delta',place:'West Bengal coastal belt',type:'flood',tier:'TIER 1 · FLOOD',why:'Cyclone, river and coastal flooding overlap in a visually dynamic landscape.',shape:'circle',coords:[22.15,88.55],radius:155000,need:['Pre/post-cyclone satellite pairs','SAR water masks','Embankment and shelter inventory','Coastal drone trajectories']},
{id:'odisha',name:'Odisha Coastal Plain',place:'Odisha coast',type:'flood',tier:'TIER 2 · FLOOD',why:'Cyclone-driven inundation requires fresh post-event imagery and robust cloudy-weather sensing.',shape:'circle',coords:[20.25,86.1],radius:145000,need:['Cyclone flood products','Sentinel-1 style SAR references','Critical-infrastructure labels','Approach views to isolated settlements']},
{id:'himalaya',name:'Himalayan Seismic Arc',place:'J&K · Himachal · Uttarakhand · Sikkim',type:'earthquake',tier:'TIER 1 · SEISMIC',why:'High seismic exposure intersects steep terrain and fragile access routes.',shape:'polygon',coords:[[34.7,74.0],[32.3,79.5],[29.0,81.2],[27.0,88.7],[29.3,89.2],[33.7,77.0]],need:['High-resolution pre-event buildings','Post-event change imagery','DEM and slope constraints','Drone street-to-roof approach views']},
{id:'northeast',name:'Northeast Seismic Belt',place:'Arunachal Pradesh · Assam · Nagaland',type:'earthquake',tier:'TIER 1 · SEISMIC',why:'High seismicity and difficult terrain make alternative positioning and safe-route mapping especially valuable.',shape:'polygon',coords:[[29.4,91.0],[28.9,97.5],[23.2,95.7],[24.2,91.3]],need:['Cloud-tolerant satellite baseline','Road/bridge inventory','DEM and landslide co-layer','Multi-angle drone approaches']},
{id:'kutch',name:'Kutch Seismic Zone',place:'Western Gujarat',type:'earthquake',tier:'TIER 2 · SEISMIC',why:'Distinct terrain and major earthquake history support a regional reference-bank cell.',shape:'circle',coords:[23.6,69.8],radius:135000,need:['Built-up and terrain mosaics','Post-event damage pairs','Road and relief-camp candidates','Low/high altitude drone views']},
{id:'andaman',name:'Andaman–Nicobar Arc',place:'Island chain',type:'earthquake',tier:'TIER 2 · SEISMIC',why:'Seismic and tsunami exposure combine with sparse connectivity and island logistics.',shape:'polygon',coords:[[13.8,92.2],[13.1,93.2],[6.5,94.0],[6.1,92.5]],need:['Island satellite catalogue','Coastline change layers','Tsunami/evacuation routes','Drone-to-shore approach pairs']},
{id:'nwhills',name:'Northwest Himalayan Slopes',place:'Himachal Pradesh · Uttarakhand',type:'landslide',tier:'TIER 1 · LANDSLIDE',why:'Monsoon, slope and road-corridor failures demand elevation-aware and season-aware references.',shape:'polygon',coords:[[33.2,75.5],[32.0,80.5],[28.7,81.0],[29.5,76.4]],need:['GSI susceptibility layer','Cartosat DEM/slope tiles','Before/after monsoon imagery','Road-corridor drone traversals']},
{id:'nehills',name:'Northeast Hill Belt',place:'Sikkim · Arunachal · Meghalaya · Mizoram',type:'landslide',tier:'TIER 1 · LANDSLIDE',why:'Cloud, vegetation and steep terrain create hard cross-view matching conditions worth prioritizing.',shape:'polygon',coords:[[28.8,88.1],[29.0,96.8],[22.0,94.7],[22.9,88.9]],need:['GSI inventories and susceptibility','SAR + optical seasonal pairs','Road-cut and slope annotations','Oblique approaches under cloud']},
{id:'western-ghats',name:'Western Ghats & Konkan',place:'Maharashtra · Goa · Karnataka · Kerala',type:'landslide',tier:'TIER 1 · LANDSLIDE',why:'A long monsoon-sensitive corridor needs dense regional coverage instead of one generic India model.',shape:'polygon',coords:[[20.8,72.8],[18.1,74.6],[8.2,77.4],[8.2,76.1],[16.0,72.9]],need:['Monsoon and dry-season satellite pairs','Slope, rainfall and inventory layers','Settlement/access-road labels','Coastal and hillside drone approaches']},
{id:'north-bihar',name:'North Bihar Multi-hazard Cell',place:'Flood plain below Himalayan arc',type:'priority',tier:'TIER 0 · MULTI-HAZARD',why:'Flood exposure, seismic risk and constrained access overlap; this is a strong pilot for a national rescue reference bank.',shape:'circle',coords:[26.25,86.0],radius:90000,need:['Authoritative flood and seismic layers','Dense satellite gallery cells','Pre-scripted drone search lanes','VIO/terrain confirmation landmarks']}
];
const map=L.map('map',{zoomControl:true,minZoom:4,maxZoom:18}).setView([22.7,79.2],5);const street=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap contributors'}).addTo(map);const satellite=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Tiles &copy; Esri'});const groups={};let selected=regions[0];
for(const [key,[title,sub]] of Object.entries(layerInfo)){const row=document.createElement('div');row.className='layer on';row.dataset.layer=key;row.innerHTML=`<span class="swatch" style="background:${colors[key]}"></span><span><strong>${title}</strong><small>${sub}</small></span><span class="switch"></span>`;row.onclick=()=>{row.classList.toggle('on');const on=row.classList.contains('on');if(on)groups[key].addTo(map);else map.removeLayer(groups[key])};document.getElementById('layers').appendChild(row);groups[key]=L.layerGroup().addTo(map)}
regions.forEach(r=>{const style={color:colors[r.type],fillColor:colors[r.type],fillOpacity:r.type==='priority'?.38:.18,weight:r.type==='priority'?3:2,dashArray:r.type==='priority'?null:'6 5'};const layer=r.shape==='circle'?L.circle(r.coords,{...style,radius:r.radius}):L.polygon(r.coords,style);layer.addTo(groups[r.type]);layer.bindTooltip(r.name,{sticky:true});layer.on('click',()=>selectRegion(r,layer));r.layer=layer});
function selectRegion(r,layer){selected=r;document.getElementById('tier').textContent=r.tier;document.getElementById('regionName').textContent=r.name;document.getElementById('regionPlace').textContent=r.place.toUpperCase();document.getElementById('regionWhy').textContent=r.why;document.getElementById('checklist').innerHTML=r.need.map(x=>`<li>${x}</li>`).join('');layer.openTooltip()}
selectRegion(regions[0],regions[0].layer);document.querySelectorAll('[data-base]').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('[data-base]').forEach(x=>x.classList.remove('active'));btn.classList.add('active');if(btn.dataset.base==='satellite'){map.removeLayer(street);satellite.addTo(map)}else{map.removeLayer(satellite);street.addTo(map)}});
document.getElementById('brief').onclick=()=>{const brief={schema:'veriswarm.hazard_reference_bank_brief.v1',status:'illustrative_planning_brief_not_operational_gis',region:selected.name,place:selected.place,hazard:selected.type,priority:selected.tier,why:selected.why,required_inputs:selected.need,official_sources:['NRSC/Bhuvan Disaster Management','GSI Bhusanket/Bhukosh','applicable NDMA/BIS/CWC layers'],use_policy:'visual retrieval proposes a location candidate; VIO/IMU/temporal consistency must confirm it'};const blob=new Blob([JSON.stringify(brief,null,2)+'\n'],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`veriswarm-${selected.id}-acquisition-brief.json`;a.click();URL.revokeObjectURL(a.href)};
</script></body></html>'''

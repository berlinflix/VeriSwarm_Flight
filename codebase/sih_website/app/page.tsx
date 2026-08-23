"use client";

import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
} from "react";
import Image from "next/image";

const workflow = [
  {
    key: "sense",
    number: "01",
    title: "Sense",
    kicker: "USB webcam · Android DroidCam · RGB",
    body: "Local RGB sources capture timestamped frames, camera health and YOLO detections. Camera output remains evidence; it never directly becomes a flight command.",
  },
  {
    key: "understand",
    number: "02",
    title: "Understand",
    kicker: "YOLOv8n · Ultralytics · OpenCV",
    body: "Our working camera proof uses a pinned YOLOv8n baseline. The rescue-specific person model, held-out metrics and Jetson qualification remain an active integration lane.",
  },
  {
    key: "verify",
    number: "03",
    title: "Verify",
    kicker: "RANSAC homography · model hash",
    body: "Projected common regions support AGREE, DISPUTE and ABSTAIN while signed receipts and a model allowlist expose stale, inconsistent or unapproved evidence.",
  },
  {
    key: "coordinate",
    number: "04",
    title: "Coordinate",
    kicker: "FactoryCity · CoSys RPC",
    body: "A measured Point A-to-B route and five-agent, ten-cell contract define the integration target. The retained authorization-aware live run is still pending.",
  },
  {
    key: "respond",
    number: "05",
    title: "Respond",
    kicker: "Evidence-backed alerts",
    body: "The command centre turns retained events into coverage, priority alerts and a responder-ready incident record—not invented certainty.",
  },
];

const teamProgress = [
  {
    initials: "SU",
    owner: "Suyash",
    lane: "Trust & integration",
    status: "Integrated core",
    detail: "Rescue event schema, dashboard projection, SQLite outbox, multi-view fusion and OP-TEE model-trust path.",
  },
  {
    initials: "SA",
    owner: "Samik",
    lane: "Rescue perception",
    status: "Training + runtime",
    detail: "Dataset, training and inference lane; also operates the proven Windows P2 multi-camera runtime.",
  },
  {
    initials: "PR",
    owner: "Pratik",
    lane: "Simulation & autonomy",
    status: "Route contract built",
    detail: "FactoryCity Point A/B, measured 95.0474 m route and five-agent, ten-cell development contract; live qualification is pending.",
  },
  {
    initials: "AB",
    owner: "Abhijan",
    lane: "Security & C2",
    status: "Security gate built",
    detail: "Dashboard proof gate, clean/model-swap stages and fail-closed movement security policy; heatmap integration follows.",
  },
  {
    initials: "AY",
    owner: "Ayush",
    lane: "Multi-view validation",
    status: "2-source proof",
    detail: "USB webcam plus Android DroidCam, projected overlap, semantic states, internal evidence and release-safe capture.",
  },
];

const missionCells = [
  { id: "00", owner: "ALPHA", state: "complete" },
  { id: "01", owner: "BRAVO", state: "complete" },
  { id: "02", owner: "CHARLIE", state: "active" },
  { id: "03", owner: "DELTA", state: "active" },
  { id: "04", owner: "ECHO", state: "alert" },
  { id: "05", owner: "ALPHA", state: "blocked" },
  { id: "06", owner: "BRAVO", state: "complete" },
  { id: "07", owner: "CHARLIE", state: "active" },
  { id: "08", owner: "DELTA", state: "idle" },
  { id: "09", owner: "ECHO", state: "idle" },
];

function Arrow() {
  return <span className="button-icon" aria-hidden="true">↗</span>;
}

function UpArrow() {
  return <span className="button-icon" aria-hidden="true">↑</span>;
}

export default function Home() {
  const [activeStep, setActiveStep] = useState(2);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrollProgress, setScrollProgress] = useState(0);
  const heroRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const hero = heroRef.current;
    if (!hero) return;
    const move = (event: PointerEvent) => {
      const bounds = hero.getBoundingClientRect();
      const x = (event.clientX - bounds.left) / bounds.width - 0.5;
      const y = (event.clientY - bounds.top) / bounds.height - 0.5;
      hero.style.setProperty("--mx", x.toFixed(3));
      hero.style.setProperty("--my", y.toFixed(3));
    };
    const reset = () => {
      hero.style.setProperty("--mx", "0");
      hero.style.setProperty("--my", "0");
    };
    hero.addEventListener("pointermove", move);
    hero.addEventListener("pointerleave", reset);
    return () => {
      hero.removeEventListener("pointermove", move);
      hero.removeEventListener("pointerleave", reset);
    };
  }, []);

  useEffect(() => {
    const updateProgress = () => {
      const available = document.documentElement.scrollHeight - window.innerHeight;
      setScrollProgress(available > 0 ? Math.min(window.scrollY / available, 1) : 0);
    };
    updateProgress();
    window.addEventListener("scroll", updateProgress, { passive: true });
    window.addEventListener("resize", updateProgress);
    return () => {
      window.removeEventListener("scroll", updateProgress);
      window.removeEventListener("resize", updateProgress);
    };
  }, []);

  const scrollToSection = (id: string) => {
    setMenuOpen(false);
    if (id === "top") {
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const handleSectionLink = (id: string) => (event: ReactMouseEvent<HTMLAnchorElement>) => {
    event.preventDefault();
    scrollToSection(id);
  };

  return (
    <main>
      <div className="noise" aria-hidden="true" />
      <nav className="nav" aria-label="Primary navigation">
        <a className="brand" href="#top" onClick={handleSectionLink("top")}>
          <span className="brand-mark"><i /><i /><i /></span>
          <span>VERI<strong>SWARM</strong></span>
        </a>
        <button
          className="menu-button"
          aria-expanded={menuOpen}
          aria-controls="site-nav"
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span /> <span />
          <span className="sr-only">Toggle navigation</span>
        </button>
        <div id="site-nav" className={`nav-links ${menuOpen ? "open" : ""}`}>
          <a href="#problem" onClick={handleSectionLink("problem")}>Problem</a>
          <a href="#system" onClick={handleSectionLink("system")}>System</a>
          <a href="#evidence" onClick={handleSectionLink("evidence")}>Evidence</a>
          <a href="#progress" onClick={handleSectionLink("progress")}>Progress</a>
          <a href="#safety" onClick={handleSectionLink("safety")}>Safety</a>
          <a className="nav-cta" href="#mission" onClick={handleSectionLink("mission")}>View mission <Arrow /></a>
        </div>
        <div className="nav-progress" aria-hidden="true">
          <i style={{ transform: `scaleX(${scrollProgress})` }} />
        </div>
      </nav>

      <section id="top" className="hero" ref={heroRef}>
        <div className="hero-grid" aria-hidden="true" />
        <div className="hero-orbit orbit-one" aria-hidden="true" />
        <div className="hero-orbit orbit-two" aria-hidden="true" />
        <div className="hero-copy">
          <div className="eyebrow"><span /> SIH 26177 · OPERATION VARUNA</div>
          <h1>Search wider.<br /><em>Verify</em> before acting.</h1>
          <p>
            VeriSwarm Rescue is an offline-first, evidence-backed multi-drone system for
            finding person candidates, mapping hazards and keeping unsafe commands grounded.
          </p>
          <div className="hero-actions">
            <a className="button primary" href="#system" onClick={handleSectionLink("system")}>Explore the system <Arrow /></a>
            <a className="button ghost" href="#evidence" onClick={handleSectionLink("evidence")}>See the real camera proof</a>
          </div>
          <div className="proof-line">
            <span><i className="pulse" /> Integration build</span>
            <span>Offline-first</span>
            <span>Fail-closed safety</span>
          </div>
        </div>

        <div className="hero-visual" aria-label="Animated rescue drone command visual">
          <div className="drone-halo" />
          <Image
            className="hero-drone"
            src="/veriswarm-drone.png"
            alt="Rescue drone in flight"
            width={1536}
            height={1024}
            priority
          />
          <div className="scan-line" />
          <div className="telemetry telemetry-a">
            <small>FORMATION</small><b>5 / 5</b><span>SECTORS ASSIGNED</span>
          </div>
          <div className="telemetry telemetry-b">
            <small>TRUST GATE</small><b>HOLD-SAFE</b><span>MODEL + RECEIPT</span>
          </div>
          <div className="telemetry telemetry-c">
            <small>LINK MODE</small><b>LOCAL</b><span>NO INTERNET RELAY</span>
          </div>
          <div className="target target-a"><i /><span>PERSON CANDIDATE</span></div>
          <div className="target target-b"><i /><span>HAZARD</span></div>
        </div>
        <a className="scroll-cue" href="#problem" onClick={handleSectionLink("problem")}><span>Scroll to mission</span><i /></a>
      </section>

      <section className="metric-ribbon" aria-label="Project mission facts">
        <div><b>02</b><span>proven live camera sources</span></div>
        <div><b>03</b><span>camera evidence panels</span></div>
        <div><b>LOCAL</b><span>no cloud camera relay</span></div>
        <div><b>HOLD</b><span>fail-closed result</span></div>
      </section>

      <section id="problem" className="section problem-section">
        <header className="section-heading">
          <span className="section-number">01 / THE PROBLEM</span>
          <h2>Disaster zones break the assumptions ordinary autonomy depends on.</h2>
        </header>
        <div className="problem-layout">
          <div className="problem-story">
            <p className="lead">
              Floodwater blocks roads. Buildings hide people. Links disappear. A single camera
              can be wrong—and a compromised model can be confidently wrong.
            </p>
            <figure className="problem-photo">
              <Image
                src="/evidence/chennai-flood-rescue.jpg"
                alt="Indian Air Force personnel performing a winch rescue during the 2015 Chennai floods"
                width={1600}
                height={1437}
              />
              <figcaption>
                <strong>Real disaster context—not team test evidence.</strong>
                Chennai flood rescue by the Indian Air Force. Photo: {" "}
                <a
                  href="https://commons.wikimedia.org/wiki/File:Indian_Air_Force_Cheetah_helicopters_rescue_op_2015_Chennai_Flood_(cropped).jpg"
                  target="_blank"
                  rel="noreferrer"
                >
                  Indian Air Force / GODL-India via Wikimedia Commons
                </a>.
              </figcaption>
            </figure>
          </div>
          <div className="problem-grid">
            <article><span>01</span><h3>Limited visibility</h3><p>No one view can cover a damaged urban area or safely confirm every observation.</p></article>
            <article><span>02</span><h3>Disconnected teams</h3><p>Cloud-dependent coordination fails exactly when local responders need it most.</p></article>
            <article><span>03</span><h3>Untrusted perception</h3><p>A detection is evidence—not permission to move, classify or declare a survivor.</p></article>
            <article><span>04</span><h3>Lost time</h3><p>Unsearched cells and ambiguous alerts delay the people who can make the final call.</p></article>
          </div>
        </div>
      </section>

      <section id="system" className="section system-section">
        <div className="section-kicker">OUR SOLUTION</div>
        <header className="section-heading split">
          <h2>One evidence chain.<br />Five coordinated roles.</h2>
          <p>Observation, trust and motion remain deliberately separate. That is how VeriSwarm stays useful when one camera, model, vehicle or link becomes weak.</p>
        </header>
        <div className="workflow">
          <div className="workflow-tabs" role="tablist" aria-label="VeriSwarm workflow">
            {workflow.map((step, index) => (
              <button
                key={step.key}
                role="tab"
                aria-selected={activeStep === index}
                className={activeStep === index ? "active" : ""}
                onClick={() => setActiveStep(index)}
              >
                <span>{step.number}</span><b>{step.title}</b><i />
              </button>
            ))}
          </div>
          <div className="workflow-detail" role="tabpanel">
            <div className="detail-index">{workflow[activeStep].number}</div>
            <span className="detail-kicker">{workflow[activeStep].kicker}</span>
            <h3>{workflow[activeStep].title}</h3>
            <p>{workflow[activeStep].body}</p>
            <div className="signal-stack" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((item) => <i key={item} style={{ "--delay": `${item * 0.12}s` } as CSSProperties} />)}
            </div>
          </div>
        </div>
      </section>

      <section id="stack" className="section stack-section">
        <header className="section-heading split">
          <div><span className="section-number">02 / WHAT WE ACTUALLY USE</span><h2>The real build stack.</h2></div>
          <p>Every item is marked by its present status. “Next gate” means it is planned or under qualification—not already demonstrated.</p>
        </header>
        <div className="stack-grid">
          <article>
            <span className="stack-status used">USED IN CAMERA PROOF</span>
            <h3>Capture + perception</h3>
            <ul><li>USB webcam + Android DroidCam</li><li>Windows P2 · OpenCV</li><li>YOLOv8n · Ultralytics 8.4.56</li></ul>
          </article>
          <article>
            <span className="stack-status used">PROVEN</span>
            <h3>Geometry + evidence</h3>
            <ul><li>RANSAC 2-D homography</li><li>Projected polygons · view/box IoU</li><li>JSONL, internal video + release probes</li></ul>
          </article>
          <article>
            <span className="stack-status built">BUILT / INTEGRATING</span>
            <h3>Trust + edge</h3>
            <ul><li>Jetson Orin Nano hardware</li><li>OP-TEE signer · model SHA allowlist</li><li>Rescue TensorRT qualification pending</li></ul>
          </article>
          <article>
            <span className="stack-status next">NEXT LIVE GATE</span>
            <h3>Simulation + control</h3>
            <ul><li>Unreal Engine FactoryCity · CoSys RPC</li><li>Python · measured Point A/B route</li><li>Five-agent, ten-cell development contract</li></ul>
          </article>
          <article>
            <span className="stack-status built">BUILT</span>
            <h3>Dashboard + data</h3>
            <ul><li>React/Vite command dashboard</li><li>Rescue event JSON · SQLite outbox</li><li>Deterministic alerts and reports</li></ul>
          </article>
          <article>
            <span className="stack-status next">IN PROGRESS</span>
            <h3>Rescue model training</h3>
            <ul><li>Kaggle source audit</li><li>VisDrone / HIT-UAV where applicable</li><li>PyTorch + Ultralytics held-out evaluation</li></ul>
          </article>
        </div>
      </section>

      <section id="evidence" className="section evidence-section">
        <header className="section-heading split">
          <div><span className="section-number">03 / REAL TEAM EVIDENCE</span><h2>Not mockups. This is our camera rig running.</h2></div>
          <p>Captured on Samik’s Windows P2 using one USB webcam and one Android phone through DroidCam.</p>
        </header>
        <div className="evidence-grid">
          <figure className="evidence-card agree">
            <Image src="/evidence/covis-agree.jpg" alt="VeriSwarm three-panel camera application showing an AGREE result" width={1600} height={1200} />
            <figcaption><span>AGREE</span><h3>Both views support the same class.</h3><p>The third panel shows the homography-projected shared region and displayed overlap metrics.</p></figcaption>
          </figure>
          <figure className="evidence-card dispute">
            <Image src="/evidence/covis-dispute.jpg" alt="VeriSwarm three-panel camera application showing a DISPUTE result" width={1600} height={1200} />
            <figcaption><span>DISPUTE</span><h3>Co-visible views make different claims.</h3><p>The system preserves the disagreement instead of forcing a presentation-friendly pass.</p></figcaption>
          </figure>
          <figure className="evidence-card model">
            <Image src="/evidence/model-startup.jpg" alt="VeriSwarm terminal output showing the semantic run mode and YOLO model SHA-256" width={1600} height={1200} />
            <figcaption><span>MODEL IDENTITY</span><h3>The run records what actually executed.</h3><p>Startup output binds the semantic mode to a selected model and its SHA-256 identity.</p></figcaption>
          </figure>
        </div>
        <div className="evidence-note"><b>Honest boundary:</b> these photos use COCO baseline objects to prove capture, projected geometry, semantic decisions and cleanup. They do not claim the final rescue-trained person model is already qualified.</div>
      </section>

      <section id="mission" className="section mission-section">
        <header className="section-heading split">
          <div><span className="section-number">04 / THE MISSION INTERFACE</span><h2>Operation Varuna</h2></div>
          <p>This interface visualizes the frozen FactoryCity route and cell-assignment design. It is an integration target, not a completed field mission.</p>
        </header>
        <div className="mission-console">
          <div className="console-topline">
            <div><i className="pulse" /> FACTORYCITY · INTERFACE PROTOTYPE</div>
            <div>NOT RETAINED FLIGHT EVIDENCE</div>
          </div>
          <div className="mission-map">
            <div className="map-label north">N ↑</div>
            <div className="map-label east">E →</div>
            <div className="map-path path-a" /><div className="map-path path-b" />
            {missionCells.map((cell, index) => (
              <div key={cell.id} className={`mission-cell ${cell.state}`}>
                <span>{cell.id}</span><b>{cell.owner}</b><i>{cell.state}</i>
                {index === 4 && <mark>!</mark>}
              </div>
            ))}
            <Image className="map-drone map-drone-a" src="/veriswarm-drone.png" width={1536} height={1024} alt="" />
            <Image className="map-drone map-drone-b" src="/veriswarm-drone.png" width={1536} height={1024} alt="" />
            <Image className="map-drone map-drone-c" src="/veriswarm-drone.png" width={1536} height={1024} alt="" />
          </div>
          <aside className="mission-feed">
            <div className="feed-header"><span>MISSION EVENTS</span><i className="pulse" /></div>
            <ol>
              <li><time>00:12</time><p><b>Sectors assigned</b><span>10 cells · 5 vehicles</span></p></li>
              <li><time>01:46</time><p><b>Candidate observed</b><span>camera support retained</span></p></li>
              <li className="warn"><time>02:03</time><p><b>Alpha held</b><span>authorization unavailable</span></p></li>
              <li><time>02:04</time><p><b>Cell 05 reassigned</b><span>mission continuity preserved</span></p></li>
            </ol>
            <div className="feed-note">Illustrative interface state. Frozen assets include Point A/B, a 95.0474 m route, a five-agent roster and ten-cell contract. The first retained authorization-aware live run remains pending.</div>
          </aside>
        </div>
      </section>

      <section className="section capability-section">
        <header className="section-heading">
          <span className="section-number">05 / WHAT MAKES IT DIFFERENT</span>
          <h2>Safety is not a slogan.<br />It is visible system state.</h2>
        </header>
        <div className="capability-grid">
          <article className="capability-large">
            <span className="card-label">SURVIVOR-FIRST EVIDENCE</span>
            <h3>One weak view cannot erase a positive observation.</h3>
            <p>Multi-view support improves confidence, while occlusion or disagreement stays visible. Security consensus labels evidence; it does not vote a person out of existence.</p>
            <div className="view-stack">
              <div><small>CAM 01</small><b>PERSON CANDIDATE</b><span>SUPPORTED</span></div>
              <div><small>CAM 02</small><b>OCCLUDED</b><span>ABSTAIN</span></div>
              <div><small>FUSION</small><b>ALERT RETAINED</b><span>1 POSITIVE VIEW</span></div>
            </div>
          </article>
          <article>
            <span className="card-label">MODEL PROVENANCE</span>
            <h3>Approved bytes—or no motion.</h3>
            <p>The executed model and runtime lineage are checked before authorization.</p>
            <div className="status-chip danger">UNAPPROVED → QUARANTINE</div>
          </article>
          <article>
            <span className="card-label">OFFLINE CONTINUITY</span>
            <h3>Events survive the link.</h3>
            <p>A durable outbox preserves ordering, retries idempotently and retains dead letters.</p>
            <div className="status-chip">BUFFERED → REPLAYED</div>
          </article>
        </div>
      </section>

      <section id="progress" className="section progress-section">
        <header className="section-heading split">
          <div><span className="section-number">06 / TEAM EXECUTION</span><h2>Built as five accountable workstreams.</h2></div>
          <p>Every claim below reflects the latest integrated code or an explicitly labelled active lane. The site can be updated as final artifacts land.</p>
        </header>
        <div className="team-list">
          {teamProgress.map((member) => (
            <article key={member.owner}>
              <div className="avatar">{member.initials}</div>
              <div className="member-title"><h3>{member.owner}</h3><span>{member.lane}</span></div>
              <p>{member.detail}</p>
              <div className="member-status"><i />{member.status}</div>
            </article>
          ))}
        </div>
      </section>

      <section id="safety" className="section safety-section">
        <div className="safety-copy">
          <span className="section-number">07 / FAIL-CLOSED BY DESIGN</span>
          <h2>The dashboard never invents a green light.</h2>
          <p>Missing, stale, malformed or inconsistent evidence is shown as unavailable and resolves to HOLD—not a presentation-friendly pass.</p>
          <div className="boundary-note">Person candidate ≠ confirmed survivor. Simulator validation ≠ certified field deployment. We do not claim field-proven five-drone rescue, thermal AI, calibrated 3-D localization or certified autonomy.</div>
        </div>
        <div className="decision-matrix">
          <div className="matrix-head"><span>INPUT</span><span>VISIBLE RESULT</span><span>ACTION</span></div>
          <div><span><i className="ok" /> Approved + fresh</span><b>VERIFIED</b><em>RELEASE</em></div>
          <div><span><i className="warn-dot" /> Weak or missing</span><b>UNVERIFIED</b><em>HOLD</em></div>
          <div><span><i className="bad" /> Model mismatch</span><b>QUARANTINED</b><em>HOLD</em></div>
          <div><span><i className="warn-dot" /> View occluded</span><b>ABSTAIN</b><em>RETAIN EVIDENCE</em></div>
        </div>
      </section>

      <section className="section roadmap-section">
        <div className="roadmap-card now">
          <span>WORKING NOW</span>
          <h3>Proven camera + trust components</h3>
          <ul>
            <li>USB webcam + DroidCam co-visibility</li>
            <li>YOLO boxes + projected overlap</li>
            <li>AGREE, DISPUTE, ABSTAIN + release proof</li>
            <li>Model-swap rejection and safety HOLD</li>
          </ul>
        </div>
        <div className="roadmap-line"><i /><i /><i /></div>
        <div className="roadmap-card next">
          <span>NEXT INTEGRATION</span>
          <h3>Measured edge-to-map run</h3>
          <ul>
            <li>Rescue-trained person model + held-out metrics</li>
            <li>Jetson TensorRT qualification</li>
            <li>First retained authorization-aware CoSys run</li>
            <li>Measured camera pose/range localization</li>
          </ul>
        </div>
      </section>

      <section className="final-cta">
        <div className="cta-orbit" aria-hidden="true" />
        <span>SIH 26177 · DISASTER SEARCH & RESCUE</span>
        <h2>Move fast.<br /><em>Keep the evidence.</em></h2>
        <p>VeriSwarm turns a fleet of drones into an accountable rescue observation network—built to continue when conditions stop being ideal.</p>
        <button className="button primary return-button" type="button" onClick={() => scrollToSection("top")}>
          Return to launch <UpArrow />
        </button>
      </section>

      <footer>
        <a className="brand" href="#top" onClick={handleSectionLink("top")}><span className="brand-mark"><i /><i /><i /></span><span>VERI<strong>SWARM</strong></span></a>
        <p>Offline-first multi-drone rescue observation validation.</p>
        <span>SIH 2026 · TEAM VERISWARM</span>
      </footer>
    </main>
  );
}

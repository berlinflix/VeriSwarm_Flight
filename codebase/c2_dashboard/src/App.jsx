import React, { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useMotionValue, useScroll, useSpring, useTransform } from "framer-motion";
import {
  Activity,
  AlertTriangle,
  Binary,
  Cpu,
  Fingerprint,
  RadioTower,
  RotateCcw,
  Server,
  ShieldCheck,
  ShieldX,
  TerminalSquare,
  Video,
  Wifi,
  X,
  Zap,
} from "lucide-react";
import flightDrone from "./assets/flight-drone.png";

const liveFeedUrl = import.meta.env.VITE_DRONE_STREAM_URL;

const scenarios = {
  honest: {
    label: "Clean Baseline",
    banner: "VERIFIED EXECUTE",
    tone: "green",
    semanticAcks: 2,
    supervisor: "EXECUTE",
    provenance: "TRUSTED",
    evidence: "PASS",
    reason: "authorized",
    terminal: [
      "alpha receipt signed through OP-TEE",
      "bravo ACK reason=ok delta=0.04",
      "charlie ACK reason=ok delta=0.05",
      "semantic quorum reached: 2/2",
      "supervisor released requested command",
    ],
  },
  patch: {
    label: "Physical Patch",
    banner: "REJECTED FALLBACK",
    tone: "red",
    semanticAcks: 0,
    supervisor: "SAFE_FALLBACK",
    provenance: "TRUSTED",
    evidence: "PASS",
    reason: "semantic_disagreement",
    terminal: [
      "patch artifact armed for camera A",
      "alpha claim changed: target occupancy clear",
      "bravo DISPUTE reason=semantic_disagreement",
      "charlie DISPUTE reason=semantic_disagreement",
      "supervisor blocked motion and held fallback",
    ],
  },
  unverified: {
    label: "No Co-Visibility",
    banner: "UNVERIFIED HOLD",
    tone: "amber",
    semanticAcks: 0,
    supervisor: "DEFER",
    provenance: "TRUSTED",
    evidence: "WARN",
    reason: "ok_no_covisibility",
    terminal: [
      "receipt accepted cryptographically",
      "bravo ACK reason=ok_no_covisibility",
      "charlie ACK reason=ok_no_covisibility",
      "semantic quorum missing: 0/2",
      "supervisor deferred command release",
    ],
  },
  model: {
    label: "Model Swap",
    banner: "HASH REJECT HOLD",
    tone: "red",
    semanticAcks: 0,
    supervisor: "HOLD",
    provenance: "REJECTED",
    evidence: "PASS",
    reason: "model_hash_not_approved",
    terminal: [
      "alpha claim carries unapproved model hash",
      "policy allow-list comparison failed",
      "bravo DISPUTE reason=model_hash_not_approved",
      "charlie DISPUTE reason=model_hash_not_approved",
      "decision layer held command",
    ],
  },
  modelIdle: {
    label: "Model Integrity",
    banner: "QUALIFIER STANDBY",
    tone: "amber",
    semanticAcks: 0,
    supervisor: "HOLD",
    provenance: "AWAITING RUN",
    evidence: "UNVERIFIED",
    reason: "qualification_not_run",
    terminal: [
      "dashboard is waiting for the Jetson qualification service",
      "clean case must reach two semantic acknowledgements first",
      "model-swap case will then present an unapproved hash",
      "Bravo and Charlie determine the actual protocol outcome",
      "no result is shown as PASS until retained evidence validates",
    ],
  },
  provisioning: {
    label: "Provisioning",
    banner: "AUTHORITY REJECT",
    tone: "red",
    semanticAcks: 0,
    supervisor: "SAFE_FALLBACK",
    provenance: "REJECTED",
    evidence: "PASS",
    reason: "authority_signature_invalid",
    terminal: [
      "provisioning authority signature failed",
      "bravo and charlie credentials quarantined",
      "untrusted allow-list update rejected",
      "command quorum recomputed without compromised nodes",
      "supervisor retained safe fallback",
    ],
  },
  ota: {
    label: "OTA / Runtime",
    banner: "RUNTIME REJECT",
    tone: "red",
    semanticAcks: 0,
    supervisor: "HOLD",
    provenance: "REJECTED",
    evidence: "PASS",
    reason: "runtime_hash_not_approved",
    terminal: [
      "OTA package received by alpha",
      "measured runtime hash missing from allow-list",
      "signed receipt retained as attack evidence",
      "peer votes excluded compromised runtime",
      "supervisor held command release",
    ],
  },
  rogue: {
    label: "Rogue Node",
    banner: "NODE ISOLATED",
    tone: "red",
    semanticAcks: 0,
    supervisor: "SAFE_FALLBACK",
    provenance: "REJECTED",
    evidence: "PASS",
    reason: "unknown_drone_id",
    terminal: [
      "unregistered node requested swarm membership",
      "identity not present in mission authority",
      "signed vote rejected before quorum tally",
      "rogue endpoint isolated from active set",
      "swarm continued with trusted membership",
    ],
  },
  replay: {
    label: "Replay",
    banner: "REPLAY BLOCKED",
    tone: "red",
    semanticAcks: 0,
    supervisor: "HOLD",
    provenance: "TRUSTED",
    evidence: "PASS",
    reason: "replayed_receipt",
    terminal: [
      "previously valid receipt observed again",
      "mission sequence and nonce already consumed",
      "freshness cache rejected duplicate evidence",
      "receipt excluded from active round",
      "supervisor held command release",
    ],
  },
  collude: {
    label: "Collusion",
    banner: "COLLUSION CONTAINED",
    tone: "red",
    semanticAcks: 1,
    supervisor: "SAFE_FALLBACK",
    provenance: "TRUSTED",
    evidence: "PASS",
    reason: "quorum_not_reached",
    terminal: [
      "delta and echo emitted coordinated ACK votes",
      "reputation-weighted quorum remained below threshold",
      "independent semantic peer disputed the claim",
      "colluding voters removed from authorization set",
      "supervisor selected safe fallback",
    ],
  },
  spoof: {
    label: "Pose Spoof",
    banner: "POSE REJECTED",
    tone: "red",
    semanticAcks: 0,
    supervisor: "DEFER",
    provenance: "TRUSTED",
    evidence: "WARN",
    reason: "pose_inconsistent",
    terminal: [
      "bravo advertised an impossible camera pose",
      "co-visibility geometry failed trusted bounds",
      "semantic vote downgraded to abstention",
      "semantic quorum unavailable for this round",
      "supervisor deferred command release",
    ],
  },
};

const attackControls = [
  { key: "model", label: "Model Hash", target: "LIVE · ALPHA", Icon: Cpu, enabled: true },
  { key: "patch", label: "Physical Patch", target: "WAITING FOR PRATIK", Icon: AlertTriangle, enabled: false },
  { key: "replay", label: "Replay Receipt", target: "NOT QUALIFIED", Icon: RotateCcw, enabled: false },
  { key: "provisioning", label: "Provisioning", target: "NOT QUALIFIED", Icon: Fingerprint, enabled: false },
  { key: "ota", label: "OTA / Runtime", target: "NOT QUALIFIED", Icon: Zap, enabled: false },
  { key: "rogue", label: "Rogue Node", target: "NOT QUALIFIED", Icon: ShieldX, enabled: false },
  { key: "collude", label: "Collusion", target: "NOT QUALIFIED", Icon: Binary, enabled: false },
  { key: "spoof", label: "Pose Spoof", target: "NOT QUALIFIED", Icon: RadioTower, enabled: false },
];

const baseDrones = [
  {
    id: "ALPHA",
    serial: "ALPHA",
    alt: 50,
    spd: 35,
    bat: 85,
    role: "OP-TEE originator",
    hash: "awaiting receipt",
    endpoint: "192.168.50.10",
    trust: "100%",
    latency: "12.4ms",
    signer: "OP-TEE VERIFIED",
    x: "-92%",
  },
  {
    id: "BRAVO",
    serial: "BRAVO",
    alt: 50,
    spd: 35,
    bat: 85,
    role: "semantic peer",
    hash: "policy verifier",
    endpoint: "192.168.50.12",
    trust: "98%",
    latency: "14.1ms",
    signer: "MISSION CA VERIFIED",
    x: "0%",
  },
  {
    id: "CHARLIE",
    serial: "CHARLIE",
    alt: 49,
    spd: 33,
    bat: 81,
    role: "semantic peer",
    hash: "policy verifier",
    endpoint: "192.168.50.13",
    trust: "96%",
    latency: "15.7ms",
    signer: "MISSION CA VERIFIED",
    x: "92%",
  },
];

function toneClass(tone) {
  return {
    green: "text-emerald-300 border-emerald-400/50 bg-emerald-950/20",
    red: "text-red-300 border-red-400/50 bg-red-950/30",
    amber: "text-amber-200 border-amber-300/50 bg-amber-950/25",
  }[tone];
}

function shortHash(value, length = 12) {
  return typeof value === "string" && value ? value.slice(0, length) : "—";
}

function qualificationScenario(proof, runningStage, error) {
  if (error) {
    return {
      ...scenarios.modelIdle,
      banner: "QUALIFIER OFFLINE",
      tone: "red",
      evidence: "FAIL",
      reason: error,
      terminal: [
        "dashboard could not obtain qualification evidence",
        `backend reason=${error}`,
        "no attack result has been inferred or displayed as PASS",
        "verify Jetson service, Bravo, Charlie, Ethernet, and clocks",
        "retry only after the backend reports READY",
      ],
    };
  }
  if (runningStage) {
    return {
      ...scenarios.modelIdle,
      banner: runningStage === "clean" ? "VERIFYING BASELINE" : "TESTING MODEL HASH",
      evidence: "RUNNING",
      reason: `${runningStage}_in_progress`,
      terminal: [
        runningStage === "clean"
          ? "Alpha is originating the approved-model baseline"
          : "Alpha is originating the unapproved-model receipt",
        "waiting for Bravo at 192.168.50.12",
        "waiting for Charlie at 192.168.50.13",
        "verifier output will determine the outcome",
        "command remains HOLD while qualification is incomplete",
      ],
    };
  }
  if (!proof?.attack?.evidence) return scenarios.modelIdle;

  const clean = proof.clean.evidence;
  const attack = proof.attack.evidence;
  const actual = attack.actual ?? {};
  const authorization = attack.authorization ?? {};
  const dashboardProof = attack.dashboard_proof ?? {};
  const valid = dashboardProof.proof_valid === true;
  const voterLines = (attack.peer_votes ?? []).map(
    (vote) => `${vote.voter} ${vote.decision} target=${shortHash(attack.consensus_target_receipt_hash)}`,
  );
  return {
    label: "Model Hash Proof",
    banner: valid ? "HASH REJECTED · HOLD" : "QUALIFICATION FAILED",
    tone: valid ? "red" : "amber",
    semanticAcks: Number(actual.semantic_acks ?? 0),
    supervisor: authorization.allowed ? "EXECUTE" : "HOLD",
    provenance: valid ? "UNAPPROVED HASH" : "UNVERIFIED",
    evidence: valid ? "PASS" : "FAIL",
    reason: dashboardProof.policy_reason ?? actual.reason ?? "qualification_failed",
    terminal: [
      `clean baseline ${clean.actual?.outcome ?? "UNKNOWN"} semantic_acks=${clean.actual?.semantic_acks ?? 0}`,
      `alpha model=${shortHash(dashboardProof.observed_model_sha256, 16)} approved=${shortHash(dashboardProof.approved_model_sha256, 16)}`,
      ...voterLines.slice(0, 2),
      `consensus ${actual.outcome ?? "UNKNOWN"}; released=${JSON.stringify(authorization.released ?? [])}`,
    ].slice(0, 5),
  };
}

function useModelHashQualification() {
  const [backend, setBackend] = useState({ ready: false, loading: true, error: null, details: null });
  const [proof, setProof] = useState(null);
  const [runningStage, setRunningStage] = useState(null);
  const [runError, setRunError] = useState(null);

  const refreshBackend = async () => {
    try {
      const response = await fetch("/api/qualification/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.error ?? "qualification_unavailable");
      setBackend({ ready: true, loading: false, error: null, details: payload });
    } catch (error) {
      setBackend({ ready: false, loading: false, error: error.message, details: null });
    }
  };

  useEffect(() => {
    refreshBackend();
    const timer = window.setInterval(refreshBackend, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const runCase = async (caseName) => {
    const response = await fetch("/api/qualification/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ case: caseName }),
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok || !payload.evidence) {
      throw new Error(payload.error ?? `${caseName}_failed`);
    }
    return payload;
  };

  const runProof = async () => {
    if (runningStage || !backend.ready) return;
    setProof(null);
    setRunError(null);
    try {
      setRunningStage("clean");
      const clean = await runCase("clean");
      const cleanEvidence = clean.evidence;
      if (
        cleanEvidence.actual?.outcome !== "ACCEPTED"
        || Number(cleanEvidence.actual?.semantic_acks ?? 0) < 2
      ) {
        throw new Error("clean_baseline_gate_failed");
      }
      setRunningStage("model_swap");
      const attack = await runCase("model_swap");
      setProof({ clean, attack });
    } catch (error) {
      setRunError(error.message);
    } finally {
      setRunningStage(null);
      refreshBackend();
    }
  };

  const resetView = () => {
    if (runningStage) return;
    setProof(null);
    setRunError(null);
  };

  return { backend, proof, runningStage, runError, runProof, resetView };
}

function AnimatedMetric({ value, suffix = "" }) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const start = shown;
    const diff = value - start;
    const started = performance.now();
    const duration = 520;
    let frame = 0;
    const tick = (now) => {
      const p = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      setShown(start + diff * eased);
      if (p < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <span>
      {Math.round(shown)}
      {suffix}
    </span>
  );
}

function CursorGravity() {
  const pointerX = useMotionValue(-400);
  const pointerY = useMotionValue(-400);
  const pointerOpacity = useMotionValue(0);
  const x = useSpring(pointerX, { stiffness: 92, damping: 20, mass: 0.34 });
  const y = useSpring(pointerY, { stiffness: 92, damping: 20, mass: 0.34 });
  const opacity = useSpring(pointerOpacity, { stiffness: 110, damping: 24 });

  useEffect(() => {
    const finePointer = window.matchMedia("(pointer: fine)");
    if (!finePointer.matches) return undefined;
    const handleMove = (event) => {
      pointerX.set(event.clientX);
      pointerY.set(event.clientY);
      pointerOpacity.set(1);
    };
    const handleLeave = () => pointerOpacity.set(0);
    window.addEventListener("pointermove", handleMove, { passive: true });
    document.documentElement.addEventListener("mouseleave", handleLeave);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      document.documentElement.removeEventListener("mouseleave", handleLeave);
    };
  }, [pointerOpacity, pointerX, pointerY]);

  return <motion.div className="cursor-gravity" aria-hidden="true" style={{ x, y, opacity }} />;
}

function DroneCard({ drone, index, activeCard, setActiveCard, onSelect }) {
  const isCenter = index === 1;
  const isActive = activeCard === index;
  const hasActiveCard = activeCard !== null;
  return (
    <motion.article
      className={`drone-card ${isActive ? "active" : ""} ${hasActiveCard && !isActive ? "receded" : ""}`}
      style={{ zIndex: isActive ? 20 : isCenter ? 2 : 1 }}
      initial={false}
      animate={{
        x: drone.x,
        y: isActive ? -18 : isCenter ? -6 : 8,
        scale: isActive ? 1.055 : hasActiveCard ? 0.985 : 1,
      }}
      transition={{ type: "spring", stiffness: 165, damping: 22, mass: 0.8 }}
      onPointerEnter={() => setActiveCard(index)}
      onPointerLeave={() => setActiveCard(null)}
      onFocus={() => setActiveCard(index)}
      onBlur={() => setActiveCard(null)}
      onClick={() => onSelect(drone)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect(drone);
        }
      }}
      tabIndex={0}
      role="button"
      aria-label={`${drone.id} telemetry tile`}
    >
      <div className="drone-glass">
        <div className="card-header">
          <div>
            <span className="node-kicker">TRUSTED NODE</span>
            <h2>{drone.id}</h2>
          </div>
          <span className={`node-online ${drone.online ? "" : "unverified"}`}><i /> {drone.online ? "READY" : "UNVERIFIED"}</span>
        </div>
        <div className="telemetry-cluster">
          <div><span>ALTITUDE</span><b><AnimatedMetric value={drone.alt} suffix="m" /></b></div>
          <div><span>SPEED</span><b><AnimatedMetric value={drone.spd} suffix="kph" /></b></div>
          <div><span>BATTERY</span><b><AnimatedMetric value={drone.bat} suffix="%" /></b></div>
        </div>
        <div className="signal-band" aria-hidden="true">
          {Array.from({ length: 18 }).map((_, bar) => <i key={bar} style={{ height: `${18 + ((bar * 17) % 34)}%` }} />)}
        </div>
        <div className="card-meta">
          <span>{drone.role}</span>
          <span className="hash-value">{drone.hash}</span>
        </div>
      </div>
    </motion.article>
  );
}

function DroneDetailsModal({ drone, onClose }) {
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") onClose();
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [onClose]);

  const details = [
    ["Mesh endpoint", drone.endpoint],
    ["Swarm role", drone.role],
    ["Identity signer", drone.signer],
    ["Runtime hash", drone.hash],
    ["Trust score", drone.trust],
    ["Link latency", drone.latency],
  ];

  return (
    <motion.div
      className="drone-modal-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.24, ease: "easeOut" }}
      onPointerDown={onClose}
    >
      <motion.section
        className="drone-modal-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby={`drone-modal-${drone.id}`}
        initial={{ opacity: 0, y: 22, scale: 0.94 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 16, scale: 0.96 }}
        transition={{ type: "spring", stiffness: 250, damping: 28, mass: 0.7 }}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <div className="modal-drone-header">
          <div>
            <span className="node-kicker">TRUSTED SWARM NODE</span>
            <h2 id={`drone-modal-${drone.id}`}>{drone.id}</h2>
            <p>{drone.role} · encrypted Ethernet mesh</p>
          </div>
          <div className="modal-header-actions">
            <span className={`node-online ${drone.online ? "" : "unverified"}`}><i /> {drone.online ? "READY" : "UNVERIFIED"}</span>
            <button className="modal-close" onClick={onClose} aria-label="Close drone details" autoFocus>
              <X size={19} />
            </button>
          </div>
        </div>

        <div className="modal-telemetry">
          <div><span>ALTITUDE</span><b>{drone.alt}m</b></div>
          <div><span>GROUND SPEED</span><b>{drone.spd}kph</b></div>
          <div><span>BATTERY</span><b>{drone.bat}%</b></div>
        </div>

        <div className="modal-detail-grid">
          {details.map(([label, value]) => (
            <div key={label}>
              <span>{label}</span>
              <b>{value}</b>
            </div>
          ))}
        </div>

        <div className="modal-signal-row">
          <span>SECURE TELEMETRY CHANNEL</span>
          <div aria-hidden="true">{Array.from({ length: 22 }).map((_, index) => <i key={index} style={{ height: `${8 + ((index * 19) % 25)}px` }} />)}</div>
          <b>LINK NOMINAL</b>
        </div>
        <p className="modal-dismiss-hint">Click outside this card or press ESC to return to command view.</p>
      </motion.section>
    </motion.div>
  );
}

function DroneDeck({ proof, backend }) {
  const [activeCard, setActiveCard] = useState(null);
  const [selectedDrone, setSelectedDrone] = useState(null);
  const dashboardProof = proof?.attack?.evidence?.dashboard_proof;
  const drones = baseDrones.map((drone) => ({
    ...drone,
    online: backend.ready,
    hash: dashboardProof
      ? drone.id === "ALPHA"
        ? shortHash(dashboardProof.observed_model_sha256)
        : shortHash(dashboardProof.approved_model_sha256)
      : drone.hash,
  }));
  return (
    <>
      <section className="drone-deck">
        {drones.map((drone, index) => (
          <DroneCard
            key={drone.id}
            drone={drone}
            index={index}
            activeCard={activeCard}
            setActiveCard={setActiveCard}
            onSelect={(nextDrone) => {
              setActiveCard(null);
              setSelectedDrone(nextDrone);
            }}
          />
        ))}
        <div className="deck-caption">
          SELECT A NODE · ALPHA / BRAVO / CHARLIE · {backend.ready ? "QUALIFIER READY" : "LINK UNVERIFIED"}
        </div>
      </section>
      <AnimatePresence>
        {selectedDrone && <DroneDetailsModal drone={selectedDrone} onClose={() => setSelectedDrone(null)} />}
      </AnimatePresence>
    </>
  );
}

function RealTimeView({ scenario }) {
  const [feedFailed, setFeedFailed] = useState(false);
  const showFeed = Boolean(liveFeedUrl) && !feedFailed;
  return (
    <section className="capsule">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">ETHERNET INGEST</span>
          <div className="panel-title">Real-Time View</div>
        </div>
        <span className={`feed-state ${showFeed ? "connected" : "standby"}`}>
          <i /> {showFeed ? "LIVE" : "AWAITING FEED"}
        </span>
      </div>
      <div className="camera-shell">
        <div className="camera-stage">
          {showFeed ? (
            <img
              className="live-feed"
              src={liveFeedUrl}
              alt="Live drone simulation feed"
              onError={() => setFeedFailed(true)}
            />
          ) : (
            <div className="feed-placeholder">
              <Video size={30} />
              <b>PRATIK SIMULATION FEED</b>
              <span>Connect the Ethernet stream bridge to populate this viewport.</span>
            </div>
          )}
          <div className="feed-overlay">
            <span><Wifi size={13} /> ETH0</span>
            <span>COSYS-AIRSIM / RPC 41451</span>
          </div>
            <div className="scanline" />
        </div>
        <div className="feed-footer">
          <span><Server size={14} /> SOURCE: {liveFeedUrl ? "VITE_DRONE_STREAM_URL" : "NOT CONFIGURED"}</span>
          <b>{scenario.reason}</b>
        </div>
      </div>
    </section>
  );
}

function useLiveTelemetry() {
  const [telemetry, setTelemetry] = useState(null);
  const [telemetryError, setTelemetryError] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch("/api/telemetry", { cache: "no-store" });
        if (!response.ok) throw new Error("telemetry unavailable");
        const payload = await response.json();
        if (active) {
          setTelemetry(payload);
          setTelemetryError(false);
        }
      } catch {
        if (active) setTelemetryError(true);
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { telemetry, telemetryError };
}

function useRescueMission() {
  const [state, setState] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch("/api/rescue/state", { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok || !payload.ok || !payload.state) {
          throw new Error(payload.error ?? "rescue_state_unavailable");
        }
        if (active) {
          setState(payload.state);
          setError(null);
        }
      } catch (requestError) {
        if (active) {
          setState(null);
          setError(requestError.message);
        }
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return { state, error };
}

function RescueMission({ rescue }) {
  const state = rescue.state;
  const alerts = state?.alerts?.slice(0, 3) ?? [];
  const mappedHazards = state?.hazards?.filter((hazard) => hazard.status === "MAPPED_HAZARD").length ?? 0;
  const missionOnline = Boolean(state);
  return (
    <section className={`rescue-mission ${missionOnline ? "online" : "offline"}`}>
      <div className="rescue-title">
        <span>OPERATION VARUNA</span>
        <b>{state?.mission_status ?? "AWAITING RESCUE EVENTS"}</b>
        <small>{state?.scenario_id ?? rescue.error ?? "collector not configured"}</small>
      </div>
      <div className="rescue-kpis">
        <div><span>COVERAGE</span><b>{state ? `${Number(state.coverage?.percent ?? 0).toFixed(1)}%` : "—"}</b></div>
        <div><span>PEOPLE</span><b>{state?.people?.length ?? "—"}</b></div>
        <div><span>HAZARDS</span><b>{state ? mappedHazards : "—"}</b></div>
        <div><span>VEHICLES</span><b>{state?.vehicles?.length ?? "—"}</b></div>
      </div>
      <div className="rescue-alerts">
        {alerts.length ? alerts.map((alert) => (
          <div key={alert.alert_id} className={`rescue-alert priority-${alert.priority.toLowerCase()}`}>
            <span>{alert.priority}</span>
            <b>{alert.message}</b>
          </div>
        )) : (
          <div className="rescue-alert empty">
            <span>{missionOnline ? "CLEAR" : "OFFLINE"}</span>
            <b>{missionOnline ? "No active rescue alerts" : "No rescue state is being fabricated"}</b>
          </div>
        )}
      </div>
    </section>
  );
}

function chartPath(values, maximum, height = 148) {
  if (!values?.length) return "";
  const points = values.length === 1 ? [values[0], values[0]] : values;
  return points
    .map((value, index) => {
      const x = 24 + (index / (points.length - 1)) * 572;
      const y = 18 + (1 - Math.min(maximum, Math.max(0, value)) / maximum) * height;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function Analytics({ scenario, qualification = {} }) {
  const { telemetry, telemetryError } = useLiveTelemetry();
  const proof = qualification?.proof;
  const proofActive = Boolean(proof?.attack?.evidence);
  const proofEvidence = [proof?.clean?.evidence, proof?.attack?.evidence].filter(Boolean);
  const proofConsensus = proofEvidence
    .map((row) => Number(row.timings_ms?.consensus))
    .filter(Number.isFinite);
  const proofSemantic = proofEvidence.map((row) => Number(row.actual?.semantic_acks ?? 0));
  const consensus = proofActive ? proofConsensus : telemetry?.consensus ?? [];
  const semantic = proofActive ? proofSemantic : telemetry?.semanticAcks ?? [];
  const consensusPath = chartPath(consensus, Math.max(25, ...consensus));
  const semanticPath = chartPath(semantic, Math.max(4, ...semantic));
  const meanConsensus = consensus.length
    ? consensus.reduce((total, value) => total + value, 0) / consensus.length
    : null;
  const latestSemantic = semantic.at(-1);
  const attackEvidence = proof?.attack?.evidence;
  const proofFile = proof?.attack?.dashboardEvidenceFile;
  const analyticsOffline = !proofActive && telemetryError;
  return (
    <section className="glass-panel analytics-panel">
      <div className="analytics-heading">
        <div>
          <span className="section-kicker">{proofActive ? "QUALIFICATION EVIDENCE" : "EVENT STREAM"}</span>
          <div className="panel-title">{proofActive ? "Model-Hash Analytics" : "Live Analytics"}</div>
        </div>
        <span className={`data-state ${analyticsOffline ? "offline" : "live"}`}>
          <i /> {proofActive ? "RETAINED PROOF" : telemetryError ? "LINK OFFLINE" : telemetry ? "LIVE DATA" : "CONNECTING"}
        </span>
      </div>
      <div className="analytics-chart">
        <svg viewBox="0 0 620 190" role="img" aria-label="Live consensus and semantic acknowledgement trends">
          <defs>
            <linearGradient id="redFill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="#ff2a4b" stopOpacity="0.36" />
              <stop offset="100%" stopColor="#ff2a4b" stopOpacity="0" />
            </linearGradient>
            <filter id="chartGlow">
              <feGaussianBlur stdDeviation="3" result="blur" />
              <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>
          {Array.from({ length: 8 }).map((_, i) => (
            <line key={`v-${i}`} x1={24 + i * 82} y1="18" x2={24 + i * 82} y2="166" className="grid-line" />
          ))}
          {Array.from({ length: 5 }).map((_, i) => (
            <line key={`h-${i}`} x1="24" y1={18 + i * 37} x2="596" y2={18 + i * 37} className="grid-line" />
          ))}
          {consensusPath && <motion.path
            className="line-red"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{ d: consensusPath, pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          />}
          {semanticPath && <motion.path
            className="line-white"
            initial={{ pathLength: 0, opacity: 0 }}
            animate={{ d: semanticPath, pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.9, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
          />}
        </svg>
        {!proofActive && !telemetry && !telemetryError && <span className="chart-loading">READING RETAINED EVENTS</span>}
        <div className="chart-legend">
          <span><i className="consensus-dot" /> CONSENSUS MS</span>
          <span><i className="semantic-dot" /> SEMANTIC ACKS</span>
        </div>
      </div>
      <div className="analytics-metrics">
        <div><span>CONSENSUS</span><b>{meanConsensus === null ? "—" : `${meanConsensus.toFixed(1)}ms`}</b></div>
        <div><span>SEMANTIC</span><b>{latestSemantic ?? "—"}</b></div>
        <div>
          <span>{proofActive ? "DISPUTES" : "ALTITUDE"}</span>
          <b>{proofActive ? attackEvidence?.actual?.disputes ?? "—" : telemetry?.altitude == null ? "—" : `${telemetry.altitude}m`}</b>
        </div>
        <div>
          <span>{proofActive ? "RESULT" : "TRUST"}</span>
          <b>{proofActive ? attackEvidence?.actual?.outcome ?? "—" : telemetry?.reputation == null ? "—" : `${Math.round(telemetry.reputation * 100)}%`}</b>
        </div>
      </div>
      <div className="analytics-source">
        <span>{proofActive ? proofFile ?? "create-once dashboard evidence" : telemetry?.source ?? "waiting for event source"}</span>
        <b>{proofActive ? attackEvidence?.authorization?.allowed ? "EXECUTE" : "HOLD" : telemetry?.action ?? scenario.supervisor}</b>
      </div>
    </section>
  );
}

function AttackSystems({ qualification }) {
  const { backend, proof, runningStage, runError, runProof, resetView } = qualification;
  const cleanEvidence = proof?.clean?.evidence;
  const attackEvidence = proof?.attack?.evidence;
  const dashboardProof = attackEvidence?.dashboard_proof;
  const pending = Boolean(runningStage);
  const status = pending
    ? { message: runningStage === "clean" ? "VERIFYING CLEAN BASELINE" : "RUNNING MODEL-HASH ATTACK", tone: "pending" }
    : runError
      ? { message: runError.toUpperCase(), tone: "error" }
      : dashboardProof?.proof_valid
        ? { message: "REAL EVIDENCE VERIFIED", tone: "success" }
        : backend.ready
          ? { message: "JETSON QUALIFIER READY", tone: "ready" }
          : { message: backend.loading ? "CONNECTING TO JETSON" : "QUALIFIER OFFLINE", tone: "error" };

  return (
    <section className="glass-panel attack-panel">
      <div className="attack-heading">
        <div>
          <span className="section-kicker">LIVE ETHERNET QUALIFICATION</span>
          <div className="panel-title">Model Integrity</div>
        </div>
        <button
          className="reset-control"
          onClick={resetView}
          disabled={pending}
          aria-label="Clear displayed qualification result"
        >
          <RotateCcw size={14} /> RESET VIEW
        </button>
      </div>
      <div className="attack-grid">
        {attackControls.map(({ key, label, target, Icon, enabled }) => (
          <button
            key={key}
            className={`attack-control ${key === "model" && dashboardProof ? "active" : ""} ${key === "model" && pending ? "pending" : ""}`}
            onClick={key === "model" ? runProof : undefined}
            disabled={!enabled || pending || !backend.ready}
            aria-pressed={key === "model" && Boolean(dashboardProof)}
          >
            <Icon size={17} />
            <span>{label}</span>
            <small>{target}</small>
          </button>
        ))}
      </div>
      <div className="qualification-proof">
        <div>
          <span>CLEAN GATE</span>
          <b>{cleanEvidence?.actual?.outcome ?? "NOT RUN"}</b>
          <small>semantic ACKs {cleanEvidence?.actual?.semantic_acks ?? "—"}/2</small>
        </div>
        <div>
          <span>MODEL HASH</span>
          <b>{attackEvidence?.actual?.outcome ?? "NOT RUN"}</b>
          <small>disputes {attackEvidence?.actual?.disputes ?? "—"}/2</small>
        </div>
        <div>
          <span>RELEASED COMMAND</span>
          <b>{attackEvidence?.authorization?.allowed ? "EXECUTE" : attackEvidence ? "HOLD" : "—"}</b>
          <small>{attackEvidence ? JSON.stringify(attackEvidence.authorization?.released ?? []) : "waiting for evidence"}</small>
        </div>
      </div>
      <div className="hash-comparison">
        <div><span>APPROVED</span><b>{shortHash(dashboardProof?.approved_model_sha256, 20)}</b></div>
        <i aria-hidden="true" />
        <div><span>OBSERVED ON ALPHA</span><b>{shortHash(dashboardProof?.observed_model_sha256, 20)}</b></div>
      </div>
      <div className="attack-status">
        <span className={status.tone} aria-live="polite"><i /> {status.message}</span>
        <button onClick={runProof} disabled={pending || !backend.ready}>
          {pending ? "RUNNING…" : "RUN CLEAN + MODEL HASH"}
        </button>
      </div>
      <div className="control-summary">
        <span>{dashboardProof?.policy_reason ?? "MODEL POLICY NOT YET TESTED"}</span>
        <b>{backend.details?.signerBackend === "optee" ? "ALPHA / OP-TEE" : "NO SIGNER CLAIM"}</b>
      </div>
    </section>
  );
}

function TerminalFeed({ scenario }) {
  return (
    <section className="glass-panel terminal-panel">
      <div className="panel-title with-icon">
        <TerminalSquare size={18} /> Encrypted Event Log
      </div>
      <div className="terminal-lines">
        {scenario.terminal.map((line, index) => (
          <motion.p
            key={`${line}-${index}`}
            initial={{ opacity: 0, x: -12 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: index * 0.04 }}
          >
            <span>{String(index + 1).padStart(2, "0")}</span> {line}
          </motion.p>
        ))}
      </div>
    </section>
  );
}

function EvidenceStrip({ scenario }) {
  const items = [
    ["Provenance", scenario.provenance, Fingerprint],
    ["Semantic ACK", String(scenario.semanticAcks), Binary],
    ["Supervisor", scenario.supervisor, ShieldCheck],
    ["Evidence", scenario.evidence, scenario.evidence === "PASS" ? ShieldCheck : AlertTriangle],
    ["Reason", scenario.reason, scenario.tone === "red" ? ShieldX : Activity],
  ];
  return (
    <div className="evidence-strip">
      {items.map(([label, value, Icon]) => (
        <div className="evidence-cell" key={label}>
          <Icon size={18} />
          <span>{label}</span>
          <b>{value}</b>
        </div>
      ))}
    </div>
  );
}

function ScrollAnalyticsScene({ scenario, qualification, rescue }) {
  const storyRef = useRef(null);
  const { scrollYProgress } = useScroll({
    target: storyRef,
    offset: ["start start", "end end"],
  });
  const progress = useSpring(scrollYProgress, {
    stiffness: 88,
    damping: 28,
    mass: 0.42,
    restDelta: 0.001,
  });

  const leftX = useTransform(progress, [0, 0.5, 1], ["-66%", "-48vw", "-52vw"]);
  const rightX = useTransform(progress, [0, 0.5, 1], ["-34%", "48vw", "52vw"]);
  const droneY = useTransform(progress, [0, 0.52, 1], ["-42%", "-62vh", "-76vh"]);
  const droneScale = useTransform(progress, [0, 0.5, 1], [0.84, 0.32, 0.22]);
  const droneOpacity = useTransform(progress, [0, 0.46, 0.64], [1, 0.88, 0]);
  const leftRotate = useTransform(progress, [0, 0.54], [-4, -19]);
  const rightRotate = useTransform(progress, [0, 0.54], [4, 19]);
  const analyticsOpacity = useTransform(progress, [0, 0.34, 0.58, 1], [0, 0, 1, 1]);
  const analyticsY = useTransform(progress, [0.28, 0.6, 1], ["22vh", "0vh", "0vh"]);
  const analyticsScale = useTransform(progress, [0.28, 0.62, 1], [0.94, 1, 1]);
  const transitionTitleOpacity = useTransform(progress, [0, 0.16, 0.48, 0.64], [0, 1, 1, 0]);
  const transitionTitleY = useTransform(progress, [0, 0.46, 0.66], [28, 0, -28]);

  return (
    <section className="scroll-story" ref={storyRef} aria-label="Mission analytics transition">
      <div className="scroll-stage">
        <motion.div
          className="transition-title"
          style={{ opacity: transitionTitleOpacity, y: transitionTitleY }}
        >
          <span>SWARM INTELLIGENCE</span>
          <h2>Evidence takes flight.</h2>
        </motion.div>

        <motion.img
          className="flight-drone flight-drone-left"
          src={flightDrone}
          alt=""
          aria-hidden="true"
          style={{ x: leftX, y: droneY, scale: droneScale, opacity: droneOpacity, rotateZ: leftRotate }}
        />
        <motion.img
          className="flight-drone flight-drone-right"
          src={flightDrone}
          alt=""
          aria-hidden="true"
          style={{ x: rightX, y: droneY, scale: droneScale, opacity: droneOpacity, rotateZ: rightRotate }}
        />

        <motion.div
          className="analytics-screen"
          style={{ opacity: analyticsOpacity, y: analyticsY, scale: analyticsScale }}
        >
          <div className="analytics-intro">
            <div>
              <span>LIVE MISSION INTELLIGENCE</span>
              <h2>Every decision. Accounted for.</h2>
            </div>
            <p>One presentation-ready view for retained evidence, live telemetry, attack controls, and cryptographic status.</p>
          </div>

          <EvidenceStrip scenario={scenario} />

          <RescueMission rescue={rescue} />

          <div className="lower-zone">
            <div className="lower-aurora" aria-hidden="true" />
            <section className="lower-tier">
              <Analytics scenario={scenario} qualification={qualification} />
              <AttackSystems qualification={qualification} />
              <TerminalFeed scenario={scenario} />
            </section>
          </div>

          <footer className="mission-footer">
            <span><Cpu size={15} /> Alpha signer: {qualification.backend.details?.signerBackend === "optee" ? "OP-TEE configured" : "unverified"}</span>
            <span><Zap size={15} /> Ethernet topology: .10 / .12 / .13 / .14</span>
            <span>Dashboard mode: live qualifier + retained evidence</span>
          </footer>
        </motion.div>
      </div>
    </section>
  );
}

export default function App() {
  const qualification = useModelHashQualification();
  const rescue = useRescueMission();
  const scenario = useMemo(
    () => qualificationScenario(qualification.proof, qualification.runningStage, qualification.runError),
    [qualification.proof, qualification.runningStage, qualification.runError],
  );
  const statusClass = useMemo(() => toneClass(scenario.tone), [scenario]);
  return (
    <main className="dashboard">
      <div className="orbital-bg" />
      <CursorGravity />
      <section className="hero-screen">
        <header className="topbar">
          <div>
            <p>VERISWARM C2 / INTERNAL QUALIFIER</p>
            <h1>Drone Command & Control</h1>
          </div>
          <div className={`mission-status ${statusClass}`}>
            <RadioTower size={18} />
            <span>{scenario.banner}</span>
          </div>
        </header>

        <section className="upper-tier">
          <DroneDeck proof={qualification.proof} backend={qualification.backend} />
          <RealTimeView scenario={scenario} />
        </section>

        <div className="scroll-cue" aria-hidden="true">
          <span>SWIPE UP FOR MISSION ANALYTICS</span>
          <i />
        </div>
      </section>

      <ScrollAnalyticsScene
        scenario={scenario}
        qualification={qualification}
        rescue={rescue}
      />
    </main>
  );
}

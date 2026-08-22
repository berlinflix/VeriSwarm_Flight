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
  X,
  Zap,
} from "lucide-react";
import flightDrone from "./assets/flight-drone.png";
import { buildCellMissionView } from "./cellMissionView.js";
import { buildMissionMap } from "./missionMap.js";
import { isApprovedCleanEvidence } from "./modelHashQualification.js";

const pratikTopViewUrl = import.meta.env.VITE_PRATIK_TOP_VIEW_URL;
const QUALIFICATION_VIEW_KEY = "veriswarm.qualification.proof.v1";

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
    id: "DELTA",
    serial: "DELTA",
    role: "CoSys rescue vehicle",
    endpoint: "cosys://delta",
    slot: -2,
  },
  {
    id: "ALPHA",
    serial: "ALPHA",
    role: "CoSys rescue vehicle",
    endpoint: "cosys://alpha",
    slot: -1,
  },
  {
    id: "BRAVO",
    serial: "BRAVO",
    role: "CoSys rescue vehicle",
    endpoint: "cosys://bravo",
    slot: 0,
  },
  {
    id: "CHARLIE",
    serial: "CHARLIE",
    role: "CoSys rescue vehicle",
    endpoint: "cosys://charlie",
    slot: 1,
  },
  {
    id: "ECHO",
    serial: "ECHO",
    role: "CoSys rescue vehicle",
    endpoint: "cosys://echo",
    slot: 2,
  },
];

const deckOffset = {
  [-2]: "-200%",
  [-1]: "-100%",
  0: "0%",
  1: "100%",
  2: "200%",
};

function metricValue(value, suffix = "") {
  return Number.isFinite(value) ? `${Math.round(value)}${suffix}` : "—";
}

function nedLabel(position) {
  if (!Array.isArray(position) || position.length !== 3 || !position.every((value) => Number.isFinite(Number(value)))) {
    return "—";
  }
  return position.map((value) => Number(value).toFixed(1)).join(", ");
}

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
  if (proof?.clean?.evidence && !proof?.attack?.evidence) {
    const clean = proof.clean.evidence;
    const actual = clean.actual ?? {};
    const authorization = clean.authorization ?? {};
    const dashboardProof = clean.dashboard_proof ?? {};
    const valid = dashboardProof.proof_valid === true
      && actual.outcome === "ACCEPTED"
      && Number(actual.semantic_acks ?? 0) >= 2;
    const voterLines = (clean.peer_votes ?? []).map(
      (vote) => `${vote.voter} ${vote.decision} target=${shortHash(clean.consensus_target_receipt_hash)}`,
    );
    return {
      label: "Approved Baseline",
      banner: valid ? "APPROVED HASH · 2/2 ACK" : "BASELINE FAILED",
      tone: valid ? "green" : "amber",
      semanticAcks: Number(actual.semantic_acks ?? 0),
      supervisor: authorization.allowed ? "EXECUTE" : "HOLD",
      provenance: valid ? "APPROVED HASH" : "UNVERIFIED",
      evidence: valid ? "PASS" : "FAIL",
      reason: dashboardProof.policy_reason ?? actual.reason ?? "clean_baseline_failed",
      terminal: [
        `alpha model=${shortHash(dashboardProof.observed_model_sha256, 16)} matches approved=${shortHash(dashboardProof.approved_model_sha256, 16)}`,
        ...voterLines.slice(0, 2),
        `clean consensus ${actual.outcome ?? "UNKNOWN"}; semantic_acks=${actual.semantic_acks ?? 0}/2`,
        "model-hash attack NOT RUN; waiting for reviewer",
      ].slice(0, 5),
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
  const [proof, setProof] = useState(() => {
    try {
      const restored = JSON.parse(window.sessionStorage.getItem(QUALIFICATION_VIEW_KEY) ?? "null");
      return restored?.clean?.evidence ? restored : null;
    } catch {
      return null;
    }
  });
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

  useEffect(() => {
    try {
      if (proof) {
        window.sessionStorage.setItem(QUALIFICATION_VIEW_KEY, JSON.stringify(proof));
      } else {
        window.sessionStorage.removeItem(QUALIFICATION_VIEW_KEY);
      }
    } catch {
      // Qualification remains usable when browser storage is unavailable.
    }
  }, [proof]);

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
    setRunError(null);
    try {
      const cleanEvidence = proof?.clean?.evidence;
      const cleanPassed = isApprovedCleanEvidence(cleanEvidence);
      if (!cleanPassed) {
        setProof(null);
        setRunningStage("clean");
        const clean = await runCase("clean");
        setProof({ clean, attack: null });
        if (!isApprovedCleanEvidence(clean.evidence)) {
          throw new Error("clean_baseline_gate_failed");
        }
      } else {
        setRunningStage("model_swap");
        const attack = await runCase("model_swap");
        setProof((current) => ({ clean: current.clean, attack }));
      }
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
  const isCenter = drone.slot === 0;
  const isActive = activeCard === index;
  const hasActiveCard = activeCard !== null;
  const depth = 3 - Math.abs(drone.slot);
  return (
    <motion.article
      className={`drone-card ${isActive ? "active" : ""} ${hasActiveCard && !isActive ? "receded" : ""}`}
      style={{ zIndex: isActive ? 20 : depth }}
      initial={false}
      animate={{
        x: deckOffset[drone.slot],
        y: isActive ? -18 : isCenter ? -7 : -Math.abs(drone.slot) * 2,
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
            <span className="node-kicker">SIMULATED VEHICLE</span>
            <h2>{drone.id}</h2>
          </div>
          <span className={`node-online ${drone.online ? "" : "unverified"}`}><i /> {drone.status}</span>
        </div>
        <div className="telemetry-cluster">
          <div><span>ALTITUDE</span><b>{Number.isFinite(drone.alt) ? <AnimatedMetric value={drone.alt} suffix="m" /> : "—"}</b></div>
          <div><span>STATE</span><b className="state-metric">{drone.vehicleState ?? "—"}</b></div>
          <div><span>BATTERY</span><b>{Number.isFinite(drone.bat) ? <AnimatedMetric value={drone.bat} suffix="%" /> : "—"}</b></div>
        </div>
        <div className="signal-band" aria-hidden="true">
          {Array.from({ length: 18 }).map((_, bar) => <i key={bar} style={{ height: `${18 + ((bar * 17) % 34)}%` }} />)}
        </div>
        <div className="card-meta">
          <span>{drone.linkState}</span>
          <span className="coverage-value">{drone.completedCells}/{drone.totalCells} CELLS</span>
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
    ["Simulation endpoint", drone.endpoint],
    ["Simulation role", drone.role],
    ["NED position (N, E, D)", nedLabel(drone.position)],
    ["Link state", drone.linkState],
    ["Owned cells", drone.cellIds.length ? drone.cellIds.join(", ") : "—"],
    ["Blocked cells", String(drone.blockedCells)],
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
            <span className="node-kicker">PRATIK COSYS SIMULATION VEHICLE</span>
            <h2 id={`drone-modal-${drone.id}`}>{drone.id}</h2>
            <p>{drone.role} · retained rescue telemetry</p>
          </div>
          <div className="modal-header-actions">
            <span className={`node-online ${drone.online ? "" : "unverified"}`}><i /> {drone.status}</span>
            <button className="modal-close" onClick={onClose} aria-label="Close drone details" autoFocus>
              <X size={19} />
            </button>
          </div>
        </div>

        <div className="modal-telemetry">
          <div><span>ALTITUDE</span><b>{metricValue(drone.alt, "m")}</b></div>
          <div><span>MISSION STATE</span><b>{drone.vehicleState ?? "—"}</b></div>
          <div><span>BATTERY</span><b>{metricValue(drone.bat, "%")}</b></div>
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
          <span>SIMULATION TELEMETRY CHANNEL</span>
          <div aria-hidden="true">{Array.from({ length: 22 }).map((_, index) => <i key={index} style={{ height: `${8 + ((index * 19) % 25)}px` }} />)}</div>
          <b>{drone.linkState}</b>
        </div>
        <p className="modal-dismiss-hint">Click outside this card or press ESC to return to command view.</p>
      </motion.section>
    </motion.div>
  );
}

function DroneDeck({ rescue }) {
  const [activeCard, setActiveCard] = useState(null);
  const [selectedDrone, setSelectedDrone] = useState(null);
  const missionView = useMemo(
    () => buildCellMissionView(rescue.state, rescue.movementConfig),
    [rescue.state, rescue.movementConfig],
  );
  const coverageByDrone = new Map((missionView.byDrone ?? []).map((item) => [item.node.toUpperCase(), item]));
  const projectedVehicles = new Map(
    (rescue?.state?.vehicles ?? []).map((vehicle) => [String(vehicle.node).toUpperCase(), vehicle]),
  );
  const drones = baseDrones.map((drone) => {
    const vehicle = projectedVehicles.get(drone.id);
    const position = vehicle?.position_ned;
    const altitude = Array.isArray(position) && Number.isFinite(Number(position[2]))
      ? Math.abs(Number(position[2]))
      : null;
    const coverage = coverageByDrone.get(drone.id);
    return {
      ...drone,
      alt: altitude,
      bat: Number.isFinite(Number(vehicle?.battery_pct)) ? Number(vehicle.battery_pct) : null,
      online: Boolean(vehicle) && vehicle.link_state !== "OFFLINE",
      status: vehicle?.state ?? "AWAITING",
      vehicleState: vehicle?.state ?? null,
      position,
      linkState: vehicle?.link_state ?? "AWAITING LINK",
      cellIds: coverage?.cellIds ?? [],
      completedCells: coverage?.completed ?? 0,
      blockedCells: coverage?.blocked ?? 0,
      totalCells: coverage?.cellIds?.length ?? 0,
    };
  });
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
          PRATIK FIVE-DRONE SIMULATION · DELTA / ALPHA / BRAVO / CHARLIE / ECHO · {rescue?.state ? "RETAINED TELEMETRY LIVE" : "AWAITING SIMULATION EVENTS"}
        </div>
      </section>
      <AnimatePresence>
        {selectedDrone && <DroneDetailsModal drone={selectedDrone} onClose={() => setSelectedDrone(null)} />}
      </AnimatePresence>
    </>
  );
}

function RealTimeView({ rescue }) {
  const [topViewFailed, setTopViewFailed] = useState(false);
  const missionView = useMemo(
    () => buildCellMissionView(rescue.state, rescue.movementConfig),
    [rescue.state, rescue.movementConfig],
  );
  const missionMap = useMemo(
    () => buildMissionMap(missionView, rescue.movementConfig, rescue.state?.vehicles ?? []),
    [missionView, rescue.movementConfig, rescue.state?.vehicles],
  );
  const showTopView = Boolean(pratikTopViewUrl) && !topViewFailed;
  const eventCount = Number(rescue.state?.events_applied ?? 0);
  return (
    <section className="capsule">
      <div className="panel-heading">
        <div>
          <span className="section-kicker">PRATIK SIMULATION · NED TOP VIEW</span>
          <div className="panel-title">Live Coverage Map</div>
        </div>
        <span className={`feed-state ${rescue.state ? "connected" : "standby"}`}>
          <i /> {rescue.state ? "LIVE EVENTS" : "AWAITING EVENTS"}
        </span>
      </div>
      <div className="mission-map-shell">
        <div className="mission-map-stage">
          {showTopView && (
            <img
              className="mission-top-view-bg"
              src={pratikTopViewUrl}
              alt="Pratik simulation top-view background"
              onError={() => setTopViewFailed(true)}
            />
          )}
          {missionMap.ready ? (
            <svg
              className="mission-map-svg"
              viewBox={`0 0 ${missionMap.width} ${missionMap.height}`}
              preserveAspectRatio="xMidYMid slice"
              role="img"
              aria-label="Live Pratik simulation coverage heatmap"
            >
              <defs>
                <pattern id="ned-grid" width="40" height="40" patternUnits="userSpaceOnUse">
                  <path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(148,163,184,0.12)" strokeWidth="1" />
                </pattern>
              </defs>
              <rect width={missionMap.width} height={missionMap.height} fill="url(#ned-grid)" />
              <text className="map-axis-label" x="22" y="26">N ↑</text>
              <text className="map-axis-label" x={missionMap.width - 50} y={missionMap.height - 18}>E →</text>
              {missionMap.cells.map((cell) => (
                <g key={cell.id}>
                  <polygon
                    className={`map-cell state-${cell.state.toLowerCase()}`}
                    points={cell.points}
                  >
                    <title>{cell.id} · {cell.state} · owner {cell.owner ?? "unknown"}</title>
                  </polygon>
                  <text className="map-cell-label" x={cell.label.x} y={cell.label.y + 3} textAnchor="middle">
                    {cell.id.replace("route_cell_", "")}
                  </text>
                </g>
              ))}
              <g className="map-endpoint start" transform={`translate(${missionMap.start.x} ${missionMap.start.y})`}>
                <circle r="12" /><text textAnchor="middle" y="4">A</text>
              </g>
              <g className="map-endpoint end" transform={`translate(${missionMap.end.x} ${missionMap.end.y})`}>
                <circle r="12" /><text textAnchor="middle" y="4">B</text>
              </g>
              {missionMap.vehicles.map((vehicle) => (
                <g key={vehicle.node} className="map-vehicle" transform={`translate(${vehicle.x} ${vehicle.y})`}>
                  <circle className="map-vehicle-pulse" r="15" />
                  <circle r="6" />
                  <text x="11" y="4">{String(vehicle.node).toUpperCase()}</text>
                  <title>{String(vehicle.node).toUpperCase()} · {vehicle.state ?? "state unavailable"}</title>
                </g>
              ))}
            </svg>
          ) : (
            <div className="map-placeholder">
              <b>IMMUTABLE MAP CONFIGURATION UNAVAILABLE</b>
              <span>The dashboard will not invent route cells or vehicle coordinates.</span>
            </div>
          )}
          <div className="map-source-badge">
            <span>{showTopView ? "PRATIK TOP VIEW + NED OVERLAY" : "NED EVENT OVERLAY"}</span>
            <b>{missionMap.vehicles.length}/5 POSITIONED</b>
          </div>
        </div>
        <div className="feed-footer">
          <span><Server size={14} /> SOURCE: PRATIK COSYS RETAINED RESCUE EVENTS</span>
          <b>{rescue.state ? `${eventCount} EVENTS APPLIED` : "AWAITING STATE"}</b>
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
  const [movementConfig, setMovementConfig] = useState(null);
  const [configError, setConfigError] = useState(null);

  useEffect(() => {
    let active = true;
    const loadConfig = async () => {
      try {
        const response = await fetch("/api/rescue/config", { cache: "no-store" });
        const payload = await response.json();
        if (!response.ok || !payload.ok || !payload.config) {
          throw new Error(payload.error ?? "movement_config_unavailable");
        }
        if (active) {
          setMovementConfig(payload.config);
          setConfigError(null);
        }
      } catch (requestError) {
        if (active) {
          setMovementConfig(null);
          setConfigError(requestError.message);
        }
      }
    };
    loadConfig();
    return () => { active = false; };
  }, []);

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

  return { state, error, movementConfig, configError };
}

function RescueMission({ rescue }) {
  const state = rescue.state;
  const alerts = state?.alerts?.slice(0, 3) ?? [];
  const mappedHazards = state?.hazards?.filter((hazard) => hazard.status === "MAPPED_HAZARD").length ?? 0;
  const missionOnline = Boolean(state);
  const eventCount = Number(state?.events_applied ?? 0);
  const rescueDetail = state?.scenario_id
    ?? (missionOnline
      ? `CONNECTED · ${eventCount} VERIFIED EVENT${eventCount === 1 ? "" : "S"} · WAITING FOR MISSION`
      : rescue.error ?? "RESCUE COLLECTOR OFFLINE");
  const alertMessage = (alert) => {
    if (alert.kind === "AUTHORIZATION" && alert.target_id && alert.message?.includes("model_hash_not_approved")) {
      return `${String(alert.target_id).toUpperCase()} QUARANTINED — UNAPPROVED MODEL HASH`;
    }
    return String(alert.message ?? "Responder review required").replaceAll("_", " ");
  };
  return (
    <section className={`rescue-mission ${missionOnline ? "online" : "offline"}`}>
      <div className="rescue-title">
        <span>OPERATION VARUNA</span>
        <b>{state?.mission_status ?? "AWAITING RESCUE EVENTS"}</b>
        <small>{rescueDetail}</small>
      </div>
      <div className="rescue-kpis">
        <div><span>COVERAGE</span><b>{state ? `${Number(state.coverage?.percent ?? 0).toFixed(1)}%` : "—"}</b></div>
        <div><span>PERSON CANDIDATES</span><b>{state?.people?.length ?? "—"}</b></div>
        <div><span>HAZARDS</span><b>{state ? mappedHazards : "—"}</b></div>
        <div><span>VEHICLES</span><b>{state?.vehicles?.length ?? "—"}</b></div>
      </div>
      <div className="rescue-alerts">
        {alerts.length ? alerts.map((alert) => (
          <div key={alert.alert_id} className={`rescue-alert priority-${alert.priority.toLowerCase()}`}>
            <span>{alert.priority}</span>
            <b>{alertMessage(alert)}</b>
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

function CellMovementPanel({ rescue }) {
  const view = useMemo(
    () => buildCellMissionView(rescue.state, rescue.movementConfig),
    [rescue.state, rescue.movementConfig],
  );
  const recentMovement = view.movement.slice(-4).reverse();
  const validationLabel = !view.ready
    ? "CONFIGURATION HOLD"
    : view.issues.length
      ? `VALIDATION HOLD · ${view.issues.length} ISSUE${view.issues.length === 1 ? "" : "S"}`
      : rescue.state
        ? "REPLAYABLE CELL STATE"
        : "WAITING FOR CELL EVENTS";
  return (
    <section className={`cell-movement-panel ${view.issues.length ? "has-conflicts" : ""}`}>
      <div className="cell-panel-heading">
        <div>
          <span className="section-kicker">READ-ONLY FACTORYCITY CELL EVIDENCE</span>
          <div className="panel-title">Coverage Heatmap & Movement Safety</div>
        </div>
        <span className={`cell-validation ${view.issues.length ? "invalid" : ""}`}>
          <i /> {validationLabel}
        </span>
      </div>

      <div className="cell-scoreboard" aria-label="Exact cell scoreboard">
        <div><span>ASSIGNED</span><b>{view.scoreboard.assigned}</b></div>
        <div><span>IN PROGRESS</span><b>{view.scoreboard.inProgress}</b></div>
        <div><span>COMPLETED</span><b>{view.scoreboard.completed}</b></div>
        <div><span>BLOCKED</span><b>{view.scoreboard.blocked}</b></div>
        <div><span>CONFLICTS</span><b>{view.scoreboard.conflicts}</b></div>
      </div>

      <div className="drone-coverage-grid" aria-label="Five-drone coverage breakdown">
        {(view.byDrone ?? []).map((drone) => (
          <div key={drone.node} className={`drone-coverage-row node-${drone.node}`}>
            <div className="drone-coverage-heading">
              <b>{drone.node.toUpperCase()}</b>
              <span>{drone.completed}/{drone.cellIds.length} COMPLETE</span>
            </div>
            <div className="drone-cell-chips">
              {view.cells.filter((cell) => cell.owner === drone.node).map((cell) => (
                <span
                  key={cell.id}
                  className={`state-${cell.state.toLowerCase()}`}
                  title={`${cell.id} · ${cell.state}`}
                >
                  {cell.id.replace("route_cell_", "")}
                </span>
              ))}
            </div>
            <small>{drone.inProgress} ACTIVE · {drone.blocked} BLOCKED · {drone.conflicts} CONFLICT</small>
          </div>
        ))}
      </div>

      <div className="cell-content-grid">
        <div className="cell-heatmap-wrap">
          <div className="cell-heatmap" role="img" aria-label="Route cells colored only from exact retained cell ID evidence">
            {view.cells.map((cell) => (
              <div
                key={cell.id}
                className={`route-cell state-${cell.state.toLowerCase()}`}
                style={{ flexGrow: cell.widthPercent, flexBasis: 0 }}
                title={`${cell.id} · ${cell.state} · owner ${cell.owner ?? "unknown"}`}
              >
                <b>{cell.id.replace("route_cell_", "")}</b>
                <span>{cell.owner ?? "—"}</span>
              </div>
            ))}
          </div>
          <div className="cell-legend">
            <span className="legend-assigned">ASSIGNED</span>
            <span className="legend-progress">IN PROGRESS</span>
            <span className="legend-completed">COMPLETED</span>
            <span className="legend-blocked">BLOCKED</span>
            <span className="legend-conflict">CONFLICT / UNKNOWN</span>
          </div>
          {view.issues.length > 0 && (
            <div className="cell-issues" aria-live="polite">
              {view.issues.slice(0, 3).map((issue) => (
                <span key={`${issue.code}:${issue.cellId ?? "none"}:${issue.detail ?? ""}`}>
                  {issue.code}{issue.cellId ? ` · ${issue.cellId}` : ""}
                </span>
              ))}
              {view.issues.length > 3 && <span>+{view.issues.length - 3} MORE RETAINED VALIDATION ISSUES</span>}
            </div>
          )}
        </div>

        <div className="movement-ledger">
          <div className="movement-ledger-title">
            <span>MOVEMENT-SAFETY EVIDENCE</span>
            <b>{view.movement.length} TRANSITIONS</b>
          </div>
          {recentMovement.length ? recentMovement.map((event) => (
            <div key={event.event_id} className={`movement-row result-${String(event.result).toLowerCase()}`}>
              <div>
                <span>{String(event.node).toUpperCase()} · {event.cell_id}</span>
                <b>{String(event.event_type).replaceAll("_", " ")}</b>
              </div>
              <div>
                <strong>{event.terminalLabel}</strong>
                <small>{Number(event.measured_distance_m).toFixed(2)}m · {event.measurement_source}</small>
              </div>
            </div>
          )) : (
            <div className="movement-empty">
              Retained obstacle, hold, deflection, rejoin, blockage and collision transitions will appear here.
            </div>
          )}
        </div>
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
  const proofActive = Boolean(proof?.clean?.evidence);
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
  const cleanEvidence = proof?.clean?.evidence;
  const attackEvidence = proof?.attack?.evidence;
  const currentEvidence = attackEvidence ?? cleanEvidence;
  const proofFile = proof?.attack?.dashboardEvidenceFile ?? proof?.clean?.dashboardEvidenceFile;
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
          <b>{proofActive ? currentEvidence?.actual?.disputes ?? 0 : telemetry?.altitude == null ? "—" : `${telemetry.altitude}m`}</b>
        </div>
        <div>
          <span>{proofActive ? "RESULT" : "TRUST"}</span>
          <b>{proofActive ? currentEvidence?.actual?.outcome ?? "—" : telemetry?.reputation == null ? "—" : `${Math.round(telemetry.reputation * 100)}%`}</b>
        </div>
      </div>
      <div className="analytics-source">
        <span>{proofActive ? proofFile ?? "create-once dashboard evidence" : telemetry?.source ?? "waiting for event source"}</span>
        <b>{proofActive ? currentEvidence?.authorization?.allowed ? "EXECUTE" : "HOLD" : telemetry?.action ?? scenario.supervisor}</b>
      </div>
    </section>
  );
}

function AttackSystems({ qualification }) {
  const { backend, proof, runningStage, runError, runProof, resetView } = qualification;
  const cleanEvidence = proof?.clean?.evidence;
  const attackEvidence = proof?.attack?.evidence;
  const dashboardProof = attackEvidence?.dashboard_proof;
  const visibleProof = dashboardProof ?? cleanEvidence?.dashboard_proof;
  const cleanPassed = isApprovedCleanEvidence(cleanEvidence);
  const pending = Boolean(runningStage);
  const status = pending
    ? { message: runningStage === "clean" ? "VERIFYING CLEAN BASELINE" : "RUNNING MODEL-HASH ATTACK", tone: "pending" }
      : runError
      ? { message: runError.toUpperCase(), tone: "error" }
      : dashboardProof?.proof_valid
        ? { message: "REAL EVIDENCE VERIFIED", tone: "success" }
        : cleanPassed
          ? { message: "APPROVED BASELINE VERIFIED · READY FOR ATTACK", tone: "success" }
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
            disabled={!enabled || pending || !backend.ready || (key === "model" && Boolean(dashboardProof))}
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
          <span>CLEAN BASELINE</span>
          <b>{cleanEvidence?.actual?.outcome ?? "NOT RUN"}</b>
          <small>semantic ACKs {cleanEvidence?.actual?.semantic_acks ?? "—"}/2</small>
        </div>
        <div>
          <span>MODEL-HASH ATTACK</span>
          <b>{attackEvidence?.actual?.outcome ?? "NOT RUN"}</b>
          <small>peer disputes {attackEvidence?.actual?.disputes ?? "—"}/2</small>
        </div>
        <div>
          <span>RELEASED COMMAND</span>
          <b>{attackEvidence?.authorization?.allowed ? "EXECUTE" : attackEvidence ? "HOLD · NO MOTION" : "—"}</b>
          <small>{attackEvidence ? JSON.stringify(attackEvidence.authorization?.released ?? []) : "waiting for evidence"}</small>
        </div>
      </div>
      <div className="hash-comparison">
        <div><span>ALLOWLIST MODEL HASH</span><b>{shortHash(visibleProof?.approved_model_sha256, 20)}</b></div>
        <i aria-hidden="true" />
        <div>
          <span>{dashboardProof ? "CLAIMED HASH ON ALPHA" : "VERIFIED HASH ON ALPHA"}</span>
          <b>{shortHash(visibleProof?.observed_model_sha256, 20)}</b>
        </div>
      </div>
      <div className="attack-status">
        <span className={status.tone} aria-live="polite"><i /> {status.message}</span>
        <button onClick={runProof} disabled={pending || !backend.ready || Boolean(dashboardProof)}>
          {pending
            ? "RUNNING…"
            : dashboardProof
              ? "RESULT RETAINED · RESET TO RERUN"
              : cleanPassed
                ? "RUN MODEL-HASH ATTACK"
                : "RUN APPROVED BASELINE"}
        </button>
      </div>
      <div className="control-summary">
        <span>{visibleProof?.policy_reason ?? "MODEL POLICY NOT YET TESTED"}</span>
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

function EvidenceStrip({ scenario, qualification }) {
  const cleanEvidence = qualification?.proof?.clean?.evidence;
  const attackEvidence = qualification?.proof?.attack?.evidence;
  const dashboardProof = attackEvidence?.dashboard_proof;
  const cleanProof = cleanEvidence?.dashboard_proof;
  const cleanPassed = isApprovedCleanEvidence(cleanEvidence);
  const items = dashboardProof ? [
    ["Clean baseline", `${cleanEvidence?.actual?.semantic_acks ?? 0}/2 ACK`, ShieldCheck],
    ["Attack ACKs", `${attackEvidence?.actual?.semantic_acks ?? 0}/2 ACK`, Binary],
    ["Peer verdict", `${attackEvidence?.actual?.disputes ?? 0}/2 DISPUTE`, ShieldX],
    ["Safety response", attackEvidence?.authorization?.allowed ? "EXECUTE" : "HOLD + QUARANTINE", ShieldCheck],
    ["Retained proof", dashboardProof.proof_valid ? "VERIFIED · UNAPPROVED HASH" : "VERIFICATION FAILED", Fingerprint],
  ] : cleanPassed ? [
    ["Approved provenance", cleanProof.proof_valid ? "VERIFIED" : "FAILED", Fingerprint],
    ["Clean baseline", `${cleanEvidence?.actual?.semantic_acks ?? 0}/2 ACK`, ShieldCheck],
    ["Model-hash attack", "NOT RUN", Binary],
    ["Safety state", cleanEvidence?.authorization?.allowed ? "EXECUTE" : "HOLD · NO MOTION", ShieldCheck],
    ["Next step", "RUN MODEL-HASH ATTACK", Activity],
  ] : cleanProof ? [
    ["Approved provenance", "VERIFICATION FAILED", ShieldX],
    ["Clean baseline", "FAILED CLOSED", ShieldX],
    ["Model-hash attack", "LOCKED", Binary],
    ["Safety state", "HOLD · NO MOTION", ShieldCheck],
    ["Next step", "RERUN APPROVED BASELINE", Activity],
  ] : [
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
  const analyticsPointerEvents = useTransform(progress, (value) => (value >= 0.72 ? "auto" : "none"));
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
          style={{ opacity: analyticsOpacity, y: analyticsY, scale: analyticsScale, pointerEvents: analyticsPointerEvents }}
        >
          <div className="analytics-intro">
            <div>
              <span>LIVE MISSION INTELLIGENCE</span>
              <h2>Every decision. Accounted for.</h2>
            </div>
            <p>One presentation-ready view for retained evidence, live telemetry, attack controls, and cryptographic status.</p>
          </div>

          <EvidenceStrip scenario={scenario} qualification={qualification} />

          <RescueMission rescue={rescue} />

          <CellMovementPanel rescue={rescue} />

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
          <DroneDeck rescue={rescue} />
          <RealTimeView rescue={rescue} />
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

"""
Adversarial-patch transfer experiment (paper Section 4.3).

Closes the "does an adversarial patch transfer across viewpoints?" question
EMPIRICALLY rather than by assumption. The semantic check works only if a patch
that fools the originator does NOT also fool its co-observing peers, who view the
same scene from a different angle. We test that directly.

A real, white-box adversarial patch is optimized against YOLOv8-nano with PGD +
Expectation-over-Transformation (EoT), in two variants:

  single-view      : EoT over photometric jitter + near-nadir tilt only (the patch
                     is tuned to the victim's own viewpoint).
  viewpoint-robust : EoT additionally over off-nadir perspective tilts spanning the
                     measured co-visibility range, so the patch is optimized to
                     survive a peer's viewpoint.

The patch suppresses detection of the scene's dominant object (the obstacle the
reactive controller of perception.yolo_action reacts to). We then measure TRANSFER:
a peer at off-nadir disparity angle phi -- emulated by a rotational homography
H = K R_x(phi) K^-1, the standard planar-viewpoint warp -- views the same patched
scene, and we record whether the target object is still suppressed in its view.
A clean (un-patched) warp at each phi is the control, confirming the object is
visible at that viewpoint, so any suppression is the patch's doing and not the warp.

Protocol meaning: if the peer still SEES the obstacle the victim's patch hid, its
reactive action diverges from the victim's "clear path" action and the semantic
check DISPUTES (caught). If the patch suppresses the obstacle for the peer too, the
actions agree and the attack EVADES. We sweep phi and mark the disparities measured
in real flight (Section 4.5): 3 m separation at ~14 m altitude ~= atan(3/14) ~= 12
deg, 6 m ~= 23 deg. Each patch is optimized over several seeds and the per-viewpoint
detection confidence is reported as mean +/- std.

Only the patch optimization is white-box; every reported detection/action comes from
the real perception pipeline (perception.yolo_action). No numbers are synthesized.

Run (GPU recommended):  python3 -m eval.adv_patch_transfer
Writes results/adv_patch_transfer_raw.csv (per seed) and
results/adv_patch_transfer.csv (per-viewpoint mean/std + suppression rate).
"""

from __future__ import annotations

import math
import statistics
from pathlib import Path

import numpy as np
import cv2
import torch
import torch.nn.functional as F
from ultralytics import YOLO
from ultralytics.utils import ASSETS

from perception.yolo_action import frame_to_action, frame_to_detections
from eval import harness as H

DEV = "cuda" if torch.cuda.is_available() else "cpu"
SIZE = 640
WEIGHTS = "yolov8n.pt"
TAU = 0.25            # detector confidence threshold: below this the object is "missed"
SEEDS = (0, 1, 2)     # patch optimization is stochastic; average for robustness

# Measured co-visibility disparities (real flight, Section 4.5): horizontal sep s at
# altitude z gives an inter-view angle atan(s/z); 3 m / 14 m ~= 12 deg, 6 m ~= 23 deg.
PHI_MARKS = {12: "3 m sep", 23: "6 m sep"}
PHI_SWEEP = [0, 3, 6, 9, 12, 15, 18, 21, 23]


# --------------------------------------------------------------------------- #
# Viewpoint warp: rotational homography of a planar scene, H = K R_x(phi) K^-1 #
# --------------------------------------------------------------------------- #
def tilt_homography(phi_deg: float, w: int = SIZE, h: int = SIZE, f_scale: float = 1.1) -> np.ndarray:
    """Homography emulating a camera rotated off-nadir by phi about its horizontal
    axis, viewing a planar scene. phi=0 is the identity."""
    phi = math.radians(phi_deg)
    f = f_scale * w
    K = np.array([[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1.0]])
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(phi), -math.sin(phi)],
                   [0, math.sin(phi), math.cos(phi)]])
    Hm = K @ Rx @ np.linalg.inv(K)
    return Hm / Hm[2, 2]


def warp_torch(img: torch.Tensor, Hm: np.ndarray) -> torch.Tensor:
    """Differentiable warpPerspective via grid_sample (border padding)."""
    n, c, hh, ww = img.shape
    Hinv = torch.tensor(np.linalg.inv(Hm), dtype=torch.float32, device=img.device)
    ys, xs = torch.meshgrid(torch.arange(hh, device=img.device),
                            torch.arange(ww, device=img.device), indexing="ij")
    dst = torch.stack([xs.flatten().float(), ys.flatten().float(),
                       torch.ones(hh * ww, device=img.device)], dim=0)
    src = Hinv @ dst
    src = src / src[2:3, :].clamp(min=1e-8)
    gx = (src[0].reshape(hh, ww) / (ww - 1)) * 2 - 1
    gy = (src[1].reshape(hh, ww) / (hh - 1)) * 2 - 1
    grid = torch.stack([gx, gy], dim=-1).unsqueeze(0).expand(n, -1, -1, -1)
    return F.grid_sample(img, grid, align_corners=True, padding_mode="border")


def load_scene(path: Path) -> np.ndarray:
    return cv2.resize(cv2.imread(str(path)), (SIZE, SIZE))


def bgr_to_tensor(bgr: np.ndarray) -> torch.Tensor:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(DEV)


def tensor_to_bgr(t: torch.Tensor) -> np.ndarray:
    rgb = (t.clamp(0, 1).squeeze(0).permute(1, 2, 0).detach().cpu().numpy() * 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def dominant_target(bgr: np.ndarray, model: YOLO):
    """Largest detection: class + pixel bbox (the obstacle to suppress)."""
    dets = frame_to_detections(bgr, model)
    if not dets:
        return None
    d = max(dets, key=lambda d: d.w * d.h)
    return {"cls": d.cls, "cx": d.x * SIZE, "cy": d.y * SIZE,
            "bw": d.w * SIZE, "bh": d.h * SIZE, "conf": d.conf}


def target_conf(bgr: np.ndarray, model: YOLO, cls: int) -> float:
    dets = [d for d in frame_to_detections(bgr, model) if d.cls == cls]
    return max((d.conf for d in dets), default=0.0)


# --------------------------------------------------------------------------- #
# Patch optimization (white-box PGD + EoT)                                     #
# --------------------------------------------------------------------------- #
def optimize_patch(base_t, net, head, target, frac, phi_max,
                   steps=250, k_eot=4, lr=6 / 255):
    """Optimize a patch over the target to suppress its detection. phi_max sets the
    EoT viewpoint range (~2 deg => single-view, large => viewpoint-robust). The
    detect head is run in training mode so it returns raw class logits ('scores'),
    letting gradients flow to the patch. Returns (patched image tensor, area frac)."""
    cls = target["cls"]
    pw = max(16, int(target["bw"] * frac))
    ph = max(16, int(target["bh"] * frac))
    r0 = int(np.clip(target["cy"] - ph / 2, 0, SIZE - ph))
    c0 = int(np.clip(target["cx"] - pw / 2, 0, SIZE - pw))

    patch = torch.rand(1, 3, ph, pw, device=DEV, requires_grad=True)
    mask = torch.zeros(1, 1, SIZE, SIZE, device=DEV)
    mask[:, :, r0:r0 + ph, c0:c0 + pw] = 1.0

    head.training = True
    try:
        for _ in range(steps):
            canvas = torch.zeros(1, 3, SIZE, SIZE, device=DEV)
            canvas[:, :, r0:r0 + ph, c0:c0 + pw] = patch
            x = base_t * (1 - mask) + canvas * mask  # differentiable composite
            loss = 0.0
            for _ in range(k_eot):
                phi = float(np.random.uniform(-phi_max, phi_max))
                xt = warp_torch(x, tilt_homography(phi)) if abs(phi) > 1e-3 else x
                xt = (xt * float(np.random.uniform(0.85, 1.15)) +
                      float(np.random.uniform(-0.05, 0.05))).clamp(0, 1)
                out = net(xt)
                scores = out["scores"] if isinstance(out, dict) else out  # (1,nc,anchors)
                loss = loss + torch.sigmoid(scores[:, cls, :]).amax()
            loss = loss / k_eot
            if patch.grad is not None:
                patch.grad = None
            loss.backward()
            with torch.no_grad():
                patch -= lr * patch.grad.sign()
                patch.clamp_(0, 1)
    finally:
        head.training = False

    with torch.no_grad():
        canvas = torch.zeros(1, 3, SIZE, SIZE, device=DEV)
        canvas[:, :, r0:r0 + ph, c0:c0 + pw] = patch
        x = base_t * (1 - mask) + canvas * mask
    return x.detach(), (pw * ph) / float(SIZE * SIZE)


def l2(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# --------------------------------------------------------------------------- #
def main():
    from ultralytics.nn.modules.head import Detect
    model = YOLO(WEIGHTS)
    net = model.model.float().to(DEV).eval()
    for p in net.parameters():
        p.requires_grad_(False)
    head = next(m for m in net.modules() if isinstance(m, Detect))

    scenes = [p for p in (ASSETS / "bus.jpg", ASSETS / "zidane.jpg") if Path(p).exists()]
    variants = [("single-view", 0.30, 2.0), ("viewpoint-robust", 0.30, 25.0)]

    raw, agg = [], []
    for scene in scenes:
        base_bgr = load_scene(scene)
        base_t = bgr_to_tensor(base_bgr)
        target = dominant_target(base_bgr, model)
        if target is None:
            print(f"skip {scene.name}: no dominant object"); continue
        cls = target["cls"]
        clean_conf = target["conf"]
        clean_action = frame_to_action(base_bgr, model)
        print(f"\n=== {scene.name}: target cls={cls} clean_conf={clean_conf:.2f} "
              f"clean_action={tuple(round(v,2) for v in clean_action)} ===")

        for vname, frac, phi_max in variants:
            for seed in SEEDS:
                torch.manual_seed(seed)
                np.random.seed(seed)
                patched_t, area = optimize_patch(base_t, net, head, target, frac, phi_max)
                patched_bgr = tensor_to_bgr(patched_t)
                victim_conf = target_conf(patched_bgr, model, cls)
                victim_action = frame_to_action(patched_bgr, model)
                for phi in PHI_SWEEP:
                    Hm = tilt_homography(phi)
                    pp = cv2.warpPerspective(patched_bgr, Hm, (SIZE, SIZE), borderMode=cv2.BORDER_REPLICATE)
                    pc = cv2.warpPerspective(base_bgr, Hm, (SIZE, SIZE), borderMode=cv2.BORDER_REPLICATE)
                    conf_p = target_conf(pp, model, cls)
                    conf_c = target_conf(pc, model, cls)
                    raw.append({"scene": scene.name, "variant": vname, "seed": seed,
                                "patch_area_pct": round(area * 100, 2), "phi_deg": phi,
                                "victim_conf": round(victim_conf, 3),
                                "peer_patched_conf": round(conf_p, 3),
                                "peer_clean_conf": round(conf_c, 3),
                                "action_delta": round(l2(victim_action, frame_to_action(pp, model)), 4)})
                print(f"  [{vname:16} seed{seed}] area={area*100:4.1f}%  "
                      f"victim {clean_conf:.2f}->{victim_conf:.2f}")

            # aggregate over seeds, per viewpoint
            for phi in PHI_SWEEP:
                sel = [r for r in raw if r["scene"] == scene.name and r["variant"] == vname and r["phi_deg"] == phi]
                pp = [r["peer_patched_conf"] for r in sel]
                pc = [r["peer_clean_conf"] for r in sel]
                vc = [r["victim_conf"] for r in sel]
                # suppression = patch hides the object for the peer, given it is
                # visible on the clean warp at this viewpoint (control).
                supp = [1.0 if (a < TAU <= b) else 0.0 for a, b in zip(pp, pc)]
                agg.append({"scene": scene.name, "variant": vname,
                            "phi_deg": phi,
                            "clean_conf_mean": round(statistics.fmean(pc), 3),
                            "victim_conf_mean": round(statistics.fmean(vc), 3),
                            "peer_patched_conf_mean": round(statistics.fmean(pp), 3),
                            "peer_patched_conf_std": round(statistics.pstdev(pp), 3),
                            "suppression_rate": round(statistics.fmean(supp), 3),
                            "n_seeds": len(sel), "marked": PHI_MARKS.get(phi, "")})

    H.write_csv("adv_patch_transfer_raw.csv", raw)
    H.write_csv("adv_patch_transfer.csv", agg)

    print("\n--- transfer summary: peer's mean detection conf of the hidden object ---")
    print("(low = patch transfers/peer fooled/EVADE; high = peer re-detects/DISPUTE)")
    for scene in {a["scene"] for a in agg}:
        for vname, _, _ in variants:
            row12 = next((a for a in agg if a["scene"] == scene and a["variant"] == vname and a["phi_deg"] == 12), None)
            row23 = next((a for a in agg if a["scene"] == scene and a["variant"] == vname and a["phi_deg"] == 23), None)
            row0 = next((a for a in agg if a["scene"] == scene and a["variant"] == vname and a["phi_deg"] == 0), None)
            if row0:
                print(f"{scene:11} {vname:16}  victim(0deg)={row0['victim_conf_mean']:.2f}  "
                      f"peer@12deg={row12['peer_patched_conf_mean']:.2f}(supp {row12['suppression_rate']:.2f})  "
                      f"peer@23deg={row23['peer_patched_conf_mean']:.2f}(supp {row23['suppression_rate']:.2f})")
    print("\nwrote results/adv_patch_transfer.csv (+ _raw.csv)")


if __name__ == "__main__":
    main()

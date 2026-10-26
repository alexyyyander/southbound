# 2.5D Scene & 3DGS Visual Layer

`/scene` is the current playable room demo. It is deliberately a **procedural
2.5D control map**, not a photoreal renderer: coordinates, device anchors,
click targets, policy checks, audit, and read-back verification all stay
deterministic. A later 3D Gaussian Splatting (3DGS) layer can make the same
room look real without changing that control plane.

## Current 2.5D model

```text
browser avatar (x, y)
        │ POST /api/scene/presence
        ▼
SceneController ── presence event ──► EventBus / SSE
        │
        ├─ low-risk reflex rule (living light / bedroom purifier)
        ▼
SouthboundRuntime.call() → AccessHub → policy → audit → adapter
        │
        ▼
state read-back → automation.verified / automation.failed → agent.notice
```

- Room is `12 × 8` metres; zones are entry, living, bedroom, study.
- Six built-in simulator devices have fixed anchor coordinates in
  `southbound/scene.py` (`PLACEMENTS`).
- The avatar is a proxy for a person or a robot; it is not camera tracking.
- Only low-risk actions run in the scene's automatic mode. High-risk devices
  still require policy and confirmation.

## 3DGS as a skin, not a second brain

Keep the 2.5D map authoritative:

| Concern | Owner |
|---|---|
| Avatar/zone coordinates, collisions | 2.5D scene |
| Device anchors and hit targets | 2.5D scene |
| Rules, policy, audit, verification | `SceneController` + `AccessHub` |
| Photoreal walls, furniture, light | optional 3DGS layer |
| Camera orbit / visual effects | optional 3DGS layer |

A device placement can later grow an optional `splat_anchor`:

```json
{
  "device_id": "living_light",
  "x": 3.4,
  "y": 2.0,
  "z": 2.55,
  "splat_anchor": {"x": 1.83, "y": 2.11, "z": -0.42}
}
```

The UI maps a click on the splat viewer's device hotspot back to
`device_id`, then calls the same northbound API.

## Capture and training pipeline

1. Capture a short, well-lit walkthrough of the room (video or photos).
2. Reconstruct camera poses with COLMAP.
3. Train with nerfstudio (`splatfacto`) or gsplat.
4. Export `.splat` or compressed `.ply`; keep a poster frame and a low-res
   preview for first load.
5. Place assets in `data/scene/` or Git LFS; never commit raw home footage
   to a public repository.
6. Map world coordinates to the splat's coordinate frame; store the transform
   with the asset, not in ad-hoc JavaScript.
7. Validate device hotspots against the 2.5D anchors before shipping.

## Browser plan

- WebGL2 viewer (Three.js + a splat renderer) as an optional `/scene?view=splat`
  mode; 2.5D canvas remains the fallback.
- Progressive loading: low-res splat first, then full asset; show a progress
  state on the stage.
- Keep the HUD, task list, device inspector, SSE feed, and automation controls
  unchanged.
- Add a "schematic / realistic" toggle; both views share the same state.
- Respect `prefers-reduced-motion` and cap device pixel ratio.

## Performance and safety

- Target a few hundred thousand splats for mobile; desktop can use more.
- Preload only when the user chooses the realistic view.
- 3DGS can reveal a real floor plan; obtain consent, blur sensitive items,
  and do not publish raw captures.
- A visual hit target must never bypass `AccessHub`; it is an input to the
  same policy/audit path.
- The 2.5D map stays available when WebGL or the asset is unavailable.

## Not implemented yet

This repository currently ships the procedural 2.5D scene only. 3DGS capture,
training, asset hosting, and the WebGL viewer are a separate phase; they
should not gate the control-layer demo.

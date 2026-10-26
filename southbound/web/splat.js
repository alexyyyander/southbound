/* Synthetic 3D Gaussian-splat-style preview.
 *
 * This is a visual prototype, not a trained 3DGS asset. The room is sampled
 * from the same procedural geometry as the 2.5D control map, then rendered as
 * depth-tested soft point splats under a perspective camera. A real `.splat`
 * capture can replace this renderer later without changing the scene state or
 * AccessHub path.
 */
(() => {
  "use strict";

  const TAU = Math.PI * 2;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function normalize(v) {
    const length = Math.hypot(v[0], v[1], v[2]) || 1;
    return [v[0] / length, v[1] / length, v[2] / length];
  }

  function cross(a, b) {
    return [
      a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0],
    ];
  }

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  }

  function perspective(fov, aspect, near, far) {
    const f = 1 / Math.tan(fov / 2);
    const range = 1 / (near - far);
    return new Float32Array([
      f / aspect, 0, 0, 0,
      0, f, 0, 0,
      0, 0, (near + far) * range, -1,
      0, 0, near * far * 2 * range, 0,
    ]);
  }

  function lookAt(eye, target, up) {
    const z = normalize([eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]);
    const x = normalize(cross(up, z));
    const y = cross(z, x);
    return new Float32Array([
      x[0], y[0], z[0], 0,
      x[1], y[1], z[1], 0,
      x[2], y[2], z[2], 0,
      -dot(x, eye), -dot(y, eye), -dot(z, eye), 1,
    ]);
  }

  function multiply(a, b) {
    const out = new Float32Array(16);
    for (let column = 0; column < 4; column += 1) {
      for (let row = 0; row < 4; row += 1) {
        out[column * 4 + row] =
          a[row] * b[column * 4] +
          a[4 + row] * b[column * 4 + 1] +
          a[8 + row] * b[column * 4 + 2] +
          a[12 + row] * b[column * 4 + 3];
      }
    }
    return out;
  }

  function transform(matrix, vector) {
    const [x, y, z, w] = vector;
    return [
      matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12] * w,
      matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13] * w,
      matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14] * w,
      matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15] * w,
    ];
  }

  function mulberry32(seed) {
    let value = seed >>> 0;
    return () => {
      value = (value + 0x6d2b79f5) >>> 0;
      let t = value;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function rgb(hex) {
    const clean = String(hex).replace("#", "");
    return [
      parseInt(clean.slice(0, 2), 16) / 255,
      parseInt(clean.slice(2, 4), 16) / 255,
      parseInt(clean.slice(4, 6), 16) / 255,
    ];
  }

  function tint(hex, shade = 1, jitter = 0.035, random = Math.random) {
    const base = rgb(hex);
    const wobble = (random() * 2 - 1) * jitter;
    return base.map((channel) => clamp(channel * shade + wobble, 0, 1));
  }

  function isOn(device) {
    if (!device) return false;
    const value = device.state || {};
    if (device.kind === "binary_sensor") return Boolean(value.open);
    if (device.kind === "sensor") return true;
    if (device.kind === "cover") return Boolean(value.moving) || Number(value.position) > 0;
    return Boolean(value.on);
  }

  class SouthboundSplatRenderer {
    constructor({ canvas, overlay, onSelect }) {
      this.canvas = canvas;
      this.overlay = overlay;
      this.onSelect = onSelect;
      this.gl = null;
      this.available = false;
      this.active = false;
      this.points = null;
      this.count = 0;
      this.vp = null;
      this.width = 0;
      this.height = 0;
      this.dpr = 1;
      this.overlayKey = "";
      this.hotspots = new Map();
      this.zoneEls = [];
      this.avatarEl = null;
      this.yaw = 0.78;
      this.pitch = 0.78;
      this.distance = 17.5;
      this.fov = (42 * Math.PI) / 180;
      this.cameraTarget = [6, 4, 1.15];
      this.drag = null;
      this._state = null;
      this._initGL();
      this._bindInput();
    }

    _initGL() {
      const gl = this.canvas.getContext("webgl", { antialias: true, alpha: false })
        || this.canvas.getContext("experimental-webgl", { antialias: true, alpha: false });
      if (!gl) {
        this.available = false;
        return;
      }
      const vertex = gl.createShader(gl.VERTEX_SHADER);
      const fragment = gl.createShader(gl.FRAGMENT_SHADER);
      gl.shaderSource(vertex, `
        attribute vec3 aPos;
        attribute vec3 aColor;
        attribute float aScale;
        uniform mat4 uView;
        uniform mat4 uProj;
        uniform float uFocal;
        varying vec3 vColor;
        void main() {
          vec4 eye = uView * vec4(aPos, 1.0);
          gl_Position = uProj * eye;
          float distance = max(0.5, -eye.z);
          gl_PointSize = clamp(aScale * uFocal / distance * 3.1, 1.4, 30.0);
          vColor = aColor;
        }
      `);
      gl.shaderSource(fragment, `
        precision mediump float;
        varying vec3 vColor;
        void main() {
          vec2 p = gl_PointCoord * 2.0 - 1.0;
          float radius = dot(p, p);
          if (radius > 1.0) discard;
          float alpha = exp(-radius * 2.8) * 0.36;
          gl_FragColor = vec4(vColor, alpha);
        }
      `);
      gl.compileShader(vertex);
      gl.compileShader(fragment);
      if (!gl.getShaderParameter(vertex, gl.COMPILE_STATUS) || !gl.getShaderParameter(fragment, gl.COMPILE_STATUS)) {
        this.available = false;
        return;
      }
      const program = gl.createProgram();
      gl.attachShader(program, vertex);
      gl.attachShader(program, fragment);
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        this.available = false;
        return;
      }
      this.gl = gl;
      this.program = program;
      this.buffer = gl.createBuffer();
      this.loc = {
        pos: gl.getAttribLocation(program, "aPos"),
        color: gl.getAttribLocation(program, "aColor"),
        scale: gl.getAttribLocation(program, "aScale"),
        view: gl.getUniformLocation(program, "uView"),
        proj: gl.getUniformLocation(program, "uProj"),
        focal: gl.getUniformLocation(program, "uFocal"),
      };
      gl.useProgram(program);
      gl.disable(gl.CULL_FACE);
      gl.disable(gl.DEPTH_TEST);
      gl.depthMask(false);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.clearColor(0.045, 0.08, 0.10, 1);
      this.available = true;
    }

    _bindInput() {
      this.canvas.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        this.drag = {
          x: event.clientX,
          y: event.clientY,
          yaw: this.yaw,
          pitch: this.pitch,
          moved: false,
        };
        this.canvas.setPointerCapture?.(event.pointerId);
      });
      this.canvas.addEventListener("pointermove", (event) => {
        if (!this.drag) return;
        const dx = event.clientX - this.drag.x;
        const dy = event.clientY - this.drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 3) this.drag.moved = true;
        this.yaw = this.drag.yaw - dx * 0.006;
        this.pitch = clamp(this.drag.pitch + dy * 0.006, 0.28, 1.28);
      });
      const endDrag = (event) => {
        if (!this.drag) return;
        this.canvas.releasePointerCapture?.(event.pointerId);
        this.drag = null;
      };
      this.canvas.addEventListener("pointerup", endDrag);
      this.canvas.addEventListener("pointercancel", endDrag);
      this.canvas.addEventListener("wheel", (event) => {
        event.preventDefault();
        this.distance = clamp(this.distance + event.deltaY * 0.012, 9, 30);
      }, { passive: false });
    }

    resize() {
      if (!this.gl) return;
      const rect = this.canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.round(rect.width * dpr);
      const height = Math.round(rect.height * dpr);
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.width = rect.width;
      this.height = rect.height;
      this.dpr = dpr;
      this.gl.viewport(0, 0, width, height);
    }

    setActive(active) {
      this.active = Boolean(active);
      if (this.active) this.resize();
    }

    _eye() {
      const target = this.cameraTarget;
      const horizontal = Math.cos(this.pitch) * this.distance;
      return [
        target[0] + horizontal * Math.cos(this.yaw),
        target[1] + horizontal * Math.sin(this.yaw),
        target[2] + Math.sin(this.pitch) * this.distance,
      ];
    }

    _project(vp, x, y, z) {
      const clip = transform(vp, [x, y, z, 1]);
      if (clip[3] <= 0.005) return null;
      const nx = clip[0] / clip[3];
      const ny = clip[1] / clip[3];
      return {
        x: (nx * 0.5 + 0.5) * this.width,
        y: (-ny * 0.5 + 0.5) * this.height,
        visible: nx > -1.15 && nx < 1.15 && ny > -1.15 && ny < 1.15,
      };
    }

    render(state, now) {
      if (!this.available || !this.active) return;
      this._state = state;
      if (!state.room) return;
      this.resize();
      if (!this.width || !this.height) return;
      if (!this.points) this.build(state.room);
      this.cameraTarget = [state.room.width / 2, state.room.depth / 2, 1.15];

      const eye = this._eye();
      const view = lookAt(eye, this.cameraTarget, [0, 0, 1]);
      const aspect = this.width / this.height;
      const proj = perspective(this.fov, aspect, 0.1, 100);
      const vp = multiply(proj, view);
      this.vp = vp;

      const gl = this.gl;
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      gl.useProgram(this.program);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      gl.enableVertexAttribArray(this.loc.pos);
      gl.vertexAttribPointer(this.loc.pos, 3, gl.FLOAT, false, 28, 0);
      gl.enableVertexAttribArray(this.loc.color);
      gl.vertexAttribPointer(this.loc.color, 3, gl.FLOAT, false, 28, 12);
      gl.enableVertexAttribArray(this.loc.scale);
      gl.vertexAttribPointer(this.loc.scale, 1, gl.FLOAT, false, 28, 24);
      gl.uniformMatrix4fv(this.loc.view, false, view);
      gl.uniformMatrix4fv(this.loc.proj, false, proj);
      const focal = this.canvas.height / (2 * Math.tan(this.fov / 2));
      gl.uniform1f(this.loc.focal, focal);
      gl.drawArrays(gl.POINTS, 0, this.count);

      this._updateOverlay(state, vp);
    }

    build(room) {
      const points = [];
      const random = mulberry32(0x5eed2026);
      const add = (x, y, z, hex, shade = 1, scale = 0.055) => {
        const color = tint(hex, shade, 0.045, random);
        points.push(
          x + (random() * 2 - 1) * 0.025,
          y + (random() * 2 - 1) * 0.025,
          z + (random() * 2 - 1) * 0.012,
          color[0], color[1], color[2],
          scale * (0.82 + random() * 0.42)
        );
      };
      const surface = (x0, x1, y0, y1, z, hex, step, shade = 1, scale = 0.055) => {
        for (let x = x0 + step * 0.5; x < x1; x += step) {
          for (let y = y0 + step * 0.5; y < y1; y += step) {
            add(x, y, z, hex, shade, scale);
          }
        }
      };
      const wallY = (y, x0, x1, z0, z1, hex, step, shade = 1, scale = 0.055) => {
        for (let x = x0 + step * 0.5; x < x1; x += step) {
          for (let z = z0 + step * 0.5; z < z1; z += step) {
            add(x, y, z, hex, shade, scale);
          }
        }
      };
      const wallX = (x, y0, y1, z0, z1, hex, step, shade = 1, scale = 0.055) => {
        for (let y = y0 + step * 0.5; y < y1; y += step) {
          for (let z = z0 + step * 0.5; z < z1; z += step) {
            add(x, y, z, hex, shade, scale);
          }
        }
      };
      const box = (x, y, z, w, d, h, hex, step = 0.16, shade = 1, scale = 0.052) => {
        surface(x, x + w, y, y + d, z + h, hex, step, shade * 1.1, scale);
        for (let px = x + step * 0.5; px < x + w; px += step) {
          for (let pz = z + step * 0.5; pz < z + h; pz += step) {
            add(px, y, pz, hex, shade * 0.78, scale);
            add(px, y + d, pz, hex, shade * 0.86, scale);
          }
        }
        for (let py = y + step * 0.5; py < y + d; py += step) {
          for (let pz = z + step * 0.5; pz < z + h; pz += step) {
            add(x, py, pz, hex, shade * 0.82, scale);
            add(x + w, py, pz, hex, shade * 0.9, scale);
          }
        }
      };
      const sphere = (cx, cy, cz, radius, hex, count = 260) => {
        for (let i = 0; i < count; i += 1) {
          const theta = random() * TAU;
          const phi = Math.acos(2 * random() - 1);
          add(
            cx + radius * Math.sin(phi) * Math.cos(theta),
            cy + radius * Math.sin(phi) * Math.sin(theta),
            cz + radius * Math.cos(phi),
            hex,
            0.9,
            0.045
          );
        }
      };

      // Floor and rugs.
      surface(0, room.width, 0, room.depth, 0, "#d4c0a0", 0.14, 1, 0.065);
      surface(1.3, 4.8, 1.25, 4.35, 0.012, "#a98562", 0.16, 1, 0.06);
      surface(8.25, 11.3, 0.65, 3.45, 0.012, "#8fa7a2", 0.16, 1, 0.06);
      surface(8.25, 11.3, 4.45, 7.35, 0.012, "#b58a55", 0.16, 1, 0.06);

      // Two cut-away walls.
      wallY(0.025, 0, room.width, 0, room.wall_height, "#dbe7e3", 0.15, 1, 0.07);
      wallX(0.025, 0, room.depth, 0, room.wall_height, "#c3d4d0", 0.15, 0.92, 0.07);

      // Window, wall art, and entry door.
      wallY(0.055, 4.6, 7.1, 1.15, 2.55, "#bce3de", 0.11, 1, 0.055);
      wallY(0.065, 8.8, 10.8, 1.25, 2.15, "#c59258", 0.12, 1, 0.05);
      wallX(0.06, 6.55, 7.9, 0, 2.2, "#8c6646", 0.12, 1, 0.055);

      // Living room.
      box(1.5, 1.5, 0, 2.4, 1.1, 0.55, "#6c8f96", 0.12, 1, 0.06);
      box(1.5, 1.5, 0, 2.4, 0.22, 1.05, "#6f8c93", 0.12, 1, 0.06);
      box(3.65, 1.5, 0, 0.22, 1.1, 0.72, "#557078", 0.11, 1, 0.055);
      box(3.9, 2.25, 0, 1.15, 0.8, 0.34, "#d0a05e", 0.11, 1, 0.055);
      box(1.2, 4.9, 0, 1.5, 0.9, 0.58, "#c59455", 0.12, 1, 0.055);
      box(0.62, 4.62, 0, 0.45, 0.45, 0.45, "#6f8c93", 0.1, 1, 0.05);
      box(2.5, 5.28, 0, 0.45, 0.45, 0.45, "#6f8c93", 0.1, 1, 0.05);
      sphere(0.95, 4.97, 0.7, 0.34, "#6b9f83", 300);
      box(0.68, 4.7, 0, 0.55, 0.55, 0.58, "#a27150", 0.11, 1, 0.055);

      // Bedroom and study.
      box(8.45, 0.75, 0, 2.55, 1.75, 0.52, "#82a0a9", 0.12, 1, 0.065);
      box(8.45, 0.75, 0, 2.55, 0.2, 0.95, "#6f8a93", 0.12, 1, 0.06);
      box(11.2, 0.72, 0, 0.5, 0.62, 0.48, "#c59455", 0.1, 1, 0.05);
      box(11.22, 4.65, 0, 0.52, 1.85, 1.8, "#6f8a93", 0.13, 1, 0.06);
      box(8.25, 4.75, 0, 2.4, 1.0, 0.72, "#c59455", 0.12, 1, 0.06);
      box(8.6, 4.9, 0, 1.2, 0.65, 0.28, "#557078", 0.1, 1, 0.05);

      // Warm pendant light and a few accent glows.
      sphere(3.4, 2.0, 2.55, 0.32, "#f2c96b", 160);

      this.points = new Float32Array(points);
      this.count = points.length / 7;
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      gl.bufferData(gl.ARRAY_BUFFER, this.points, gl.STATIC_DRAW);
    }

    _buildOverlay(state) {
      if (!state.room) return;
      const key = state.placements.map((item) => item.device_id).join("|") + "|" + state.room.zones.map((zone) => zone.id).join("|");
      if (key === this.overlayKey) return;
      this.overlayKey = key;
      this.overlay.innerHTML = "";
      this.hotspots.clear();
      this.zoneEls = [];

      for (const zone of state.room.zones) {
        const el = document.createElement("span");
        el.className = "splat-zone";
        el.textContent = zone.label;
        this.overlay.appendChild(el);
        this.zoneEls.push({ zone, el });
      }
      this.avatarEl = document.createElement("div");
      this.avatarEl.className = "splat-avatar";
      this.avatarEl.innerHTML = "<span>YOU</span>";
      this.overlay.appendChild(this.avatarEl);

      for (const spot of state.placements) {
        const item = state.devices.find((device) => device.id === spot.device_id);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "splat-hotspot";
        button.textContent = `${spot.tier}  ${item?.name || spot.name || spot.device_id}`;
        button.addEventListener("click", (event) => {
          event.stopPropagation();
          this.onSelect?.(spot.device_id);
        });
        this.overlay.appendChild(button);
        this.hotspots.set(spot.device_id, button);
      }
    }

    _updateOverlay(state, vp) {
      this._buildOverlay(state);
      const place = (el, point) => {
        if (!point || !point.visible) {
          el.style.display = "none";
          return;
        }
        el.style.display = "";
        el.style.left = `${point.x}px`;
        el.style.top = `${point.y}px`;
      };

      for (const { zone, el } of this.zoneEls) {
        place(el, this._project(vp, zone.x, zone.y, 0.25));
      }
      for (const spot of state.placements) {
        const button = this.hotspots.get(spot.device_id);
        const item = state.devices.find((device) => device.id === spot.device_id);
        if (!button) continue;
        button.classList.toggle("is-on", isOn(item));
        button.classList.toggle("is-selected", state.selectedId === spot.device_id);
        place(button, this._project(vp, spot.x, spot.y, spot.z + 0.18));
      }
      place(this.avatarEl, this._project(vp, state.local.x, state.local.y, 0.72));
    }
  }

  window.SouthboundSplatRenderer = SouthboundSplatRenderer;
})();

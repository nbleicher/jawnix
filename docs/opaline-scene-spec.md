# Recreate this Three.js scene: Opaline

Build a single full-screen HTML page containing a real-time Three.js scene called **Opaline**: an iridescent liquid soap-film blob. A real 3D sphere mesh, perlin-displaced in object space so the lumps and the film pattern ride the surface as it tumbles smoothly through a full 360° on two axes. Shaded as a thin oil film: a cycling amber → magenta → cyan → deep-blue interference ramp, dark slick patches, a hue-shifted fresnel rim and a white-cyan glint. The environment is tuned to the film palette — an ember/indigo duotone background with warm and electric-blue corner flames, a two-tone mote drift, and a soft aura behind the blob.

## What it looks like

- A rounded, softly lumpy sphere floating dead-center, tumbling slowly and continuously on two axes with a gentle precession wobble.
- Its surface is an oil-film iridescence: bands of amber, magenta, cyan and deep blue stream across it, with dark oil-slick patches drifting through. A world-space key light sweeps a bright/dark terminator across the tumbling pattern — that moving terminator is the main depth cue.
- A hue-shifted fresnel rim glows around the silhouette (pushed toward deep blue underneath) and a tight white-cyan specular glint rides the crests.
- Behind the blob sits a warm-below / cool-above additive aura, and the whole frame is an ember-to-indigo duotone background with faint warm + electric-blue flame licks in the lower corners.
- Faint two-tone motes (warm gold + electric blue) drift through the space, attached to the camera.
- The pointer dents the surface like a finger pressing a bubble; dragging grabs and spins the blob with inertia; moving the mouse gently parallaxes the camera.

## Page & boilerplate

- A full-viewport page. `html, body { margin: 0; padding: 0; height: 100%; background: #000; overflow: hidden; }` and `canvas { display: block; width: 100%; height: 100%; }`. A single `<canvas id="scene">` fills the window. No scrolling — static full-screen.
- Load Three.js **r143** via an importmap (use these exact URLs):

```html
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.143.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.143.0/examples/jsm/"
  }
}
</script>
```

- Everything runs in one `<script type="module">`. Imports:

```js
import * as THREE from 'three'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js'
import { GammaCorrectionShader } from 'three/addons/shaders/GammaCorrectionShader.js'
```

- Renderer: `new THREE.WebGL1Renderer({ canvas, antialias: true })`. Pixel ratio is capped at 2: `renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))`. Set `renderer.shadowMap.enabled = true` and `renderer.shadowMap.type = THREE.VSMShadowMap` (no shadow casters are actually used, but keep the flags).
- Scene: `scene.background = new THREE.Color(0x000000)`, `scene.fog = null` (the background quad paints the real backdrop).
- Camera: `new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 200)`, positioned at `(0, 0, 6)`. Add the camera to the scene. Enable all render layers on it: with `const LAYERS = { NONE: 0, TORUS_SCENE: 1, BLOOM_SCENE: 2, ENTIRE_SCENE: 3 }`, call `camera.layers.enable(1)`, `camera.layers.enable(2)`, `camera.layers.enable(3)`.
- Helpers to include:

```js
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v))
function hexToVec3(hex) {
  const n = parseInt(hex.slice(1), 16)
  return new THREE.Vector3(((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255)
}
```

- Post-processing: a single `EffectComposer` on the renderer with three passes in order — `RenderPass(scene, camera)`, then `UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0, 0, 1)` (strength 0, radius 0, threshold 1 — bloom is effectively off but wired in), then `ShaderPass(GammaCorrectionShader)`. There is exactly one scene render → one bloom → gamma; the background quad is folded into the same render so nothing flickers.
- On resize: `renderer.setPixelRatio(dpr)` with `dpr = Math.min(window.devicePixelRatio, 2)`, `renderer.setSize(w, h, false)`, update `camera.aspect` + `camera.updateProjectionMatrix()`, `composer.setPixelRatio(dpr)`, `composer.setSize(w, h)`, and update the mote material's `uRes` uniform to `(w * dpr, h * dpr)`. Call resize once at startup.
- Render loop via `requestAnimationFrame`: ease the smoothed mouse toward its target each frame (`mouse.x += (mouseTarget.x - mouse.x) * 0.06`, same for y), run the blob's per-frame update with that smoothed mouse, then `composer.render()`.

## Fixed parameters (bake these in)

Hardcode these constants where used — do not expose them.

**Film / blob colors (hex → use `hexToVec3`):**
- colorA (amber): `#ffaa2e`
- colorB (magenta): `#ef4fd8`
- colorC (cyan): `#3fd2ff`
- colorD (deep blue): `#2130e8`

**Blob shading:**
- brightness: `1.35`
- fresnelPow: `1.01`
- rimGain: `0.73`
- filmScale: `0.24`
- filmSpeed: `0.64` (film-pattern phase rate)
- oilAmount: `0.43`
- opacity: `1`

**Displacement / motion:**
- displaceFreq: `0.71`
- displaceStr: `0.35`
- flowSpeed: `0.45` (noise-flow phase rate)
- spin: `0.1` (base yaw rate)
- tumble: `0.3` (precession wobble amount)
- dragSpin: `1` (drag-to-spin gain)

**Pointer dent:**
- pointerRadius: `1.6`
- pointerStrength: `0.29`

**Aura / halo:**
- haloStrength: `0.76`

**Bloom (single composer):**
- bloomStr: `0`, bloomRadius: `0`, bloomThresh: `1`

**Camera:**
- parallax: `1.75`

**Background quad:**
- bgColor: `#12060d`, bgColor2: `#03040c`
- flameColor: `#ff8a2a`, flameColor2: `#2f6bff`
- flameAmt: `0.16`

**Motes:**
- atmoColor (warm gold): `#ffb45e`, atmoColor2 (electric blue): `#4fc8ff`
- atmoCount: `260`, atmoSize: `20`, atmoSpeed: `1`

## Geometry

- The blob is a `THREE.SphereBufferGeometry(1.6, 200, 200)` — radius 1.6, 200 width segments, 200 height segments. All lumping and denting happens in the vertex shader; the base geometry is a smooth high-res sphere.
- The mesh is added to a `THREE.Group` (the group is what rotates), and the group is added to the scene. Set `mesh.frustumCulled = false`, and enable layers 3 and 2 on the mesh (`mesh.layers.enable(3)`, `mesh.layers.enable(2)`).
- Background quad: `new THREE.Mesh(new THREE.PlaneBufferGeometry(2, 2), bgMat)` drawn in clip space (see its vertex shader). `bgQuad.frustumCulled = false`, `bgQuad.renderOrder = -10`, `bgQuad.layers.enable(3)`, added to the scene.
- Aura billboard: `new THREE.Mesh(new THREE.PlaneBufferGeometry(11, 11), haloMat)` positioned at `(0, 0, -2.2)`, `frustumCulled = false`, `layers.enable(3)`, added directly to the scene (not the group). Each frame copy the camera's quaternion onto it so it always faces the camera.

## Material & shaders

The blob uses a `THREE.ShaderMaterial` with `transparent: true, depthTest: true, depthWrite: true`. Its uniforms (initialize with the fixed values above):

```js
uniforms = {
  iAlpha:         { value: 0 },
  uFlow:          { value: 0 },
  uFilmT:         { value: 0 },
  uDispFreq:      { value: 0.71 },
  uDispStr:       { value: 0.35 },
  uCursor:        { value: new THREE.Vector3() },
  uRepelRadius:   { value: 1.6 },
  uRepelStrength: { value: 0.29 },
  uActivity:      { value: 0 },
  uColA:          { value: hexToVec3('#ffaa2e') },
  uColB:          { value: hexToVec3('#ef4fd8') },
  uColC:          { value: hexToVec3('#3fd2ff') },
  uColD:          { value: hexToVec3('#2130e8') },
  uBrightness:    { value: 1.35 },
  uFresnelPow:    { value: 1.01 },
  uRimGain:       { value: 0.73 },
  uFilmScale:     { value: 0.24 },
  uOil:           { value: 0.43 }
}
```

The vertex shader displaces the sphere in **object space** (so the lumps ride the rotation), applies a **world-space** cursor dent, and rebuilds the normal numerically from two tangent probes of the same deform. It includes a 4D Perlin noise helper, injected verbatim via a JS template string `${PERLIN4D}` before `float surf(...)`.

The **PERLIN4D** helper string (copy verbatim):

```glsl
vec4 permute(vec4 x){return mod(((x*34.0)+1.0)*x, 289.0);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159 - 0.85373472095314 * r;}
vec4 fade(vec4 t) {return t*t*t*(t*(t*6.0-15.0)+10.0);}
float perlin4d(vec4 P){
  vec4 Pi0 = floor(P); vec4 Pi1 = Pi0 + 1.0;
  Pi0 = mod(Pi0, 289.0); Pi1 = mod(Pi1, 289.0);
  vec4 Pf0 = fract(P); vec4 Pf1 = Pf0 - 1.0;
  vec4 ix = vec4(Pi0.x, Pi1.x, Pi0.x, Pi1.x);
  vec4 iy = vec4(Pi0.yy, Pi1.yy);
  vec4 iz0 = vec4(Pi0.zzzz); vec4 iz1 = vec4(Pi1.zzzz);
  vec4 iw0 = vec4(Pi0.wwww); vec4 iw1 = vec4(Pi1.wwww);
  vec4 ixy = permute(permute(ix) + iy);
  vec4 ixy0 = permute(ixy + iz0); vec4 ixy1 = permute(ixy + iz1);
  vec4 ixy00 = permute(ixy0 + iw0); vec4 ixy01 = permute(ixy0 + iw1);
  vec4 ixy10 = permute(ixy1 + iw0); vec4 ixy11 = permute(ixy1 + iw1);
  vec4 gx00 = ixy00 / 7.0; vec4 gy00 = floor(gx00) / 7.0; vec4 gz00 = floor(gy00) / 6.0;
  gx00 = fract(gx00) - 0.5; gy00 = fract(gy00) - 0.5; gz00 = fract(gz00) - 0.5;
  vec4 gw00 = vec4(0.75) - abs(gx00) - abs(gy00) - abs(gz00);
  vec4 sw00 = step(gw00, vec4(0.0));
  gx00 -= sw00 * (step(0.0, gx00) - 0.5); gy00 -= sw00 * (step(0.0, gy00) - 0.5);
  vec4 gx01 = ixy01 / 7.0; vec4 gy01 = floor(gx01) / 7.0; vec4 gz01 = floor(gy01) / 6.0;
  gx01 = fract(gx01) - 0.5; gy01 = fract(gy01) - 0.5; gz01 = fract(gz01) - 0.5;
  vec4 gw01 = vec4(0.75) - abs(gx01) - abs(gy01) - abs(gz01);
  vec4 sw01 = step(gw01, vec4(0.0));
  gx01 -= sw01 * (step(0.0, gx01) - 0.5); gy01 -= sw01 * (step(0.0, gy01) - 0.5);
  vec4 gx10 = ixy10 / 7.0; vec4 gy10 = floor(gx10) / 7.0; vec4 gz10 = floor(gy10) / 6.0;
  gx10 = fract(gx10) - 0.5; gy10 = fract(gy10) - 0.5; gz10 = fract(gz10) - 0.5;
  vec4 gw10 = vec4(0.75) - abs(gx10) - abs(gy10) - abs(gz10);
  vec4 sw10 = step(gw10, vec4(0.0));
  gx10 -= sw10 * (step(0.0, gx10) - 0.5); gy10 -= sw10 * (step(0.0, gy10) - 0.5);
  vec4 gx11 = ixy11 / 7.0; vec4 gy11 = floor(gx11) / 7.0; vec4 gz11 = floor(gy11) / 6.0;
  gx11 = fract(gx11) - 0.5; gy11 = fract(gy11) - 0.5; gz11 = fract(gz11) - 0.5;
  vec4 gw11 = vec4(0.75) - abs(gx11) - abs(gy11) - abs(gz11);
  vec4 sw11 = step(gw11, vec4(0.0));
  gx11 -= sw11 * (step(0.0, gx11) - 0.5); gy11 -= sw11 * (step(0.0, gy11) - 0.5);
  vec4 g0000 = vec4(gx00.x,gy00.x,gz00.x,gw00.x); vec4 g1000 = vec4(gx00.y,gy00.y,gz00.y,gw00.y);
  vec4 g0100 = vec4(gx00.z,gy00.z,gz00.z,gw00.z); vec4 g1100 = vec4(gx00.w,gy00.w,gz00.w,gw00.w);
  vec4 g0010 = vec4(gx10.x,gy10.x,gz10.x,gw10.x); vec4 g1010 = vec4(gx10.y,gy10.y,gz10.y,gw10.y);
  vec4 g0110 = vec4(gx10.z,gy10.z,gz10.z,gw10.z); vec4 g1110 = vec4(gx10.w,gy10.w,gz10.w,gw10.w);
  vec4 g0001 = vec4(gx01.x,gy01.x,gz01.x,gw01.x); vec4 g1001 = vec4(gx01.y,gy01.y,gz01.y,gw01.y);
  vec4 g0101 = vec4(gx01.z,gy01.z,gz01.z,gw01.z); vec4 g1101 = vec4(gx01.w,gy01.w,gz01.w,gw01.w);
  vec4 g0011 = vec4(gx11.x,gy11.x,gz11.x,gw11.x); vec4 g1011 = vec4(gx11.y,gy11.y,gz11.y,gw11.y);
  vec4 g0111 = vec4(gx11.z,gy11.z,gz11.z,gw11.z); vec4 g1111 = vec4(gx11.w,gy11.w,gz11.w,gw11.w);
  vec4 norm00 = taylorInvSqrt(vec4(dot(g0000, g0000), dot(g0100, g0100), dot(g1000, g1000), dot(g1100, g1100)));
  g0000 *= norm00.x; g0100 *= norm00.y; g1000 *= norm00.z; g1100 *= norm00.w;
  vec4 norm01 = taylorInvSqrt(vec4(dot(g0001, g0001), dot(g0101, g0101), dot(g1001, g1001), dot(g1101, g1101)));
  g0001 *= norm01.x; g0101 *= norm01.y; g1001 *= norm01.z; g1101 *= norm01.w;
  vec4 norm10 = taylorInvSqrt(vec4(dot(g0010, g0010), dot(g0110, g0110), dot(g1010, g1010), dot(g1110, g1110)));
  g0010 *= norm10.x; g0110 *= norm10.y; g1010 *= norm10.z; g1110 *= norm10.w;
  vec4 norm11 = taylorInvSqrt(vec4(dot(g0011, g0011), dot(g0111, g0111), dot(g1011, g1011), dot(g1111, g1111)));
  g0011 *= norm11.x; g0111 *= norm11.y; g1011 *= norm11.z; g1111 *= norm11.w;
  float n0000 = dot(g0000, Pf0);
  float n1000 = dot(g1000, vec4(Pf1.x, Pf0.yzw));
  float n0100 = dot(g0100, vec4(Pf0.x, Pf1.y, Pf0.zw));
  float n1100 = dot(g1100, vec4(Pf1.xy, Pf0.zw));
  float n0010 = dot(g0010, vec4(Pf0.xy, Pf1.z, Pf0.w));
  float n1010 = dot(g1010, vec4(Pf1.x, Pf0.y, Pf1.z, Pf0.w));
  float n0110 = dot(g0110, vec4(Pf0.x, Pf1.yz, Pf0.w));
  float n1110 = dot(g1110, vec4(Pf1.xyz, Pf0.w));
  float n0001 = dot(g0001, vec4(Pf0.xyz, Pf1.w));
  float n1001 = dot(g1001, vec4(Pf1.x, Pf0.yz, Pf1.w));
  float n0101 = dot(g0101, vec4(Pf0.x, Pf1.y, Pf0.z, Pf1.w));
  float n1101 = dot(g1101, vec4(Pf1.xy, Pf0.z, Pf1.w));
  float n0011 = dot(g0011, vec4(Pf0.xy, Pf1.zw));
  float n1011 = dot(g1011, vec4(Pf1.x, Pf0.y, Pf1.zw));
  float n0111 = dot(g0111, vec4(Pf0.x, Pf1.yzw));
  float n1111 = dot(g1111, Pf1);
  vec4 fade_xyzw = fade(Pf0);
  vec4 n_0w = mix(vec4(n0000, n1000, n0100, n1100), vec4(n0001, n1001, n0101, n1101), fade_xyzw.w);
  vec4 n_1w = mix(vec4(n0010, n1010, n0110, n1110), vec4(n0011, n1011, n0111, n1111), fade_xyzw.w);
  vec4 n_zw = mix(n_0w, n_1w, fade_xyzw.z);
  vec2 n_yzw = mix(n_zw.xy, n_zw.zw, fade_xyzw.y);
  float n_xyzw = mix(n_yzw.x, n_yzw.y, fade_xyzw.x);
  return 2.2 * n_xyzw;
}
```

**Blob vertex shader** (copy verbatim; `${PERLIN4D}` is where the helper above is injected):

```glsl
uniform float uFlow; uniform float uDispFreq; uniform float uDispStr;
uniform vec3 uCursor; uniform float uRepelRadius; uniform float uRepelStrength; uniform float uActivity;
varying vec3 vNrm; varying vec3 vWorld; varying vec3 vObj; varying float vNoise;
${PERLIN4D}
float surf(vec3 p, float t) {
  float n = perlin4d(vec4(p * uDispFreq, t));
  n += 0.4 * perlin4d(vec4(p * uDispFreq * 2.15 + 4.7, t * 1.55));
  return n;
}
// displace in OBJECT space (lumps ride the rotation), dent in WORLD space (stays under the cursor)
vec3 deform(vec3 p, float t, out float n, out vec3 obj) {
  vec3 dir = normalize(p);
  n = surf(p, t);
  float amp = uDispStr;
  obj = p + dir * n * amp;
  vec3 q = (modelMatrix * vec4(obj, 1.0)).xyz;
  vec3 toP = q - uCursor;
  float fall = smoothstep(uRepelRadius, 0.0, length(toP));
  q += normalize(toP + vec3(0.0001, 0.0, 0.0)) * fall * uRepelStrength * uActivity;
  return q;
}
void main() {
  float t = uFlow;
  float n0; float nd; vec3 obj0; vec3 objd;
  vec3 wp = deform(position, t, n0, obj0);
  // numeric normal: probe the same deform along the sphere tangent frame
  vec3 sn = normalize(position);
  vec3 up = abs(sn.y) > 0.99 ? vec3(1.0, 0.0, 0.0) : vec3(0.0, 1.0, 0.0);
  vec3 tang = normalize(cross(up, sn));
  vec3 bitan = normalize(cross(sn, tang));
  float e = 0.05;
  vec3 pT = deform(position + tang * e, t, nd, objd);
  vec3 pB = deform(position + bitan * e, t, nd, objd);
  vec3 nrm = normalize(cross(pT - wp, pB - wp));
  vec3 outward = normalize((modelMatrix * vec4(sn, 0.0)).xyz);
  vNrm = nrm * sign(dot(nrm, outward));
  vWorld = wp;
  vObj = obj0;
  vNoise = n0;
  gl_Position = projectionMatrix * viewMatrix * vec4(wp, 1.0);
}
```

**Blob fragment shader** (copy verbatim):

```glsl
uniform float iAlpha; uniform float uFilmT;
uniform vec3 uColA; uniform vec3 uColB; uniform vec3 uColC; uniform vec3 uColD;
uniform float uBrightness; uniform float uFresnelPow; uniform float uRimGain;
uniform float uFilmScale; uniform float uOil;
varying vec3 vNrm; varying vec3 vWorld; varying vec3 vObj; varying float vNoise;
// thin-film interference ramp: amber, magenta, cyan, deep blue, back to amber
vec3 filmColor(float t) {
  t = fract(t);
  vec3 c = mix(uColA, uColB, smoothstep(0.05, 0.35, t));
  c = mix(c, uColC, smoothstep(0.35, 0.62, t));
  c = mix(c, uColD, smoothstep(0.62, 0.82, t));
  return mix(c, uColA, smoothstep(0.82, 1.0, t));
}
// cheap trig domain warp for the streaming liquid streaks
vec3 swirl(vec3 p, float t) {
  float c1 = 0.9, a = 1.7;
  p.x += c1 * sin(t + a * p.y); p.y += c1 * cos(t + a * p.x);
  p.y += c1 * sin(t + a * p.z); p.z += c1 * cos(t + a * p.y);
  return p;
}
void main() {
  vec3 N = normalize(vNrm);
  vec3 V = normalize(cameraPosition - vWorld);
  float ndv = clamp(dot(N, V), 0.0, 1.0);
  float fres = pow(1.0 - ndv, uFresnelPow);
  float t = uFilmT;
  // film pattern sampled in OBJECT space so it rides the rotation
  vec3 sp = swirl(vObj * uFilmScale, t);
  float streak = 0.5 + 0.5 * sin(sp.x + sp.y * 1.7 + sp.z * 0.9);
  float streak2 = 0.5 + 0.5 * sin(sp.y * 1.3 - sp.z * 1.9 + 2.0);
  // film thickness parameter: liquid streaks + surface crests + view angle
  float phase = streak * 0.85 + vNoise * 0.55 + (1.0 - ndv) * 0.55 + t * 0.05;
  vec3 base = filmColor(phase);
  // world-space key light with deep contrast — the terminator sweeping
  // across the rotating pattern is the main 3D depth cue
  vec3 L = normalize(vec3(-0.5, 0.75, 0.6));
  float diff = max(dot(N, L), 0.0);
  float shade = 0.16 + 0.84 * pow(diff, 1.25);
  // crests catch light, valleys sink into shadow
  float crest = 0.82 + 0.34 * clamp(vNoise, -1.0, 1.0);
  vec3 col = base * shade * crest;
  // complementary film glow filling the shadow side so it stays iridescent, not dead
  float back = max(dot(N, -L), 0.0);
  col += filmColor(phase + 0.5) * back * back * 0.22;
  // dark oil-slick patches drifting through the film
  float patch = smoothstep(0.55, 0.9, streak2) * uOil;
  col *= 1.0 - patch * 0.75;
  // hue-shifted fresnel rim, pushed toward deep blue on the underside
  float rimPhase = phase + 0.35 + 0.22 * (0.5 - 0.5 * N.y);
  col += filmColor(rimPhase) * fres * uRimGain;
  // white-cyan glint
  vec3 H = normalize(L + V);
  float spec = pow(max(dot(N, H), 0.0), 90.0) * 1.2;
  col += vec3(0.95, 1.0, 1.05) * spec;
  col *= uBrightness;
  gl_FragColor = vec4(col, iAlpha);
}
```

## Atmosphere / extra layers

**Background quad** — a clip-space full-screen quad drawn first (renderOrder -10), painting the ember/indigo duotone with warm + electric-blue corner flames. `THREE.ShaderMaterial` with `depthTest: false, depthWrite: false`. Uniforms: `iTime` (0, advanced each frame with elapsed seconds), `uBg = hexToVec3('#12060d')`, `uBg2 = hexToVec3('#03040c')`, `uFlameA = hexToVec3('#ff8a2a')`, `uFlameB = hexToVec3('#2f6bff')`, `uFlameAmt = 0.16`.

Background vertex shader (verbatim):

```glsl
varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 1.0, 1.0); }
```

Background fragment shader (verbatim):

```glsl
uniform float iTime; uniform vec3 uBg; uniform vec3 uBg2; uniform vec3 uFlameA; uniform vec3 uFlameB; uniform float uFlameAmt;
varying vec2 vUv;
vec3 warp3d(vec3 pos, float t){ float curv=.8,a=1.9,b=0.7; pos*=2.;
  pos.x+=curv*sin(t+a*pos.y)+t*b; pos.y+=curv*cos(t+a*pos.x);
  pos.y+=curv*sin(t+a*pos.z)+t*b; pos.z+=curv*cos(t+a*pos.y);
  pos.z+=curv*sin(t+a*pos.x)+t*b; pos.x+=curv*cos(t+a*pos.z);
  return 0.5+0.5*cos(pos.xyz+vec3(1,2,4)); }
void main(){
  vec2 uv = 2.*vUv - 1.;
  vec3 w = pow(warp3d(vec3(uv.x, sin(uv.y), uv.y), iTime*1.5), vec3(1.5));
  vec3 flame = 1.5*uFlameA*w.x; flame*=w.y; flame += uFlameB*w.z;
  flame *= smoothstep(0.25, 1., abs(uv.y));
  float md = smoothstep(-0.7, 1., -uv.y*uv.x); flame *= md*md;
  vec3 bg = mix(uBg, uBg2, smoothstep(0.15, 1.25, length(uv)));
  gl_FragColor = vec4(bg + flame*uFlameAmt, 1.);
}
```

**Aura billboard** — the 11×11 plane behind the blob at z = -2.2. `THREE.ShaderMaterial` with `transparent: true, depthTest: true, depthWrite: false, blending: THREE.AdditiveBlending`. Uniforms: `iAlpha` (0, driven by the appear ramp), `uWarm = hexToVec3('#ffaa2e')` (colorA), `uCool = hexToVec3('#3fd2ff')` (colorC), `uStrength = 0.76`. Copy the camera quaternion onto it each frame so it stays a facing billboard.

Aura vertex shader (verbatim):

```glsl
varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
```

Aura fragment shader (verbatim):

```glsl
uniform float iAlpha; uniform vec3 uWarm; uniform vec3 uCool; uniform float uStrength;
varying vec2 vUv;
void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float d = length(p);
  float a = pow(max(0.0, 1.0 - d), 3.5) * uStrength * iAlpha;
  vec3 col = mix(uWarm, uCool, smoothstep(-0.6, 0.9, p.y));
  gl_FragColor = vec4(col, a);
}
```

**Motes** — a camera-attached `THREE.Points` cloud of two-tone drifting particles. Count 260. Build three attributes: `position` (each component `2*Math.random()-1`), `size` (`20 * (0.4 + Math.random())`), `seed` (`Math.random()`). Material is a `THREE.ShaderMaterial` with `transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, depthTest: false`. Uniforms: `uTime` (0), `uColor = hexToVec3('#ffb45e')`, `uColor2 = hexToVec3('#4fc8ff')`, `uRes = new THREE.Vector2(window.innerWidth * window.devicePixelRatio, window.innerHeight * window.devicePixelRatio)`. Set `points.frustumCulled = false`, enable layer 3, add to scene. In an `onBeforeRender` callback (or per-frame): set `uTime = t * atmoSpeed(=1) * 8.0` with `t` in seconds, copy the camera position onto the points object, and also advance the background quad's `iTime` to `t`.

Mote vertex shader (verbatim):

```glsl
attribute float size; attribute float seed; uniform float uTime; uniform vec2 uRes;
varying float vA; varying float vSeed;
vec3 warp(vec3 p, float t){ float c=0.9,a=1.9,b=0.02,s=0.05; p*=2.;
  p.x+=c*sin(s*t+a*p.y)+t*b; p.y+=c*cos(s*t+a*p.x); p.y+=c*sin(s*t+a*p.z)+t*b;
  p.z+=c*cos(s*t+a*p.y); p.z+=c*sin(s*t+a*p.x)+t*b; p.x+=c*cos(s*t+a*p.z);
  return cos(p+vec3(1,2,4)); }
void main(){
  vec3 v = position*4.0 + warp(position, uTime)*1.2;
  vec4 mv = modelViewMatrix * vec4(v, 1.0);
  float r = length(v); float farF = 1.0 - smoothstep(5.0, 6.5, r); float nearF = smoothstep(0.0, 0.5, -mv.z);
  vA = farF * nearF; vSeed = seed;
  gl_PointSize = size * uRes.y / 900.0 / -mv.z; gl_PointSize = max(gl_PointSize, 1.0);
  gl_Position = projectionMatrix * mv;
}
```

Mote fragment shader (verbatim):

```glsl
uniform vec3 uColor; uniform vec3 uColor2; varying float vA; varying float vSeed;
void main(){ vec2 p = gl_PointCoord - 0.5; float l = length(p); if (l > 0.5) discard;
  float tex = smoothstep(0.5, 0.0, l);
  vec3 col = mix(uColor, uColor2, step(0.7, vSeed));
  gl_FragColor = vec4(col * tex, tex * vA * 0.55); }
```

## Animation & interaction

Keep timing/phase bookkeeping on the blob object: `appearStart = performance.now()`, `t0 = performance.now()/1000`, and accumulators `spinPhase, pitchPhase, spinVel, pitchVel, flowPhase, filmPhase` (all starting 0).

Each frame (called with the smoothed `mouse = {x, y}`):

- `t = performance.now()/1000`; `dt = Math.min(0.05, t - t0)`; then `t0 = t`.
- **Camera parallax + breathing drift:** `camera.position.set(m.x * 1.75 + 0.12*Math.sin(t*0.13), m.y * 1.75 + 0.1*Math.cos(t*0.17), 6)` then `camera.lookAt(0, 0, 0)`.
- Update the world cursor (below), then copy the camera quaternion onto the aura billboard.
- **Grab & spin with inertia:** `spinVel += POINTER.dragX * 1 * 0.35`; `pitchVel += POINTER.dragY * 1 * 0.3`; then reset `POINTER.dragX = POINTER.dragY = 0`; then damp both `spinVel *= 0.94`, `pitchVel *= 0.94`.
- **Phase accumulation** (accumulate phases, not speed-scaled time, so speed stays smooth): `spinPhase += dt*0.1 + spinVel`; `pitchPhase += dt*0.1*0.37 + pitchVel`; `flowPhase += dt*0.45`; `filmPhase += dt*0.64`.
- **Group rotation:** `group.rotation.y = spinPhase`; `group.rotation.x = pitchPhase + 0.3*Math.sin(t*0.21)`; `group.rotation.z = 0.3*0.6*Math.cos(t*0.16)` — a full free 360° tumble on two axes plus a slow precession wobble.
- Feed uniforms: `uFlow = flowPhase`, `uFilmT = filmPhase`, `uCursor.copy(POINTER.world)`, `uActivity = POINTER.activity`.
- **Appear ramp:** `elapsed = performance.now() - appearStart`; `appear = clamp((elapsed - 300)/1400, 0, 1)`; set blob `iAlpha = appear * 1` (opacity) and aura `iAlpha = appear`.

**Pointer plumbing** (window listeners, all `{ passive: true }`):

- `mouseTarget` / `mouse` each `{x, y}` in NDC-ish [-1, 1]. Smooth `mouse` toward `mouseTarget` at 0.06/frame in the render loop.
- A `POINTER` object: `{ world: new THREE.Vector3(), activity: 0, active: false, lastMove: performance.now(), pressed: false, dragX: 0, dragY: 0, lastPX: 0, lastPY: 0 }`.
- `pointermove`: set `mouseTarget.x = clientX/innerWidth*2 - 1`, `mouseTarget.y = -(clientY/innerHeight*2 - 1)`, `active = true`, `lastMove = now`; and if `pressed`, add `(clientX - lastPX)/innerWidth` to `dragX` and `(clientY - lastPY)/innerHeight` to `dragY`. Always update `lastPX/lastPY`.
- `pointerdown`: same target/active update, set `pressed = true`, seed `lastPX/lastPY`.
- `pointerup` / `pointercancel`: `pressed = false`. `document` `mouseleave`: `active = false`, `pressed = false`.
- `updatePointerWorld()`: aim a ray from the camera through the smoothed mouse NDC (`_ndc.set(mouse.x, mouse.y, 0.5).unproject(camera)`, direction = that minus camera position, normalized) and intersect the plane z = 0 (`tt = -camera.position.z / dir.z`, guard `Math.abs(dir.z) > 1e-4` and `tt > 0 && Number.isFinite(tt)`); target is the hit point, else `(0,0,0)` when inactive. Lerp `POINTER.world` toward the target at 0.12. Then update activity: `idle = (now - lastMove)/1000`; `activity += (((active && idle < 3) ? 1 : 0) - activity) * 0.06`. This `activity` gates the cursor-dent strength so the dent fades in/out with pointer presence.

## Assets

None.
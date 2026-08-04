import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { GammaCorrectionShader } from "three/examples/jsm/shaders/GammaCorrectionShader.js";

export interface OpalineSceneOptions {
  host: HTMLElement;
  reducedMotion: boolean;
  onFailure: () => void;
  onReady: () => void;
}

export interface OpalineSceneController {
  dispose: () => void;
  setPaused: (paused: boolean) => void;
}

const LAYERS = {
  NONE: 0,
  TORUS_SCENE: 1,
  BLOOM_SCENE: 2,
  ENTIRE_SCENE: 3,
} as const;

const clamp = (value: number, low: number, high: number) =>
  Math.max(low, Math.min(high, value));

function hexToVec3(hex: string) {
  const value = parseInt(hex.slice(1), 16);
  return new THREE.Vector3(
    ((value >> 16) & 255) / 255,
    ((value >> 8) & 255) / 255,
    (value & 255) / 255,
  );
}

const PERLIN4D = /* glsl */ `
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
}`;

const BLOB_VERTEX_SHADER = /* glsl */ `
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
}`;

const BLOB_FRAGMENT_SHADER = /* glsl */ `
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
}`;

const BACKGROUND_VERTEX_SHADER = /* glsl */ `
varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 1.0, 1.0); }
`;

const BACKGROUND_FRAGMENT_SHADER = /* glsl */ `
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
}`;

const HALO_VERTEX_SHADER = /* glsl */ `
varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
`;

const HALO_FRAGMENT_SHADER = /* glsl */ `
uniform float iAlpha; uniform vec3 uWarm; uniform vec3 uCool; uniform float uStrength;
varying vec2 vUv;
void main() {
  vec2 p = vUv * 2.0 - 1.0;
  float d = length(p);
  float a = pow(max(0.0, 1.0 - d), 3.5) * uStrength * iAlpha;
  vec3 col = mix(uWarm, uCool, smoothstep(-0.6, 0.9, p.y));
  gl_FragColor = vec4(col, a);
}`;

const MOTE_VERTEX_SHADER = /* glsl */ `
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
}`;

const MOTE_FRAGMENT_SHADER = /* glsl */ `
uniform vec3 uColor; uniform vec3 uColor2; varying float vA; varying float vSeed;
void main(){ vec2 p = gl_PointCoord - 0.5; float l = length(p); if (l > 0.5) discard;
  float tex = smoothstep(0.5, 0.0, l);
  vec3 col = mix(uColor, uColor2, step(0.7, vSeed));
  gl_FragColor = vec4(col * tex, tex * vA * 0.55); }
`;

export function createOpalineScene(
  canvas: HTMLCanvasElement,
  { host, reducedMotion, onFailure, onReady }: OpalineSceneOptions,
): OpalineSceneController {
  let disposed = false;
  let animationFrame = 0;
  let failureReported = false;
  let paused = false;

  const reportFailure = () => {
    if (disposed || failureReported) return;
    failureReported = true;
    if (animationFrame) cancelAnimationFrame(animationFrame);
    onFailure();
  };

  const onContextCreationError = () => reportFailure();
  const onContextLost = (event: Event) => {
    event.preventDefault();
    reportFailure();
  };
  canvas.addEventListener("webglcontextcreationerror", onContextCreationError);
  canvas.addEventListener("webglcontextlost", onContextLost);

  let renderer: THREE.WebGL1Renderer;
  try {
    renderer = new THREE.WebGL1Renderer({ canvas, antialias: true });
  } catch (error) {
    canvas.removeEventListener("webglcontextcreationerror", onContextCreationError);
    canvas.removeEventListener("webglcontextlost", onContextLost);
    throw error;
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.VSMShadowMap;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  scene.fog = null;

  let hostRect = host.getBoundingClientRect();

  const camera = new THREE.PerspectiveCamera(
    45,
    Math.max(1, hostRect.width) / Math.max(1, hostRect.height),
    0.1,
    200,
  );
  camera.position.set(0, 0, 6);
  scene.add(camera);
  camera.layers.enable(LAYERS.TORUS_SCENE);
  camera.layers.enable(LAYERS.BLOOM_SCENE);
  camera.layers.enable(LAYERS.ENTIRE_SCENE);

  const blobUniforms = {
    iAlpha: { value: 0 },
    uFlow: { value: 0 },
    uFilmT: { value: 0 },
    uDispFreq: { value: 0.71 },
    uDispStr: { value: 0.35 },
    uCursor: { value: new THREE.Vector3() },
    uRepelRadius: { value: 1.6 },
    uRepelStrength: { value: 0.29 },
    uActivity: { value: 0 },
    uColA: { value: hexToVec3("#ffaa2e") },
    uColB: { value: hexToVec3("#ef4fd8") },
    uColC: { value: hexToVec3("#3fd2ff") },
    uColD: { value: hexToVec3("#2130e8") },
    uBrightness: { value: 1.35 },
    uFresnelPow: { value: 1.01 },
    uRimGain: { value: 0.73 },
    uFilmScale: { value: 0.24 },
    uOil: { value: 0.43 },
  };
  const blobGeometry = new THREE.SphereBufferGeometry(1.6, 200, 200);
  const blobMaterial = new THREE.ShaderMaterial({
    uniforms: blobUniforms,
    vertexShader: BLOB_VERTEX_SHADER,
    fragmentShader: BLOB_FRAGMENT_SHADER,
    transparent: true,
    depthTest: true,
    depthWrite: true,
  });
  const blob = new THREE.Mesh(blobGeometry, blobMaterial);
  blob.frustumCulled = false;
  blob.layers.enable(LAYERS.ENTIRE_SCENE);
  blob.layers.enable(LAYERS.BLOOM_SCENE);
  const group = new THREE.Group();
  group.add(blob);
  scene.add(group);

  const backgroundUniforms = {
    iTime: { value: 0 },
    uBg: { value: hexToVec3("#12060d") },
    uBg2: { value: hexToVec3("#03040c") },
    uFlameA: { value: hexToVec3("#ff8a2a") },
    uFlameB: { value: hexToVec3("#2f6bff") },
    uFlameAmt: { value: 0.16 },
  };
  const backgroundGeometry = new THREE.PlaneBufferGeometry(2, 2);
  const backgroundMaterial = new THREE.ShaderMaterial({
    uniforms: backgroundUniforms,
    vertexShader: BACKGROUND_VERTEX_SHADER,
    fragmentShader: BACKGROUND_FRAGMENT_SHADER,
    depthTest: false,
    depthWrite: false,
  });
  const background = new THREE.Mesh(backgroundGeometry, backgroundMaterial);
  background.frustumCulled = false;
  background.renderOrder = -10;
  background.layers.enable(LAYERS.ENTIRE_SCENE);
  scene.add(background);

  const haloUniforms = {
    iAlpha: { value: 0 },
    uWarm: { value: hexToVec3("#ffaa2e") },
    uCool: { value: hexToVec3("#3fd2ff") },
    uStrength: { value: 0.76 },
  };
  const haloGeometry = new THREE.PlaneBufferGeometry(11, 11);
  const haloMaterial = new THREE.ShaderMaterial({
    uniforms: haloUniforms,
    vertexShader: HALO_VERTEX_SHADER,
    fragmentShader: HALO_FRAGMENT_SHADER,
    transparent: true,
    depthTest: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const halo = new THREE.Mesh(haloGeometry, haloMaterial);
  halo.position.set(0, 0, -2.2);
  halo.frustumCulled = false;
  halo.layers.enable(LAYERS.ENTIRE_SCENE);
  scene.add(halo);

  const motePositions = new Float32Array(260 * 3);
  const moteSizes = new Float32Array(260);
  const moteSeeds = new Float32Array(260);
  for (let index = 0; index < 260; index += 1) {
    motePositions[index * 3] = 2 * Math.random() - 1;
    motePositions[index * 3 + 1] = 2 * Math.random() - 1;
    motePositions[index * 3 + 2] = 2 * Math.random() - 1;
    moteSizes[index] = 20 * (0.4 + Math.random());
    moteSeeds[index] = Math.random();
  }
  const moteGeometry = new THREE.BufferGeometry();
  moteGeometry.setAttribute(
    "position",
    new THREE.BufferAttribute(motePositions, 3),
  );
  moteGeometry.setAttribute("size", new THREE.BufferAttribute(moteSizes, 1));
  moteGeometry.setAttribute("seed", new THREE.BufferAttribute(moteSeeds, 1));
  const moteUniforms = {
    uTime: { value: 0 },
    uColor: { value: hexToVec3("#ffb45e") },
    uColor2: { value: hexToVec3("#4fc8ff") },
    uRes: {
      value: new THREE.Vector2(
        hostRect.width * window.devicePixelRatio,
        hostRect.height * window.devicePixelRatio,
      ),
    },
  };
  const moteMaterial = new THREE.ShaderMaterial({
    uniforms: moteUniforms,
    vertexShader: MOTE_VERTEX_SHADER,
    fragmentShader: MOTE_FRAGMENT_SHADER,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    depthTest: false,
  });
  const motes = new THREE.Points(moteGeometry, moteMaterial);
  motes.frustumCulled = false;
  motes.layers.enable(LAYERS.ENTIRE_SCENE);
  scene.add(motes);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  composer.addPass(
    new UnrealBloomPass(
      new THREE.Vector2(hostRect.width, hostRect.height),
      0,
      0,
      1,
    ),
  );
  composer.addPass(new ShaderPass(GammaCorrectionShader));

  let renderStaticFrame: (() => void) | undefined;
  const resize = () => {
    hostRect = host.getBoundingClientRect();
    const width = Math.max(1, hostRect.width);
    const height = Math.max(1, hostRect.height);
    const dpr = Math.min(window.devicePixelRatio, 2);
    renderer.setPixelRatio(dpr);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    composer.setPixelRatio(dpr);
    composer.setSize(width, height);
    moteUniforms.uRes.value.set(width * dpr, height * dpr);
    renderStaticFrame?.();
  };
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  window.addEventListener("resize", resize, { passive: true });
  resize();

  const mouseTarget = { x: 0, y: 0 };
  const mouse = { x: 0, y: 0 };
  const POINTER = {
    world: new THREE.Vector3(),
    activity: 0,
    active: false,
    lastMove: performance.now(),
    pressed: false,
    dragX: 0,
    dragY: 0,
    lastPX: 0,
    lastPY: 0,
  };

  const updatePointerTarget = (event: PointerEvent) => {
    const width = Math.max(1, hostRect.width);
    const height = Math.max(1, hostRect.height);
    mouseTarget.x = clamp(
      ((event.clientX - hostRect.left) / width) * 2 - 1,
      -1,
      1,
    );
    mouseTarget.y = clamp(
      -(((event.clientY - hostRect.top) / height) * 2 - 1),
      -1,
      1,
    );
    POINTER.active = true;
    POINTER.lastMove = performance.now();
  };
  const pointerMove = (event: PointerEvent) => {
    updatePointerTarget(event);
    if (POINTER.pressed) {
      POINTER.dragX += (event.clientX - POINTER.lastPX) / Math.max(1, hostRect.width);
      POINTER.dragY += (event.clientY - POINTER.lastPY) / Math.max(1, hostRect.height);
    }
    POINTER.lastPX = event.clientX;
    POINTER.lastPY = event.clientY;
  };
  const pointerDown = (event: PointerEvent) => {
    updatePointerTarget(event);
    POINTER.pressed = true;
    POINTER.lastPX = event.clientX;
    POINTER.lastPY = event.clientY;
  };
  const pointerUp = () => {
    POINTER.pressed = false;
  };
  const mouseLeave = () => {
    POINTER.active = false;
    POINTER.pressed = false;
  };

  if (!reducedMotion) {
    window.addEventListener("pointermove", pointerMove, { passive: true });
    window.addEventListener("pointerdown", pointerDown, { passive: true });
    window.addEventListener("pointerup", pointerUp, { passive: true });
    window.addEventListener("pointercancel", pointerUp, { passive: true });
    document.addEventListener("mouseleave", mouseLeave, { passive: true });
  }

  const _ndc = new THREE.Vector3();
  const _direction = new THREE.Vector3();
  const _target = new THREE.Vector3();
  const updatePointerWorld = () => {
    if (POINTER.active) {
      _ndc.set(mouse.x, mouse.y, 0.5).unproject(camera);
      _direction.copy(_ndc).sub(camera.position).normalize();
      if (Math.abs(_direction.z) > 1e-4) {
        const distance = -camera.position.z / _direction.z;
        if (distance > 0 && Number.isFinite(distance)) {
          _target.copy(camera.position).addScaledVector(_direction, distance);
        }
      }
    } else {
      _target.set(0, 0, 0);
    }
    POINTER.world.lerp(_target, 0.12);
    const idle = (performance.now() - POINTER.lastMove) / 1000;
    POINTER.activity +=
      ((POINTER.active && idle < 3 ? 1 : 0) - POINTER.activity) * 0.06;
  };

  const appearStart = performance.now();
  let t0 = performance.now() / 1000;
  let spinPhase = 0;
  let pitchPhase = 0;
  let spinVel = 0;
  let pitchVel = 0;
  let flowPhase = 0;
  let filmPhase = 0;

  const update = (time: number, staticFrame = false) => {
    const dt = staticFrame ? 0 : Math.min(0.05, time - t0);
    t0 = time;
    camera.position.set(
      mouse.x * 1.75 + 0.12 * Math.sin(time * 0.13),
      mouse.y * 1.75 + 0.1 * Math.cos(time * 0.17),
      6,
    );
    camera.lookAt(0, 0, 0);
    updatePointerWorld();
    halo.quaternion.copy(camera.quaternion);

    spinVel += POINTER.dragX * 1 * 0.35;
    pitchVel += POINTER.dragY * 1 * 0.3;
    POINTER.dragX = 0;
    POINTER.dragY = 0;
    spinVel *= 0.94;
    pitchVel *= 0.94;

    spinPhase += dt * 0.1 + spinVel;
    pitchPhase += dt * 0.1 * 0.37 + pitchVel;
    flowPhase += dt * 0.45;
    filmPhase += dt * 0.64;
    group.rotation.y = spinPhase;
    group.rotation.x = pitchPhase + 0.3 * Math.sin(time * 0.21);
    group.rotation.z = 0.3 * 0.6 * Math.cos(time * 0.16);
    blobUniforms.uFlow.value = flowPhase;
    blobUniforms.uFilmT.value = filmPhase;
    blobUniforms.uCursor.value.copy(POINTER.world);
    blobUniforms.uActivity.value = POINTER.activity;

    const elapsed = performance.now() - appearStart;
    const appear = staticFrame ? 1 : clamp((elapsed - 300) / 1400, 0, 1);
    blobUniforms.iAlpha.value = appear;
    haloUniforms.iAlpha.value = appear;
    moteUniforms.uTime.value = time * 1 * 8.0;
    motes.position.copy(camera.position);
    backgroundUniforms.iTime.value = time;
  };

  const render = () => {
    animationFrame = 0;
    if (disposed || failureReported || paused) return;
    try {
      mouse.x += (mouseTarget.x - mouse.x) * 0.06;
      mouse.y += (mouseTarget.y - mouse.y) * 0.06;
      update(performance.now() / 1000);
      composer.render();
    } catch {
      reportFailure();
      return;
    }
    if (!paused) animationFrame = requestAnimationFrame(render);
  };

  try {
    if (reducedMotion) {
      renderStaticFrame = () => {
        update(8, true);
        composer.render();
      };
      renderStaticFrame();
    } else {
      animationFrame = requestAnimationFrame(render);
    }
    onReady();
  } catch (error) {
    reportFailure();
    throw error;
  }

  return {
    dispose() {
      disposed = true;
      if (animationFrame) cancelAnimationFrame(animationFrame);
      resizeObserver.disconnect();
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", pointerMove);
      window.removeEventListener("pointerdown", pointerDown);
      window.removeEventListener("pointerup", pointerUp);
      window.removeEventListener("pointercancel", pointerUp);
      document.removeEventListener("mouseleave", mouseLeave);
      canvas.removeEventListener(
        "webglcontextcreationerror",
        onContextCreationError,
      );
      canvas.removeEventListener("webglcontextlost", onContextLost);
      blobGeometry.dispose();
      blobMaterial.dispose();
      backgroundGeometry.dispose();
      backgroundMaterial.dispose();
      haloGeometry.dispose();
      haloMaterial.dispose();
      moteGeometry.dispose();
      moteMaterial.dispose();
      composer.renderTarget1.dispose();
      composer.renderTarget2.dispose();
      renderer.dispose();
    },
    setPaused(nextPaused) {
      if (disposed || reducedMotion || paused === nextPaused) return;
      paused = nextPaused;
      if (paused) {
        if (animationFrame) cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      } else if (!failureReported) {
        t0 = performance.now() / 1000;
        animationFrame = requestAnimationFrame(render);
      }
    },
  };
}

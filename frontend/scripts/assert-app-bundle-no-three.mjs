import assert from "node:assert/strict";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const distRoot = path.join(frontendRoot, "dist");
const manifest = JSON.parse(
  await readFile(path.join(distRoot, ".vite", "manifest.json"), "utf8"),
);

const entries = Object.entries(manifest);
const entry = entries.find(([, chunk]) => chunk.isEntry);
assert(entry, "Vite manifest does not contain an application entry chunk");

const threeEntries = entries.filter(
  ([key, chunk]) =>
    key.includes("opaline-three") ||
    chunk.name === "opaline-three" ||
    path.basename(chunk.file).startsWith("opaline-three-"),
);
assert.equal(
  threeEntries.length,
  1,
  `Expected one isolated Opaline Three.js chunk, found ${threeEntries.length}`,
);

function dependencyGraph(rootKey) {
  const seen = new Set();
  const visit = (key) => {
    if (seen.has(key)) return;
    seen.add(key);
    const chunk = manifest[key];
    assert(chunk, `Manifest references missing chunk ${key}`);
    for (const imported of chunk.imports ?? []) visit(imported);
  };
  visit(rootKey);
  return seen;
}

const [entryKey] = entry;
const appGraph = dependencyGraph(entryKey);
const [threeKey, threeChunk] = threeEntries[0];
assert(
  !appGraph.has(threeKey),
  `Working-app entry graph unexpectedly imports ${threeChunk.file}`,
);

const authEntry = entries.find(([key]) =>
  key.endsWith("src/app/routes/CustomerAuth.tsx"),
);
assert(authEntry, "Customer auth route is missing from the dynamic manifest");
const [authKey] = authEntry;
assert(
  (manifest[entryKey].dynamicImports ?? []).includes(authKey),
  "Customer auth route is not lazy-loaded from the application entry",
);
assert(
  dependencyGraph(authKey).has(threeKey),
  "Customer auth route no longer owns the isolated Three.js chunk",
);

const appFiles = [...appGraph].map((key) => manifest[key].file);
for (const file of appFiles) {
  const source = await readFile(path.join(distRoot, file), "utf8");
  assert(
    !source.includes('REVISION="143"') &&
      !source.includes("three.module.js") &&
      !source.includes("WebGL1Renderer"),
    `Working-app chunk ${file} contains Three.js source`,
  );
}

const indexHtml = await readFile(path.join(distRoot, "index.html"), "utf8");
assert(
  !indexHtml.includes(threeChunk.file),
  "index.html eagerly preloads the authentication-only Three.js chunk",
);

const appBytes = (
  await Promise.all(
    appFiles.map(async (file) => (await stat(path.join(distRoot, file))).size),
  )
).reduce((total, size) => total + size, 0);
const threeBytes = (await stat(path.join(distRoot, threeChunk.file))).size;
assert(
  threeBytes > 400_000,
  `Isolated Three.js chunk is unexpectedly small (${threeBytes} bytes); the assertion may no longer identify it correctly`,
);

console.log(
  `Bundle boundary verified: ${appBytes} app bytes exclude ${threeBytes} auth-only Three.js bytes.`,
);

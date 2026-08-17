import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
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
const threeEntries = entries.filter(
  ([key, chunk]) =>
    key.includes("three") ||
    chunk.name === "opaline-three" ||
    path.basename(chunk.file).startsWith("opaline-three-"),
);
assert.equal(
  threeEntries.length,
  0,
  `The Match shell must not ship Three.js; found ${threeEntries.map(([, chunk]) => chunk.file).join(", ")}`,
);

for (const [, chunk] of entries) {
  if (!chunk.file?.endsWith(".js")) continue;
  const source = await readFile(path.join(distRoot, chunk.file), "utf8");
  assert(
    !source.includes('REVISION="143"') &&
      !source.includes("three.module.js") &&
      !source.includes("WebGL1Renderer"),
    `Chunk ${chunk.file} contains Three.js source`,
  );
}

console.log("Bundle boundary verified: no Three.js in the Match shell.");

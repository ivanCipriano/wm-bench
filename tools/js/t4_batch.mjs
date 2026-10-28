// Attacchi T4 su un lotto di file JavaScript, in un solo processo Node (SPEC §12; ADR-014 §4).
//
//   node t4_batch.mjs terser        f00000.js ...  # T4.1 senza rinomina: compress di default
//   node t4_batch.mjs terser-mangle f00000.js ...  # T4.1 con rinomina: compress + mangle (toplevel=false)
//   node t4_batch.mjs esbuild       f00000.js ...  # T4.2: bundle, cjs, node, senza tree-shaking,
//                                                  #       pacchetti npm esterni
//   node t4_batch.mjs esbuild-bundled f00000.js ... # diagnostica: come esbuild, pacchetti non esterni
//
// Ogni file si riscrive al suo posto. Al primo errore il processo esce con codice 1: il ToolRunner
// divide il lotto finché l'errore resta su un solo file (TOOL_ERROR con il codice originale).
// esbuild legge il codice da stdin con un nome fisso, così il commento con il percorso che mette
// in testa al bundle non dipende dalla posizione del file nel lotto. Con --packages=external i
// require/import di pacchetti npm restano tali, come i moduli di Node (decisione dell'utente del
// 28 ottobre 2026: in un progetto le dipendenze restano installate a parte, e copiarle nel file
// aggiungerebbe codice senza watermark).
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname } from "node:path";

const require = createRequire(import.meta.url);
const terser = require("terser");
const esbuild = require("esbuild");

const TERSER = {
  terser: { compress: {}, mangle: false },
  "terser-mangle": { compress: {}, mangle: { toplevel: false } },
};

async function apply(mode, file) {
  const code = readFileSync(file, "utf8");
  if (mode in TERSER) {
    const result = await terser.minify(code, TERSER[mode]);
    return result.code;
  }
  const result = esbuild.buildSync({
    stdin: { contents: code, resolveDir: dirname(file), sourcefile: "input.js", loader: "js" },
    bundle: true,
    format: "cjs",
    platform: "node",
    treeShaking: false,
    ...(mode === "esbuild" ? { packages: "external" } : {}),
    write: false,
    logLevel: "silent",
  });
  return result.outputFiles[0].text;
}

const [mode, ...files] = process.argv.slice(2);
if (!(mode in TERSER) && mode !== "esbuild" && mode !== "esbuild-bundled") {
  process.stderr.write(`unknown mode: ${mode}\n`);
  process.exit(2);
}
for (const file of files) {
  try {
    writeFileSync(file, await apply(mode, file), "utf8");
  } catch (error) {
    process.stderr.write(`${file}: ${error && error.message ? error.message : error}\n`);
    process.exit(1);
  }
}

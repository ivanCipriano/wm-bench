// Configurazione di ESLint per l'attacco T1.2 (SPEC §12): regole raccomandate con --fix.
// Si lancia con `eslint -c <questo file>` dalla cartella dei file da correggere: con -c la base
// dei percorsi è la cartella corrente, mentre gli import si risolvono da tools/js/node_modules.
import js from "@eslint/js";
import globals from "globals";

export default [
  js.configs.recommended,
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "commonjs",
      globals: { ...globals.node },
    },
  },
];

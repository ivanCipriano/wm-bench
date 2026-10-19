#!/usr/bin/env bash
# Tool non Python degli attacchi (SPEC §20.2, ADR-012). Da lanciare sul NODO DI LOGIN (serve
# internet), con bench-core nel PATH; idempotente:
#
#   bash scripts/setup_tools.sh
#
# 1) JavaScript: la prima volta genera tools/js/package-lock.json (da committare), poi `npm ci`
#    in tools/js/node_modules/ (prettier, eslint, @eslint/js, globals, terser, esbuild).
# 2) Java: google-java-format (tools/java/fetch_jars.sh, SHA-256 verificato) e OpenRewrite:
#    il repository Maven tools/java/m2/ si popola con una prova online su un progetto minimo,
#    poi la stessa prova gira offline (-o), come nei job.
# 3) Controlla che le ricette di CommonStaticAnalysis coincidano con
#    docs/attacks/t1_2_java_recipes.txt.
# 4) Prova ogni tool su un file minimo e stampa le versioni effettive (da riportare all'agente).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WMB="${WMB:-/mnt/beegfs/did_tesi_nlp_330/icipriano/wm_bench}"
export PATH="$WMB/bench-core/bin:$PATH"
cd "$REPO_ROOT"
umask 002

# Cache fuori dalla home (quota).
export npm_config_cache="$WMB/tools_cache/npm"
mkdir -p "$npm_config_cache"
M2="$REPO_ROOT/tools/java/m2"
GJF_FLAGS=(
    --add-exports=jdk.compiler/com.sun.tools.javac.api=ALL-UNNAMED
    --add-exports=jdk.compiler/com.sun.tools.javac.code=ALL-UNNAMED
    --add-exports=jdk.compiler/com.sun.tools.javac.file=ALL-UNNAMED
    --add-exports=jdk.compiler/com.sun.tools.javac.parser=ALL-UNNAMED
    --add-exports=jdk.compiler/com.sun.tools.javac.tree=ALL-UNNAMED
    --add-exports=jdk.compiler/com.sun.tools.javac.util=ALL-UNNAMED
)
mkdir -p "$WMB/tmp"
WORK="$(mktemp -d "$WMB/tmp/setup_tools.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "##### 1) JavaScript (tools/js)"
(
    cd tools/js
    if [ ! -f package-lock.json ]; then
        echo "package-lock.json missing: generating it (commit it afterwards)"
        npm install --package-lock-only --ignore-scripts --no-audit --no-fund
    fi
    npm ci --no-audit --no-fund
)

echo "##### 2a) google-java-format"
bash tools/java/fetch_jars.sh
GJF="$REPO_ROOT/tools/java/google-java-format-1.24.0-all-deps.jar"

echo "##### 2b) OpenRewrite (repository Maven in tools/java/m2)"
PROJ="$WORK/rewrite"
mkdir -p "$PROJ/src/main/java/p0000"
cp tools/java/rewrite/pom.xml "$PROJ/pom.xml"
cat > "$PROJ/src/main/java/p0000/Solution.java" <<'JAVA'
package p0000;

import java.util.*;

class Solution {
    public boolean check(List<Integer> xs) {
        if (xs.size() == 0) return true;
        int a = 0, b = 1;
        return xs.get(0) == a ? true : false;
    }
}
JAVA
cp "$PROJ/src/main/java/p0000/Solution.java" "$WORK/Solution.orig.java"
mvn -B -q -Dmaven.repo.local="$M2" -f "$PROJ/pom.xml" rewrite:run
echo "--- online run: diff"
diff "$WORK/Solution.orig.java" "$PROJ/src/main/java/p0000/Solution.java" || true
cp "$WORK/Solution.orig.java" "$PROJ/src/main/java/p0000/Solution.java"
rm -rf "$PROJ/target"
mvn -B -q -o -Dmaven.repo.local="$M2" -f "$PROJ/pom.xml" rewrite:run
echo "--- offline run (-o): OK"

echo "##### 3) ricette di CommonStaticAnalysis"
JAR="$M2/org/openrewrite/recipe/rewrite-static-analysis/1.20.0/rewrite-static-analysis-1.20.0.jar"
unzip -p "$JAR" META-INF/rewrite/common-static-analysis.yml \
    | sed -n '/^recipeList:/,$p' | grep -E '^  - ' | sed 's/^  - //' > "$WORK/recipes.txt"
sed -n '/^## Attive/,/^$/p' docs/attacks/t1_2_java_recipes.txt | grep -v '^#' | grep -v '^$' \
    > "$WORK/recipes_doc.txt"
if diff "$WORK/recipes_doc.txt" "$WORK/recipes.txt"; then
    echo "recipes: $(wc -l < "$WORK/recipes.txt") active, identical to docs/attacks/t1_2_java_recipes.txt"
else
    echo "RECIPES DIFFER from docs/attacks/t1_2_java_recipes.txt" >&2
    exit 1
fi

echo "##### 4) prova dei tool su file minimi"
printf 'def f( x ):\n  return x+1\n' > "$WORK/a.py"
printf 'class A { int f(int x){return x+1;} }\n' > "$WORK/A.java"
printf '#include <stdio.h>\n#include <math.h>\n#include <vector>\nusing namespace std;\nint f(vector<int> v){if(v.size()) return 1; else return 2;}\n' > "$WORK/a.cpp"
# Header interni del compilatore per clang-tidy (stddef.h…): quelli del gcc di conda (ADR-012).
CXX_INCLUDE="$(c++ -print-file-name=include)"
printf 'const f = (x)=>{return x+1}\nmodule.exports = f;\n' > "$WORK/a.js"
BIN="$REPO_ROOT/tools/js/node_modules/.bin"
black -q "$WORK/a.py" && echo "black: OK"
ruff check --fix --isolated --no-cache -q "$WORK/a.py" && echo "ruff: OK"
java "${GJF_FLAGS[@]}" -jar "$GJF" --replace "$WORK/A.java" && echo "google-java-format: OK"
clang-format -i --style="file:$REPO_ROOT/tools/cpp/.clang-format" "$WORK/a.cpp" && echo "clang-format: OK"
clang-tidy --quiet --fix --config-file="$REPO_ROOT/tools/cpp/.clang-tidy" "$WORK/a.cpp" -- -std=c++17 \
    -isystem "$CXX_INCLUDE" && echo "clang-tidy: OK"
"$BIN/prettier" --write --log-level warn --no-editorconfig \
    --config "$REPO_ROOT/tools/js/.prettierrc.json" "$WORK/a.js" && echo "prettier: OK"
(cd "$WORK" && "$BIN/eslint" --fix -c "$REPO_ROOT/tools/js/eslint.config.mjs" a.js) && echo "eslint: OK"
cat "$WORK/a.cpp"

echo "##### versioni effettive"
echo "node        $(node --version)"
echo "npm         $(npm --version)"
echo "prettier    $("$BIN/prettier" --version)"
echo "eslint      $("$BIN/eslint" --version)"
echo "terser      $("$BIN/terser" --version)"
echo "esbuild     $("$BIN/esbuild" --version)"
echo "java        $(java -version 2>&1 | head -n 1)"
echo "mvn         $(mvn -v 2>/dev/null | head -n 1)"
echo "gjf         $(java "${GJF_FLAGS[@]}" -jar "$GJF" --version 2>&1)"
echo "clang-format $(clang-format --version)"
echo "clang-tidy  $(clang-tidy --version | grep -i version | head -n 1)"
echo "black       $(black --version | head -n 1)"
echo "ruff        $(ruff --version)"
python - <<'PY'
import importlib.metadata as md
for name in ("libcst", "codebleu", "python-minifier", "tree-sitter"):
    print(f"{name:<11} {md.version(name)}")
PY
echo "##### fatto"

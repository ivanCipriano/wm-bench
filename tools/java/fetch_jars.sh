#!/usr/bin/env bash
# Scarica google-java-format (jar all-deps, SPEC §20.2) in tools/java/ e ne verifica lo SHA-256.
# L'hash è quello del jar di Maven Central (lo SHA-1 pubblicato da Central coincide), fissato
# il 19 ottobre 2026. Idempotente: un jar già presente e corretto non si riscarica.
set -euo pipefail

VERSION="1.24.0"
SHA256="812f805f58112460edf01bf202a8e61d0fd1f35c0d4fabd54220640776ec57a1"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JAR="$DIR/google-java-format-$VERSION-all-deps.jar"
URL="https://repo1.maven.org/maven2/com/google/googlejavaformat/google-java-format/$VERSION/google-java-format-$VERSION-all-deps.jar"

check() { [ -f "$JAR" ] && echo "$SHA256  $JAR" | sha256sum -c --status; }

if check; then
    echo "google-java-format $VERSION: already present ($JAR)"
    exit 0
fi
curl -fsSL -o "$JAR.part" "$URL"
mv "$JAR.part" "$JAR"
if ! check; then
    echo "SHA-256 mismatch for $JAR" >&2
    sha256sum "$JAR" >&2
    rm -f "$JAR"
    exit 1
fi
echo "google-java-format $VERSION: downloaded and verified ($JAR)"

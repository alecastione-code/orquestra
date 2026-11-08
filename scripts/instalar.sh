#!/bin/bash
# Compila o app, instala o runtime, o `orq` e o LaunchAgent do servidor. Sem Xcode, sem assinatura paga.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
CASA="$HOME/Library/Application Support/Orquestra"
RUN="$CASA/runtime"
# 08/11: o serviço roda pelo executável de dentro do Python.app (é ele que tem Acesso Total ao Disco); /usr/bin/python3 é bloqueado em ~/Documents.
PYSRV=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python
[ -x "$PYSRV" ] || PYSRV=/usr/bin/python3
APP="$HOME/Applications/Orquestra.app"
PLIST="$HOME/Library/LaunchAgents/com.orquestra.servidor.plist"
SDK="/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk"
[ -d "$SDK" ] || SDK="$(xcrun --show-sdk-path)"

echo "→ copiando o runtime para $RUN (fora de ~/Documents, para o LaunchAgent não esbarrar em permissões)"
mkdir -p "$RUN" "$HOME/.local/bin" "$HOME/Applications" "$HOME/Library/LaunchAgents" "$CASA/logs"
rsync -a --delete --exclude '__pycache__' "$RAIZ/orquestra" "$RAIZ/web" "$RAIZ/bin" "$RAIZ/scripts" "$RAIZ/config" "$RUN/"
ln -sf "$RUN/bin/orq" "$HOME/.local/bin/orq"
ln -sf "$RUN/scripts/hook-pulso.sh" "$HOME/.local/bin/orq-pulso"   # caminho sem espaço para o hook

echo "→ compilando o app (swiftc)"
TMP="$(mktemp -d)"
swiftc -O -target "$(uname -m)-apple-macos13.0" -sdk "$SDK" "$RAIZ/app/Orquestra.swift" -o "$TMP/Orquestra"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$TMP/Orquestra" "$APP/Contents/MacOS/Orquestra"
printf '%s\n' "$RUN" > "$APP/Contents/Resources/raiz.txt"
cp "$RAIZ/logo/Orquestra.icns" "$APP/Contents/Resources/Orquestra.icns"
cat > "$APP/Contents/Info.plist" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Orquestra</string>
<key>CFBundleDisplayName</key><string>Orquestra</string>
<key>CFBundleIdentifier</key><string>com.orquestra.app</string>
<key>CFBundleExecutable</key><string>Orquestra</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleIconFile</key><string>Orquestra</string>
<key>NSHumanReadableCopyright</key><string>© 2026 Alexandre Castione. Criado por Alexandre Castione.</string>
<key>CFBundleShortVersionString</key><string>1.0.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>12.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict></plist>
PL
codesign --force --deep -s - "$APP" 2>&1 | tail -1 || true
rm -rf "$TMP"

echo "→ LaunchAgent com.orquestra.servidor"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.orquestra.servidor</string>
<key>ProgramArguments</key><array><string>$PYSRV</string><string>-m</string><string>orquestra.servidor</string></array>
<key>WorkingDirectory</key><string>$RUN</string>
<key>EnvironmentVariables</key><dict><key>PYTHONPATH</key><string>$RUN</string><key>PYTHONUNBUFFERED</key><string>1</string></dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$CASA/logs/servidor.log</string>
<key>StandardErrorPath</key><string>$CASA/logs/servidor.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.orquestra.servidor" 2>/dev/null || true
# o bootout é assíncrono: sem esperar, o bootstrap falhava às vezes com "Input/output error"
for i in 1 2 3 4 5 6 7 8; do launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break; sleep 1; done
launchctl kickstart -k "gui/$(id -u)/com.orquestra.servidor"

echo "→ LaunchAgent com.orquestra.ponte (a cada 5 min; sem token no Keychain não faz nada)"
PONTE="$HOME/Library/LaunchAgents/com.orquestra.ponte.plist"
cat > "$PONTE" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.orquestra.ponte</string>
<key>ProgramArguments</key><array><string>$RUN/bin/orq</string><string>ponte</string></array>
<key>StartInterval</key><integer>300</integer>
<key>RunAtLoad</key><true/>
<key>StandardErrorPath</key><string>$CASA/logs/ponte.log</string>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/com.orquestra.ponte" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PONTE" 2>/dev/null || true

for i in 1 2 3 4 5 6 7 8; do curl -fs http://127.0.0.1:4747/api/visao >/dev/null && break; sleep 1; done
curl -fs http://127.0.0.1:4747/api/visao >/dev/null && echo "✓ servidor no ar em http://127.0.0.1:4747" || echo "✗ servidor não respondeu (veja $CASA/logs/servidor.log)"
echo "✓ app: $APP   ·   orq: $HOME/.local/bin/orq (garanta ~/.local/bin no PATH)"
echo "Hook de pulso (opcional): veja docs/HOOKS.md — script em $RUN/scripts/hook-pulso.sh"
echo "Preços e webhook: edite em $CASA/config/ (sobrepõe o config/ do repositório)"
echo "Abrir: open \"$APP\""

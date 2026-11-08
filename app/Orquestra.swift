import Cocoa
import WebKit

let url = URL(string: "http://127.0.0.1:4747")!
let urlInicial = URL(string: ProcessInfo.processInfo.environment["ORQUESTRA_URL"] ?? "") ?? url
let raiz = ProcessInfo.processInfo.environment["ORQUESTRA_RAIZ"] ?? ""

func servidorNoAr() -> Bool {
    var ok = false
    let sem = DispatchSemaphore(value: 0)
    var req = URLRequest(url: url.appendingPathComponent("api/visao")); req.timeoutInterval = 2
    URLSession.shared.dataTask(with: req) { _, r, _ in ok = (r as? HTTPURLResponse)?.statusCode == 200; sem.signal() }.resume()
    _ = sem.wait(timeout: .now() + 3)
    return ok
}

func iniciarServidor() {
    guard !servidorNoAr() else { return }
    // primeiro tenta o LaunchAgent (dono do servidor); só se ele não subir, o app inicia um próprio
    let k = Process()
    k.executableURL = URL(fileURLWithPath: "/bin/launchctl")
    k.arguments = ["kickstart", "gui/\(getuid())/com.orquestra.servidor"]
    try? k.run(); k.waitUntilExit()
    for _ in 0..<6 { if servidorNoAr() { return }; Thread.sleep(forTimeInterval: 1) }
    // raiz do projeto gravada pelo instalar.sh em Resources/raiz.txt
    let arq = Bundle.main.path(forResource: "raiz", ofType: "txt") ?? ""
    let dir = (try? String(contentsOfFile: arq, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines) ?? raiz
    guard !dir.isEmpty else { return }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = ["-m", "orquestra.servidor"]
    p.currentDirectoryURL = URL(fileURLWithPath: dir)
    p.environment = ["PYTHONPATH": dir, "HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
    try? p.run()
}

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var janela: NSWindow!
    var web: WKWebView!
    var item: NSStatusItem!
    var timer: Timer?

    func applicationDidFinishLaunching(_ n: Notification) {
        iniciarServidor()
        web = WKWebView(frame: .zero)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        janela = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1200, height: 800),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        janela.title = "Orquestra"
        janela.titlebarAppearsTransparent = true
        janela.backgroundColor = NSColor(red: 0.086, green: 0.09, blue: 0.106, alpha: 1)
        janela.appearance = NSAppearance(named: .darkAqua)
        janela.contentView = web
        janela.setFrameAutosaveName("OrquestraJanela")
        janela.center()
        janela.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        carregar()

        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "◈ …"
        let m = NSMenu()
        m.addItem(withTitle: "Abrir Orquestra", action: #selector(abrir), keyEquivalent: "o").target = self
        m.addItem(withTitle: "Recarregar painel", action: #selector(recarregar), keyEquivalent: "r").target = self
        m.addItem(.separator())
        m.addItem(withTitle: "Sair", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = m
        atualizarStatus()
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in self?.atualizarStatus() }
    }

    func carregar() { web.load(URLRequest(url: urlInicial)) }
    @objc func abrir() { janela.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
    @objc func recarregar() { carregar() }
    func applicationShouldHandleReopen(_ s: NSApplication, hasVisibleWindows f: Bool) -> Bool { abrir(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { false }

    // O WKWebView não mostra alert/confirm/prompt sozinho: sem isto, os botões do painel que perguntam algo (Pegar, Feita,
    // Bloqueada, Redistribuir…) não faziam nada. Aqui viram diálogos do macOS.
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler ok: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.runModal(); ok()
    }
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage m: String, initiatedByFrame f: WKFrameInfo, completionHandler ok: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = m; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancelar")
        ok(a.runModal() == .alertFirstButtonReturn)
    }
    func webView(_ w: WKWebView, runJavaScriptTextInputPanelWithPrompt p: String, defaultText d: String?, initiatedByFrame f: WKFrameInfo, completionHandler ok: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = p; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancelar")
        let campo = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24)); campo.stringValue = d ?? ""
        a.accessoryView = campo
        ok(a.runModal() == .alertFirstButtonReturn ? campo.stringValue : nil)
    }
    // Links claude://… (abrir uma sessão no app Claude) saem do painel e abrem no sistema.
    func webView(_ w: WKWebView, decidePolicyFor n: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = n.request.url, let e = u.scheme, e != "http", e != "https", e != "about" {
            NSWorkspace.shared.open(u); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        // servidor ainda subindo: tenta de novo
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.carregar() }
    }

    func atualizarStatus() {
        var req = URLRequest(url: url.appendingPathComponent("api/visao")); req.timeoutInterval = 4
        URLSession.shared.dataTask(with: req) { data, _, _ in
            var t = "◈ off"
            if let d = data, let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
                let vivos = j["vivos"] as? Int ?? 0
                var cx = ""
                if let cs = j["cotas"] as? [[String: Any]], let c = cs.first(where: { ($0["agente"] as? String) == "codex" }),
                   let u = c["usado_pct"] as? Double { cx = " · Codex \(Int(u.rounded()))%" }
                let alerta = ((j["alertas"] as? [Any])?.isEmpty == false) ? " ⚠" : ""
                let av = j["ao_vivo"] as? [String: Any]
                let ativas = av?["ativa"] as? Int ?? vivos
                let trav = (av?["travada"] as? Int ?? 0) + (av?["falhou"] as? Int ?? 0)
                let tr = trav > 0 ? " · \(trav) travada\(trav > 1 ? "s" : "")" : ""
                t = "◈ \(ativas) ativas\(tr)\(cx)\(alerta)"
            }
            DispatchQueue.main.async { self.item.button?.title = t }
        }.resume()
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()

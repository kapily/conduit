import Foundation
import Darwin
import XCTest

private enum FixtureFailure: Error {
    case missingControl(String), bridge(String), wrongScreen
}

final class HarnessMobileUITests: XCTestCase {
    let conduit = XCUIApplication(bundleIdentifier: "app.cogwheel.conduit.debug")
    let system = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let question = "Give me a checklist for a tray of basil seedlings."
    let answers = ["Label each pot", "Water the seedlings", "Check the soil again tomorrow."]
    let newQuestion = "Which marker belongs in the arugula tray?"
    let newAnswer = "Put the copper marker in the arugula tray."
    let address = "http://127.0.0.1:43671/"
    var endpoint = ""
    var key = ""

    override func setUpWithError() throws {
        continueAfterFailure = false
        let file = try XCTUnwrap(Bundle(for: Self.self).url(forResource: "Bootstrap", withExtension: "plist"))
        let props = try PropertyListDecoder().decode([String:String].self, from: Data(contentsOf: file))
        endpoint = try XCTUnwrap(props["endpoint"])
        key = try XCTUnwrap(props["key"])
    }

    @discardableResult
    private func bridge(_ name: String, details: String = "") throws -> String {
        // A loopback socket in the test runner works independently of the app's ATS settings.
        guard let parts = URLComponents(string: endpoint), parts.host == "127.0.0.1",
              let port = parts.port, let port16 = UInt16(exactly: port) else {
            throw FixtureFailure.bridge(name)
        }
        let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { throw FixtureFailure.bridge(name) }
        defer { Darwin.close(fd) }
        var timeout = timeval(tv_sec: 70, tv_usec: 0)
        _ = setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        _ = setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var opt: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &opt, socklen_t(MemoryLayout<Int32>.size))
        var target = sockaddr_in()
        target.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        target.sin_family = sa_family_t(AF_INET)
        target.sin_port = port16.bigEndian
        target.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let connected = withUnsafePointer(to: &target) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard connected == 0 else { throw FixtureFailure.bridge(name) }
        let payload = Data(details.utf8)
        var request = Data(("POST /" + name + " HTTP/1.0\r\nHost: localhost\r\nX-Garden-Runner: " + key + "\r\nContent-Length: " + String(payload.count) + "\r\n\r\n").utf8)
        request.append(payload)
        let written = request.withUnsafeBytes { raw -> Bool in
            guard let base = raw.baseAddress else { return false }
            var offset = 0
            while offset < raw.count {
                let n = Darwin.write(fd, base.advanced(by: offset), raw.count - offset)
                guard n > 0 else { return false }
                offset += n
            }
            return true
        }
        guard written else { throw FixtureFailure.bridge(name) }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while data.count < 16384 {
            let n = buffer.withUnsafeMutableBytes { Darwin.read(fd, $0.baseAddress!, $0.count) }
            if n == 0 { break }
            guard n > 0 else { throw FixtureFailure.bridge(name) }
            data.append(contentsOf: buffer.prefix(n))
        }
        guard let response = String(data: data, encoding: .utf8), response.hasPrefix("HTTP/1.0 200 "),
              let boundary = response.range(of: "\r\n\r\n") else { throw FixtureFailure.bridge(name) }
        return String(response[boundary.upperBound...])

    }

    private func find(_ text: String, in root: XCUIElement, exact: Bool = false, tap: Bool = false) -> XCUIElement? {
        let expression = exact ? "label ==[c] %@ OR identifier ==[c] %@ OR value ==[c] %@" :
            "label CONTAINS[c] %@ OR identifier CONTAINS[c] %@ OR value CONTAINS[c] %@"
        let predicate = NSPredicate(format: expression, text, text, text)
        let query = root.descendants(matching: .any).matching(predicate)
        if tap {
            let button = root.buttons.matching(predicate).firstMatch
            if button.exists && button.isHittable { return button }
            return query.allElementsBoundByIndex.first { $0.exists && $0.isHittable }
        }
        let element = query.firstMatch
        return element.exists ? element : nil
    }

    private func until(_ seconds: TimeInterval, _ step: () -> XCUIElement?) -> XCUIElement? {
        let deadline = Date().addingTimeInterval(seconds)
        repeat {
            if let control = step() { return control }
            Thread.sleep(forTimeInterval: 0.25)
        } while Date() < deadline
        return nil
    }

    private func control(_ label: String, in root: XCUIElement, exact: Bool = true,
                         tap: Bool = false, seconds: TimeInterval = 25) throws -> XCUIElement {
        guard let item = until(seconds, { find(label, in: root, exact: exact, tap: tap) }) else {
            throw FixtureFailure.missingControl(label)
        }
        return item
    }

    private func dialog() {
        let names = ["Allow Paste", "Allow", "OK", "Continue"]
        for root in [conduit, system] {
            let alert = root.alerts.firstMatch
            if alert.exists {
                for name in names {
                    let button = alert.buttons[name]
                    if button.exists && button.isHittable { button.tap(); return }
                }
            }
        }
    }

    private func waitCatalog() throws {
        _ = try control("Open Harness workspace", in: conduit, tap: true, seconds: 60)
    }

    private func diagnostic(_ checkpoint: String) throws {
        // Only callable before credentials are issued; the host filters this in memory.
        try bridge("prepair-" + checkpoint, details: String(conduit.debugDescription.prefix(36000)))
    }

    private func formShowing() -> Bool {
        return find("Server Address", in: conduit) != nil &&
            find("Launch token", in: conduit) != nil
    }

    private func coordinate(for heading: String, secure: Bool) throws -> XCUICoordinate {
        let typed = secure ? conduit.secureTextFields.firstMatch : conduit.textFields.firstMatch
        if typed.exists && typed.isHittable {
            return typed.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        }
        let matches = conduit.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS[c] %@", heading))
            .allElementsBoundByIndex.filter { $0.exists && $0.frame.width > 0 && $0.frame.height > 0 && $0.frame.height < 110 }
        if let editable = matches.filter({ $0.elementType != .staticText && $0.frame.height >= 30 }).last {
            return editable.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5))
        }
        guard let title = matches.first else { throw FixtureFailure.missingControl("verified form heading") }
        let frame = conduit.frame
        // Each visible heading is followed by 8pt of space and the corresponding input.
        let hit = CGPoint(x: frame.midX, y: title.frame.maxY + 26)
        guard frame.insetBy(dx: 1, dy: 5).contains(hit) else { throw FixtureFailure.wrongScreen }
        return conduit.coordinate(withNormalizedOffset: .zero).withOffset(
            CGVector(dx: hit.x - frame.minX, dy: hit.y - frame.minY))
    }

    private func acceptedAddress() -> Bool {
        if conduit.textFields.allElementsBoundByIndex.contains(where: {
            ($0.value as? String)?.contains("127.0.0.1:43671") == true
        }) { return true }
        return find("127.0.0.1:43671", in: conduit) != nil
    }

    private func submitForm() {
        if let connect = find("Connect", in: conduit, exact: true, tap: true) {
            connect.tap()
        } else {
            conduit.typeText("\n")
            if formShowing() { conduit.typeText("\n") }
        }
    }

    private func emptyCatalog(_ seconds: TimeInterval) -> Bool {
        until(seconds) {
            dialog()
            return find("No conversations yet", in: conduit, exact: true)
        } != nil
    }

    private func pair() throws {
        try bridge("phase-chooser")
        conduit.launch()
        var chooser = until(45) { find("DeepSeek Harness", in: conduit, tap: true) }
        for _ in 0..<3 where chooser == nil {
            conduit.swipeUp()
            chooser = until(3) { find("DeepSeek Harness", in: conduit, tap: true) }
        }
        guard let button = chooser else { throw FixtureFailure.wrongScreen }
        try diagnostic("chooser-found")
        try bridge("chooser")
        button.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        try diagnostic("chooser-center-tapped")
        if until(10, { formShowing() ? conduit : nil }) == nil {
            dialog()
            if let fresh = find("DeepSeek Harness", in: conduit, tap: true) {
                fresh.coordinate(withNormalizedOffset: CGVector(dx: 0.3, dy: 0.5)).tap()
            }
            try diagnostic("chooser-retapped")
            guard until(25, { formShowing() ? conduit : nil }) != nil else { throw FixtureFailure.wrongScreen }
        }
        try diagnostic("form-visible")
        let url = try coordinate(for: "Server Address", secure: false)
        try diagnostic("field-located")
        try bridge("pairing")
        url.tap()
        try diagnostic("before-secret")
        try bridge("paste-link")
        url.press(forDuration: 0.9)
        let paste = until(6) {
            find("Paste", in: conduit, exact: true, tap: true) ??
            find("Paste", in: system, exact: true, tap: true)
        }
        if let paste { paste.tap() }
        let pasted = until(8) {
            dialog()
            return acceptedAddress() ? conduit : nil
        } != nil
        try bridge("clear-clipboard")
        if pasted {
            submitForm()
            if emptyCatalog(15) { try bridge("paired"); return }
        }
        guard formShowing() else { throw FixtureFailure.wrongScreen }
        if !acceptedAddress() {
            try coordinate(for: "Server Address", secure: false).tap()
            conduit.typeText(address)
        }
        try coordinate(for: "Launch token", secure: true).tap()
        let token = try bridge("fixture-token")
        guard !token.isEmpty else { throw FixtureFailure.wrongScreen }
        // Avoid adding text to a token that the full launch-link formatter already filled.
        if !pasted {
            let secure = conduit.secureTextFields.firstMatch
            if secure.exists && secure.isHittable { secure.typeText(token) }
            else { conduit.typeText(token) }
        }
        submitForm()
        guard emptyCatalog(55) else { throw FixtureFailure.wrongScreen }
        try bridge("paired")
    }


    private func verifyNative() throws {
        _ = try control(question, in: conduit, exact: false, seconds: 35)
        for answer in answers { _ = try control(answer, in: conduit, exact: false, seconds: 45) }
    }

    private func nativeComposer() throws -> XCUIElement {
        let item = until(30) {
            if let item = find("Message...", in: conduit, tap: true) { return item }
            if let row = find("Seedling notebook", in: conduit, exact: true, tap: true) { row.tap() }
            return [conduit.textViews.firstMatch, conduit.textFields.firstMatch].first { $0.exists && $0.isHittable }
        }
        guard let item else { throw FixtureFailure.missingControl("native composer") }
        return item
    }

    private func list() throws {
        try control("Chats", in: conduit, tap: true).tap()
        try waitCatalog()
    }

    private func reopen() throws {
        let selections = ["Seedling notes", "Give me a checklist", "Untitled", "Arugula tray"]
        let found = until(25) {
            for name in selections {
                if let item = find(name, in: conduit, tap: true) { return item }
            }
            let buttons = conduit.buttons.matching(NSPredicate(format: "label ==[c] %@", "New Chat")).allElementsBoundByIndex
            return buttons.count > 1 ? buttons.filter { $0.isHittable }.max { $0.frame.midY < $1.frame.midY } : nil
        }
        guard let item = found else { throw FixtureFailure.missingControl("server session") }
        item.tap()
        try verifyNative()
    }

    private func verifyWeb() throws {
        try bridge("phase-workspace")
        try control("Open Harness workspace", in: conduit, tap: true).tap()
        let web = conduit.webViews.firstMatch
        guard web.waitForExistence(timeout: 50) else { throw FixtureFailure.missingControl("official WebKit workspace") }
        let webInput = until(25) {
            dialog()
            if let local = find("Continue", in: web, exact: true, tap: true) { local.tap() }
            if let edit = find("Message or run a task", in: web, tap: true) { return edit }
            return [web.textViews.firstMatch, web.textFields.firstMatch].first { $0.exists && $0.isHittable }
        }
        var sent = false
        if let input = webInput {
            input.tap()
            let editable = [web.textViews.firstMatch, web.textFields.firstMatch].first { $0.exists && $0.isHittable }
            // typeText is reliable on the focused application even when WebKit flattens contenteditable.
            if let editable {
                editable.typeText(newQuestion)
            } else {
                conduit.typeText(newQuestion)
            }
            if let send = until(10, { find("Send message", in: web, exact: true, tap: true) }) {
                send.tap()
                sent = until(30) { find(newAnswer, in: web) } != nil
            }
        }
        if !sent {
            if find(question, in: web) == nil {
                let rowLabel = "Seedling notes"
                if find(rowLabel, in: web, tap: true) == nil {
                    for label in ["Open left sidebar", "Open sidebar", "Expand sidebar", "Toggle sidebar", "Show sidebar"] {
                        if let menu = find(label, in: web, tap: true) { menu.tap(); break }
                    }
                }
                try control(rowLabel, in: web, exact: false, tap: true, seconds: 15).tap()
            }
            _ = try control(question, in: web, exact: false)
            for answer in answers { _ = try control(answer, in: web, exact: false) }
        }
        try bridge("workspace")
        try control("Back", in: conduit, tap: true).tap()
        try waitCatalog()
        try bridge(sent ? "web-sent" : "web-history")
    }

    func testInstalledConduit() throws {
        try pair()
        try bridge("record")
        try control("New Chat", in: conduit, tap: true).tap()
        let composer = try nativeComposer()
        composer.tap()
        composer.typeText(question)
        try control("Send", in: conduit, tap: true).tap()
        try verifyNative()
        try bridge("transcript")
        try bridge("reply")
        try list()
        try reopen()
        conduit.terminate()
        conduit.launch()
        try waitCatalog()
        try reopen()
        try bridge("native")
        try list()
        try verifyWeb()
    }
}

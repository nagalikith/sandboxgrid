from playwright.sync_api import sync_playwright, TimeoutError
import uuid
import os
import sys
import datetime
import json
import time
from typing import Dict, List, Any, Callable, Optional
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RuntimeConfig:
    artifacts_dir: str
    sessions_dir: str
    log_file: str
    cdp_endpoint: str
    log_emitter: Optional[Callable[[str], None]] = None

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        artifacts_dir = os.getenv("ARTIFACTS_DIR", "/home/neko/artifacts")
        cdp_endpoint = os.getenv("CDP_ENDPOINT", "http://127.0.0.1:9222")
        sessions_dir = os.path.join(artifacts_dir, "sessions")
        log_file = os.path.join(artifacts_dir, "agent.log")
        os.makedirs(artifacts_dir, exist_ok=True)
        os.makedirs(sessions_dir, exist_ok=True)
        return cls(
            artifacts_dir=artifacts_dir,
            sessions_dir=sessions_dir,
            log_file=log_file,
            cdp_endpoint=cdp_endpoint,
        )


def make_logger(cfg: RuntimeConfig) -> Callable[[str], None]:
    def log(msg: str) -> None:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        line = f"[{ts}] {msg}"
        print(line, flush=True)
        os.makedirs(os.path.dirname(cfg.log_file), exist_ok=True)
        with open(cfg.log_file, "a") as f:
            f.write(line + "\n")
        if cfg.log_emitter:
            try:
                cfg.log_emitter(line)
            except Exception:
                pass
    return log


class BrowserRunner:
    """Stateless helper to attach to browser and expose overlay."""

    def __init__(self, cfg: RuntimeConfig, log: Callable[[str], None]) -> None:
        self.cfg = cfg
        self.log = log

    def attach(self, playwright, storage_state_path: Optional[str] = None):
        browser = playwright.chromium.connect_over_cdp(self.cfg.cdp_endpoint)
        context = None
        page = None
        if storage_state_path:
            try:
                context = browser.new_context(storage_state=storage_state_path)
                page = context.new_page()
            except Exception:
                context = None
                page = None
        if context is None:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
        try:
            page.bring_to_front()
        except Exception:
            pass
        inject_overlay(page)
        update_overlay(page, "status", {"text": "Agent connected to browser"})
        return browser, context, page

def inject_overlay(page):
    """Inject a visual overlay to show agent actions"""
    js = """
    if (!document.getElementById('agent-overlay')) {
        const div = document.createElement('div');
        div.id = 'agent-overlay';
        div.style.cssText = 'position:fixed;top:0;right:0;width:300px;height:100vh;background:rgba(0,0,0,0.85);color:#0f0;z-index:2147483647;font-family:monospace;padding:15px;box-sizing:border-box;pointer-events:none;box-shadow:-2px 0 5px rgba(0,0,0,0.5);display:flex;flex-direction:column;backdrop-filter:blur(5px);';
        div.innerHTML = '<h3 style="margin:0 0 15px;border-bottom:1px solid #4CAF50;padding-bottom:5px;color:#fff;font-size:16px;text-transform:uppercase;letter-spacing:1px;">Agent Actions</h3><div id="agent-log" style="flex:1;overflow-y:auto;padding-right:5px;scrollbar-width:thin;"></div>';
        document.documentElement.appendChild(div);
    }
    """
    try:
        page.evaluate(js)
    except:
        pass

def update_overlay(page, action_type, data):
    """Update the overlay with a new action"""
    info = ""
    if action_type == "click":
        info = data.get("selector", "")
    elif action_type == "type":
        info = f"'{data.get('text', '')}' -> {data.get('selector', '')}"
    elif action_type == "navigate":
        info = data.get("url", "")
    elif action_type == "scroll":
        info = f"To {data.get('x')}, {data.get('y')}"
    elif action_type == "screenshot":
        info = data.get("filename", "")
    elif action_type == "status":
        info = data.get("text", "")
    else:
        info = str(data)
    
    info = info.replace("'", "\\'").replace('"', '\\"')
    js = f"""
    (() => {{
        const log = document.getElementById('agent-log');
        if (log) {{
            const entry = document.createElement('div');
            entry.style.cssText = 'margin-bottom:10px;border-left:3px solid #4CAF50;padding-left:8px;animation:fadeIn 0.3s;background:rgba(255,255,255,0.05);padding:8px;border-radius:0 4px 4px 0;';
            entry.innerHTML = '<div style="color:#81C784;font-weight:bold;font-size:12px;margin-bottom:4px;">{action_type.upper()}</div><div style="color:#eee;font-size:11px;word-break:break-all;line-height:1.4;">{info}</div>';
            log.appendChild(entry);
            entry.scrollIntoView({{behavior:'smooth'}});
        }}
    }})()
    """
    try:
        page.evaluate(js)
    except:
        pass


    context = browser.contexts[0]
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.bring_to_front()
    except Exception:
        pass
    inject_overlay(page)
    update_overlay(page, "status", {"text": "Agent connected to browser"})
    return browser, context, page

class InteractiveController:
    """Handles user interaction and control after automated tasks"""
    
    def __init__(self, page, cfg: RuntimeConfig, recorder=None):
        self.page = page
        self.cfg = cfg
        self.recorder = recorder
        self.running = True
        self.monitoring = False
        
    def start_monitoring(self):
        """Monitor browser for user actions"""
        if self.monitoring:
            return
            
        self.monitoring = True
        log("User control enabled - browser ready for manual interaction")
        inject_overlay(self.page)
        
        # Setup interaction recording
        def on_interaction(action_type, data):
            if self.recorder:
                self.recorder.record_action(action_type, data)
                log(f"Recorded {action_type}")

        try:
            self.page.expose_function("record_interaction", lambda t, d: on_interaction(t, d))
        except:
            pass  # Function might already be exposed

        js_recorder = """
            (() => {
                function getSelector(el) {
                    if (!el) return '';
                    if (el.id) return '#' + el.id;
                    
                    let path = [];
                    while (el.nodeType === 1) {
                        let selector = el.nodeName.toLowerCase();
                        if (el.id) {
                            selector = '#' + el.id;
                            path.unshift(selector);
                            break;
                        }
                        let sib = el, nth = 1;
                        while (sib = sib.previousElementSibling) {
                            if (sib.nodeName.toLowerCase() === selector) nth++;
                        }
                        if (nth !== 1) selector += ":nth-of-type("+nth+")";
                        path.unshift(selector);
                        el = el.parentNode;
                    }
                    return path.join(" > ");
                }
                document.addEventListener('click', (e) => {
                    window.record_interaction('click', {selector: getSelector(e.target)});
                }, true);
                document.addEventListener('change', (e) => {
                    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
                        window.record_interaction('type', {selector: getSelector(e.target), text: e.target.value});
                    }
                }, true);
            })();
        """
        self.page.add_init_script(js_recorder)
        self.page.evaluate(js_recorder)

        # Monitor for navigation changes
        def on_navigate(frame):
            if self.recorder and frame == self.page.main_frame:
                self.recorder.record_action("navigate", {"url": frame.url})
                log(f"Recorded navigation: {frame.url}")
        
        self.page.on("framenavigated", on_navigate)
    
    def show_menu(self):
        """Display interactive menu"""
        print("\n" + "="*60)
        print("  BROWSER CONTROL MENU")
        print("="*60)
        print("  Commands:")
        print("    screenshot [name] - Take a screenshot")
        print("    navigate <url>    - Navigate to URL")
        print("    click <selector>  - Click element")
        print("    type <sel> <text> - Type text")
        print("    record on/off     - Enable/disable action recording")
        print("    info              - Show current page info")
        print("    save              - Save session (if recording)")
        print("    export            - Export session artifact")
        print("    help              - Show this menu")
        print("    quit / exit       - Exit and close")
        print("="*60)
        print("  Browser is ready for manual interaction!")
        print("  Press Ctrl+C or type 'quit' to exit\n")
    
    def execute_command(self, command: str):
        """Execute user commands"""
        parts = command.strip().split()
        if not parts:
            return True
            
        cmd = parts[0].lower()
        
        try:
            if cmd in ["quit", "exit", "q"]:
                return False
                
            elif cmd == "screenshot":
                name = parts[1] if len(parts) > 1 else f"manual_{int(time.time())}"
                if not name.endswith('.png'):
                    name += '.png'
                
                if self.recorder:
                    path = os.path.join(self.recorder.session_dir, name)
                else:
                    path = os.path.join(self.cfg.artifacts_dir, name)
                
                self.page.screenshot(path=path)
                print(f"✓ Screenshot saved: {path}")
                
                if self.recorder:
                    self.recorder.record_action("screenshot", {"filename": name})
                    
            elif cmd == "navigate":
                if len(parts) < 2:
                    print("✗ Error: URL required")
                    return True
                    
                url = parts[1]
                print(f"Navigating to {url}...")
                self.page.goto(url)
                print("✓ Navigation complete")
                
            elif cmd == "click":
                if len(parts) < 2:
                    print("✗ Error: Selector required")
                    return True
                selector = " ".join(parts[1:])
                try:
                    self.page.click(selector)
                    print(f"✓ Clicked: {selector}")
                    if self.recorder:
                        self.recorder.record_action("click", {"selector": selector})
                except Exception as e:
                    print(f"✗ Error clicking: {e}")

            elif cmd == "type":
                if len(parts) < 3:
                    print("✗ Error: Selector and text required")
                    return True
                selector = parts[1]
                text = " ".join(parts[2:])
                try:
                    self.page.fill(selector, text)
                    print(f"✓ Typed: {text}")
                    if self.recorder:
                        self.recorder.record_action("type", {"selector": selector, "text": text})
                except Exception as e:
                    print(f"✗ Error typing: {e}")

            elif cmd == "record":
                if len(parts) < 2:
                    print("✗ Error: Use 'record on' or 'record off'")
                    return True
                    
                if parts[1].lower() == "on":
                    if not self.recorder:
                        self.recorder = SessionRecorder()
                        print(f"✓ Recording started: {self.recorder.session_id}")
                    else:
                        print("✓ Recording already active")
                elif parts[1].lower() == "off":
                    if self.recorder:
                        print("✓ Recording paused")
                    else:
                        print("✓ No active recording")
                        
            elif cmd == "info":
                print(f"\n  Current URL: {self.page.url}")
                print(f"  Title: {self.page.title()}")
                if self.recorder:
                    print(f"  Recording: Active (Session: {self.recorder.session_id})")
                    print(f"  Actions recorded: {len(self.recorder.actions)}")
                else:
                    print(f"  Recording: Inactive")
                print()
                
            elif cmd == "save":
                if not self.recorder:
                    print("✗ Error: No active recording session")
                    return True
                    
                session_file = self.recorder.save_session()
                print(f"✓ Session saved: {session_file}")
                
            elif cmd == "export":
                if not self.recorder:
                    print("✗ Error: No active recording session")
                    return True
                    
                artifact_path = self.recorder.export_artifact()
                print(f"✓ Artifact exported: {artifact_path}")
                
            elif cmd == "help":
                self.show_menu()
                
            else:
                print(f"✗ Unknown command: {cmd}")
                print("  Type 'help' for available commands")
                
        except Exception as e:
            print(f"✗ Error executing command: {str(e)}")
            
        return True
    
    def run(self):
        """Run interactive control loop"""
        self.show_menu()
        self.start_monitoring()
        
        try:
            while self.running:
                try:
                    command = input("\n> ").strip()
                    if not self.execute_command(command):
                        break
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print("\n\nReceived interrupt signal")
                    break
                    
        finally:
            if self.recorder:
                print("\nSaving session before exit...")
                self.recorder.save_session()
                print("Session saved successfully")
            print("\nExiting browser control. Thank you!")

class SessionRecorder:
    def __init__(self, cfg: RuntimeConfig, session_id: str = None):
        self.cfg = cfg
        self.session_id = session_id or str(uuid.uuid4())
        self.actions = []
        self.start_time = time.time()
        self.session_dir = os.path.join(self.cfg.sessions_dir, self.session_id)
        os.makedirs(self.session_dir, exist_ok=True)
        self.log = make_logger(cfg)
        
    def record_action(self, action_type: str, data: Dict[str, Any]):
        """Record an action with timestamp"""
        timestamp = time.time() - self.start_time
        action = {
            "type": action_type,
            "timestamp": timestamp,
            "data": data
        }
        self.actions.append(action)
        self.log(f"Recorded action: {action_type} at {timestamp:.2f}s")
        
    def save_session(self, metadata: Dict[str, Any] = None):
        """Save the recorded session to disk"""
        session_data = {
            "session_id": self.session_id,
            "created_at": datetime.datetime.now().isoformat(),
            "duration": time.time() - self.start_time,
            "actions": self.actions,
            "metadata": metadata or {}
        }
        
        session_file = os.path.join(self.session_dir, "session.json")
        with open(session_file, "w") as f:
            json.dump(session_data, f, indent=2)
        
        self.log(f"Session saved: {session_file}")
        return session_file
    
    def export_artifact(self):
        """Export the entire session as a portable artifact"""
        artifact_path = os.path.join(self.cfg.artifacts_dir, f"{self.session_id}.tar.gz")
        
        # Create tar.gz of session directory
        import tarfile
        with tarfile.open(artifact_path, "w:gz") as tar:
            tar.add(self.session_dir, arcname=self.session_id)
        
        self.log(f"Artifact exported: {artifact_path}")
        return artifact_path

class SessionReplayer:
    def __init__(self, cfg: RuntimeConfig, session_id: str):
        self.cfg = cfg
        self.session_id = session_id
        self.session_dir = os.path.join(self.cfg.sessions_dir, session_id)
        self.session_data = self.load_session()
        self.log = make_logger(cfg)
        
    def load_session(self) -> Dict[str, Any]:
        """Load session data from disk"""
        session_file = os.path.join(self.session_dir, "session.json")
        if not os.path.exists(session_file):
            raise FileNotFoundError(f"Session not found: {session_file}")
        
        with open(session_file, "r") as f:
            return json.load(f)
    
    def replay(self, page, speed: float = 1.0):
        """Replay the recorded session"""
        self.log(f"Replaying session: {self.session_id}")
        actions = self.session_data["actions"]
        
        # Inject overlay at start
        inject_overlay(page)
        
        last_timestamp = 0
        for i, action in enumerate(actions):
            print(f"Executing action {i+1}/{len(actions)}: {action['type']}", end='\r')
            
            # Wait for the appropriate time
            wait_time = (action["timestamp"] - last_timestamp) / speed
            if wait_time > 0:
                time.sleep(wait_time)
            
            # Execute the action
            self._execute_action(page, action)
            last_timestamp = action["timestamp"]
        
        print(f"\nReplay complete - {len(actions)} actions executed")
        self.log("Replay complete")
    
    def _execute_action(self, page, action: Dict[str, Any]):
        """Execute a single recorded action"""
        action_type = action["type"]
        data = action["data"]
        
        # Update UI
        update_overlay(page, action_type, data)
        
        try:
            if action_type == "navigate":
                page.goto(data["url"])
                inject_overlay(page)
            elif action_type == "click":
                # Visual feedback for click
                try:
                    page.eval_on_selector(data["selector"], "el => { el.style.outline = '2px solid #ff0000'; el.style.transition = 'all 0.2s'; }")
                    time.sleep(0.3)
                    page.click(data["selector"])
                    try:
                        page.eval_on_selector(data["selector"], "el => el.style.outline = ''")
                    except:
                        pass
                except:
                    page.click(data["selector"])
            elif action_type == "type":
                page.fill(data["selector"], "")
                page.type(data["selector"], data["text"], delay=50)
            elif action_type == "scroll":
                page.evaluate(f"window.scrollTo({data['x']}, {data['y']})")
                page.evaluate(f"window.scrollTo({{top: {data['y']}, left: {data['x']}, behavior: 'smooth'}})")
            elif action_type == "screenshot":
                screenshot_path = os.path.join(self.session_dir, data["filename"])
                page.screenshot(path=screenshot_path)
            else:
                self.log(f"Unknown action type: {action_type}")
        except Exception as e:
            self.log(f"Error executing action {action_type}: {str(e)}")

def record_session(
    cfg: RuntimeConfig,
    url: str,
    duration: int = 30,
    interactive: bool = False,
    storage_state_path: Optional[str] = None,
):
    """Record a new browser session"""
    recorder = SessionRecorder(cfg)
    log = recorder.log
    
    log(f"Starting session recording: {recorder.session_id}")
    
    with sync_playwright() as p:
        browser, context, page = BrowserRunner(cfg, log).attach(p, storage_state_path=storage_state_path)
        
        # Record initial navigation
        recorder.record_action("navigate", {"url": url})
        page.goto(url, wait_until="domcontentloaded")
        update_overlay(page, "navigate", {"url": url})
        
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except TimeoutError:
            log("networkidle timeout, continuing anyway")
        
        # Take initial screenshot
        screenshot_filename = f"screenshot_0.png"
        screenshot_path = os.path.join(recorder.session_dir, screenshot_filename)
        page.screenshot(path=screenshot_path)
        recorder.record_action("screenshot", {"filename": screenshot_filename})
        update_overlay(page, "screenshot", {"filename": screenshot_filename})
        
        # Record for specified duration
        log(f"Recording for {duration} seconds...")
        start_time = time.time()
        screenshot_count = 1
        
        while time.time() - start_time < duration:
            time.sleep(2)
            
            # Periodic screenshots
            screenshot_filename = f"screenshot_{screenshot_count}.png"
            screenshot_path = os.path.join(recorder.session_dir, screenshot_filename)
            page.screenshot(path=screenshot_path)
            recorder.record_action("screenshot", {"filename": screenshot_filename})
            update_overlay(page, "screenshot", {"filename": screenshot_filename})
            screenshot_count += 1
        
        # Save session data
        metadata = {
            "url": url,
            "duration": duration,
            "screenshots": screenshot_count
        }
        recorder.save_session(metadata)
        
        log(f"Automated recording complete: {recorder.session_id}")
        update_overlay(page, "status", {"text": "Recording complete"})
        print(f"\n✓ Recording complete!")
        print(f"  Session ID: {recorder.session_id}")
        print(f"  Actions recorded: {len(recorder.actions)}")
        
        # Interactive mode
        if interactive:
            print(f"\nEntering interactive mode...")
            controller = InteractiveController(page, cfg, recorder)
            controller.run()
        
        # Export artifact
        artifact_path = recorder.export_artifact()
        
        return recorder.session_id

def replay_session(
    cfg: RuntimeConfig,
    session_id: str,
    speed: float = 1.0,
    interactive: bool = False,
    storage_state_path: Optional[str] = None,
):
    """Replay a previously recorded session"""
    replayer = SessionReplayer(cfg, session_id)
    
    with sync_playwright() as p:
        browser, context, page = BrowserRunner(cfg, replayer.log).attach(p, storage_state_path=storage_state_path)
        
        replayer.replay(page, speed)
        
        # Interactive mode after replay
        if interactive:
            print(f"\nReplay complete! Entering interactive mode...")
            controller = InteractiveController(page, cfg)
            controller.run()
    
    replayer.log(f"Replay complete: {session_id}")

def interactive_mode(cfg: RuntimeConfig, url: str = None, storage_state_path: Optional[str] = None):
    """Start browser in interactive mode"""
    log = make_logger(cfg)
    log("Starting interactive browser control")
    
    with sync_playwright() as p:
        browser, context, page = BrowserRunner(cfg, log).attach(p, storage_state_path=storage_state_path)
        
        if url:
            log(f"Navigating to {url}")
            page.goto(url)
            update_overlay(page, "navigate", {"url": url})
        
        controller = InteractiveController(page, cfg)
        controller.run()

def import_artifact(cfg: RuntimeConfig, artifact_path: str) -> str:
    """Import a previously exported artifact"""
    import tarfile
    
    log = make_logger(cfg)
    log(f"Importing artifact: {artifact_path}")
    
    with tarfile.open(artifact_path, "r:gz") as tar:
        tar.extractall(path=cfg.sessions_dir)
    
    # Extract session_id from artifact filename
    session_id = Path(artifact_path).stem
    log(f"Artifact imported: {session_id}")
    
    return session_id

def list_sessions(cfg: RuntimeConfig):
    """List all available sessions"""
    sessions = []
    for session_dir in os.listdir(cfg.sessions_dir):
        session_path = os.path.join(cfg.sessions_dir, session_dir)
        session_file = os.path.join(session_path, "session.json")
        
        if os.path.exists(session_file):
            with open(session_file, "r") as f:
                session_data = json.load(f)
                sessions.append({
                    "session_id": session_data["session_id"],
                    "created_at": session_data["created_at"],
                    "duration": session_data["duration"],
                    "actions": len(session_data["actions"])
                })
    
    return sessions

def run_artifact(
    cfg: RuntimeConfig,
    url: str,
    interactive: bool = False,
    storage_state_path: Optional[str] = None,
):
    """Original functionality - run a single artifact"""
    artifact_id = str(uuid.uuid4())
    screenshot_path = os.path.join(cfg.artifacts_dir, f"{artifact_id}.png")

    log = make_logger(cfg)
    log(f"artifact_id={artifact_id}")
    log(f"connecting to chromium via CDP")

    with sync_playwright() as p:
        browser, context, page = BrowserRunner(cfg, log).attach(p, storage_state_path=storage_state_path)

        log(f"navigating to {url}")
        page.goto(url, wait_until="domcontentloaded")
        update_overlay(page, "navigate", {"url": url})

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except TimeoutError:
            log("networkidle timeout, continuing anyway")

        page.wait_for_timeout(1000)
        page.screenshot(path=screenshot_path)
        update_overlay(page, "screenshot", {"filename": os.path.basename(screenshot_path)})
        log(f"screenshot saved -> {screenshot_path}")
        log("artifact run complete")
        
        # Interactive mode
        if interactive:
            print(f"\nEntering interactive mode...")
            controller = InteractiveController(page, cfg)
            controller.run()

    return artifact_id

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python run_artifact.py <url> [-i]                      # Run single artifact")
        print("  python run_artifact.py record <url> [duration] [-i]    # Record session")
        print("  python run_artifact.py replay <session_id> [speed] [-i]# Replay session")
        print("  python run_artifact.py import <artifact.tar.gz>        # Import artifact")
        print("  python run_artifact.py list                            # List sessions")
        print("  python run_artifact.py interactive [url]               # Start interactive mode")
        print("\n  Add -i flag to any command for interactive control after execution")
        sys.exit(1)
    
    # Check for interactive flag
    interactive = "-i" in sys.argv or "--interactive" in sys.argv
    args = [arg for arg in sys.argv[1:] if arg not in ["-i", "--interactive"]]
    
    command = args[0]
    cfg = RuntimeConfig.from_env()
    
    if command == "record":
        if len(args) < 2:
            print("Error: URL required for recording")
            sys.exit(1)
        url = args[1]
        duration = int(args[2]) if len(args) > 2 else 30
        session_id = record_session(cfg, url, duration, interactive)
        print(f"\n✓ Session recorded: {session_id}")
        
    elif command == "replay":
        if len(args) < 2:
            print("Error: Session ID required for replay")
            sys.exit(1)
        session_id = args[1]
        speed = float(args[2]) if len(args) > 2 else 1.0
        replay_session(cfg, session_id, speed, interactive)
        
    elif command == "import":
        if len(args) < 2:
            print("Error: Artifact path required for import")
            sys.exit(1)
        artifact_path = args[1]
        session_id = import_artifact(cfg, artifact_path)
        print(f"✓ Artifact imported: {session_id}")
        
    elif command == "list":
        sessions = list_sessions(cfg)
        print(f"\n Found {len(sessions)} sessions:\n")
        for session in sessions:
            print(f"  ID: {session['session_id']}")
            print(f"  Created: {session['created_at']}")
            print(f"  Duration: {session['duration']:.2f}s")
            print(f"  Actions: {session['actions']}")
            print()
            
    elif command == "interactive":
        url = args[1] if len(args) > 1 else None
        interactive_mode(cfg, url)
        
    else:
        # Default behavior - run single artifact
        url = command
        run_artifact(cfg, url, interactive)

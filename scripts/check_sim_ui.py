"""Offline-server Edge smoke check. Always closes its browser and port-8099 child server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stalk.sim.panic_replay import load_cache, stamp


def main():
    # Refuse to interfere with anything already using the requested port.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8099))
    grouped, _, _ = load_cache()
    days = sorted(grouped['NIFTY 50'])
    panic_days = [d for i, d in enumerate(days[1:], 1)
                  if grouped['NIFTY 50'][d][0][1] / grouped['NIFTY 50'][days[i-1]][-1][4] - 1 < -.005]
    env = os.environ.copy()
    env['STALK_NO_BOOT'] = '1'
    log = (ROOT / 'data/ui_check_sim_server.log').open('w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'stalk.api.server:app', '--host', '127.0.0.1', '--port', '8099', '--log-level', 'warning'],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    result = {}
    try:
        with httpx.Client(base_url='http://127.0.0.1:8099', timeout=100) as client:
            for _ in range(100):
                try:
                    if client.get('/api/sim').status_code == 200:
                        break
                except httpx.ConnectError:
                    time.sleep(.2)
            else:
                raise RuntimeError('Server failed to start')
            for _ in range(30):
                pick = client.post('/api/sim/new'); pick.raise_for_status()
                state = client.get('/api/sim').json()
                active = [d for d in panic_days if d in [r['day'] for r in state['days']]]
                if active:
                    break
            else:
                raise RuntimeError('No trading week drawn')
            day = active[0]
            client.post('/api/sim/control', json={'action': 'day', 'value': day}).raise_for_status()
            client.post('/api/sim/control', json={'action': 'speed', 'value': 1200}).raise_for_status()
            client.post('/api/sim/control', json={'action': 'play'}).raise_for_status()
            time.sleep(2)
            client.post('/api/sim/control', json={'action': 'pause'}).raise_for_status()
            state = client.get('/api/sim').json()
            assert state['positions'] or [t for t in state['trades'] if t['day'] == day]
            if "--native" in sys.argv:
                print("UI_READY " + json.dumps({"week": state["week"], "day": day, "clock": state["clock"]}), flush=True)
                input("Press enter after native inspection to stop the server: ")
                result = {"week": state["week"], "day": day, "clock": state["clock"], "method": "native Brave fallback; Playwright Edge blocked", "visual_verification": "not asserted; inspect separately"}
            else:
                env["PLAYWRIGHT_PACKAGE"] = str(Path(playwright.__file__).parent / "driver" / "package")
                env["SIM_CHECK_STATE"] = json.dumps({**state, "panic_days": panic_days})
                browser_check = subprocess.run(["node", "scripts/check_sim_ui.cjs"], cwd=ROOT, env=env,
                                               capture_output=True, text=True, encoding="utf-8", timeout=100)
                if browser_check.returncode:
                    raise RuntimeError(browser_check.stderr or browser_check.stdout)
                result = json.loads(browser_check.stdout.strip().splitlines()[-1])

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=5)
        log.close()
        result['server_stopped'] = proc.poll() is not None
        (ROOT / 'data/ui_check_sim.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

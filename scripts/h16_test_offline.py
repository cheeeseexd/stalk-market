"""Run the whole suite without network; allow only Windows asyncio's socketpair."""
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    out = ROOT/'data/h16'
    out.mkdir(parents=True, exist_ok=True)
    local = threading.local()
    original_pair, original_connect = socket.socketpair, socket.socket.connect
    pair_calls, blocked = [], []

    def socketpair(*args, **kwargs):
        local.pair = True
        try:
            pair_calls.append(1)
            return original_pair(*args, **kwargs)
        finally:
            local.pair = False

    def connect(sock, address):
        if getattr(local, 'pair', False) and address[0] in ('127.0.0.1', '::1'):
            return original_connect(sock, address)
        blocked.append(repr(address))
        raise AssertionError('Network disabled for offline tests')

    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'), top_level_dir=str(ROOT))
    with (out/'unit_tests.log').open('w', encoding='utf-8') as stream, ExitStack() as stack:
        stack.enter_context(patch.object(socket, 'socketpair', socketpair))
        stack.enter_context(patch.object(socket.socket, 'connect', connect))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('No network')))
        stack.enter_context(patch.object(socket, 'create_connection', side_effect=AssertionError('No network')))
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    summary = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped), success=result.wasSuccessful(),
                   network='Blocked; only socketpair-internal loopback allowed for Windows asyncio',
                   socketpair_calls=len(pair_calls), unexpected_connect_attempts=blocked)
    (out/'unit_test_exit.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary))
    if not result.wasSuccessful():
        print((out/'unit_tests.log').read_text(encoding='utf-8')[-12000:])
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())

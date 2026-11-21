import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apb.http import RelayError
from apb.mcp_server import Adapter, serve


def request(request_id, method, **params):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


class StdioTest(unittest.TestCase):
    def test_non_utf8_parent_pipe(self):
        # Mock only the Relay so the subprocess exercises the real stdio entry point.
        launcher = (
            "from unittest.mock import patch\n"
            "from apb.mcp_server import main\n"
            "with patch('apb.mcp_server.read_secret'), patch('apb.mcp_server.RelayClient') as relay:\n"
            "    relay.return_value.call.return_value = {'label': '\\u4e2d\\u6587 \\U0001f680'}\n"
            "    main()\n"
        )
        call = request(1, "tools/call", name="list_peers", arguments={})
        call["params"]["_meta"] = {"label": "\u4e2d\u6587 \U0001f680"}
        data = json.dumps(call, ensure_ascii=False).encode() + b"\n\xff\n"
        data += json.dumps(request(2, "ping")).encode() + b"\n"
        result = subprocess.run(
            [sys.executable, "-c", launcher],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONIOENCODING": "cp1252", "APB_RELAY_URL": "http://127.0.0.1:8787",
                 "APB_CALLER_TOKEN_FILE": "unused"},
            input=data, capture_output=True, check=True, timeout=10,
        )
        response, malformed, ping = map(json.loads, result.stdout.splitlines())
        self.assertEqual(json.loads(response["result"]["content"][0]["text"])["label"],
                         "\u4e2d\u6587 \U0001f680")
        self.assertEqual(malformed["error"]["code"], -32700)
        self.assertEqual(ping["result"], {})

    def test_invalid_params_do_not_close_connection(self):
        bad_params = {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}
        bad_args = request(2, "tools/call", name="list_peers", arguments=1)
        ping = request(3, "ping")
        stdin = io.StringIO("".join(json.dumps(item) + "\n" for item in (bad_params, bad_args, ping)))
        stdout = io.StringIO()
        serve(SimpleNamespace(call=Mock()), stdin, stdout)
        first, second, third = map(json.loads, stdout.getvalue().splitlines())
        self.assertEqual(first["error"]["code"], -32602)
        self.assertEqual(second["error"]["code"], -32602)
        self.assertEqual(third["result"], {})

    @patch("apb.mcp_server.time.sleep")
    def test_failed_status_poll_keeps_accepted_request_id(self, _sleep):
        accepted = {"id": "req_example", "status": "queued", "result": None, "is_error": False}
        relay = Mock()
        relay.call.side_effect = [accepted, RelayError("transport", "offline")]
        result = Adapter(relay).request("peer", "start_session", {}, "client-example")
        self.assertEqual(result["id"], accepted["id"])
        self.assertEqual(result["status"], "queued")
        self.assertIn("request_status", result["note"])
        self.assertEqual([call.args[0] for call in relay.call.call_args_list], ["POST", "GET"])

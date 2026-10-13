"""The developer examples are code people copy, so they are tested like code.

Each example is loaded from ``examples/`` and run in process against the real pieces: the MCP
example talks HTTP to the real MCP application, the voice example talks HTTP to the real ASR
worker application (with the test engine in place of the model), and the evaluation example runs
the real runner against the test gateway.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from forge_integrations.asr_service.app import create_app
from forge_integrations.config import McpSettings

from .helpers import HAVE_FFMPEG, FakeEngine, make_wav
from .mcp_helpers import McpHarness, serve_app
from .test_asr_service import TOKEN, settings_for  # names only: the test classes are not imported

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load_example(relative: str):
    path = EXAMPLES / relative
    spec = importlib.util.spec_from_file_location("example_" + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(module, argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err):  # argparse prints usage errors to the real stderr
        code = module.main(argv, env=env or {}, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def generous() -> McpSettings:
    return McpSettings(
        rate_limit_default_per_minute=100_000,
        rate_limit_generate_per_minute=100_000,
        rate_limit_project_per_minute=100_000,
        rate_limit_requests_per_minute=100_000,
    )


class McpStdlibExampleTests(unittest.TestCase):
    def setUp(self):
        self.example = load_example("mcp/call_tools.py")
        self.h = McpHarness(settings=generous())
        self.token = self.h.issue_all().token

    def env(self, **extra):
        return {"FORGE_MCP_URL": f"http://127.0.0.1:{self.h.port}/mcp", "FORGE_MCP_TOKEN": self.token, **extra}

    def test_list_and_call_work_in_both_protocol_eras(self):
        with self.h.serve():
            for era in ("modern", "legacy"):
                with self.subTest(era=era):
                    code, out, err = run(self.example, ["--era", era, "list"], self.env())
                    self.assertEqual((code, err), (0, ""))
                    self.assertEqual([line.split("\t")[0] for line in out.splitlines()][:3], ["get_model_info", "generate", "chat"])
                    code, out, err = run(self.example, ["--era", era, "call", "get_model_info"], self.env())
                    self.assertEqual(code, 0, err)
                    self.assertEqual(json.loads(out)["llm"]["repo_id"], "NCAIR1/N-ATLaS")
                    code, out, err = run(self.example, ["--era", era, "call", "generate", "--args", '{"prompt": "Say hello"}'], self.env())
                    self.assertEqual(code, 0, err)
                    self.assertEqual(json.loads(out)["text"], "scripted reply")

    def test_a_tool_error_is_shown_and_exits_one(self):
        with self.h.serve():
            code, out, err = run(self.example, ["call", "generate", "--args", '{"prompt": ""}'], self.env())
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"]["code"], "invalid_argument")

    def test_a_wrong_token_exits_one_without_echoing_it(self):
        bad = "fmcp_deadbeef_" + "z" * 43
        with self.h.serve():
            code, out, err = run(self.example, ["list"], self.env(FORGE_MCP_TOKEN=bad))
        self.assertEqual(code, 1)
        self.assertIn("401", err)
        self.assertNotIn(bad, out + err)

    def test_usage_errors_exit_two(self):
        self.assertEqual(run(self.example, ["list"], {})[0], 2)  # no token
        self.assertEqual(run(self.example, ["call", "x", "--args", "[1]"], {"FORGE_MCP_TOKEN": "t"})[0], 2)
        self.assertEqual(run(self.example, ["call", "x", "--args", "{oops"], {"FORGE_MCP_TOKEN": "t"})[0], 2)
        code, _, err = run(self.example, ["list"], {"FORGE_MCP_TOKEN": "t", "FORGE_MCP_URL": "http://127.0.0.1:9/mcp"})
        self.assertEqual(code, 1)
        self.assertIn("could not reach the server", err)

    def test_the_client_config_files_are_valid_json_with_no_secret_in_them(self):
        for name in ("claude-code.mcp.json", "cursor.mcp.json", "vscode.mcp.json"):
            with self.subTest(file=name):
                text = (EXAMPLES / "mcp" / "configs" / name).read_text(encoding="utf-8")
                document = json.loads(text)
                self.assertNotIn("fmcp_", text)  # tokens come from the environment or a prompt
                self.assertTrue(document.get("mcpServers") or document.get("servers"))


class EvaluationExampleTests(unittest.TestCase):
    def setUp(self):
        self.example = load_example("evaluation/run_dataset.py")

    def test_dry_run_plans_without_calling_a_model(self):
        code, out, err = run(self.example, ["--starter", "starter-nigerian-languages", "--dry-run"])
        self.assertEqual(code, 0, err)
        plan = json.loads(out)
        self.assertEqual((plan["cases"], plan["model_calls"]), (15, 15))
        self.assertEqual(plan["languages"], ["en", "ha", "ig", "yo"])
        self.assertIn("Dry run", err)

    def test_a_real_run_writes_json_and_csv_and_says_pass_rate_is_not_accuracy(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"EVAL_GATEWAY_FACTORY": "tests.cli_factories:make_gateway"}
            code, out, err = run(self.example, ["--starter", "starter-general-checks", "--out", tmp, "--prompt-version", "v1"], env)
            self.assertEqual(code, 0, err)
            summary = json.loads(out)
            files = sorted(p.suffix for p in Path(tmp).iterdir())
            self.assertEqual(files, [".csv", ".json"])
            self.assertEqual(summary["status"], "completed")
            self.assertIn("not model accuracy", err)
            document = json.loads(next(Path(tmp).glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(document["prompt_version"], "v1")
            self.assertEqual(len(document["results"]), 8)

    def test_problems_are_reported_plainly(self):
        code, _, err = run(self.example, ["--starter", "starter-general-checks"], {})
        self.assertEqual(code, 2)
        self.assertIn("EVAL_GATEWAY_FACTORY", err)
        code, _, err = run(self.example, ["--starter", "starter-general-checks"], {"EVAL_GATEWAY_FACTORY": "tests.cli_factories:broken_factory"})
        self.assertEqual(code, 2)
        self.assertIn("RuntimeError", err)
        self.assertNotIn("hunter2", err)  # the factory's own message is never shown
        self.assertEqual(run(self.example, ["--starter", "no-such-set", "--dry-run"])[0], 2)

    def test_an_invalid_dataset_file_lists_every_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"name": "x", "cases": [{"name": "c", "prompt": "p", "checks": [{"type": "nope"}]}], "extra": 1}), encoding="utf-8")
            code, _, err = run(self.example, ["--dataset", str(path), "--dry-run"])
            self.assertEqual(code, 2)
            self.assertIn("extra", err)
            path.write_text("{not json", encoding="utf-8")
            code, _, err = run(self.example, ["--dataset", str(path), "--dry-run"])
            self.assertEqual(code, 2)
            self.assertIn("not valid JSON", err)


class VoiceExampleTests(unittest.TestCase):
    def setUp(self):
        self.example = load_example("voice/transcribe_file.py")
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def env(self, port: int) -> dict[str, str]:
        return {
            "ASR_SERVICE_URL": f"http://127.0.0.1:{port}",
            "ASR_SERVICE_TOKEN": TOKEN,
            "ALLOW_PRIVATE_NETWORK_TARGETS": "true",
            "ALLOW_INSECURE_HTTP_TARGETS": "true",
        }

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg is not installed")
    def test_a_wav_file_is_transcribed_through_the_real_worker_application(self):
        clip = self.tmp / "clip.wav"
        clip.write_bytes(make_wav(1.0))
        app = create_app(settings_for(str(self.tmp)), FakeEngine())
        with serve_app(app) as (_, _, port):
            code, out, err = run(self.example, [str(clip), "--language", "yo"], self.env(port))
            self.assertEqual(code, 0, err)
            self.assertEqual(out.strip(), "ẹ káàbọ̀")
            self.assertIn("NCAIR1/Yoruba-ASR", err)
            self.assertNotIn(TOKEN, out + err)
            code, out, err = run(self.example, [str(clip), "--language", "yo", "--json"], self.env(port))
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["transcription"]["model"], "NCAIR1/Yoruba-ASR")

    def test_bad_input_is_refused_before_any_request_is_made(self):
        env = self.env(9)  # nothing listens here: a request would fail differently
        junk = self.tmp / "notes.wav"
        junk.write_bytes(b"this is not audio at all")
        code, _, err = run(self.example, [str(junk), "--language", "yo"], env)
        self.assertEqual(code, 1)
        self.assertIn("audio_unsupported_type", err)
        long_clip = self.tmp / "long.wav"
        long_clip.write_bytes(make_wav(31.0))
        code, _, err = run(self.example, [str(long_clip), "--language", "yo"], env)
        self.assertEqual(code, 1)
        self.assertIn("audio_too_long", err)
        mismatched = self.tmp / "clip.wav"
        mismatched.write_bytes(make_wav(1.0))
        code, _, err = run(self.example, [str(mismatched), "--language", "yo", "--mime-type", "audio/mpeg"], env)
        self.assertEqual(code, 1)
        self.assertIn("audio_signature_mismatch", err)

    def test_configuration_problems_exit_two(self):
        clip = self.tmp / "clip.wav"
        clip.write_bytes(make_wav(1.0))
        self.assertEqual(run(self.example, [str(clip), "--language", "yo"], {})[0], 2)
        self.assertEqual(run(self.example, [str(self.tmp / "missing.wav"), "--language", "yo"], self.env(9))[0], 2)
        self.assertEqual(run(self.example, [str(clip), "--language", "fr"], self.env(9))[0], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

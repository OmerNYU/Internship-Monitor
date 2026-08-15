import json
from contextlib import redirect_stdout
from io import StringIO
from unittest import TestCase

import httpx
from pydantic import ValidationError

from internship_monitor.cli import main
from internship_monitor.config import OllamaConfiguration
from internship_monitor.intelligence import (
    OllamaHealthProvider,
    ProviderHealthStatus,
)


class IntelligenceProviderTests(TestCase):
    def test_disabled_provider_does_not_make_a_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(500)

        health = OllamaHealthProvider(
            OllamaConfiguration(),
            enabled=False,
            transport=httpx.MockTransport(handler),
        ).health()

        self.assertEqual(health.status, ProviderHealthStatus.DISABLED)
        self.assertEqual(requests, [])

    def test_available_ollama_reports_version_and_installed_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.12.6"})
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={"models": [{"name": "qwen3-embedding:0.6b"}, {"name": "qwen3:4b"}]},
                )
            return httpx.Response(404)

        health = OllamaHealthProvider(
            OllamaConfiguration(),
            enabled=True,
            transport=httpx.MockTransport(handler),
        ).health()

        self.assertTrue(health.is_available)
        self.assertEqual(health.version, "0.12.6")
        self.assertEqual(health.installed_models, ("qwen3-embedding:0.6b", "qwen3:4b"))

    def test_unreachable_or_malformed_ollama_is_non_throwing_unavailable(self) -> None:
        def unavailable(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline")

        def malformed(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        unavailable_health = OllamaHealthProvider(
            OllamaConfiguration(),
            enabled=True,
            transport=httpx.MockTransport(unavailable),
        ).health()
        malformed_health = OllamaHealthProvider(
            OllamaConfiguration(),
            enabled=True,
            transport=httpx.MockTransport(malformed),
        ).health()

        self.assertEqual(unavailable_health.status, ProviderHealthStatus.UNAVAILABLE)
        self.assertEqual(malformed_health.status, ProviderHealthStatus.UNAVAILABLE)

    def test_ollama_configuration_accepts_local_loopback_and_private_ip_origins(self) -> None:
        for base_url in (
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://[::1]:11434",
            "http://172.25.112.1:11435",
            "http://192.168.1.10:11434",
            "http://10.0.0.5:11434",
        ):
            with self.subTest(base_url=base_url):
                self.assertEqual(OllamaConfiguration(base_url=base_url).base_url, base_url)

    def test_ollama_configuration_rejects_non_local_or_non_origin_urls(self) -> None:
        for base_url in (
            "https://ollama.com",
            "http://8.8.8.8:11434",
            "http://example.com:11434",
            "http://user:password@127.0.0.1:11434",
            "http://127.0.0.1:11434/api",
            "http://127.0.0.1:11434?probe=true",
            "http://127.0.0.1:11434#fragment",
            "http://[::1",
        ):
            with self.subTest(base_url=base_url), self.assertRaises(ValidationError):
                OllamaConfiguration(base_url=base_url)

    def test_cli_reports_disabled_intelligence_without_contacting_ollama(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["intelligence-status"])

        self.assertEqual(exit_code, 0)
        self.assertIn("status=disabled", output.getvalue())
        self.assertIn("no local health check was attempted", output.getvalue())

        json_output = StringIO()
        with redirect_stdout(json_output):
            exit_code = main(["intelligence-status", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(json_output.getvalue())["status"], "disabled")

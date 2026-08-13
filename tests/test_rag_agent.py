import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import httpx

from internship_monitor.analysis import DeterministicAssessor, RoleMatchLevel
from internship_monitor.config import load_search_configuration
from internship_monitor.evaluation import load_gold_cases
from internship_monitor.intelligence import (
    CorpusError,
    CorpusKind,
    LocalRagRetriever,
    OllamaAdjudicationClient,
    RetrievedContext,
    build_corpus_index,
    load_private_documents,
)

PROJECT_ROOT = Path(__file__).parents[1]
FIXTURE = PROJECT_ROOT / "evaluation" / "gold.example.v1.jsonl"


class FakeEmbeddingClient:
    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors = {
            "alpha evidence": (1.0, 0.0),
            "beta evidence": (0.0, 1.0),
            "balanced query": (1.0, 1.0),
        }
        return tuple(vectors.get(text.strip(), (1.0, 1.0)) for text in texts)


class StaticRetriever:
    def retrieve(self, query, *, kinds=None, limit=4):
        return (RetrievedContext("synthetic-profile", CorpusKind.PROFILE, 0, "Python API", 0.9),)


class RagAndAgentTests(TestCase):
    def _configuration(self):
        return load_search_configuration(PROJECT_ROOT / "config/profile.example.yaml")

    def test_private_corpus_validation_and_deterministic_retrieval(self) -> None:
        with TemporaryDirectory() as directory:
            corpus = Path(directory) / "corpus"
            corpus.mkdir()
            (corpus / "alpha.md").write_text(
                "---\n"
                "schema_version: 1\n"
                "id: alpha\n"
                "kind: profile\n"
                "title: Alpha\n"
                "---\n"
                "alpha evidence\n"
            )
            (corpus / "beta.md").write_text(
                "---\nschema_version: 1\nid: beta\nkind: project\ntitle: Beta\n---\nbeta evidence\n"
            )
            self.assertEqual(len(load_private_documents(corpus)), 2)
            count = build_corpus_index(
                configuration=self._configuration(),
                corpus_dir=corpus,
                index_path=Path(directory) / "rag.sqlite3",
                embedding_cache_path=Path(directory) / "embeddings.sqlite3",
                client=FakeEmbeddingClient(),
            )
            retriever = LocalRagRetriever(
                configuration=self._configuration(),
                index_path=Path(directory) / "rag.sqlite3",
                embedding_cache_path=Path(directory) / "embeddings.sqlite3",
                client=FakeEmbeddingClient(),
            )
            results = retriever.retrieve(
                "balanced query", kinds=(CorpusKind.PROFILE, CorpusKind.PROJECT), limit=2
            )
        self.assertGreater(count, 2)
        self.assertEqual([item.document_id for item in results[:2]], ["alpha", "beta"])

    def test_private_corpus_rejects_unknown_fields(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.md"
            path.write_text(
                "---\n"
                "schema_version: 1\n"
                "id: bad\n"
                "kind: profile\n"
                "title: Bad\n"
                "unexpected: value\n"
                "---\n"
                "body\n"
            )
            with self.assertRaisesRegex(CorpusError, "invalid corpus front matter"):
                load_private_documents(Path(directory))

    def test_agent_client_uses_only_local_read_only_tools_and_requires_citations(self) -> None:
        listing = load_gold_cases(FIXTURE)[0].listing.model_copy(
            update={"title": "Platform Intern", "description": "Python internship evidence."}
        )
        assessment = DeterministicAssessor(self._configuration()).assess(listing)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
            if (
                request.url.path == "/api/chat"
                and len([item for item in requests if item.url.path == "/api/chat"]) == 1
            ):
                return httpx.Response(
                    200,
                    json={
                        "message": {
                            "tool_calls": [
                                {"function": {"name": "retrieve_profile_context", "arguments": {}}}
                            ]
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps(
                            {
                                "role_level": "relevant",
                                "confidence": 0.9,
                                "evidence": ["Python"],
                                "context_ids": ["synthetic-profile"],
                            }
                        )
                    }
                },
            )

        client = OllamaAdjudicationClient(
            self._configuration(), transport=httpx.MockTransport(handler)
        )
        verdict, calls, contexts = client.adjudicate(listing, assessment, StaticRetriever())

        self.assertEqual(verdict.role_level, RoleMatchLevel.RELEVANT)
        self.assertEqual(calls, ("retrieve_profile_context",))
        self.assertEqual(contexts[0].document_id, "synthetic-profile")
        self.assertEqual(
            [item.url.path for item in requests], ["/api/tags", "/api/chat", "/api/chat"]
        )
        self.assertIn("format", json.loads(requests[1].content))

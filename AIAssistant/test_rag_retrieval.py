"""Small deterministic checks for RAG ranking policies."""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from modules import rag_module
from modules.rag_module import ChunkResult, _format_context_block, _rrf_fuse


class TestReciprocalRankFusion(unittest.TestCase):
    def test_agreement_outranks_single_retriever_hit(self):
        # Chunk 2 is supported by both retrievers; chunk 1 only by semantic.
        ranked = _rrf_fuse([[1, 2], [2, 3]], [0.55, 0.45])
        self.assertEqual(ranked[0][0], 2)

    def test_ignores_invalid_chunk_ids(self):
        ranked = _rrf_fuse([[-1, 7]], [0.55])
        self.assertEqual([chunk_id for chunk_id, _ in ranked], [7])

    def test_context_uses_private_evidence_labels_without_filenames(self):
        context = _format_context_block([
            ChunkResult(
                text="[Tai lieu TXT: Docs/people.txt]\nChris Hoang is the DevManager.",
                source_path="Docs/people.txt", loader_type="tai_lieu_txt",
            ),
        ], "PROJECT DOCUMENT EVIDENCE")

        self.assertIn("Evidence 1", context)
        self.assertIn("Chris Hoang is the DevManager.", context)
        self.assertNotIn("people.txt", context)
        self.assertNotIn("TÀI LIỆU THAM KHẢO", context)

    def test_hybrid_retrieval_honours_candidate_budget(self):
        class _Node:
            def __init__(self, chunk_index):
                self.metadata = {"chunk_index": chunk_index}

        class _Hit:
            def __init__(self, chunk_index):
                self.node = _Node(chunk_index)
                self.score = 1.0

        class _Retriever:
            def __init__(self, hits):
                self.hits = hits

            def retrieve(self, _query):
                return self.hits

        original = (rag_module.vector_retriever, rag_module.bm25_retriever,
                    rag_module.knowledge_chunks)
        try:
            rag_module.vector_retriever = _Retriever([_Hit(0), _Hit(1)])
            rag_module.bm25_retriever = _Retriever([_Hit(2), _Hit(1)])
            rag_module.knowledge_chunks = ["dense", "shared", "sparse"]
            self.assertEqual(
                rag_module.hybrid_retrieve("query", k=1, final_k=3),
                ["dense", "sparse"],
            )
        finally:
            (rag_module.vector_retriever, rag_module.bm25_retriever,
             rag_module.knowledge_chunks) = original


if __name__ == "__main__":
    unittest.main()

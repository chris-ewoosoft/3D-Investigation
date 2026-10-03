"""Embedding model adapters."""
from __future__ import annotations


class E5LlamaEmbedding:
    """Adapter that lets LlamaIndex use the application's E5 encoder/prefixes."""

    def __init__(self, model, query_prefix: str, passage_prefix: str):
        from llama_index.core.embeddings import BaseEmbedding
        
        # Capture prefixes in the closure so we don't depend on global config
        _q_prefix = query_prefix
        _p_prefix = passage_prefix

        class E5Embedding(BaseEmbedding):
            model: object

            def _get_query_embedding(self, query: str):
                return self.model.encode(_q_prefix + query, normalize_embeddings=True).tolist()

            async def _aget_query_embedding(self, query: str):
                return self._get_query_embedding(query)

            def _get_text_embedding(self, text: str):
                return self.model.encode(_p_prefix + text, normalize_embeddings=True).tolist()

            def _get_text_embeddings(self, texts: list[str]):
                return self.model.encode(
                    [_p_prefix + text for text in texts], 
                    normalize_embeddings=True
                ).tolist()

        self.value = E5Embedding(model=model)

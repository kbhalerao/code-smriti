"""
Embedding Generator using nomic-embed-text via Ollama
Generates 768-dimensional embeddings for code and documentation
"""

from typing import List, Union
import os
import numpy as np
import requests
from loguru import logger

from config import WorkerConfig
from parsers.code_parser import CodeChunk
from parsers.document_parser import DocumentChunk

config = WorkerConfig()


class EmbeddingGenerator:
    """
    Generates embeddings for code and documentation chunks using Ollama API
    """

    def __init__(self):
        """Initialize connection to Ollama"""
        # Use localhost for native execution, docker will use host.docker.internal
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        # Extract just the model name (nomic-embed-text) from full identifier
        self.model_name = "nomic-embed-text"

        logger.info(f"Initializing Ollama embedding client: {self.ollama_host}")

        # Test connection
        try:
            response = requests.get(f"{self.ollama_host}/api/tags", timeout=5)
            response.raise_for_status()
            logger.info(f"✓ Connected to Ollama (model: {self.model_name}, dims: {config.embedding_dimensions})")
        except Exception as e:
            logger.error(f"Failed to connect to Ollama at {self.ollama_host}: {e}")
            raise

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text using Ollama API

        Args:
            text: Input text

        Returns:
            List of floats representing the embedding vector
        """
        try:
            # Add task instruction prefix for document embedding
            text_with_prefix = f"search_document: {text}"

            response = requests.post(
                f"{self.ollama_host}/api/embeddings",
                json={
                    "model": self.model_name,
                    "prompt": text_with_prefix
                },
                timeout=30
            )
            response.raise_for_status()
            embedding = response.json().get("embedding", [])

            if len(embedding) != config.embedding_dimensions:
                logger.warning(f"Expected {config.embedding_dimensions} dims, got {len(embedding)}")

            return embedding
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return [0.0] * config.embedding_dimensions

    def prepare_text_for_embedding(
        self,
        chunk: Union[CodeChunk, DocumentChunk]
    ) -> str:
        """
        Prepare text from a chunk for embedding generation
        For code chunks, we might want to include metadata
        For document chunks, just the content

        Args:
            chunk: CodeChunk or DocumentChunk

        Returns:
            Prepared text string
        """
        if isinstance(chunk, CodeChunk):
            # For code, include the code text and potentially function/class name
            text = chunk.code_text

            # Optionally prepend with metadata for better context
            if chunk.chunk_type == "function" and "function_name" in chunk.metadata:
                text = f"Function: {chunk.metadata['function_name']}\n{text}"
            elif chunk.chunk_type == "class" and "class_name" in chunk.metadata:
                text = f"Class: {chunk.metadata['class_name']}\n{text}"

            return text

        elif isinstance(chunk, DocumentChunk):
            # For documents, use the content
            return chunk.content

        return ""

    async def generate_embeddings(
        self,
        chunks: List[Union[CodeChunk, DocumentChunk]],
        batch_size: int = 16  # Ollama can handle more concurrent requests
    ) -> None:
        """
        Generate embeddings for a list of chunks using Ollama API
        Updates the chunks in-place with their embeddings

        Args:
            chunks: List of CodeChunk or DocumentChunk objects
            batch_size: Number of chunks to process at once (not used with sequential Ollama calls)
        """
        if not chunks:
            return

        logger.info(f"Generating embeddings for {len(chunks)} chunks using Ollama")
        total = len(chunks)

        try:
            # Process each chunk sequentially (Ollama API doesn't support batch requests)
            for i, chunk in enumerate(chunks, 1):
                # Log progress every 100 chunks
                if i % 100 == 0 or i == 1:
                    logger.info(f"Processing chunk {i}/{total} ({(i/total)*100:.1f}%)")

                # Prepare text for this chunk
                text = self.prepare_text_for_embedding(chunk)

                # Add task instruction prefix
                text_with_prefix = f"search_document: {text}"

                # Generate embedding via Ollama API
                try:
                    response = requests.post(
                        f"{self.ollama_host}/api/embeddings",
                        json={
                            "model": self.model_name,
                            "prompt": text_with_prefix
                        },
                        timeout=30
                    )
                    response.raise_for_status()
                    embedding = response.json().get("embedding", [])

                    if len(embedding) != config.embedding_dimensions:
                        logger.warning(f"Chunk {i}: Expected {config.embedding_dimensions} dims, got {len(embedding)}")

                    chunk.embedding = embedding

                except Exception as e:
                    logger.error(f"Error generating embedding for chunk {i}: {e}")
                    chunk.embedding = [0.0] * config.embedding_dimensions

            logger.info(f"✓ Generated embeddings for {len(chunks)} chunks")

        except Exception as e:
            logger.error(f"Error generating embeddings: {e}", exc_info=True)
            # Assign zero vectors as fallback for remaining chunks
            for chunk in chunks:
                if not hasattr(chunk, 'embedding') or chunk.embedding is None:
                    chunk.embedding = [0.0] * config.embedding_dimensions

    def compute_similarity(
        self,
        embedding1: List[float],
        embedding2: List[float]
    ) -> float:
        """
        Compute cosine similarity between two embeddings

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Similarity score between 0 and 1
        """
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)

        # Cosine similarity
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        similarity = dot_product / (norm1 * norm2)

        return float(similarity)

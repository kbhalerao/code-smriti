"""
Embedding Generator using nomic-embed-text
Generates 768-dimensional embeddings for code and documentation
"""

from typing import List, Union
import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer

from config import WorkerConfig
from parsers.code_parser import CodeChunk
from parsers.document_parser import DocumentChunk

config = WorkerConfig()


class EmbeddingGenerator:
    """
    Generates embeddings for code and documentation chunks
    """

    def __init__(self):
        """Initialize the embedding model"""
        logger.info(f"Loading embedding model: {config.embedding_model}")

        self.model = SentenceTransformer(
            config.embedding_model,
            trust_remote_code=True,
            revision='7710840340a098cfb869c4f65e87cf2b1b70caca'
        )

        logger.info(f"✓ Embedding model loaded (dims: {config.embedding_dimensions})")

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text

        Args:
            text: Input text

        Returns:
            List of floats representing the embedding vector
        """
        try:
            # Add task instruction prefix for document embedding
            text_with_prefix = f"search_document: {text}"
            embedding = self.model.encode(text_with_prefix, convert_to_tensor=False)
            return embedding.tolist()
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
        batch_size: int = 8  # Conservative for Docker memory limits
    ) -> None:
        """
        Generate embeddings for a list of chunks in batches
        Updates the chunks in-place with their embeddings

        Args:
            chunks: List of CodeChunk or DocumentChunk objects
            batch_size: Number of chunks to process at once
        """
        if not chunks:
            return

        logger.info(f"Generating embeddings for {len(chunks)} chunks (batch_size={batch_size})")
        total_batches = (len(chunks) + batch_size - 1) // batch_size

        try:
            # Process in batches to avoid preparing all texts at once (memory optimization)
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i:i + batch_size]
                batch_num = i // batch_size + 1

                # Log progress every 100 batches
                if batch_num % 100 == 0 or batch_num == 1:
                    logger.info(f"Processing batch {batch_num}/{total_batches} ({(batch_num/total_batches)*100:.1f}%)")

                # Prepare texts for this batch only
                batch_texts = [self.prepare_text_for_embedding(chunk) for chunk in batch_chunks]

                # Add task instruction prefix for document embedding
                prefixed_batch = [f"search_document: {text}" for text in batch_texts]

                # Generate embeddings for the batch
                batch_embeddings = self.model.encode(
                    prefixed_batch,
                    convert_to_tensor=False,
                    show_progress_bar=False
                )

                # Assign embeddings immediately to free memory
                for chunk, embedding in zip(batch_chunks, batch_embeddings):
                    chunk.embedding = embedding.tolist()

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

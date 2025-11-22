"""
Local Embedding Generator using sentence-transformers
Provides fast batch processing with GPU/MPS acceleration
Uses same model as Ollama for compatibility
"""

from typing import List, Union
import os
import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer

from config import WorkerConfig
from parsers.code_parser import CodeChunk
from parsers.document_parser import DocumentChunk
from parsers.commit_parser import CommitChunk

config = WorkerConfig()


class LocalEmbeddingGenerator:
    """
    Generates embeddings using sentence-transformers library
    Optimized for batch processing and GPU/MPS acceleration
    """

    def __init__(self):
        """Initialize the embedding model"""
        logger.info("Initializing local embedding generator")

        # Disable tokenizer parallelism to avoid warnings when forking git processes
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        # Use nomic-ai model optimized for code/text retrieval
        # nomic-embed-text-v1.5 (768 dimensions)
        # Better semantic understanding for code compared to all-mpnet-base-v2
        model_name = "nomic-ai/nomic-embed-text-v1.5"

        try:
            self.model = SentenceTransformer(
                model_name,
                trust_remote_code=True
            )

            # Log device being used (CPU, CUDA, or MPS for Apple Silicon)
            device = self.model.device
            logger.info(f"✓ Local embedding model loaded: {model_name}")
            logger.info(f"  Device: {device}")
            logger.info(f"  Dimensions: {config.embedding_dimensions}")

        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise

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

            embedding = self.model.encode(
                text_with_prefix,
                convert_to_tensor=False,
                show_progress_bar=False
            )

            return embedding.tolist()
        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return [0.0] * config.embedding_dimensions

    def prepare_text_for_embedding(
        self,
        chunk: Union[CodeChunk, DocumentChunk, CommitChunk]
    ) -> str:
        """
        Prepare text from a chunk for embedding generation
        For code chunks, we might want to include metadata
        For document chunks, just the content
        For commit chunks, use the commit message

        Applies 6000 char limit as final safety check

        Args:
            chunk: CodeChunk, DocumentChunk, or CommitChunk

        Returns:
            Prepared text string (max 6000 chars)
        """
        if isinstance(chunk, CodeChunk):
            # For code, include the code text and potentially function/class name
            text = chunk.code_text

            # Optionally prepend with metadata for better context
            if chunk.chunk_type == "function" and "function_name" in chunk.metadata:
                text = f"Function: {chunk.metadata['function_name']}\n{text}"
            elif chunk.chunk_type == "class" and "class_name" in chunk.metadata:
                text = f"Class: {chunk.metadata['class_name']}\n{text}"

        elif isinstance(chunk, DocumentChunk):
            # For documents, use the content
            text = chunk.content

        elif isinstance(chunk, CommitChunk):
            # For commits, use the commit message
            # These are valuable for semantic search of project history
            text = chunk.commit_message

        else:
            text = ""

        # Final safety check: enforce 6000 char limit to stay under 8192 tokens
        MAX_CHARS = 6000
        if len(text) > MAX_CHARS:
            chunk_type = type(chunk).__name__
            chunk_id = getattr(chunk, 'chunk_id', 'unknown')
            logger.warning(
                f"Truncating {chunk_type} at embedding time: {len(text)} chars → {MAX_CHARS} chars "
                f"(chunk_id: {chunk_id[:16]}...)"
            )
            # Keep start and end
            text = text[:4500] + f"\n... [truncated {len(text) - 5900} chars] ...\n" + text[-1400:]

        return text

    async def generate_embeddings(
        self,
        chunks: List[Union[CodeChunk, DocumentChunk, CommitChunk]],
        batch_size: int = 128,  # Increased from 16; safe with 6K char max per chunk
        batch_callback = None  # Optional callback to write batches immediately
    ) -> None:
        """
        Generate embeddings for a list of chunks using batch processing
        Updates the chunks in-place with their embeddings
        Optionally calls batch_callback after each batch for streaming writes

        Args:
            chunks: List of CodeChunk, DocumentChunk, or CommitChunk objects
            batch_size: Number of chunks to process at once
            batch_callback: Optional async function to call with each batch after embedding
        """
        if not chunks:
            return

        logger.info(f"Generating embeddings for {len(chunks)} chunks (batch_size={batch_size})")
        total_batches = (len(chunks) + batch_size - 1) // batch_size

        try:
            # Process in batches for memory efficiency
            for i in range(0, len(chunks), batch_size):
                batch_chunks = chunks[i:i + batch_size]
                batch_num = i // batch_size + 1

                # Log progress every 10 batches
                if batch_num % 10 == 0 or batch_num == 1 or batch_num == total_batches:
                    progress_pct = (batch_num / total_batches) * 100
                    logger.info(f"Processing batch {batch_num}/{total_batches} ({progress_pct:.1f}%)")

                # Prepare texts for this batch only
                batch_texts = [self.prepare_text_for_embedding(chunk) for chunk in batch_chunks]

                # Add task instruction prefix for document embedding
                prefixed_batch = [f"search_document: {text}" for text in batch_texts]

                # Generate embeddings for the batch (GPU/MPS accelerated)
                batch_embeddings = self.model.encode(
                    prefixed_batch,
                    convert_to_tensor=False,
                    show_progress_bar=False,
                    batch_size=batch_size
                )

                # Assign embeddings immediately to free memory
                for chunk, embedding in zip(batch_chunks, batch_embeddings):
                    chunk.embedding = embedding.tolist()

                # Stream write to database if callback provided
                if batch_callback:
                    await batch_callback(batch_chunks)

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

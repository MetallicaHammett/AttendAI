"""
Utility functions for AttendAI
"""
import numpy as np
import base64
import json
from typing import Optional

def serialize_face_embedding(embedding: np.ndarray) -> bytes:
    """
    Serialize a face embedding (numpy array) to bytes for database storage
    """
    if embedding is None:
        raise ValueError("Cannot serialize None embedding")

    # Convert to float64 for consistency and serialize
    embedding_float64 = embedding.astype(np.float64)
    return embedding_float64.tobytes()

def deserialize_face_embedding(embedding_bytes: bytes) -> np.ndarray:
    """
    Deserialize face embedding bytes back to numpy array
    """
    if embedding_bytes is None or len(embedding_bytes) == 0:
        raise ValueError("Cannot deserialize empty or None embedding bytes")

    # Convert bytes back to float64 array
    return np.frombuffer(embedding_bytes, dtype=np.float64)

def base64_encode_embedding(embedding: np.ndarray) -> str:
    """
    Encode face embedding as base64 string (for API transmission)
    """
    serialized = serialize_face_embedding(embedding)
    return base64.b64encode(serialized).decode('utf-8')

def base64_decode_embedding(encoded_string: str) -> np.ndarray:
    """
    Decode base64 string back to face embedding
    """
    try:
        decoded_bytes = base64.b64decode(encoded_string.encode('utf-8'))
        return deserialize_face_embedding(decoded_bytes)
    except Exception as e:
        raise ValueError(f"Failed to decode base64 embedding: {e}")

def calculate_face_similarity(embedding1: np.ndarray, embedding2: np.ndarray) -> float:
    """
    Calculate similarity between two face embeddings (returns distance, lower is more similar)
    """
    if embedding1.shape != embedding2.shape:
        raise ValueError("Embeddings must have the same shape")

    # Euclidean distance
    return np.linalg.norm(embedding1 - embedding2)

def validate_face_encoding(encoding: np.ndarray) -> bool:
    """
    Validate that a face encoding is valid
    """
    if encoding is None:
        return False

    if not isinstance(encoding, np.ndarray):
        return False

    if encoding.size == 0:
        return False

    # Face encodings from face_recognition library are typically 128-dimensional
    if encoding.shape != (128,):
        logger.warning(f"Unexpected face encoding shape: {encoding.shape}")
        # Don't reject based on shape alone, as different models may have different dimensions

    # Check for NaN or infinite values
    if np.any(np.isnan(encoding)) or np.any(np.isinf(encoding)):
        return False

    return True
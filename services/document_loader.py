import os
from typing import List, Dict

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from docx import Document
except ImportError:
    Document = None

from logger_config import logger


def _chunk_text(text: str, filename: str, chunk_size: int = 500, overlap: int = 50) -> List[Dict[str, str | int]]:
    """
    Splits text by whitespace into words and creates overlapping chunks.

    Args:
        text: The raw text to chunk.
        filename: The original filename the text was extracted from.
        chunk_size: Maximum number of words per chunk.
        overlap: Number of overlapping words between consecutive chunks.

    Returns:
        List of dictionaries containing the chunk text, filename, and index.
    """
    words = text.split()
    chunks = []
    chunk_index = 0

    if not words:
        return chunks

    i = 0
    while i < len(words):
        # Extract up to chunk_size words
        chunk_words = words[i : i + chunk_size]
        chunk_text = " ".join(chunk_words).strip()
        
        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "filename": filename,
                "chunk_index": chunk_index
            })
            chunk_index += 1

        # Advance index, moving back by 'overlap' words if this isn't the last chunk
        i += chunk_size
        if i < len(words):
            i -= overlap

    return chunks


def load_document(file_path: str, filename: str) -> List[Dict[str, str | int]]:
    """
    Reads a document (PDF, DOCX, TXT), extracts its text, and returns overlapping chunks.

    Args:
        file_path: Absolute path to the file.
        filename: Original assigned filename (used in chunk metadata).

    Returns:
        List of chunk dictionaries.
        
    Raises:
        ValueError: If the file type is unsupported or a parsing dependency is missing.
    """
    if not os.path.isfile(file_path):
        raise ValueError(f"File not found: {file_path}")

    ext = os.path.splitext(filename)[1].lower()
    full_text = ""

    try:
        if ext == ".pdf":
            if fitz is None:
                raise ValueError("PyMuPDF is not installed. Please install 'PyMuPDF' to parse PDFs.")
            with fitz.open(file_path) as doc:
                for page in doc:
                    full_text += page.get_text("text") + "\n"

        elif ext == ".docx":
            if Document is None:
                raise ValueError("python-docx is not installed. Please install 'python-docx' to parse DOCX files.")
            doc = Document(file_path)
            for paragraph in doc.paragraphs:
                full_text += paragraph.text + "\n"

        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                full_text = f.read()

        else:
            raise ValueError(f"Unsupported file type '{ext}'. Supported types are: .pdf, .docx, .txt")

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Failed to load document {filename}: {e}")
        raise ValueError(f"Error reading document {filename}: {e}")

    # Chunk the extracted text
    chunks = _chunk_text(full_text, filename)
    logger.info(f"document_loader extracted {len(chunks)} chunks from {filename}")
    
    return chunks

"""
File Processor Service

Processes downloaded files to be added to the context Claude.
Supports text files, images and PDF.
"""

import base64
import logging
import os
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class FileType(Enum):
    """Supported File Types"""
    TEXT = "text"
    IMAGE = "image"
    PDF = "pdf"
    UNSUPPORTED = "unsupported"


@dataclass
class ProcessedFile:
    """File processing result"""
    file_type: FileType
    filename: str
    content: str  # Text content or base64 for images
    mime_type: str
    size_bytes: int
    error: Optional[str] = None
    saved_path: Optional[str] = None  # Path to the saved file in the working directory

    @property
    def is_valid(self) -> bool:
        return self.error is None


class FileProcessorService:
    """
    File processing service for adding to context Claude.

    Supported Formats:
    - Text: .md, .txt, .py, .js, .ts, .json, .yaml, .yml, .toml, .xml, .html, .css, .go, .rs, .java, .kt
    - Images: .png, .jpg, .jpeg, .gif, .webp
    - PDF: .pdf (convert to text)
    """

    # Size restrictions
    MAX_TEXT_SIZE = 1 * 1024 * 1024  # 1 MB
    MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
    MAX_PDF_SIZE = 2 * 1024 * 1024    # 2 MB

    # Supported Extensions
    TEXT_EXTENSIONS = {
        ".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx",
        ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
        ".css", ".scss", ".less", ".go", ".rs", ".java", ".kt",
        ".c", ".cpp", ".h", ".hpp", ".sh", ".bash", ".zsh",
        ".sql", ".graphql", ".vue", ".svelte", ".astro",
        ".dockerfile", ".env", ".gitignore", ".editorconfig",
        ".csv", ".ini", ".cfg", ".conf", ".log", ".rb", ".php",
        ".swift", ".m", ".mm", ".pl", ".pm", ".r", ".scala",
        ".clj", ".ex", ".exs", ".erl", ".hs", ".lua", ".nim",
        ".zig", ".v", ".d", ".f90", ".f95", ".jl", ".dart",
    }

    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    PDF_EXTENSIONS = {".pdf"}

    IMAGE_MIME_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    # Languages ​​for syntax highlighting
    LANG_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".jsx": "jsx",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "zsh",
        ".sql": "sql",
        ".graphql": "graphql",
        ".md": "markdown",
        ".vue": "vue",
        ".svelte": "svelte",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".scala": "scala",
        ".clj": "clojure",
        ".ex": "elixir",
        ".exs": "elixir",
        ".hs": "haskell",
        ".lua": "lua",
        ".dart": "dart",
        ".r": "r",
    }

    def detect_file_type(self, filename: str) -> FileType:
        """Determine file type by extension"""
        ext = self._get_extension(filename)

        if ext in self.TEXT_EXTENSIONS:
            return FileType.TEXT
        elif ext in self.IMAGE_EXTENSIONS:
            return FileType.IMAGE
        elif ext in self.PDF_EXTENSIONS:
            return FileType.PDF
        else:
            # Checking for files without extension (Dockerfile, Makefile, etc.)
            basename = os.path.basename(filename).lower()
            if basename in {"dockerfile", "makefile", "rakefile", "gemfile", "procfile"}:
                return FileType.TEXT
            return FileType.UNSUPPORTED

    def _get_extension(self, filename: str) -> str:
        """Get file extension in lowercase"""
        _, ext = os.path.splitext(filename.lower())
        return ext

    def validate_file(self, filename: str, size: int) -> Tuple[bool, Optional[str]]:
        """
        File validation before processing.

        Returns:
            Tuple[is_valid, error_message]
        """
        file_type = self.detect_file_type(filename)

        if file_type == FileType.UNSUPPORTED:
            ext = self._get_extension(filename) or "(no extension)"
            return False, f"Unsupported file type: {ext}"

        max_size = {
            FileType.TEXT: self.MAX_TEXT_SIZE,
            FileType.IMAGE: self.MAX_IMAGE_SIZE,
            FileType.PDF: self.MAX_PDF_SIZE,
        }.get(file_type, self.MAX_TEXT_SIZE)

        if size > max_size:
            max_mb = max_size / (1024 * 1024)
            return False, f"The file is too large (max. {max_mb:.1f} MB)"

        return True, None

    async def process_file(
        self,
        file_content: BytesIO,
        filename: str,
        mime_type: Optional[str] = None
    ) -> ProcessedFile:
        """
        Process the file and return it ready for Claude content.

        Args:
            file_content: File contents as BytesIO
            filename: File name
            mime_type: MIME type (optional)

        Returns:
            ProcessedFile with ready-made content
        """
        file_type = self.detect_file_type(filename)
        content_bytes = file_content.read()
        size = len(content_bytes)

        # Validation
        is_valid, error = self.validate_file(filename, size)
        if not is_valid:
            return ProcessedFile(
                file_type=file_type,
                filename=filename,
                content="",
                mime_type=mime_type or "",
                size_bytes=size,
                error=error
            )

        try:
            if file_type == FileType.TEXT:
                content = self._process_text(content_bytes)
                mime = mime_type or "text/plain"
            elif file_type == FileType.IMAGE:
                content = self._process_image(content_bytes)
                ext = self._get_extension(filename)
                mime = mime_type or self.IMAGE_MIME_TYPES.get(ext, "image/png")
            elif file_type == FileType.PDF:
                content = await self._process_pdf(content_bytes)
                mime = mime_type or "application/pdf"
            else:
                return ProcessedFile(
                    file_type=file_type,
                    filename=filename,
                    content="",
                    mime_type="",
                    size_bytes=size,
                    error="Unsupported file type"
                )

            logger.info(f"Processed file: {filename} ({file_type.value}, {size} bytes)")

            return ProcessedFile(
                file_type=file_type,
                filename=filename,
                content=content,
                mime_type=mime,
                size_bytes=size
            )

        except Exception as e:
            logger.error(f"Error processing file {filename}: {e}")
            return ProcessedFile(
                file_type=file_type,
                filename=filename,
                content="",
                mime_type=mime_type or "",
                size_bytes=size,
                error=f"Processing error: {str(e)}"
            )

    def _process_text(self, content_bytes: bytes) -> str:
        """Process text file"""
        # Trying to decode as UTF-8, then latin-1 How fallback
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return content_bytes.decode("latin-1")
            except UnicodeDecodeError:
                return content_bytes.decode("utf-8", errors="replace")

    def _process_image(self, content_bytes: bytes) -> str:
        """Process image - return base64"""
        return base64.b64encode(content_bytes).decode("utf-8")

    async def _process_pdf(self, content_bytes: bytes) -> str:
        """
        Process PDF - extract text.

        Requires pypdf or pdfplumber.
        """
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content_bytes))
            text_parts = []

            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

            if not text_parts:
                return "[PDF: Failed to extract text (possibly a scanned document)]"

            return "\n\n".join(text_parts)

        except ImportError:
            logger.warning("pypdf not installed, PDF processing unavailable")
            return "[PDF: pypdf not installed - content is not available. Install: pip install pypdf]"
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            return f"[PDF: text extraction error - {str(e)}]"

    def save_to_working_dir(
        self,
        processed_file: ProcessedFile,
        working_dir: str
    ) -> Optional[str]:
        """
        Save the file to the project's working directory.

        Args:
            processed_file: Processed file
            working_dir: Project working directory

        Returns:
            Path to the saved file or None in case of error
        """
        try:
            # Create a folder .uploads for temporary files
            uploads_dir = os.path.join(working_dir, ".uploads")
            os.makedirs(uploads_dir, exist_ok=True)

            file_path = os.path.join(uploads_dir, processed_file.filename)

            if processed_file.file_type == FileType.IMAGE:
                # Decoding base64 and save
                image_data = base64.b64decode(processed_file.content)
                with open(file_path, "wb") as f:
                    f.write(image_data)
            else:
                # Save text files as is
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(processed_file.content)

            processed_file.saved_path = file_path
            logger.info(f"File saved to {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Error saving file to working dir: {e}")
            return None

    def format_for_prompt(
        self,
        processed_file: ProcessedFile,
        task_text: str = "",
        working_dir: Optional[str] = None
    ) -> str:
        """
        Format the processed file for adding to prompt.

        Args:
            processed_file: Processed file
            task_text: User task text
            working_dir: Working directory for saving images

        Returns:
            Formatted prompt with file
        """
        if processed_file.error:
            error_block = f"[File processing error {processed_file.filename}: {processed_file.error}]"
            if task_text:
                return f"{error_block}\n\n{task_text}"
            return error_block

        if processed_file.file_type == FileType.TEXT:
            # For text files - insert the content into the code block
            lang = self._detect_language(processed_file.filename)
            file_block = f"📎 **File: {processed_file.filename}** ({processed_file.size_bytes // 1024} KB)\n```{lang}\n{processed_file.content}\n```"

            if task_text:
                return f"{file_block}\n\n---\n\n{task_text}"
            return file_block

        elif processed_file.file_type == FileType.IMAGE:
            # For images - save to the working directory and specify the path
            if working_dir:
                saved_path = self.save_to_working_dir(processed_file, working_dir)
                if saved_path:
                    image_instruction = (
                        f"📎 **Image saved:** `{saved_path}`\n\n"
                        f"Use Read tool to read and analyze this image.\n"
                        f"File path: {saved_path}"
                    )
                    if task_text:
                        return f"{image_instruction}\n\n---\n\n**User task:** {task_text}"
                    return image_instruction

            # Fallback if you couldn't save
            image_marker = f"[Image: {processed_file.filename} - could not save for analysis]"
            if task_text:
                return f"{image_marker}\n\n{task_text}"
            return image_marker

        elif processed_file.file_type == FileType.PDF:
            # PDF - extracted text
            file_block = f"📎 **PDF: {processed_file.filename}** ({processed_file.size_bytes // 1024} KB)\n```\n{processed_file.content}\n```"

            if task_text:
                return f"{file_block}\n\n---\n\n{task_text}"
            return file_block

        return task_text

    def _detect_language(self, filename: str) -> str:
        """Define the language for syntax highlighting"""
        ext = self._get_extension(filename)
        return self.LANG_MAP.get(ext, "")

    def get_supported_extensions(self) -> dict:
        """Get a list of supported extensions by type"""
        return {
            "text": sorted(self.TEXT_EXTENSIONS),
            "image": sorted(self.IMAGE_EXTENSIONS),
            "pdf": sorted(self.PDF_EXTENSIONS),
        }

    def format_multiple_files_for_prompt(
        self,
        files: list[ProcessedFile],
        task_text: str = "",
        working_dir: Optional[str] = None
    ) -> str:
        """
        Format multiple files to add to prompt.

        Used for media groups (albums) - when the user
        sends multiple files in one message.

        Args:
            files: List of processed files
            task_text: User task text
            working_dir: Working directory for saving images

        Returns:
            Formatted prompt with all files
        """
        if not files:
            return task_text

        if len(files) == 1:
            # One file - use the usual method
            return self.format_for_prompt(files[0], task_text, working_dir)

        # Several files - creating a combined one prompt
        file_blocks = []

        for i, pf in enumerate(files, 1):
            if pf.error:
                file_blocks.append(f"📎 **File {i}: {pf.filename}** - Error: {pf.error}")
                continue

            if pf.file_type == FileType.TEXT:
                lang = self._detect_language(pf.filename)
                block = f"📎 **File {i}: {pf.filename}** ({pf.size_bytes // 1024} KB)\n```{lang}\n{pf.content}\n```"
                file_blocks.append(block)

            elif pf.file_type == FileType.IMAGE:
                if working_dir:
                    saved_path = self.save_to_working_dir(pf, working_dir)
                    if saved_path:
                        block = (
                            f"📎 **Image {i}: {pf.filename}** saved in `{saved_path}`\n"
                            f"Use Read tool for analysis: {saved_path}"
                        )
                        file_blocks.append(block)
                        continue

                # Fallback
                file_blocks.append(f"📎 **Image {i}: {pf.filename}** - failed to save")

            elif pf.file_type == FileType.PDF:
                block = f"📎 **PDF {i}: {pf.filename}** ({pf.size_bytes // 1024} KB)\n```\n{pf.content}\n```"
                file_blocks.append(block)

        # Combine all blocks
        files_section = "\n\n".join(file_blocks)

        if task_text:
            return f"{files_section}\n\n---\n\n**User task:** {task_text}"

        return files_section

    def get_files_summary(self, files: list[ProcessedFile]) -> str:
        """
        Get a short description of the file list.

        Args:
            files: List of processed files

        Returns:
            String of the form "3 file: image1.jpg, image2.jpg, +1"
        """
        if not files:
            return "no files"

        total = len(files)
        if total == 1:
            return files[0].filename

        # We show the first 2 name, the rest as "+N"
        names = [f.filename for f in files[:2]]
        if total > 2:
            names.append(f"+{total - 2}")

        return f"{total} files: {', '.join(names)}"

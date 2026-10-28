# SPDX-License-Identifier: Apache-2.0
# Vendored from https://github.com/maseko-lucky-9/sentrysearch (sentrysearch/base_embedder.py);
# only this header and ruff-format whitespace differ from upstream.
# at commit 3694dd0cb27cc57920b2b816996ce5a22662e45c. See ../README.md and /VENDOR.md.
"""Abstract base class for video/text embedding backends."""

from abc import ABC, abstractmethod


class BaseEmbedder(ABC):
    @abstractmethod
    def embed_video_chunk(self, chunk_path: str, verbose: bool = False) -> list[float]: ...

    @abstractmethod
    def embed_query(self, query_text: str, verbose: bool = False) -> list[float]: ...

    @abstractmethod
    def embed_image(self, image_path: str, verbose: bool = False) -> list[float]: ...

    @abstractmethod
    def dimensions(self) -> int: ...

from __future__ import annotations

import os
import random
import re


def safe_output_stem(
    task: str | None,
    *,
    fallback: str = "result",
    max_words: int = 2,
    max_word_length: int = 12,
) -> str:
    words = re.findall(r"[a-z0-9]+", (task or "").lower())
    selected = [word[:max_word_length] for word in words if word][:max_words]
    if not selected:
        selected = [fallback]
    stem = "_".join(selected).strip("_")
    stem = re.sub(r"_+", "_", stem)
    return stem or fallback


def build_output_filename(
    task: str | None,
    *,
    extension: str,
    fallback: str = "result",
    max_words: int = 2,
) -> str:
    stem = safe_output_stem(
        task,
        fallback=fallback,
        max_words=max_words,
    )
    suffix = f"{random.randint(100000, 999999):06d}"
    if not extension:
        normalized_ext = ""
    else:
        normalized_ext = extension if extension.startswith(".") else f".{extension}"
    return f"{stem}_{suffix}{normalized_ext}"


def build_output_path(
    directory: str,
    task: str | None,
    *,
    extension: str,
    fallback: str = "result",
    max_words: int = 2,
) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(
        directory,
        build_output_filename(
            task,
            extension=extension,
            fallback=fallback,
            max_words=max_words,
        ),
    )

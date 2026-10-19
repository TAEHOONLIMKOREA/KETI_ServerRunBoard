"""명령어 라인에서 비밀값을 가린다 (PLAN 8장).

`--token=abc`, `--password abc`, `HF_TOKEN=abc` 같은 형태의 값을 `***` 로 바꾸고
길이를 자른다. 키워드가 단어 끝에 올 때만 가려서 `--tokenizer`, `--keyframe` 은 건드리지 않는다.
"""

from __future__ import annotations

import re

MAX_CMDLINE = 200

# `key` 는 단어 경계(앞이 글자가 아님)에서만 키워드로 본다: secret_key, api-key 는 잡고 monkey 는 안 잡는다.
_KEYWORD = (
    r"(?:token|passw(?:or)?d|pwd|secret|credentials?"
    r"|api[_-]?key|access[_-]?key|private[_-]?key|(?<![A-Za-z])key)"
)
_PATTERN = re.compile(
    rf"((?:--?)?[\w.-]*?{_KEYWORD})(?![A-Za-z])([=:]|\s+)(\S+)",
    re.IGNORECASE,
)


def mask_cmdline(cmdline: str, max_len: int = MAX_CMDLINE) -> str:
    masked = _PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}***", cmdline)
    return masked[:max_len]


def join_cmdline(argv: list[str] | None) -> str:
    if not argv:
        return ""
    return " ".join(arg for arg in argv if arg)

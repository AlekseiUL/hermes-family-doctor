from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .security import canonical_json, digest


ACTIVE_INSTRUCTION_MARKERS = (
    "ignore previous", "system prompt", "execute command", "tool call", "change config", "write memory",
)


@dataclass(frozen=True)
class DocumentPreview:
    checksum: str
    source_locator: str
    content_classification: str
    instruction_attempt_detected: bool
    retention_applied: bool
    extracted_text: str
    redacted_instruction_lines: int
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "checksum": self.checksum,
            "source_locator": self.source_locator,
            "content_classification": self.content_classification,
            "instruction_attempt_detected": self.instruction_attempt_detected,
            "retention_applied": self.retention_applied,
            "extracted_text": self.extracted_text,
            "redacted_instruction_lines": self.redacted_instruction_lines,
            "truncated": self.truncated,
        }


def preview_document(document: Mapping[str, Any]) -> DocumentPreview:
    text = str(document.get("text", ""))
    lowered = text.casefold()
    instruction_attempt = any(marker in lowered for marker in ACTIVE_INSTRUCTION_MARKERS)
    safe_lines: list[str] = []
    redacted = 0
    for line in text.splitlines():
        if any(marker in line.casefold() for marker in ACTIVE_INSTRUCTION_MARKERS):
            safe_lines.append("[untrusted instruction line removed]")
            redacted += 1
        else:
            safe_lines.append(line)
    minimized = "\n".join(safe_lines)
    max_preview_chars = 4000
    truncated = len(minimized) > max_preview_chars
    return DocumentPreview(
        checksum=digest(canonical_json(dict(document))),
        source_locator=str(document.get("source_locator", "unprovenanced")),
        content_classification="untrusted_data",
        instruction_attempt_detected=instruction_attempt,
        retention_applied=False,
        extracted_text=minimized[:max_preview_chars],
        redacted_instruction_lines=redacted,
        truncated=truncated,
    )

# Excerpt from HAVEN (private codebase): apps/core/decide.py
# The typed-decision core and the statistical gate that decides when a local
# model has earned a question. Trimmed: the model-call, shadow/teacher queue and
# nightly scoring loop are elided (marked below). The functions shown are
# unchanged apart from docstrings.
"""Typed decisions, not generations.

A yes/no/which choice is asked as a question with a fixed answer set: the model
returns JSON matching a schema whose `choice` is an enum of the options plus
"other", with a confidence. Below the caller's threshold, or "other", the
decision is not taken. `confident` is False and the caller does its own "ask"
(surface it, keep the current state, leave it to the user).

This replaced free-text verdicts parsed with regexes ("WINNER: B", a bare
letter, a JSON list fished out of prose).

Every decision lands in `decision_log` (question, choice, confidence, reason,
model, ms) so thresholds are tuned from what actually happened.

The decision layer: who decides is set per question. A small local model
(Qwen3.5-4B on llama.cpp, CPU only) answers a question only after it has EARNED
it: its answers above a confidence threshold calibrated for that question must
agree with the teacher (Claude) well enough that the lower 95% Wilson bound
clears TRUST_BAR. Nothing is earned by default, so a new question belongs to
Claude until the record says otherwise. Both sides answer every decision
(shadow mode), so the labels are not biased toward the hard cases.
"""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)
OTHER = "other"
DEFAULT_THRESHOLD = 0.7
TRUST_BAR = 0.90   # lower 95% bound on agreement a question must clear to go local


@dataclass
class Decision:
    choice: Optional[str]     # one of the options, or None when not confident
    confidence: float
    reason: str
    confident: bool
    raw_choice: str = ""      # what the model picked, even when not confident


def _choices(options: Sequence[str]) -> list:
    """The options plus OTHER, once. A question may have its own "other";
    listing it twice made the schema invalid and every such call failed."""
    return list(dict.fromkeys(list(options) + [OTHER]))


def _schema(options: Sequence[str], many: bool) -> dict:
    one = {
        "type": "object",
        "properties": {
            "choice": {"type": "string", "enum": _choices(options)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["choice", "confidence", "reason"],
        "additionalProperties": False,
    }
    if not many:
        return one
    item = dict(one)
    item["properties"] = dict(one["properties"], i={"type": "integer"})
    item["required"] = ["i"] + one["required"]
    return {"type": "object",
            "properties": {"answers": {"type": "array", "items": item}},
            "required": ["answers"], "additionalProperties": False}


def _system(question: str, options: Sequence[str]) -> str:
    return (question.strip() + "\n\nAnswer only through the schema. choice is exactly one of: "
            + ", ".join(_choices(options))
            + f". Use {OTHER!r} when none fits. confidence is your probability (0-1) that "
              "the choice is right; be honest — a low number is useful, a falsely high one "
              "is not. reason is one short clause.")


def _judge(raw: dict, options: Sequence[str], threshold: float) -> Decision:
    choice = str(raw.get("choice") or "").strip()
    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    reason = str(raw.get("reason") or "")[:200]
    ok = choice in options and conf >= threshold
    return Decision(choice if ok else None, conf, reason, ok, raw_choice=choice)


def decide(question_id: str, question: str, options: Sequence[str], context: str = "",
           threshold: float = DEFAULT_THRESHOLD, model: str = "haiku", item: str = "",
           record: bool = True) -> Decision:
    """One typed choice. Raises on a model failure; the caller owns that path.
    `record=False` (a replayed day) asks Claude and writes nothing: a replay must
    not teach the policy or show up in the decision record."""
    t0 = time.monotonic()
    system, prompt, schema = _system(question, options), context or ".", _schema(options, False)
    if not record:
        return _judge(_ask_model(system, prompt, schema, model), options, threshold)
    mode = _local_mode()
    pol = _policy(question_id) if mode == "on" else None
    if pol:                                   # the local model has earned this question
        try:
            raw = _ask_local(system, prompt, schema)
            d = _judge(raw, options, _earned(pol, raw, threshold))
            if d.confident:
                lm = str((raw.get("_meta") or {}).get("model") or "?")
                _log(question_id, d, "local:" + lm, int((time.monotonic() - t0) * 1000), item=item)
                _teach(question_id, system, [prompt], schema, [d], lm, model, items=[item or question_id])
                return d
        except Exception:
            logger.warning("decide: local leg failed, falling back to %s", model, exc_info=True)
    raw = _ask_model(system, prompt, schema, model)
    d = _judge(raw, options, threshold)
    _log(question_id, d, model, int((time.monotonic() - t0) * 1000), item=item)
    if mode in ("shadow", "on"):
        _shadow(question_id, system, [prompt], schema, [d], model, items=[item or question_id])
    return d


# ... elided: _ask_model (structured-output call), _ask_local (llama.cpp with a
# JSON grammar), _log / _shadow / _teach (decision_log, shadow and teacher
# queues), decide_many ...


def _wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5) / (1 + z * z / n)


def _calibrate(pairs: List[tuple]) -> tuple:
    """pairs = [(local_confidence, agreed)]. The lowest confidence threshold
    whose answers at or above it clear TRUST_BAR on the Wilson lower bound
    (covering the most decisions), or (None, best bound seen)."""
    best_lb = 0.0
    for t in sorted({round(c or 0.0, 3) for c, _ in pairs}):
        above = [a for c, a in pairs if (c or 0.0) >= t]
        lb = _wilson_lower(sum(above), len(above))
        best_lb = max(best_lb, lb)
        if lb >= TRUST_BAR:
            return t, lb, len(above), sum(above)
    return None, best_lb, 0, 0

# ... elided: score(), which runs nightly over the shadow record and a replay of
# history, calls _calibrate per question, and writes decision_policy ...

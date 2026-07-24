"""
Prompt builder (Phase 3) — turn retrieved chunks + the question into the exact
messages we send the LLM.

This is the HEART of "grounding": the system message tells the model it may use
ONLY the supplied content and must not invent anything; the user message hands it
that content plus the question. Good grounding here is what makes AskVeda
trustworthy instead of a confident guesser.
"""

# The behavioural contract. Kept strict on grounding + gentle on safety (per the
# product decision: calm doctor-routing, never alarm styling).
# The exact token the model must emit when our content can't answer. A STRUCTURAL
# signal beats trying to guess from prose — it lets us log the content gap
# reliably and return our own friendly message instead of caching a decline.
NO_ANSWER = "NO_ANSWER"

SYSTEM_PROMPT = f"""You are AskVeda, a warm and careful assistant for ParentVeda, \
helping parents through pregnancy and early parenting.

Follow these rules strictly:
1. Answer using ONLY the CONTENT provided in the user's message. Do not use outside \
knowledge or general assumptions.
2. If the CONTENT does not DIRECTLY answer the question, reply with EXACTLY \
`{NO_ANSWER}` and nothing else — no apology, no improvising, no general knowledge. \
This applies even when the content is loosely related or on the same broad topic: \
partial relevance is NOT an answer. NEVER pad with tangential facts and then admit \
you don't know — if your reply would contain anything like "the content doesn't \
mention X" or "unfortunately the content provided doesn't cover", you MUST emit \
`{NO_ANSWER}` instead. NEVER invent facts, numbers, or medical advice.
3. For "can I…?" questions, if the content gives a verdict (yes / in moderation / \
avoid some / avoid), state it clearly and up front.
4. Be warm, concise, and plain-spoken. Short paragraphs. No hype, no emojis.
5. If the question suggests a possible emergency (heavy bleeding, severe pain, \
reduced or no baby movements, fainting, etc.), gently and calmly suggest they \
contact their doctor or midwife — without alarming language.
6. TENSE AND STAGE — important. You are told where the person asking is in her \
journey. Frame the answer for THAT moment: if she asks about something already behind \
her, use the PAST tense ("during your pregnancy, the anatomy scan checked…"); if it is \
still ahead of her, use the FUTURE tense ("at around week 20, you'll have…"). NEVER \
speak as if she is still pregnant when her baby has already been born. Never withhold \
information because of her stage — just frame it correctly for where she is.
7. If you are NOT told where she is in her journey, do NOT assume or mention one. \
Never say things like "since you've already had your baby" or "now that you're \
pregnant" unless you were explicitly told so — answer neutrally instead. Guessing \
wrong here is upsetting.
"""


def describe_stage(
    week: int | None = None,
    trimester: str | None = None,
    child_age_months: int | None = None,
) -> str | None:
    """A plain-English note telling the model where she is — so it gets tense right.

    This is CONTEXT, never a gate: she can ask about any stage of the journey, and
    the model answers fully — just phrased correctly for where she stands today.
    """
    if child_age_months is not None:
        return (
            f"She is NO LONGER PREGNANT — she has given birth and her baby is now "
            f"{child_age_months} month(s) old. Her whole pregnancy is in the PAST. "
            "You MUST write about anything pregnancy-related in the past tense, "
            'addressing her directly — e.g. "during your pregnancy, the anatomy scan '
            'checked…" or "when you were around 20 weeks, it looked at…". Do NOT use '
            "the present or future tense for pregnancy events, and do not use vague "
            "passive phrasing to dodge the tense. But only bring her pregnancy up "
            "at all if the question is actually about pregnancy — for everyday "
            "questions just answer normally for a parent of a "
            f"{child_age_months}-month-old."
        )
    if week:
        return (
            f"She is currently at week {week} of pregnancy. Anything before week {week} "
            "is in her past; anything after it is still ahead of her."
        )
    if trimester:
        return f"She is currently in the {trimester} trimester of pregnancy."
    return None


def _format_chunk(index: int, chunk: dict) -> str:
    """Render one retrieved chunk as a labelled block the LLM can cite from."""
    header_bits = [f"[{index}] {chunk.get('title') or 'Untitled'}"]
    if chunk.get("week"):
        header_bits.append(f"(week {chunk['week']})")
    elif chunk.get("trimester"):
        header_bits.append(f"({chunk['trimester']} trimester)")
    if chunk.get("verdict"):
        header_bits.append(f"[verdict: {chunk['verdict']}]")
    header = " ".join(header_bits)
    body = (chunk.get("chunk_text") or "").strip()
    return f"{header}\n{body}"


def build_web_messages(
    question: str,
    results: list[dict],
    stage_note: str | None = None,
) -> list[dict]:
    """Messages for the TRUSTED-WEB fallback (Phase 7).

    Same grounding discipline as our own content — answer only from what's
    supplied — but the material came from whitelisted health authorities, so the
    answer must say so. Being transparent about where an answer came from is the
    point: she can see it's from the NHS/WHO rather than from our own library.
    """
    passages = "\n\n".join(
        f"[{i + 1}] {r.get('title')} ({r.get('url')})\n{(r.get('content') or '').strip()}"
        for i, r in enumerate(results)
    )
    stage_line = f"ABOUT THE PERSON ASKING: {stage_note}\n\n" if stage_note else ""

    user_message = (
        f"{stage_line}"
        "TRUSTED SOURCES (the only material you may use — these are recognised "
        "health authorities, not our own content):\n"
        f"{passages}\n\n"
        f"QUESTION: {question}\n\n"
        "Answer using ONLY the sources above, framed correctly for her stage. Keep "
        "it short and warm. End with one line naming the source(s) you used, e.g. "
        '"Source: NHS". If the sources do not actually answer it, reply with '
        f"exactly {NO_ANSWER}."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def build_messages(
    question: str,
    chunks: list[dict],
    stage_note: str | None = None,
) -> list[dict]:
    """Build the OpenAI-style [system, user] messages for one question.

    `stage_note` (from describe_stage) tells the model where she is in her journey
    so it frames the answer in the right tense — context, not a content filter.
    """
    if chunks:
        context = "\n\n".join(_format_chunk(i + 1, c) for i, c in enumerate(chunks))
    else:
        context = "(no relevant content was found)"

    stage_line = f"ABOUT THE PERSON ASKING: {stage_note}\n\n" if stage_note else ""

    user_message = (
        f"{stage_line}"
        "CONTENT (the only source you may use):\n"
        f"{context}\n\n"
        f"QUESTION: {question}\n\n"
        "Answer using ONLY the content above, framed correctly for her stage. "
        f"If the content doesn't cover it, reply with exactly {NO_ANSWER}."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


# ---------------------------------------------------------------------------
# Structured output (the 7-section feed): sections 1–3 come from the LLM in ONE
# call — the direct ANSWER, "what this MEANING for you", and a few ACTIONS. We ask
# for a labelled format (not JSON — a small model follows labels far more reliably)
# and parse it defensively.
# ---------------------------------------------------------------------------
import re  # noqa: E402  (kept local to the structured helpers)

_STRUCTURED_FORMAT = (
    "Write your reply in EXACTLY this labelled format, and nothing else:\n"
    "ANSWER: <a warm, direct answer to her question, framed correctly for her stage>\n"
    "MEANING: <one or two sentences on what this means for HER specifically>\n"
    "ACTIONS:\n"
    "- <a concrete, useful next step>\n"
    "- <another next step>\n\n"
    f"If the material does NOT directly cover the question, put exactly {NO_ANSWER} "
    "as the ANSWER and leave MEANING and ACTIONS blank."
)


def build_full_messages(question: str, chunks: list[dict], stage_note: str | None = None) -> list[dict]:
    """Like build_messages, but asks for the 3 LLM sections (answer/meaning/actions)."""
    context = "\n\n".join(_format_chunk(i + 1, c) for i, c in enumerate(chunks)) \
        if chunks else "(no relevant content was found)"
    stage_line = f"ABOUT THE PERSON ASKING: {stage_note}\n\n" if stage_note else ""
    user_message = (
        f"{stage_line}"
        "CONTENT (the only source you may use):\n"
        f"{context}\n\n"
        f"QUESTION: {question}\n\n"
        f"{_STRUCTURED_FORMAT}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def build_web_full_messages(question: str, results: list[dict], stage_note: str | None = None) -> list[dict]:
    """Structured version of the trusted-web prompt (must also name its source)."""
    passages = "\n\n".join(
        f"[{i + 1}] {r.get('title')} ({r.get('url')})\n{(r.get('content') or '').strip()}"
        for i, r in enumerate(results)
    )
    stage_line = f"ABOUT THE PERSON ASKING: {stage_note}\n\n" if stage_note else ""
    user_message = (
        f"{stage_line}"
        "TRUSTED SOURCES (the only material you may use — recognised health "
        "authorities, not our own content):\n"
        f"{passages}\n\n"
        f"QUESTION: {question}\n\n"
        f"{_STRUCTURED_FORMAT}\n"
        'In the ANSWER, end with one line naming the source(s), e.g. "Source: NHS".'
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def parse_structured(text: str) -> dict:
    """Parse the labelled ANSWER/MEANING/ACTIONS reply → {answer, meaning, actions}.

    Defensive: if the labels are missing (the model ignored the format), the whole
    text becomes the answer, with empty meaning/actions.
    """
    t = (text or "").strip()
    # `[ \t]*` (not `\s*`) after each label so the newline before the NEXT label is
    # preserved for the lookahead — otherwise an empty MEANING swallows "ACTIONS:".
    a = re.search(r"ANSWER:[ \t]*(.*?)(?=\n[ \t]*MEANING:|\n[ \t]*ACTIONS:|\Z)", t, re.S | re.I)
    m = re.search(r"MEANING:[ \t]*(.*?)(?=\n[ \t]*ACTIONS:|\Z)", t, re.S | re.I)
    ac = re.search(r"ACTIONS:[ \t]*(.*)\Z", t, re.S | re.I)

    answer = a.group(1).strip() if a else t
    meaning = m.group(1).strip() if m else ""
    actions: list[str] = []
    if ac:
        for line in ac.group(1).splitlines():
            s = line.strip().lstrip("-•*").strip()
            # Drop a stray "Source: …" citation line — it belongs in the answer,
            # not as a recommended action.
            if s and not s.lower().startswith("source:"):
                actions.append(s)
    return {"answer": answer, "meaning": meaning, "actions": actions[:5]}

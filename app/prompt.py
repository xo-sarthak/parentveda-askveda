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
6. HER OWN CLINICIAN OUTRANKS YOU — and outranks the CONTENT. If she says her \
doctor, midwife or clinic told her something, that is the most authoritative thing \
in the conversation. NEVER tell her to override it, and never answer "who is \
right?" in your own favour. If the content differs, say plainly that they differ, \
that her clinician knows her history and you do not, and that the question is \
worth putting back to them. The ranking, strongest first: her treating clinician → \
a lab result → a scan → a medication schedule confirmed against a prescription or \
clinic instruction → her own recorded observations (an LH strip, a temperature, \
the day her period began) → data from a device or wearable → anything ParentVeda \
worked out → a population statistic. Two consequences worth stating: a scan dates \
a pregnancy better than any calculation from a last period, so a dating scan beats \
a week we calculated; and what she noticed herself beats what a ring or a \
thermometer recorded, because she knows the context a sensor cannot.
7. A POPULATION STATISTIC IS NEVER A STATEMENT ABOUT HER. Facts like "most \
couples conceive within a year" or "forty per cent of cases" describe a group, \
never this person. NEVER turn one into a personal prediction, a chance, odds, a \
success rate, or a timeline for her — not even encouragingly. Do NOT write things \
like "the odds are in your favour", "you should conceive soon", "you've only been \
trying eight months", or "there's probably nothing wrong": you cannot know any of \
that, and it can delay someone from getting help. You may state the population \
fact plainly and say it does not tell us about her specifically. Judging whether \
anything IS wrong belongs to a doctor — route there warmly instead of reassuring.
8. TENSE AND STAGE — important. You are told where the person asking is in her \
journey. Frame the answer for THAT moment: if she asks about something already behind \
her, use the PAST tense ("during your pregnancy, the anatomy scan checked…"); if it is \
still ahead of her, use the FUTURE tense ("at around week 20, you'll have…"). NEVER \
speak as if she is still pregnant when her baby has already been born. Never withhold \
information because of her stage — just frame it correctly for where she is.
9. If you are NOT told where she is in her journey, do NOT assume or mention one. \
Never say things like "since you've already had your baby" or "now that you're \
pregnant" unless you were explicitly told so — answer neutrally instead. Guessing \
wrong here is upsetting.
"""


_TTC_CHAPTERS = {
    "preparingtogether": "preparing together, before actively trying",
    "knowingyourrhythm": "learning to read her cycle",
    "tryingtogether": "actively trying this cycle",
    "thewaitingdays": "in the two-week wait after ovulation",
    "anewbeginning": "at the very start of a possible pregnancy",
}
_TTC_PATHS = {
    "natural": "trying naturally",
    "ovulationinduction": "on ovulation induction",
    "iui": "going through IUI",
    "ivf": "going through IVF",
    "frozenembryotransfer": "going through a frozen embryo transfer",
}


# WHO owns the timing of this cycle. More decisive than ttc_path: the same
# treatment behaves in opposite ways depending on whether a clinic is monitoring
# it, so "she is on IVF" is a weaker signal than "her clinic owns the timing".
# A medicated cycle overrides her body's own signals, which makes fertile-window
# and period-countdown talk not merely irrelevant but wrong.
_TIMING_OWNERSHIP = {
    "parentveda": (
        "Nobody clinical is timing this cycle — it is her own. Her fertile window, "
        "ovulation signs and the date her period is due are all meaningful, and it "
        "is fine to talk about them."
    ),
    "clinic_guided": (
        "A clinic is involved, but her own body still sets the timing — this is a "
        "natural-cycle transfer or an IUI timed to her own LH surge. Her LH strips "
        "and temperature are exactly what the clinic is acting on, so take them "
        "seriously rather than dismissing them. The clinic's schedule, not a "
        "calendar, decides what happens next."
    ),
    "clinic_controlled": (
        "Her cycle is fully medicated and the clinic controls the timing. This "
        "changes what is TRUE for her: do NOT talk about a fertile window, "
        "predicting ovulation, an LH surge, or when her period is due — on a "
        "medicated cycle those are meaningless, and luteal support delays a period "
        "so 'you might be late' reads as false hope. Her own body signals are not "
        "a reliable guide here. The wait ends with the clinic's beta blood test, "
        "not a home test or a period. Refer to her clinic's schedule and dates."
    ),
}


# WHERE a clinic round stands today. The app sends this only while a clinic owns
# the cycle (the same resolver its home screen uses, so the answer and the home
# never disagree), as the name of its TtcRoundPhase enum
# (lib/ttc/ttc_treatment_round.dart). Planned and own-cycle are never sent.
#
# Why a step matters more than a chapter here: "she is NOT pregnant" is true
# through stimulation, false after a positive test, and UNKNOWN in the wait. A
# fixed opener told a woman whose blood test had just come back positive that she
# was not pregnant. So the step decides which of three truths the opener states.
_STEP_NOT_PREGNANT = "not_pregnant"
_STEP_UNKNOWN = "unknown"
_STEP_EARLY_PREGNANCY = "early_pregnancy"

_TREATMENT_STEPS = {
    "gettingready": (_STEP_NOT_PREGNANT,
        "Her treatment round is in its getting-ready part (a pill cycle, "
        "down-regulation, a baseline scan, or estrogen before a frozen transfer)."),
    "stimulation": (_STEP_NOT_PREGNANT,
        "She is in the stimulation part of her round: daily injections or "
        "tablets, with monitoring scans and blood tests at the clinic."),
    "trigger": (_STEP_NOT_PREGNANT,
        "She has had, or is about to have, her trigger shot. Its timing is set "
        "to the hour by her clinic and must be followed exactly."),
    "procedure": (_STEP_NOT_PREGNANT,
        "Today is her egg collection or IUI day."),
    "embryodays": (_STEP_NOT_PREGNANT,
        "Her eggs have been collected and the lab is growing the embryos. The "
        "clinic calls with updates; the next step is a transfer or freezing."),
    "transfer": (_STEP_UNKNOWN,
        "She has had an embryo transfer, or it is today."),
    "waiting": (_STEP_UNKNOWN,
        "She is in the wait between her embryo transfer or IUI and her clinic's "
        "blood test."),
    "testday": (_STEP_UNKNOWN,
        "It is her blood test day, or she is waiting to hear the result."),
    "result": (_STEP_EARLY_PREGNANCY,
        "Her clinic's blood test after this round was positive."),
    "betweenrounds": (_STEP_NOT_PREGNANT,
        "Her last treatment round has closed without a pregnancy, and she is "
        "between rounds. Do not assume why it closed or what she will do next."),
}

_OPENERS = {
    _STEP_NOT_PREGNANT: "She is TRYING TO CONCEIVE — she is NOT pregnant.",
    _STEP_UNKNOWN: (
        "She is TRYING TO CONCEIVE and does NOT yet know whether she is "
        "pregnant. Nobody can know until her clinic's blood test."
    ),
    _STEP_EARLY_PREGNANCY: (
        "She has just had a POSITIVE pregnancy blood test after fertility "
        "treatment. This is a very early pregnancy that her clinic is monitoring."
    ),
}

_CLOSERS = {
    _STEP_NOT_PREGNANT: (
        "CRITICAL: she is not pregnant, so NEVER write phrases like 'during your "
        "pregnancy', 'in early pregnancy', 'your baby', 'as your bump grows' or "
        "'many pregnant women'. Some of the CONTENT you are given may have been "
        "written for pregnant women — take the facts from it but NEVER carry over "
        "its pregnant-reader framing. Speak to someone who is hoping to conceive. "
        "Never promise a timeline or an outcome, and never imply the delay is her "
        "fault."
    ),
    _STEP_UNKNOWN: (
        "CRITICAL: nobody knows yet whether this worked. Do NOT write as if she "
        "is pregnant ('your baby', 'during your pregnancy') and do NOT write as "
        "if it has failed. Never read her symptoms as a sign either way: the "
        "medicines she is on cause most of them. Do not suggest a home test "
        "before the blood test; the trigger medicine can show a false positive. "
        "Never predict the result, give odds, or promise an outcome. Her clinic's "
        "instructions come first."
    ),
    _STEP_EARLY_PREGNANCY: (
        "CRITICAL: do NOT tell her she is not pregnant. It is very early: never "
        "promise how it will go, never give odds, and never read a symptom or a "
        "number as a sign either way. Her clinic plans what comes next (repeat "
        "blood tests, the first scan, when to stop or keep taking her medicines), "
        "so point her back to them for anything about her own care. Keep the "
        "tone warm and steady, not celebratory: she may be anxious."
    ),
}


def describe_ttc_stage(
    chapter: str | None = None,
    ttc_path: str | None = None,
    months_trying: int | None = None,
    cycle_day: int | None = None,
    timing_ownership: str | None = None,
    treatment_step: str | None = None,
) -> str:
    """Framing note for someone TRYING TO CONCEIVE.

    The register here is delicate: she is NOT pregnant, and how long she has been
    trying changes everything about how an answer should land. "We started last
    month" and "we have been trying two years" must never read the same.

    The one exception is a clinic round: [treatment_step] decides whether "not
    pregnant" is true, unknown (the wait) or false (a positive result). An
    unknown step falls back to the plain "not pregnant" framing.
    """
    step = _TREATMENT_STEPS.get((treatment_step or "").lower())
    truth = step[0] if step else _STEP_NOT_PREGNANT
    bits = [_OPENERS[truth]]
    if step:
        bits.append(step[1])
    ch = _TTC_CHAPTERS.get((chapter or "").lower())
    if ch:
        bits.append(f"Right now she is {ch}.")
    path = _TTC_PATHS.get((ttc_path or "").lower())
    if path:
        bits.append(f"She is {path} — use the vocabulary of that path.")
    # WHO owns the timing matters more than which treatment it is.
    owner = _TIMING_OWNERSHIP.get((timing_ownership or "").lower())
    if owner:
        bits.append(owner)
    # A cycle day is only meaningful when her own body is setting the timing.
    if cycle_day and (timing_ownership or "").lower() != "clinic_controlled":
        bits.append(f"She is on day {cycle_day} of her cycle.")
    if months_trying is not None:
        if months_trying >= 12:
            bits.append(
                f"They have been trying for {months_trying} months — over a year. "
                "Do NOT be breezy or offer easy reassurance; this has been long and "
                "hard. Be warm, matter-of-fact, and take the question seriously."
            )
        elif months_trying >= 6:
            bits.append(f"They have been trying for {months_trying} months.")
        else:
            bits.append(f"They started trying about {months_trying} month(s) ago.")
    bits.append(_CLOSERS[truth])
    return " ".join(bits)


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
            "is in her past; anything after it is still ahead of her. "
            "This week is CALCULATED by the app, not measured: if she quotes a dating "
            "scan or a date her clinician gave her, THAT is more accurate than this "
            "number and you must say so rather than defending the app's figure."
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
    "MEANING: <one or two sentences on what this means for HER — her situation "
    "and what to do with the answer. NOT a prediction: no odds, no chance, no "
    "success rate, no timeline, and no reassurance that nothing is wrong.>\n"
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

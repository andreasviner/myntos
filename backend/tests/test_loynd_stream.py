"""Reading Løynd's streamed frames, including the ones that are not answers.

Every frame here arrives over the network from another process, so the parser's
job is as much about what it refuses as what it reads: a frame it cannot
understand must cost nothing, and a frame that says the generation failed must
not be mistaken for content.
"""

from __future__ import annotations

import pytest

from myntos.integrations.loynd import LoyndUnavailableError, _delta


def test_a_content_delta_is_the_text_in_it():
    line = 'data: {"choices":[{"index":0,"delta":{"content":"Kjøp av"}}]}'
    assert _delta(line) == ["Kjøp av"]


@pytest.mark.parametrize(
    "line",
    [
        "",
        ":ping",
        "event: message",
        "data: [DONE]",
        "data: ",
        # The opening frame, which carries a role and no text.
        'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}',
        # The closing one, which carries a finish reason and no text.
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
        # The usage frame, which has no choices at all.
        'data: {"choices":[],"usage":{"total_tokens":76}}',
        # Not JSON. A frame we cannot read is not a reason to abandon the rest.
        "data: {this is not json",
        # JSON, but not an object.
        "data: [1, 2, 3]",
        # An empty string is not a piece of an answer.
        'data: {"choices":[{"delta":{"content":""}}]}',
    ],
)
def test_everything_that_is_not_content_is_nothing(line: str):
    assert _delta(line) == []


def test_a_models_monologue_is_not_its_answer():
    """`reasoning_content` has its own channel, and it is not the description.

    Streaming it into the field would type a model's thinking into the text
    that goes in the ledger.
    """
    line = 'data: {"choices":[{"delta":{"reasoning_content":"Let me think"}}]}'
    assert _delta(line) == []


def test_a_failure_after_the_first_byte_is_raised():
    """The status line is already spent by then, so the error arrives as a frame.

    Raising is what lets the caller keep the pieces that did arrive and stop
    asking for more — see extraction.extract_streaming.
    """
    with pytest.raises(LoyndUnavailableError):
        _delta('data: {"error":{"message":"capacity","type":"overloaded_error"}}')

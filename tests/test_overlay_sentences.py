from app.api.chat import _extract_overlay_sentences


def test_extracts_complete_sentences_and_keeps_remainder():
    sentences, remainder = _extract_overlay_sentences("Hello there. How are you? I am fi")
    assert sentences == ["Hello there.", "How are you?"]
    assert remainder == "I am fi"


def test_no_complete_sentence_keeps_everything_buffered():
    sentences, remainder = _extract_overlay_sentences("still typing without punctuation")
    assert sentences == []
    assert remainder == "still typing without punctuation"


def test_swallows_grouped_terminators():
    sentences, remainder = _extract_overlay_sentences("Wait... really?! Yes")
    assert sentences == ["Wait...", "really?!"]
    assert remainder == "Yes"


def test_incremental_appending_across_chunks():
    buf = ""
    out = []
    for chunk in ["The bo", "ss is nor", "th. Go now.", " Then re"]:
        buf += chunk
        got, buf = _extract_overlay_sentences(buf)
        out.extend(got)
    assert out == ["The boss is north.", "Go now."]
    # Leading whitespace on a fresh chunk stays in the remainder (harmless — the
    # caller strips before pushing); it's consumed once the next sentence closes.
    assert buf.strip() == "Then re"

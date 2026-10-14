import asyncio

import pytest

from voice.speech_output import SpeechText


def test_short_opening_comma_does_not_hide_later_sentence_boundary():
    speech = SpeechText()
    text = "好的，今天的安排已经整理好了。"

    assert "".join(speech.feed(text)) == text


def test_first_phrase_does_not_wait_for_a_long_unpunctuated_reply():
    speech = SpeechText()
    text = "这是一段还没有出现标点但是应该尽早开始播放的较长回答内容"

    phrases = speech.feed(text)

    assert phrases
    assert len(phrases[0]) <= 24
    assert "".join(phrases + speech.feed("", final=True)) == text


@pytest.mark.asyncio
async def test_stalled_text_stream_flushes_before_generation_finishes():
    queue = asyncio.Queue()
    queue.put_nowait("我在认真听")
    speech = SpeechText()
    stream = speech.stream(queue)
    try:
        assert await asyncio.wait_for(anext(stream), timeout=1) == "我在认真听"
        queue.put_nowait("，你慢慢说。")
        queue.put_nowait(None)
        assert "".join([phrase async for phrase in stream]) == "，你慢慢说。"
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_empty_and_code_only_streams_do_not_create_spoken_segments():
    queue = asyncio.Queue()
    for text in ["```python\n", "print('hello')\n", "```", None]:
        queue.put_nowait(text)
    assert [phrase async for phrase in SpeechText().stream(queue)] == []


def test_timed_flush_keeps_partial_code_fences_for_the_next_token():
    speech = SpeechText()
    assert speech.feed("听我说。``", flush=True) == ["听我说。"]
    assert speech.feed("`python\n秘密代码\n```\n继续聊天。", final=True) == ["继续聊天。"]


@pytest.mark.asyncio
async def test_backlogged_tokens_preserve_all_text_and_sentence_order():
    queue = asyncio.Queue()
    text = "你好。" + "没有标点的内容" * 60 + "最后一句。"
    for token in text:
        queue.put_nowait(token)
    queue.put_nowait(None)
    assert "".join([phrase async for phrase in SpeechText().stream(queue)]) == text

import asyncio
import re

import pytest

from app.services.voice.speech_output import SpeechText


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


@pytest.mark.parametrize("text", ["。", "？", "！”", "……", "，", "**。**", "🙂", "\n”\n"])
def test_punctuation_only_fragments_are_not_sent_to_tts(text):
    assert SpeechText().feed(text, final=True) == []


def test_closing_quote_after_a_sentence_does_not_abort_later_speech():
    speech = SpeechText()
    phrases = speech.feed("我说：“你好。")
    phrases += speech.feed("”", flush=True)
    phrases += speech.feed("接下来还有完整的回答。", final=True)
    assert phrases == ["我说：“你好。", "接下来还有完整的回答。"]


def test_backlogged_sentences_after_the_opening_share_a_tts_request():
    speech = SpeechText()
    assert speech.feed("先说第一句。第二句和第三句，应该连续播报。最后一句。", final=True) == [
        "先说第一句。",
        "第二句和第三句，应该连续播报。最后一句。",
    ]


@pytest.mark.parametrize(
    ("source", "spoken"),
    [
        ("请访问 https://www.baidu.com 获取详情。", "请访问获取详情。"),
        ("www.baidu.com", ""),
        ("这是[百度官网](https://www.baidu.com)。", "这是百度官网。"),
        ("这是一张![猫咪图片](https://example.com/cat.png)。", "这是一张猫咪图片。"),
        ("我很开心🙂，谢谢你❤️。", "我很开心，谢谢你。"),
        ('看看<img src="https://example.com/cat.png">这张图。', "看看这张图。"),
    ],
)
def test_speech_omits_visual_markup_and_raw_links(source, spoken):
    assert "".join(SpeechText().feed(source, final=True)) == spoken


def test_partial_url_and_markdown_link_wait_for_more_text():
    speech = SpeechText()
    phrases = speech.feed("请访问 https:", flush=True)
    phrases += speech.feed("//www.baidu.com 看介绍。", final=True)
    assert "".join(phrases) == "请访问看介绍。"

    speech = SpeechText()
    phrases = speech.feed("这是[百度官网](https:", flush=True)
    phrases += speech.feed("//www.baidu.com)的介绍。", final=True)
    assert "".join(phrases) == "这是百度官网的介绍。"


@pytest.mark.parametrize(
    ("source", "spoken"),
    [
        ("请访问 https://www.baidu.com 看介绍。", "请访问看介绍。"),
        ("请看 www.baidu.com 的页面。", "请看的页面。"),
        ("这是[百度官网](https://www.baidu.com)的介绍。", "这是百度官网的介绍。"),
        ('看看<img src="https://example.com/cat.png">这张图。', "看看这张图。"),
    ],
)
def test_visual_markup_is_safe_across_stream_splits(source, spoken):
    for offset in range(1, len(source)):
        speech = SpeechText()
        phrases = speech.feed(source[:offset], flush=True)
        phrases += speech.feed(source[offset:], final=True)
        actual = re.sub(r"\s+", "", "".join(phrases)).rstrip("。")
        assert actual == spoken.rstrip("。"), offset

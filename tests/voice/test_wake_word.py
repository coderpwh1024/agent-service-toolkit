import pytest

from voice.wake_word import match_wake_prefix


@pytest.mark.parametrize(
    ("text", "command", "complete"),
    [
        ("小美小美", "", True),
        ("小美，小美，天气怎么样？", "天气怎么样？", True),
        ("  小 美！小美、小美：小美，查一下天气", "查一下天气", True),
        ("小美小美小美", "", True),
        ("小美", "", False),
        ("小美，天气怎么样", "天气怎么样", False),
        ("小美小美，介绍小美的功能", "介绍小美的功能", True),
    ],
)
def test_wake_match_removes_only_leading_calls(text, command, complete):
    match = match_wake_prefix(text, "小美小美")
    assert match is not None
    assert match.command == command
    assert match.complete is complete


@pytest.mark.parametrize("text", ["今天天气很好", "请叫小美小美", "小米小米", "", "小美丽"])
def test_unrelated_text_does_not_confirm_wake(text):
    match = match_wake_prefix(text, "小美小美")
    assert match is None or not match.complete


def test_custom_keyword_still_matches_with_punctuation():
    match = match_wake_prefix("你好，助手，你好助手，查天气", "你好助手")
    assert match is not None
    assert match.complete
    assert match.command == "查天气"

from __future__ import annotations

from common import frame_helper


class FakeLocator:
    def __init__(self, visible: bool = True, count: int = 1, click_error: bool = False):
        self.visible = visible
        self._count = count
        self.click_error = click_error
        self.actions: list[str] = []

    @property
    def first(self):
        return self

    def count(self) -> int:
        return self._count

    def is_visible(self, timeout: int = 0) -> bool:
        return self.visible

    def wait_for(self, state: str = "visible", timeout: int = 0) -> None:
        self.actions.append("wait_for")

    def click(self, timeout: int = 0) -> None:
        if self.click_error:
            raise RuntimeError("click failed")
        self.actions.append("click")

    def evaluate(self, script: str, arg=None):
        self.actions.append("evaluate")
        return True


class FakeFrame:
    def __init__(
        self,
        locators: dict[str, FakeLocator] | None = None,
        evaluate_result=None,
        default_locator: FakeLocator | None = None,
    ):
        self.locators = locators or {}
        self.evaluate_result = evaluate_result
        self.default_locator = default_locator
        self.evaluate_calls: list[tuple[str, object]] = []

    def locator(self, selector: str) -> FakeLocator:
        if selector in self.locators:
            return self.locators[selector]
        if self.default_locator is not None:
            return self.default_locator
        return FakeLocator(count=0)

    def evaluate(self, script: str, arg=None):
        self.evaluate_calls.append((script, arg))
        return self.evaluate_result


class FakePage:
    def __init__(self, frames: list[FakeFrame]):
        self.frames = frames


def test_iter_contexts_includes_page_and_frames():
    page = FakePage([FakeFrame(), FakeFrame()])
    labels = [label for label, _ in frame_helper.iter_contexts(page)]
    assert labels == ["page", "frame[0]", "frame[1]"]

    only_frames = [label for label, _ in frame_helper.iter_contexts(page, include_page=False)]
    assert only_frames == ["frame[0]", "frame[1]"]


def test_find_frame_match_all_requires_every_selector():
    partial = FakeFrame({"#StDt": FakeLocator()})
    complete = FakeFrame(
        {"#StDt": FakeLocator(), "#EdDt": FakeLocator(), 'input[name="Submit4"]': FakeLocator()}
    )
    page = FakePage([partial, complete])
    frame = frame_helper.find_frame_containing(
        page,
        ["#StDt", "#EdDt", 'input[name="Submit4"]'],
        timeout=1,
        match_all=True,
    )
    assert frame is complete


def test_find_frame_prefer_visible_vs_first_hit():
    invisible = FakeFrame({"#dlAll": FakeLocator(visible=False)})
    visible = FakeFrame({"#dlAll": FakeLocator(visible=True)})
    page = FakePage([invisible, visible])

    assert frame_helper.find_frame_containing(
        page, ["#dlAll"], timeout=1, prefer_visible=True
    ) is visible
    assert frame_helper.find_frame_containing(
        page, ["#dlAll"], timeout=1, prefer_visible=False
    ) is invisible


def test_find_frame_times_out(monkeypatch):
    monkeypatch.setattr(frame_helper.time, "sleep", lambda _s: None)
    page = FakePage([FakeFrame()])
    assert (
        frame_helper.find_frame_containing(page, ["#missing"], timeout=0.05, poll=0.01) is None
    )


def test_set_input_value_dispatches_events(monkeypatch):
    monkeypatch.setattr(frame_helper.time, "sleep", lambda _s: None)
    frame = FakeFrame({"#StDt": FakeLocator()})
    frame_helper.set_input_value(frame, "#StDt", "20261120")

    assert frame.locators["#StDt"].actions == ["wait_for"]
    assert len(frame.evaluate_calls) == 1
    _script, arg = frame.evaluate_calls[0]
    assert arg == {"selector": "#StDt", "value": "20261120"}
    assert "dispatchEvent" in _script


def test_click_text_in_context_passes_args():
    context = FakeFrame(evaluate_result={"clicked": True, "tag": "BUTTON"})
    result = frame_helper.click_text_in_context(context, "查询", action="click", exact=True)
    assert result["clicked"] is True
    _script, arg = context.evaluate_calls[0]
    assert arg == {"text": "查询", "exact": True, "action": "click"}


def test_click_button_texts_uses_locator_first(monkeypatch):
    monkeypatch.setattr(frame_helper.time, "sleep", lambda _s: None)
    button = FakeLocator(visible=True)
    page = FakePage([FakeFrame(default_locator=button), FakeFrame()])

    assert frame_helper.click_button_texts(page, ["确认"]) is True
    assert button.actions == ["click"]


def test_click_button_texts_falls_back_to_js(monkeypatch):
    monkeypatch.setattr(frame_helper.time, "sleep", lambda _s: None)
    fallback = FakeFrame(evaluate_result={"clicked": True, "tag": "INPUT"})
    page = FakePage([fallback])

    assert frame_helper.click_button_texts(page, ["确认"]) is True
    assert fallback.evaluate_calls, "未走 JS 兜底"


def test_click_button_texts_returns_false_when_absent(monkeypatch):
    monkeypatch.setattr(frame_helper.time, "sleep", lambda _s: None)
    empty = FakeFrame(evaluate_result={"clicked": False})
    page = FakePage([empty])

    assert frame_helper.click_button_texts(page, ["不存在"]) is False

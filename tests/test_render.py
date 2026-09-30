from slidecast import PlaywrightRenderer


class FakePage:
    def __init__(self, context):
        self.context = context
        self.shots = []

    def set_content(self, html, wait_until=None):
        self.html = html

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path):
        self.shots.append(path)


class FakeContext:
    def __init__(self):
        self.closed = False
        self.page = FakePage(self)

    def new_page(self):
        return self.page

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self):
        self.contexts = []
        self.closed = False

    def new_context(self, **kwargs):
        self.contexts.append((kwargs, FakeContext()))
        return self.contexts[-1][1]

    def close(self):
        self.closed = True


def test_a_borrowed_browser_is_used_and_left_open(tmp_path):
    browser = FakeBrowser()
    with PlaywrightRenderer(browser=browser, wait_ms=0) as r:
        r.screenshot("<h1>hi</h1>", tmp_path / "s.png", width=640, height=400)
    kwargs, context = browser.contexts[0]
    assert kwargs["viewport"] == {"width": 640, "height": 400}
    assert context.page.shots == [str(tmp_path / "s.png")] and context.closed
    assert not browser.closed

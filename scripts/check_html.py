from html.parser import HTMLParser

VOID = {"meta", "br", "img", "hr", "input", "link", "rect", "line", "circle",
        "polygon", "polyline", "path", "use"}


class P(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.err = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.err.append((tag, list(self.stack[-3:])))


html = open("report.html", encoding="utf-8").read()
p = P()
p.feed(html)
print("unclosed:", p.stack[:6])
print("mismatch:", p.err[:6])
print("size:", len(html))
print("check sections:", html.count('<section class="check"'))
print("cards:", html.count('class="card"'))
print("details:", html.count("<details"))
print("svg:", html.count("<svg"))

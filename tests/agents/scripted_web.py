"""Deterministic scripted model for static-website missions (responses marked is_mock).

The first engineer answer has a planted defect (the menu page lacks a product the
objective requires and the home page links to a page that does not exist); the
QA agent's independent acceptance checks must catch it, and the repair fixes it.
"""
import json

from packages.contracts import ModelResponse, ModelUsage, ProviderName

WEB_OBJECTIVE = ("Build a website for the Bean There coffee shop: a home page, a menu page with espresso, "
                 "cappuccino and croissant with prices, and an order page where a table can build an order.")

WEB_PLAN = {
    "summary": "Three-page static site for Bean There: home, menu with prices, and a browser-only order builder.",
    "deliverable": "site/ with index.html, menu.html, order.html, css/style.css, js/order.js",
    "interface_contract": ("Pages: index.html (h1 'Bean There', links to menu.html and order.html), menu.html "
                           "(ul#menu with li.item for Espresso 2.50, Cappuccino 3.20, Croissant 2.10), order.html "
                           "(form#order with select#product and button#add, div#summary)."),
    "tasks": [
        {"id": "build_site", "role": "engineer", "title": "Build the website",
         "instructions": "Write site/index.html, menu.html, order.html, css/style.css and js/order.js.",
         "depends_on": []},
        {"id": "acceptance_checks", "role": "qa", "title": "Write acceptance checks",
         "instructions": "Write qa_checks/acceptance.json from the objective and contract.", "depends_on": []},
        {"id": "review_site", "role": "reviewer", "title": "Review the website",
         "instructions": "Review site/ for defects and report findings.", "depends_on": ["build_site"]},
    ],
    "clarifications_needed": [],
}

HEAD = ('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{title}</title>\n'
        '<link rel="stylesheet" href="css/style.css">\n</head>\n')

ORDER_JS = """const prices = { espresso: 2.5, cappuccino: 3.2, croissant: 2.1 };
const lines = [];
document.querySelector('#add').addEventListener('click', (event) => {
  event.preventDefault();
  const product = document.querySelector('#product').value;
  lines.push(product);
  const total = lines.reduce((sum, p) => sum + prices[p], 0);
  document.querySelector('#summary').textContent = `${lines.length} item(s), total ${total.toFixed(2)} EUR`;
});
"""


def site_files(fixed: bool):
    menu_items = ['<li class="item">Espresso 2.50</li>', '<li class="item">Croissant 2.10</li>']
    if fixed:
        menu_items.insert(1, '<li class="item">Cappuccino 3.20</li>')
    home_link = "order.html" if fixed else "orders.html"  # planted: page does not exist
    return [
        {"path": "site/index.html", "content": HEAD.format(title="Bean There") +
         '<body>\n<h1>Bean There</h1>\n<nav><a href="menu.html">Menu</a> <a href="' + home_link +
         '">Order</a></nav>\n</body>\n</html>'},
        {"path": "site/menu.html", "content": HEAD.format(title="Menu") +
         '<body>\n<h1>Menu</h1>\n<ul id="menu">\n' + "\n".join(menu_items) +
         '\n</ul>\n<a href="index.html">Home</a>\n</body>\n</html>'},
        {"path": "site/order.html", "content": HEAD.format(title="Order") +
         '<body>\n<h1>Order</h1>\n<form id="order">\n<label for="product">Product</label>\n'
         '<select id="product"><option value="espresso">Espresso</option>'
         '<option value="cappuccino">Cappuccino</option><option value="croissant">Croissant</option></select>\n'
         '<button id="add">Add</button>\n</form>\n<div id="summary"></div>\n<a href="index.html">Home</a>\n'
         '<script src="js/order.js"></script>\n</body>\n</html>'},
        {"path": "site/css/style.css", "content": "body { font-family: sans-serif; margin: 0 auto; max-width: 40rem; }\n"
         "@media (max-width: 600px) { body { padding: 0 1rem; } }\n"},
        {"path": "site/js/order.js", "content": ORDER_JS},
    ]


ACCEPTANCE = {"checks": [
    {"id": "home_exists", "description": "home page", "type": "page_exists", "page": "index.html"},
    {"id": "home_title", "description": "shop name", "type": "has_element", "page": "index.html",
     "selector": "h1", "text": "Bean There"},
    {"id": "home_links_menu", "description": "navigation to menu", "type": "links_to", "page": "index.html",
     "target": "menu.html"},
    {"id": "home_links_order", "description": "navigation to order", "type": "links_to", "page": "index.html",
     "target": "order.html"},
    {"id": "menu_items", "description": "three products", "type": "has_element", "page": "menu.html",
     "selector": "li.item", "min_count": 3},
    {"id": "menu_cappuccino", "description": "cappuccino price", "type": "contains_text", "page": "menu.html",
     "text": "Cappuccino 3.20"},
    {"id": "order_form", "description": "order builder", "type": "has_element", "page": "order.html",
     "selector": "form#order"},
    {"id": "order_add", "description": "add button", "type": "has_element", "page": "order.html",
     "selector": "button#add"},
]}


class ScriptedWebModel:
    def __init__(self, *, fix_on_repair=True, overrides=None):
        self.fix_on_repair = fix_on_repair
        self.overrides = overrides or {}
        self.requests = []

    def text_for(self, request):
        role = request.agent
        if role in self.overrides:
            value = self.overrides[role]
            return value(request) if callable(value) else value
        if role == "planner":
            return json.dumps(WEB_PLAN)
        if role == "engineer":
            repairing = "INDEPENDENT VERIFIER FAILURE REPORT" in request.prompt
            return json.dumps({"files": site_files(fixed=repairing and self.fix_on_repair),
                               "notes": "site written", "uncertainty": ""})
        if role == "qa":
            return json.dumps({"files": [{"path": "qa_checks/acceptance.json", "content": json.dumps(ACCEPTANCE)}],
                               "notes": "checks from the objective", "uncertainty": ""})
        if role == "reviewer":
            return json.dumps({"findings": [{"severity": "low", "path": "site/order.html",
                                             "message": "No way to remove an item from the order."}],
                               "notes": "advisory"})
        raise AssertionError("unexpected agent " + str(role))

    def __call__(self, request):
        self.requests.append(request)
        return ModelResponse(text=self.text_for(request), provider=ProviderName.MOCK, model_name="scripted-web-v1",
                             usage=ModelUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                             latency_ms=1, is_mock=True)

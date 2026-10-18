import time
from dataclasses import dataclass, field
from typing import Callable

from jarvis.messages import Node

WIDTH, HEIGHT = 1080, 2400
TOP = 96  # below the status bar
MARGIN = 32
SIDE_WIDTH = 220
KEYBOARD_TOP = 1450
STICKY_TOP = 2200

LAUNCHER = "com.android.launcher3"
DOORDASH = "com.dd.doordash"
APPS = {DOORDASH: "DoorDash"}
WINDOWS = {LAUNCHER: 1, DOORDASH: 2, "dashpass": 3, "upsell": 4}

VIEW = "android.view.ViewGroup"
TEXT = "android.widget.TextView"
BUTTON = "android.widget.Button"
IMAGE_BUTTON = "android.widget.ImageButton"
EDIT = "android.widget.EditText"
RADIO = "android.widget.RadioButton"
CHECKBOX = "android.widget.CheckBox"
TAB = "com.google.android.material.tabs.TabLayout$TabView"
LIST = "androidx.recyclerview.widget.RecyclerView"
SCROLL_VIEW = "android.widget.ScrollView"


@dataclass(frozen=True)
class Store:
    name: str
    rating: str
    stars: str
    time: str
    fee: str
    distance: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class MenuItem:
    name: str
    price: int
    blurb: str
    section: str
    has_options: bool = False


@dataclass
class CartLine:
    name: str
    price: int
    quantity: int = 1
    detail: str = ""

    @property
    def label(self) -> str:
        return f"{self.quantity} × {self.name}"


TONYS = "Tony's Pizzeria"
STORES = [
    Store(TONYS, "4.7★ (2,300+)", "4.7★", "25–35 min", "$0.99 delivery", "1.2 mi", ("pizza",)),
    Store("Burger Barn", "4.4★ (1,100+)", "4.4★", "20–30 min", "$1.49 delivery", "0.8 mi", ("burgers", "burger")),
    Store("Slice House", "4.5★ (900+)", "4.5★", "30–40 min", "$1.99 delivery", "2.0 mi", ("pizza",)),
    Store("Sushi Go", "4.6★ (700+)", "4.6★", "35–45 min", "$2.49 delivery", "2.4 mi", ("sushi",)),
    Store("Pizza Planet", "4.2★ (400+)", "4.2★", "40–50 min", "$2.99 delivery", "3.1 mi", ("pizza",)),
]
MENU = [
    MenuItem("Pepperoni Pizza", 1899, "Classic pepperoni, mozzarella", "Pizzas", has_options=True),
    MenuItem("Margherita Pizza", 1649, "Fresh basil, mozzarella", "Pizzas", has_options=True),
    MenuItem("Garlic Knots", 699, "Six knots with marinara", "Sides"),
    MenuItem("Soda", 249, "Coke, Sprite or Fanta, 20 oz", "Drinks"),
]
TABS = ["Popular", "Pizzas", "Sides", "Drinks"]
SIZES = [("Small", '10"', 1499), ("Medium", '12"', 1899), ("Large", '16"', 2499)]
CRUSTS = ["Regular", "Thin"]
TOPPINGS = [("Extra cheese", 150), ("Mushrooms", 100)]
DRINKS = [("Coke", 249), ("Sprite", 249)]
TIPS = [200, 347, 500]
DELIVERY_FEE = 99
SERVICE_FEE = 175


def money(cents: int) -> str:
    return f"${cents // 100:,}.{cents % 100:02d}"


@dataclass(eq=False)
class El:
    text: str | None = None
    description: str | None = None
    hint: str | None = None
    resource_id: str | None = None
    class_name: str = VIEW
    clickable: bool = False
    editable: bool = False
    scrollable: bool = False
    checkable: bool = False
    checked: bool = False
    selected: bool = False
    focused: bool = False
    heading: bool = False
    enabled: bool = True
    scroll: list[str] = field(default_factory=list)
    children: list["El"] = field(default_factory=list)
    tap: Callable[[], None] | None = None
    typed: Callable[[str], None] | None = None
    submit: Callable[[], None] | None = None
    scrolled: Callable[[str], None] | None = None
    key: str | None = None
    h: int = 0  # height of a leaf; containers grow to fit their children
    w: int = 0  # fixed width; 0 fills the parent
    pad: int = 0
    across: bool = False  # children side by side
    side: bool = False  # sits at the right edge of its parent row
    at: int | None = None  # fixed top, for sticky bottom buttons
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)


def text(value: str, **kw) -> El:
    return El(text=value, class_name=TEXT, h=70, **kw)


def heading(value: str, **kw) -> El:
    return El(text=value, class_name=TEXT, heading=True, h=110, **kw)


def button(value: str, tap: Callable[[], None] | None = None, **kw) -> El:
    kw.setdefault("class_name", BUTTON)
    kw.setdefault("h", 120)
    return El(text=value, clickable=True, tap=tap or (lambda: None), **kw)


def group(*children: El, tap: Callable[[], None] | None = None, **kw) -> El:
    return El(children=list(children), clickable=tap is not None, tap=tap, **kw)


def back_button(tap: Callable[[], None]) -> El:
    return El(description="Back", class_name=IMAGE_BUTTON, clickable=True, tap=tap, w=144, h=144)


@dataclass
class Page:
    title: str | None
    els: list[El]
    keyboard_top: int | None = None
    loading: bool = False


@dataclass
class Entry:
    node: Node
    el: El
    key: str
    fingerprint: tuple


@dataclass
class Built:
    package: str
    window_id: int
    title: str | None
    keyboard_top: int | None
    loading: bool
    entries: dict[int, Entry]
    by_key: dict[str, int]

    def nodes(self) -> list[Node]:
        return [entry.node for entry in self.entries.values()]

    def find(self, key: str) -> Entry | None:
        index = self.by_key.get(key)
        return self.entries[index] if index is not None else None


class FakeDash:
    def __init__(self, cart_leftover: bool = False):
        self.package = LAUNCHER
        self.page = "dd_home"
        self.history: list[str] = []
        self.popup: str | None = None
        self.opened_before = False
        self.search_text = ""
        self.query = ""
        self.store = TONYS
        self.tab = "Popular"
        self.item = MENU[0].name
        self.size: str | None = None
        self.crust = "Regular"
        self.toppings: set[str] = set()
        self.quantity = 1
        self.cart = [CartLine("Garlic Knots", 699)] if cart_leftover else []
        self.tip = 347
        self.offsets: dict[str, int] = {}
        self.menu_swapped = False
        self.placing_reads = 0
        self.placing_since = 0.0

    # ---- what the phone calls ----

    def snapshot(self) -> Built:
        window_id = WINDOWS[self.package]
        if self.package == DOORDASH and self.popup:
            page = Page(None, getattr(self, POPUPS[self.popup])())
            window_id = WINDOWS[self.popup]
        elif self.package == DOORDASH:
            page = getattr(self, PAGES[self.page])()
        else:
            page = getattr(self, PAGES[self.package])()
        y = TOP
        for el in page.els:
            if el.at is not None:
                _place(el, MARGIN, el.at, WIDTH - MARGIN)
            else:
                y = _place(el, MARGIN, y, WIDTH - MARGIN)
        entries, by_key = _flatten(page.els)
        return Built(self.package, window_id, page.title, page.keyboard_top, page.loading, entries, by_key)

    def advance(self) -> None:
        if self.package == DOORDASH and self.page == "dd_placing":
            if self.placing_reads >= 2 or time.monotonic() - self.placing_since >= 1.5:
                self.page = "dd_placed"

    def screen_sent(self) -> None:
        if self.package == DOORDASH and self.page == "dd_placing":
            self.placing_reads += 1

    def open(self, package: str) -> bool:
        if package not in APPS:
            return False
        if package == DOORDASH and not self.opened_before:
            self.opened_before = True
            self.page = "dd_home"
            self.popup = "dashpass"
        self.package = package
        return True

    def home(self) -> None:
        self.package = LAUNCHER

    def back(self) -> None:
        if self.package == LAUNCHER:
            return
        if self.package != DOORDASH:
            self.package = LAUNCHER
        elif self.popup:
            self.popup = None
        elif self.page == "dd_placing":
            return
        elif self.page == "dd_placed":
            self.page, self.history = "dd_home", []
        elif self.history:
            self.page = self.history.pop()
        else:
            self.package = LAUNCHER

    def show_popup(self) -> None:
        self.popup = "dashpass"

    def swap_menu(self) -> None:
        self.menu_swapped = not self.menu_swapped

    def menu(self) -> list[MenuItem]:
        return [MENU[1], MENU[0], *MENU[2:]] if self.menu_swapped else list(MENU)

    def subtotal(self) -> int:
        return sum(line.price * line.quantity for line in self.cart)

    # ---- state changes ----

    def _go(self, page: str) -> None:
        self.history.append(self.page)
        self.page = page
        self.offsets.clear()

    def _back_to(self, page: str) -> None:
        while self.history and self.page != page:
            self.page = self.history.pop()

    def _search_for(self, query: str) -> None:
        self.query = self.search_text = query
        self._go("dd_results")

    def _submit_search(self) -> None:
        if self.search_text.strip():
            self._search_for(self.search_text.strip())

    def _open_store(self, name: str) -> None:
        self.store, self.tab = name, "Popular"
        self._go("dd_store")

    def _set_tab(self, tab: str) -> None:
        self.tab = tab
        self.offsets.pop("menu", None)

    def _add_menu_item(self, item: MenuItem) -> None:
        if item.has_options:
            self.item, self.size, self.crust, self.toppings, self.quantity = item.name, None, "Regular", set(), 1
            self._go("dd_item")
        else:
            self._add_line(CartLine(item.name, item.price))

    def _add_line(self, new: CartLine) -> None:
        for line in self.cart:
            if line.name == new.name and line.detail == new.detail:
                line.quantity += new.quantity
                return
        self.cart.append(new)

    def _item_price(self) -> int:
        item = next(m for m in MENU if m.name == self.item)
        base = item.price
        if self.size:
            base = dict((name, price) for name, _, price in SIZES)[self.size] + item.price - MENU[0].price
        extras = sum(price for name, price in TOPPINGS if name in self.toppings)
        return base + extras

    def _toggle_topping(self, name: str) -> None:
        self.toppings ^= {name}

    def _change_quantity(self, by: int) -> None:
        self.quantity = max(1, self.quantity + by)

    def _add_item_to_cart(self) -> None:
        details = (["Thin crust"] if self.crust == "Thin" else []) + sorted(self.toppings)
        self._add_line(CartLine(f"{self.size} {self.item}", self._item_price(), self.quantity, ", ".join(details)))
        self._back_to("dd_store")
        if not any(line.name in dict(DRINKS) or line.name == "Soda" for line in self.cart):
            self.popup = "upsell"

    def _add_drink(self, name: str, price: int) -> None:
        self._add_line(CartLine(name, price))
        self.popup = None

    def _remove_line(self, index: int) -> None:
        del self.cart[index]

    def _place_order(self) -> None:
        self.cart = []
        self.page, self.history = "dd_placing", []
        self.placing_reads, self.placing_since = 0, time.monotonic()

    def _scroll(self, name: str, direction: str) -> None:
        self.offsets[name] = self.offsets.get(name, 0) + (1 if direction == "down" else -1)

    def _set(self, name: str, value) -> Callable[[], None]:
        return lambda: setattr(self, name, value)

    def _typer(self, name: str) -> Callable[[str], None]:
        return lambda value: setattr(self, name, value)

    # ---- pages ----

    def _paged_list(self, name: str, pages: list[list[El]], **kw) -> El:
        offset = max(0, min(self.offsets.get(name, 0), len(pages) - 1))
        scroll = (["down"] if offset < len(pages) - 1 else []) + (["up"] if offset > 0 else [])
        return El(class_name=kw.pop("class_name", LIST), scrollable=True, scroll=scroll, children=pages[offset],
                  scrolled=lambda direction: self._scroll(name, direction), key=name, **kw)

    def _list(self, name: str, rows: list[El], size: int, **kw) -> El:
        starts = list(range(0, max(len(rows) - size, 0) + 1, size))
        if starts[-1] + size < len(rows):
            starts.append(len(rows) - size)
        return self._paged_list(name, [rows[start:start + size] for start in starts], **kw)

    def _store_row(self, store: Store) -> El:
        return group(text(store.name), text(f"{store.stars} · {store.time} · {store.fee}"),
                     tap=lambda: self._open_store(store.name), key=f"store:{store.name}", pad=24)

    def _launcher(self) -> Page:
        icons = [button(label, lambda p=package: self.open(p), class_name=TEXT, h=200) for package, label in APPS.items()]
        return Page(None, [group(*icons, across=True)])

    def _home(self) -> Page:
        categories = [button(name, lambda q=name: self._search_for(q)) for name in ("Pizza", "Burgers", "Sushi")]
        return Page("DoorDash", [
            text("Deliver to 12 Main St"),
            button("Search DoorDash", lambda: self._go("dd_search"), class_name=TEXT,
                   resource_id=f"{DOORDASH}:id/search_bar"),
            group(*categories, across=True),
            self._list("nearby", [self._store_row(s) for s in STORES[:4]], 2, description="Nearby"),
        ])

    def _search(self) -> Page:
        search_field = El(text=self.search_text or None, hint="Search stores, dishes", class_name=EDIT,
                          resource_id=f"{DOORDASH}:id/search_input", editable=True, clickable=True, focused=True,
                          h=130, tap=lambda: None, typed=self._typer("search_text"),
                          submit=self._submit_search)
        recent = [button(query, lambda q=query: self._search_for(q), class_name=TEXT) for query in ("tacos", "sushi near me")]
        return Page("Search", [back_button(self.back), search_field, heading("Recent searches"), *recent],
                    keyboard_top=KEYBOARD_TOP)

    def _results(self) -> Page:
        query = self.query.casefold()
        found = [s for s in STORES if query in s.name.casefold() or any(tag in query for tag in s.tags)]
        els = [back_button(self.back), heading(f'Results for "{self.query}"')]
        if found:
            els.append(self._list("results", [self._store_row(s) for s in found], 3, description="Stores"))
        else:
            els.append(text(f'No results for "{self.query}"'))
        return Page("Search results", els)

    def _store_page(self) -> Page:
        store = next(s for s in STORES if s.name == self.store)
        els = [back_button(self.back), heading(store.name), text(f"{store.rating} · {store.distance} · {store.fee}")]
        if store.name != TONYS:
            return Page(store.name, [*els, text("Closed right now · opens at 5:00 PM")])
        tabs = [El(text=tab, class_name=TAB, clickable=True, selected=tab == self.tab, h=110,
                   tap=lambda t=tab: self._set_tab(t)) for tab in TABS]
        items = [m for m in self.menu() if self.tab == "Popular" or m.section == self.tab]
        rows = [group(text(m.name), text(money(m.price)), text(m.blurb),
                      button("Add", lambda m=m: self._add_menu_item(m), side=True, h=96),
                      tap=lambda m=m: self._add_menu_item(m), key=f"menu:{m.name}", pad=24) for m in items]
        els += [group(*tabs, across=True),
                self._list("menu", rows, 3, description="Most ordered" if self.tab == "Popular" else self.tab)]
        if self.cart:
            count = sum(line.quantity for line in self.cart)
            label = f"View cart · {count} item{'s' if count > 1 else ''} · {money(self.subtotal())}"
            els.append(button(label, lambda: self._go("dd_cart"), at=STICKY_TOP, h=140))
        return Page(store.name, els)

    def _item_page(self) -> Page:
        item = next(m for m in MENU if m.name == self.item)
        offset = item.price - MENU[0].price
        sizes = [El(text=f"{name} {inches} {money(price + offset)}", class_name=RADIO, checkable=True, clickable=True,
                    checked=self.size == name, tap=self._set("size", name), h=130) for name, inches, price in SIZES]
        crusts = [El(text=name, class_name=RADIO, checkable=True, clickable=True, checked=self.crust == name,
                     tap=self._set("crust", name), h=130) for name in CRUSTS]
        toppings = [El(text=f"{name} +{money(price)}", class_name=CHECKBOX, checkable=True, clickable=True,
                       checked=name in self.toppings, tap=lambda n=name: self._toggle_topping(n), h=130)
                    for name, price in TOPPINGS]
        quantity = group(text("Quantity"), button("−", lambda: self._change_quantity(-1)), text(str(self.quantity)),
                         button("+", lambda: self._change_quantity(1)), across=True, key="quantity")
        add = button(f"Add to cart · {money(self._item_price() * self.quantity)}", self._add_item_to_cart,
                     enabled=self.size is not None, at=STICKY_TOP, h=140)
        return Page(item.name, [
            back_button(self.back), heading(item.name), text(item.blurb),
            group(heading("Choose a size (required)"), *sizes, key="sizes"),
            group(heading("Crust"), *crusts, key="crusts"),
            group(heading("Toppings"), *toppings, key="toppings"),
            quantity, add,
        ])

    def _cart_page(self) -> Page:
        els = [back_button(self.back), heading(f"Your cart · {TONYS}")]
        if not self.cart:
            els.append(text("Your cart is empty"))
        for i, line in enumerate(self.cart):
            parts = [text(line.label), *([text(line.detail)] if line.detail else []), text(money(line.price * line.quantity))]
            els.append(group(*parts, button("Remove", lambda i=i: self._remove_line(i), side=True, h=96),
                             key=f"line:{line.name}:{line.detail}", pad=24))
        els.append(group(text("Subtotal"), text(money(self.subtotal())), across=True, key="subtotal"))
        els.append(button("Checkout", lambda: self._go("dd_checkout"), enabled=bool(self.cart), at=STICKY_TOP, h=140))
        return Page("Your cart", els)

    def _checkout_page(self) -> Page:
        subtotal = self.subtotal()
        total = subtotal + DELIVERY_FEE + SERVICE_FEE + self.tip
        chips = [button(money(tip), self._set("tip", tip), selected=tip == self.tip, h=110) for tip in TIPS]
        tip_block = group(heading("Dasher tip"), group(*chips, across=True, key="tips"), key="tip_block")
        amounts = [("Subtotal", subtotal), ("Delivery fee", DELIVERY_FEE), ("Service fee", SERVICE_FEE),
                   ("Tip", self.tip), ("Total", total)]
        summary = [group(text(name), text(money(cents)), across=True, key=f"sum:{name}") for name, cents in amounts]
        top = [text("Deliver to 12 Main St, Apt 4"), text("25–35 min"),
               group(text("Payment"), text("Visa •••• 4242"), tap=lambda: None, key="payment", pad=24), tip_block]
        bottom = [tip_block, *summary, button("Place order", self._place_order, h=140)]
        return Page("Checkout", [back_button(self.back), heading("Checkout"),
                                 self._paged_list("checkout", [top, bottom], class_name=SCROLL_VIEW)])

    def _placing_page(self) -> Page:
        return Page("Placing your order", [heading("Placing your order…")], loading=True)

    def _placed_page(self) -> Page:
        return Page("Order placed", [heading("Order placed!"), text("Order #4512"), text("Arriving 7:45–7:55 PM"),
                                     button("Track order")])

    def _dashpass_popup(self) -> list[El]:
        return [heading("Get $0 delivery fees with DashPass"), button("Try free for 30 days"),
                button("No thanks", self._set("popup", None))]

    def _upsell_popup(self) -> list[El]:
        rows = [group(text(name), text(money(price)), button("Add", lambda n=name, p=price: self._add_drink(n, p),
                                                             side=True, h=96), key=f"drink:{name}", pad=24)
                for name, price in DRINKS]
        return [heading("Add a drink?"), *rows, button("No thanks", self._set("popup", None))]



PAGES = {
    LAUNCHER: "_launcher",
    "dd_home": "_home",
    "dd_search": "_search",
    "dd_results": "_results",
    "dd_store": "_store_page",
    "dd_item": "_item_page",
    "dd_cart": "_cart_page",
    "dd_checkout": "_checkout_page",
    "dd_placing": "_placing_page",
    "dd_placed": "_placed_page",
}
POPUPS = {"dashpass": "_dashpass_popup", "upsell": "_upsell_popup"}


def _place(el: El, left: int, top: int, right: int) -> int:
    if el.w:
        right = min(right, left + el.w)
    flow = [child for child in el.children if not child.side]
    side = [child for child in el.children if child.side]
    if not el.children:
        bottom = top + el.h
    elif el.across:
        step = (right - left) // max(len(flow), 1)
        bottom = top
        for i, child in enumerate(flow):
            bottom = max(bottom, _place(child, left + i * step, top, left + (i + 1) * step))
    else:
        y = top + el.pad
        inner_right = right - el.pad - (SIDE_WIDTH if side else 0)
        for child in flow:
            y = _place(child, left + el.pad, y, inner_right)
        bottom = max(y + el.pad, top + max((child.h for child in side), default=0) + 2 * el.pad)
    for child in side:
        child_top = top + (bottom - top - child.h) // 2
        _place(child, right - SIDE_WIDTH, child_top, right - el.pad)
    el.bounds = (left, top, right, bottom)
    return bottom


def _has_content(el: El) -> bool:
    return bool(el.text or el.description or el.hint) or el.clickable or el.editable or el.scrollable or el.checkable


def _kept(el: El) -> bool:
    return _has_content(el) or sum(_holds_kept(child) for child in el.children) >= 2


def _holds_kept(el: El) -> bool:
    return _kept(el) or any(_holds_kept(child) for child in el.children)


def _flatten(roots: list[El]) -> tuple[dict[int, Entry], dict[str, int]]:
    found: list[tuple[El, int | None, str]] = []
    used: set[str] = set()

    def visit(el: El, parent: int | None, parent_key: str) -> None:
        base = el.key or f"{parent_key}/{el.text or el.description or el.hint or el.class_name.rsplit('.', 1)[-1]}"
        key, n = base, 2
        while key in used:
            key, n = f"{base}#{n}", n + 1
        used.add(key)
        index = parent
        if _kept(el):
            found.append((el, parent, key))
            index = len(found)
        for child in el.children:
            visit(child, index, key)

    for root in roots:
        visit(root, None, "")

    texts: dict[int, list[str]] = {i: [] for i in range(1, len(found) + 1)}
    for i in range(len(found), 0, -1):
        el, parent, _ = found[i - 1]
        texts[i][:0] = [t for t in (el.text, el.description) if t]
        if parent:
            texts[parent][:0] = texts[i]

    entries: dict[int, Entry] = {}
    for i, (el, parent, key) in enumerate(found, start=1):
        node = Node(
            index=i, parent=parent, text=el.text, description=el.description, hint=el.hint,
            resource_id=el.resource_id, class_name=el.class_name, bounds=el.bounds, clickable=el.clickable,
            editable=el.editable, scrollable=el.scrollable, checkable=el.checkable, checked=el.checked,
            selected=el.selected, focused=el.focused, heading=el.heading, enabled=el.enabled, scroll=list(el.scroll),
        )
        group_text = " ".join(texts[parent]) if parent else ""
        fingerprint = (el.class_name, el.resource_id, el.text, el.description, group_text)
        entries[i] = Entry(node, el, key, fingerprint)
    return entries, {entry.key: i for i, entry in entries.items()}

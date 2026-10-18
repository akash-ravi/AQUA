from jarvis.messages import Node, Screen
from jarvis.screen import (
    describe_change,
    effective_target,
    find_lines,
    normalize,
    proof_texts,
    render,
    signature,
    subtree_text,
)

APPS = {"com.dd.doordash": "DoorDash"}


def make_screen(nodes: list[dict], **fields) -> Screen:
    values = {"screen_id": "s-1", "package_name": "com.dd.doordash", "width": 1080, "height": 2400}
    values.update(fields)
    return Screen(**values, nodes=[Node(**node) for node in nodes])


def menu_nodes(selected_tab: str = "Popular") -> list[dict]:
    return [
        {"index": 1, "text": "Back", "class_name": "android.widget.ImageButton", "bounds": (0, 96, 144, 240), "clickable": True},
        {"index": 2, "text": "Tony's Pizzeria", "heading": True, "bounds": (48, 260, 900, 340)},
        {"index": 3, "text": "4.7★ (2,300+) · 1.2 mi · $0.99 delivery", "bounds": (48, 350, 1000, 400)},
        {"index": 4, "class_name": "androidx.recyclerview.widget.RecyclerView", "bounds": (0, 420, 1080, 2300),
         "scrollable": True, "scroll": ["down"]},
        {"index": 5, "parent": 4, "bounds": (0, 420, 1080, 700), "clickable": True},
        {"index": 6, "parent": 5, "text": "Pepperoni Pizza", "bounds": (48, 450, 700, 510)},
        {"index": 7, "parent": 5, "text": "$18.99 · Classic pepperoni, mozzarella", "bounds": (48, 520, 700, 570)},
        {"index": 8, "parent": 5, "text": "Add", "class_name": "android.widget.Button", "bounds": (860, 520, 1032, 600),
         "clickable": True},
        {"index": 9, "parent": 4, "bounds": (0, 700, 1080, 980), "clickable": True},
        {"index": 10, "parent": 9, "text": "Margherita Pizza", "bounds": (48, 730, 700, 790)},
        {"index": 11, "parent": 9, "text": "$16.49 · Fresh basil, mozzarella", "bounds": (48, 800, 700, 850)},
        {"index": 12, "parent": 9, "text": "Add", "class_name": "android.widget.Button", "bounds": (860, 800, 1032, 880),
         "clickable": True},
    ]


MENU_TEXT = """App: DoorDash | Screen: Tony's Pizzeria | Keyboard: closed
[1] button "Back"
[2] heading "Tony's Pizzeria"
[3] text "4.7★ (2,300+) · 1.2 mi · $0.99 delivery"
[4] list (more below)
  [5] item "Pepperoni Pizza · $18.99 · Classic pepperoni, mozzarella"
    [8] button "Add" (for Pepperoni Pizza)
  [9] item "Margherita Pizza · $16.49 · Fresh basil, mozzarella"
    [12] button "Add" (for Margherita Pizza)"""


def test_menu_renders_exactly_like_the_spec():
    view = render(make_screen(menu_nodes(), title="Tony's Pizzeria"), APPS)
    assert view.text == MENU_TEXT
    assert view.app == "DoorDash"
    assert view.title == "Tony's Pizzeria"
    assert sorted(view.by_index) == [1, 2, 3, 4, 5, 8, 9, 12]
    assert view.by_index[8].for_item == "Pepperoni Pizza"
    assert view.by_index[5].depth == 1 and view.by_index[8].depth == 2


def test_title_falls_back_to_first_heading_and_app_to_package():
    view = render(make_screen(menu_nodes()))
    assert view.title == "Tony's Pizzeria"
    assert view.text.startswith("App: com.dd.doordash | Screen: Tony's Pizzeria | Keyboard: closed")


def test_loading_is_in_the_header():
    view = render(make_screen(menu_nodes(), loading=True), APPS)
    assert view.text.splitlines()[0].endswith(" | Loading")


def test_field_with_hint_and_keyboard():
    nodes = [
        {"index": 1, "description": "Back", "bounds": (0, 96, 144, 240), "clickable": True},
        {"index": 2, "class_name": "android.widget.EditText", "hint": "Search stores, dishes", "bounds": (150, 96, 1000, 240),
         "editable": True, "focused": True, "clickable": True},
        {"index": 3, "text": "Recent searches", "bounds": (48, 300, 600, 360)},
        {"index": 4, "text": "sushi", "bounds": (48, 380, 600, 440), "clickable": True},
        {"index": 5, "text": "Hidden under keyboard", "bounds": (48, 1500, 600, 1560), "clickable": True},
        {"index": 6, "text": "Half covered", "bounds": (48, 1400, 600, 1500)},
    ]
    view = render(make_screen(nodes, keyboard_top=1450), APPS)
    lines = view.text.splitlines()
    assert lines[0] == "App: DoorDash | Screen: (no title) | Keyboard: open, typing in [2]"
    assert '[2] field (empty, hint "Search stores, dishes") (typing here)' in lines
    assert 5 not in view.by_index
    assert 6 in view.by_index


def test_field_value_password_and_error():
    nodes = [
        {"index": 1, "text": "pizza", "editable": True, "bounds": (0, 0, 500, 100)},
        {"index": 2, "hint": "Password", "editable": True, "password": True, "bounds": (0, 200, 500, 300)},
        {"index": 3, "text": "Name", "editable": True, "error": "Choose a size", "bounds": (0, 400, 500, 500)},
    ]
    view = render(make_screen(nodes, keyboard_top=2000), APPS)
    lines = view.text.splitlines()
    assert lines[0].endswith("Keyboard: open")
    assert '[1] field "pizza"' in lines
    assert '[2] field (password) (empty, hint "Password")' in lines
    assert '[3] field "Name" (error: "Choose a size")' in lines


def test_repeated_buttons_get_their_item_from_an_earlier_sibling():
    nodes = [
        {"index": 1, "text": "Coke $2.49", "bounds": (0, 100, 500, 200)},
        {"index": 2, "text": "Order", "clickable": True, "bounds": (600, 100, 900, 200)},
        {"index": 3, "text": "Sprite $2.49", "bounds": (0, 300, 500, 400)},
        {"index": 4, "text": "Order", "clickable": True, "bounds": (600, 300, 900, 400)},
        {"index": 5, "text": "Checkout", "clickable": True, "bounds": (0, 2200, 1080, 2300)},
    ]
    view = render(make_screen(nodes), APPS)
    assert view.by_index[2].for_item == "Coke $2.49"
    assert view.by_index[4].for_item == "Sprite $2.49"
    assert view.by_index[5].for_item is None
    assert '[4] button "Order" (for Sprite $2.49)' in view.text


def test_folded_children_and_dropped_groups():
    nodes = [
        {"index": 1, "bounds": (0, 0, 1080, 2400)},
        {"index": 2, "parent": 1, "bounds": (0, 100, 1080, 300), "clickable": True},
        {"index": 3, "parent": 2, "text": "Tony's Pizzeria", "bounds": (10, 110, 500, 150)},
        {"index": 4, "parent": 2, "bounds": (10, 160, 500, 290)},
        {"index": 5, "parent": 4, "text": "4.7★", "bounds": (10, 160, 200, 200)},
        {"index": 6, "parent": 4, "text": "25–35 min", "bounds": (210, 160, 500, 200)},
        {"index": 7, "parent": 1, "bounds": (0, 400, 1080, 600)},
        {"index": 8, "parent": 7, "text": "1 × Large Pepperoni Pizza", "bounds": (10, 410, 700, 460)},
        {"index": 9, "parent": 7, "text": "$24.99", "description": "$24.99", "bounds": (800, 410, 1000, 460)},
        {"index": 10, "parent": 1, "class_name": "android.widget.ImageView", "bounds": (0, 700, 300, 900)},
        {"index": 11, "parent": 1, "class_name": "android.widget.ImageView", "description": "Pizza photo",
         "bounds": (0, 900, 300, 1100)},
        {"index": 12, "parent": 1, "class_name": "android.widget.ImageButton", "clickable": True,
         "bounds": (980, 0, 1080, 100)},
        {"index": 13, "parent": 1, "resource_id": "com.dd.doordash:id/cart_button", "clickable": True,
         "bounds": (900, 2300, 1000, 2400)},
    ]
    view = render(make_screen(nodes), APPS)
    assert view.text.splitlines()[1:] == [
        '[2] button "Tony\'s Pizzeria · 4.7★ · 25–35 min"',
        '[8] text "1 × Large Pepperoni Pizza"',
        '[9] text "$24.99"',
        '[11] image "Pizza photo"',
        "[12] button (no label, top-right)",
        '[13] button "id: cart_button"',
    ]


def test_clickable_child_inside_a_row_is_still_printed():
    nodes = [
        {"index": 1, "clickable": True, "bounds": (0, 0, 1080, 200)},
        {"index": 2, "parent": 1, "text": "Extra cheese +$1.50", "bounds": (0, 0, 800, 200)},
        {"index": 3, "parent": 1, "text": "Info", "clickable": True, "bounds": (800, 0, 1080, 200)},
    ]
    view = render(make_screen(nodes), APPS)
    assert view.text.splitlines()[1:] == ['[1] item "Extra cheese +$1.50"', '  [3] button "Info"']


def test_roles_and_states():
    nodes = [
        {"index": 1, "text": 'Large 16" $24.99', "class_name": "android.widget.RadioButton", "checkable": True,
         "checked": True, "clickable": True, "bounds": (0, 0, 1080, 100)},
        {"index": 2, "text": "Wi-Fi", "class_name": "android.widget.Switch", "checkable": True, "bounds": (0, 100, 1080, 200)},
        {"index": 3, "text": "Mushrooms +$1.00", "class_name": "android.widget.CheckBox", "checkable": True,
         "bounds": (0, 200, 1080, 300)},
        {"index": 4, "text": "Popular", "class_name": "com.google.android.material.tabs.TabLayout$TabView", "clickable": True,
         "selected": True, "bounds": (0, 300, 300, 400)},
        {"index": 5, "text": "Add to cart · $0.00", "clickable": True, "enabled": False, "bounds": (0, 2200, 1080, 2300)},
        {"index": 6, "text": "Rows", "class_name": "android.widget.TableLayout", "bounds": (0, 400, 1080, 500)},
        {"index": 7, "text": "Quantity", "state": "2 of 5", "bounds": (0, 500, 1080, 600)},
        {"index": 8, "text": "Dark mode", "state": "On", "class_name": "android.widget.Switch", "checkable": True,
         "checked": True, "bounds": (0, 600, 1080, 700)},
    ]
    view = render(make_screen(nodes), APPS)
    assert view.text.splitlines()[1:] == [
        '[1] radio "Large 16" $24.99" (checked)',
        '[2] switch "Wi-Fi" (off)',
        '[3] check "Mushrooms +$1.00" (not checked)',
        '[4] tab "Popular" (selected)',
        '[5] button "Add to cart · $0.00" (disabled)',
        '[6] text "Rows"',
        '[7] text "Quantity" (2 of 5)',
        '[8] switch "Dark mode" (on)',
    ]


def test_scroll_hints():
    def list_line(directions):
        nodes = [{"index": 1, "scrollable": True, "scroll": directions, "bounds": (0, 0, 1080, 2000)}]
        return render(make_screen(nodes), APPS).text.splitlines()[1]

    assert list_line(["down"]) == "[1] list (more below)"
    assert list_line(["up"]) == "[1] list (more above)"
    assert list_line(["up", "down"]) == "[1] list (more above and below)"
    assert list_line(["right"]) == "[1] list (more to the right)"
    assert list_line(["left"]) == "[1] list (more to the left)"
    assert list_line([]) == "[1] list"


def test_long_labels_and_lines_are_cut():
    long_text = "word " * 60
    nodes = [{"index": 1, "text": long_text, "bounds": (0, 0, 1080, 100), "state": "x" * 200}]
    view = render(make_screen(nodes), APPS)
    line = view.lines[0]
    assert len(line.label) == 80 and line.label.endswith("…")
    shown = view.text.splitlines()[1]
    assert len(shown) == 160 and shown.endswith("…")


def test_too_many_lines_drops_list_texts_first_and_keeps_the_last_lines():
    nodes = [{"index": 1, "text": "Top button", "clickable": True, "bounds": (0, 0, 1080, 100)},
             {"index": 2, "scrollable": True, "scroll": ["down"], "bounds": (0, 100, 1080, 2000)}]
    for i in range(3, 33):
        nodes.append({"index": i, "parent": 2, "text": f"Row {i}", "bounds": (0, 100 + i * 10, 1080, 105 + i * 10)})
    for i in range(33, 43):
        nodes.append({"index": i, "text": f"Bottom {i}", "clickable": True, "bounds": (0, 2000, 1080, 2100)})
    view = render(make_screen(nodes), APPS, max_lines=20)
    assert len(view.lines) == 20
    shown = [line.index for line in view.lines]
    assert shown[:2] == [1, 2]
    assert shown[-8:] == list(range(35, 43))
    assert all(index not in view.by_index for index in range(33 - 22, 33))
    assert view.text.splitlines()[-1] == "(… 22 more lines not shown)"


def test_over_the_cap_without_list_texts_drops_other_lines():
    nodes = [{"index": i, "text": f"Button {i}", "clickable": True, "bounds": (0, i * 10, 1080, i * 10 + 10)}
             for i in range(1, 31)]
    view = render(make_screen(nodes), APPS, max_lines=10)
    assert [line.index for line in view.lines] == [1, 2] + list(range(23, 31))
    assert view.text.endswith("(… 20 more lines not shown)")


def test_describe_change_new_screen():
    before = render(make_screen(menu_nodes(), title="Tony's Pizzeria"), APPS)
    after = render(make_screen([{"index": 1, "text": "Your cart", "heading": True, "bounds": (0, 0, 500, 100)}]), APPS)
    assert describe_change(before, after, True) == "new screen: Your cart"
    assert describe_change(before, after, False) == "new screen: Your cart (screen was still changing)"


def size_nodes(selected: str | None, price: str) -> list[dict]:
    nodes = [{"index": 1, "text": "Pepperoni Pizza", "heading": True, "bounds": (0, 0, 1080, 100)}]
    for i, size in enumerate(["Small 10in $14.99", "Medium 12in $18.99", "Large 16in $24.99"], start=2):
        nodes.append({"index": i, "text": size, "class_name": "android.widget.RadioButton", "checkable": True,
                      "selected": size == selected, "clickable": True, "bounds": (0, i * 100, 1080, i * 100 + 100)})
    nodes.append({"index": 5, "text": price, "clickable": True, "bounds": (0, 2200, 1080, 2300)})
    return nodes


def test_describe_change_selection_and_label():
    before = render(make_screen(size_nodes(None, "Add to cart")), APPS)
    after = render(make_screen(size_nodes("Large 16in $24.99", "Add to cart · $24.99")), APPS)
    assert before.signature != after.signature
    assert describe_change(before, after, True) == (
        'same screen; changed: "Large 16in $24.99" now selected; button now "Add to cart · $24.99"'
    )


def test_describe_change_selection_only_is_not_no_change():
    before = render(make_screen(size_nodes("Small 10in $14.99", "Add")), APPS)
    after = render(make_screen(size_nodes("Large 16in $24.99", "Add")), APPS)
    change = describe_change(before, after, True)
    assert change.startswith("same screen; changed: ")
    assert '"Small 10in $14.99" no longer selected' in change
    assert '"Large 16in $24.99" now selected' in change


def test_describe_change_no_change():
    before = render(make_screen(menu_nodes(), title="Tony's Pizzeria"), APPS)
    after = render(make_screen(menu_nodes(), title="Tony's Pizzeria", screen_id="s-2"), APPS)
    assert describe_change(before, after, True) == "no change"
    assert describe_change(before, after, False) == "no change (screen was still changing)"


def test_describe_change_typed_text_and_keyboard():
    field = {"index": 1, "hint": "Search", "editable": True, "bounds": (0, 0, 1080, 100)}
    rest = [{"index": 2, "text": "Recent", "bounds": (0, 200, 1080, 300)}, {"index": 3, "text": "Go", "clickable": True,
                                                                             "bounds": (0, 300, 1080, 400)}]
    before = render(make_screen([field, *rest]), APPS)
    after = render(make_screen([{**field, "text": "pizza"}, *rest], keyboard_top=1500), APPS)
    assert describe_change(before, after, True) == 'same screen; changed: keyboard opened; field now "pizza"'


def test_describe_change_added_line():
    base = size_nodes(None, "Add")
    before = render(make_screen(base), APPS)
    after = render(make_screen([*base, {"index": 6, "text": "Choose a size", "bounds": (0, 1000, 1080, 1100)}]), APPS)
    assert describe_change(before, after, True) == 'same screen; changed: new text "Choose a size"'


def clock_nodes(clock: str, eta: str) -> list[dict]:
    return [
        {"index": 1, "text": "Order placed!", "heading": True, "bounds": (0, 0, 1080, 100)},
        {"index": 2, "text": clock, "bounds": (0, 100, 1080, 200)},
        {"index": 3, "text": eta, "bounds": (0, 200, 1080, 300)},
        {"index": 4, "text": "Track order", "clickable": True, "bounds": (0, 300, 1080, 400)},
    ]


def test_signature_ignores_clock_digits():
    before = render(make_screen(clock_nodes("7:41 PM", "Arriving in 12 min")), APPS)
    after = render(make_screen(clock_nodes("7:42 PM", "Arriving in 11 min")), APPS)
    assert signature(before) == signature(after) == before.signature
    assert describe_change(before, after, True) == "no change"


def test_signature_changes_with_keyboard_and_clickable_digits():
    base = clock_nodes("7:41 PM", "soon")
    plain = render(make_screen(base), APPS)
    with_keyboard = render(make_screen(base, keyboard_top=1500), APPS)
    assert plain.signature != with_keyboard.signature
    changed = [*base[:3], {**base[3], "text": "Track order 2"}]
    assert render(make_screen(changed), APPS).signature != plain.signature


def test_effective_target_and_subtree_text():
    screen = make_screen(menu_nodes())
    assert effective_target(screen, 6).index == 5
    assert effective_target(screen, 8).index == 8
    assert effective_target(screen, 3).index == 3
    text = subtree_text(screen, 5)
    assert "Pepperoni Pizza" in text and "$18.99" in text and "Add" in text
    loop = make_screen([{"index": 1, "parent": 2, "text": "a", "bounds": (0, 0, 10, 10)},
                        {"index": 2, "parent": 1, "text": "b", "bounds": (0, 0, 10, 10)}])
    assert effective_target(loop, 1).index == 1


def test_proof_texts_skip_fields_and_passwords():
    nodes = [
        {"index": 1, "text": "Order placed!", "bounds": (0, 0, 1080, 100)},
        {"index": 2, "text": "Order #4512", "bounds": (0, 100, 1080, 200)},
        {"index": 3, "text": "typed by me", "editable": True, "bounds": (0, 200, 1080, 300)},
        {"index": 4, "clickable": True, "bounds": (0, 300, 1080, 400)},
        {"index": 5, "parent": 4, "text": "Arriving 7:45–7:55 PM", "bounds": (0, 300, 1080, 350)},
    ]
    texts = proof_texts(render(make_screen(nodes), APPS))
    assert "Order placed!" in texts and "Order #4512" in texts
    assert "Arriving 7:45–7:55 PM" in texts
    assert "typed by me" not in texts


def test_find_lines_and_normalize():
    view = render(make_screen(menu_nodes()), APPS)
    assert [line.index for line in find_lines(view, "add")] == [8, 12]
    assert [line.index for line in find_lines(view, "  ADD ", role="button")] == [8, 12]
    assert find_lines(view, "Add", role="item") == []
    assert normalize("  Tony’s   Pizzeria — “Large”!! ") == "tony's pizzeria - \"large\""
    assert normalize("Ｏｒｄｅｒ placed…") == "order placed"

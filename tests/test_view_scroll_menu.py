"""0.16.1: one scroll area, and a More menu that looks like the kit's menus."""

from __future__ import annotations

import re


def _page() -> str:
    from inventory_plugin.view import PAGE

    return PAGE


def test_only_main_scrolls_and_it_contains_its_absolutely_positioned_children():
    css = _page()
    # The kit's checkbox inputs and .sr-only labels are position:absolute. Without a positioned
    # .main they sat against the page and stretched it to the list's height: two scrollbars.
    main = re.search(r"\.main\{([^}]*)\}", css).group(1)
    assert "overflow:auto" in main and "position:relative" in main and "overscroll-behavior:contain" in main
    assert re.search(r"html,body\{height:100%;overflow:hidden\}", css)


def test_more_menu_items_are_kit_menu_items_not_buttons():
    page = _page()
    assert '<button class="pl-menu__item"' not in page
    assert 'class="pl-menu__item" role="menuitem" tabindex="-1"' in page
    # The kit highlights through data-highlighted (Radix sets it; we set it ourselves).
    assert 'setAttribute("data-highlighted", "")' in page
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Escape"):
        assert f'e.key === "{key}"' in page

"""Responsive policy in logical 96-DPI pixels, shared by layout and tests."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Layout:
    navigation: str
    sidebar: int
    padding: int
    metric_columns: int
    form_columns: int
    provider_columns: int
    page_width: int


def layout_for(width: float) -> Layout:
    width = max(320, width)
    navigation = "full" if width >= 1040 else "compact" if width >= 760 else "top"
    sidebar = {"full": 190, "compact": 88, "top": 0}[navigation]
    padding = 24 if width >= 900 else 14
    page_width = max(240, int(width - sidebar - 2 * padding - 18))
    return Layout(navigation, sidebar, padding,
                  4 if page_width >= 740 else 2 if page_width >= 390 else 1,
                  2 if page_width >= 620 else 1,
                  2 if page_width >= 450 else 1,
                  page_width)

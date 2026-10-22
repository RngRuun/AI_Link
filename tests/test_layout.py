"""Headless responsive-policy and refresh-state regression tests; no desktop input."""
import unittest
import queue
from unittest.mock import Mock

from ailink.desktop_ui import DesktopUI
from ailink.layout import layout_for


class LayoutTests(unittest.TestCase):
    def test_small_window_retains_content_width_by_using_top_navigation(self):
        layout=layout_for(560)
        self.assertEqual(layout.navigation,"top")
        self.assertEqual(layout.sidebar,0)
        self.assertEqual(layout.form_columns,1)
        self.assertEqual(layout.metric_columns,2)
        self.assertGreaterEqual(layout.page_width,500)

    def test_medium_window_uses_compact_navigation(self):
        layout=layout_for(800)
        self.assertEqual(layout.navigation,"compact")
        self.assertEqual(layout.sidebar,88)
        self.assertEqual(layout.form_columns,2)

    def test_wide_window_uses_full_navigation_and_four_metrics(self):
        layout=layout_for(1440)
        self.assertEqual(layout.navigation,"full")
        self.assertEqual(layout.metric_columns,4)
        self.assertEqual(layout.form_columns,2)

    def test_breakpoints_keep_minimum_space_for_each_column(self):
        for width in range(560,2561,7):
            with self.subTest(width=width):
                layout=layout_for(width)
                if layout.form_columns==2:
                    self.assertGreaterEqual(layout.page_width,620)
                if layout.metric_columns==4:
                    self.assertGreaterEqual(layout.page_width,740)
                if layout.navigation=="top":
                    self.assertEqual(layout.sidebar,0)

    def test_boundary_transitions(self):
        self.assertEqual(layout_for(759).navigation,"top")
        self.assertEqual(layout_for(760).navigation,"compact")
        self.assertEqual(layout_for(1039).navigation,"compact")
        self.assertEqual(layout_for(1040).navigation,"full")


class FakeTree:
    def __init__(self):
        self.rows={"0":("old",)}
        self.selected=("0",)
        self.v=(.4,.9)
        self.h=(.3,.8)
        self.deletes=0
    def selection(self): return self.selected
    def yview(self): return self.v
    def xview(self): return self.h
    def get_children(self): return tuple(self.rows)
    def delete(self,*items):
        self.deletes+=1
        self.rows.clear()
    def insert(self,parent,position,iid,values): self.rows[iid]=values
    def exists(self,item): return item in self.rows
    def selection_set(self,items): self.selected=tuple(items)
    def yview_moveto(self,value): self.v=(value,self.v[1])
    def xview_moveto(self,value): self.h=(value,self.h[1])


class RefreshTests(unittest.TestCase):
    def test_render_error_does_not_stop_future_ui_queue_polls(self):
        ui=DesktopUI.__new__(DesktopUI)
        ui.root=Mock()
        ui.messages=queue.Queue()
        ui.messages.put(("state",{"example":True}))
        ui.render=Mock(side_effect=RuntimeError("simulated render failure"))
        ui.callback_error=Mock()
        ui.drain()
        ui.callback_error.assert_called_once()
        ui.root.after.assert_called_once_with(100,ui.drain)

    def test_refresh_preserves_selection_and_both_scroll_positions(self):
        ui=DesktopUI.__new__(DesktopUI)
        tree=FakeTree()
        ui.replace_rows(tree,[("new",),("another",)])
        self.assertEqual(tree.selected,("0",))
        self.assertEqual(tree.v[0],.4)
        self.assertEqual(tree.h[0],.3)

    def test_unchanged_data_does_not_rebuild_table(self):
        ui=DesktopUI.__new__(DesktopUI)
        tree=FakeTree()
        ui.replace_rows(tree,[("stable",)])
        before=tree.deletes
        ui.replace_rows(tree,[("stable",)])
        self.assertEqual(tree.deletes,before)


if __name__=="__main__":
    unittest.main()

"""Status widget update contract; no game environment."""
import unittest
from unittest.mock import Mock,patch
from autoplayer.ui import Application

class StatusDisplayTests(unittest.TestCase):
    def test_short_and_long_messages_never_change_widget_geometry(self):
        app=Application.__new__(Application);app.status=Mock();app.status_text=Mock();app.status_text._last_text=None
        with patch('autoplayer.dashboard.replace_text') as replace:
            for text in ('gameplay / 移動','現在の画面から攻略を進める。'*500,'停止しました'):
                app.status.get.return_value=text;app.refresh_status_text()
                replace.assert_called_with(app.status_text,text)
        app.status_text.configure.assert_not_called();app.status_text.pack.assert_not_called()
    def test_unchanged_message_is_not_repainted(self):
        app=Application.__new__(Application);app.status=Mock();app.status.get.return_value='移動';app.status_text=Mock();app.status_text._last_text='移動'
        with patch('autoplayer.dashboard.replace_text') as replace:app.refresh_status_text();replace.assert_not_called()

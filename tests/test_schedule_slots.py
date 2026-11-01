import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import campus_watch as watch


class ScheduledSlotTests(unittest.TestCase):
    def test_delayed_retry_preserves_its_intended_slot(self):
        now=dt.datetime(2026,11,1,16,10,tzinfo=dt.timezone(dt.timedelta(hours=8)))
        self.assertEqual(watch.monitor_slot(now,'0,10,20 1 * * *'),'2026-11-01T09:00+08:00')
        self.assertEqual(watch.monitor_slot(now,'0,10,20 7 * * *'),'2026-11-01T15:00+08:00')
        self.assertEqual(watch.monitor_slot(now.replace(hour=2),'0,10,20 7 * * *'),'2026-10-31T15:00+08:00')

    def test_later_completed_slot_also_suppresses_an_older_delayed_event(self):
        state={'completed_slot':'2026-11-01T15:00+08:00'}
        self.assertTrue(watch.slot_complete(state,'2026-11-01T09:00+08:00'))
        self.assertFalse(watch.slot_complete(state,'2026-11-02T09:00+08:00'))
        self.assertFalse(watch.slot_complete({'completed_slot':'broken'},'2026-11-01T09:00+08:00'))

    def test_successful_slot_skips_retry_but_manual_run_still_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp);(data/'jobs.json').write_text('[]')
            health={'sources':[{'source_id':'watch-jd','last_count':0}]}
            with patch.object(watch,'DATA',data),patch.object(watch,'STATE',data/'notify.json'),patch.object(watch,'monitor_slot',return_value='2026-11-01T15:00+08:00'),patch.object(watch.sync,'run',return_value=health) as sync,patch.object(watch,'deliver') as send,patch('sys.argv',['watch']),patch.dict('os.environ',{'GITHUB_EVENT_NAME':'schedule','GITHUB_STEP_SUMMARY':''}):
                watch.main()
                self.assertEqual(sync.call_count,1)
                self.assertTrue((data/'monitor_slot_state.json').exists())
                watch.main()
                self.assertEqual(sync.call_count,1)
                with patch.dict('os.environ',{'GITHUB_EVENT_NAME':'workflow_dispatch'}):watch.main()
                self.assertEqual(sync.call_count,2)
                send.assert_not_called()

    def test_failed_delivery_keeps_slot_unfinished_and_retry_sends_once(self):
        with tempfile.TemporaryDirectory() as temp:
            data=Path(temp)
            job={'company_name':'京东','source_id':'watch-jd','title':'2027届产品经理','identifier':'new','official_url':'https://example.com/job/42'}
            (data/'jobs.json').write_text(json.dumps([job]),encoding='utf-8')
            state=data/'notify.json';state.write_text(json.dumps({'sources_initialized':['watch-jd'],'delivered':{}}))
            health={'sources':[{'source_id':'watch-jd','last_count':1}]}
            with patch.object(watch,'DATA',data),patch.object(watch,'STATE',state),patch.object(watch,'monitor_slot',return_value='2026-11-01T15:00+08:00'),patch.object(watch.sync,'run',return_value=health),patch('sys.argv',['watch']),patch.dict('os.environ',{'GITHUB_EVENT_NAME':'schedule','GITHUB_STEP_SUMMARY':''}):
                with patch.object(watch,'deliver',side_effect=RuntimeError('offline')):
                    with self.assertRaises(RuntimeError):watch.main()
                self.assertFalse((data/'monitor_slot_state.json').exists())
                with patch.object(watch,'deliver') as send:
                    watch.main();watch.main()
                    send.assert_called_once()

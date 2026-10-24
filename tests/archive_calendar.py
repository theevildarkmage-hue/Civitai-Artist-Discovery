"""Canonical Central calendar, DST and non-destructive legacy-boundary conflict."""
import test_safety,tempfile,unittest,json,socket
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from discovery.archive_calendar import build_request
from discovery.history import HistoryArchive,previous_local_day,LOCAL_ZONE
from community_harness import Harness
from v2_visual_baseline import install_fixture
from playwright.sync_api import sync_playwright

class Calendar(unittest.TestCase):
    def test_fall_back_repeated_hour_round_trips(self):
        for hour,fold in ((6,0),(7,1)):
            utc=datetime(2026,11,1,hour,30,tzinfo=timezone.utc)
            local=utc.astimezone(LOCAL_ZONE)
            self.assertEqual(local.hour,1);self.assertEqual(local.fold,fold)
            self.assertEqual(local.astimezone(timezone.utc),utc)

    def test_central_default_and_dst_day_lengths(self):
        self.assertEqual(previous_local_day(datetime(2026,10,24,2,tzinfo=timezone.utc)),'2026-10-22')
        for date,hours in (('2026-03-08',23),('2025-11-02',25)):
            value,start,end,zone,segment=build_request({'date':date})
            elapsed=(datetime.fromisoformat(end).astimezone(timezone.utc)-datetime.fromisoformat(start).astimezone(timezone.utc)).total_seconds()/3600
            self.assertEqual(elapsed,hours);self.assertEqual(zone,'America/Chicago')
        with self.assertRaises(ValueError):build_request({'date':'2026-10-20','startUtc':'2026-10-20T00:00:00Z','endUtc':'2026-10-21T00:00:00Z'})

    def test_conflicting_archive_cannot_resume_or_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=HistoryArchive(Path(temp)/'history')
            key='2026-10-20#morning'
            with archive.connect() as db:
                db.execute("insert into days(day,complete,timezone,start_utc,end_utc) values(?,0,'UTC','2026-10-20T00:00:00Z','2026-10-20T12:00:00Z')",(key,))
            def snapshot():
                with archive.connect() as db:return [tuple(r) for r in db.execute('select * from days')]
            before=snapshot();args=build_request({'date':'2026-10-20','segment':'morning'})
            for method in (archive.start,archive.rebuild):
                with self.assertRaisesRegex(ValueError,'preserved'):method(*args)
                self.assertEqual(snapshot(),before)

    def test_browser_machine_timezone_does_not_change_archive_calendar(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        h=Harness(port=port,sample=False)
        try:
            h.start()
            with sync_playwright() as pw:
                browser=pw.chromium.launch(headless=True)
                for timezone_id in ('UTC','America/Los_Angeles','Asia/Tokyo'):
                    page=browser.new_page(timezone_id=timezone_id,extra_http_headers=h.headers())
                    page.clock.install(time=datetime(2026,10,24,2,tzinfo=timezone.utc))
                    install_fixture(page,h.base)
                    page.goto(h.base,wait_until='networkidle')
                    self.assertEqual(page.evaluate('centralToday()'),'2026-10-23')
                    self.assertEqual(page.evaluate('galleryCalendarState().newest'),'2026-10-22')
                    self.assertEqual(page.evaluate('dayRequest("2026-10-20","morning")'),{'date':'2026-10-20','segment':'morning','timezone':'America/Chicago'})
                    page.close()
                browser.close()
        finally:h.stop()

if __name__=='__main__':unittest.main()

"""Community is enabled only after delayed auth bootstrap establishes the account."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture

server=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'
try:
 with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    for width in (1440,390):
        page=browser.new_page(viewport={'width':width,'height':900})
        install_fixture(page,base,cached_tags=True)
        pending=[];errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/api/auth-status',lambda route:pending.append(route))
        page.route('**/api/community/config',lambda r:r.fulfill(json={'development':False}))
        page.route('**/api/community/status*',lambda r:r.fulfill(json={'member':{'id':42},'quota':{'remaining':3},'beta':{'available':True,'submissions':False}}))
        page.route('**/api/community/gallery*',lambda r:r.fulfill(json={'items':[],'nextCursor':None}))
        page.goto(base,wait_until='domcontentloaded')
        tab=page.locator('#tabCommunity');tab.wait_for()
        assert tab.is_disabled(),'Community was clickable before authorization bootstrap'
        page.wait_for_timeout(150)
        assert pending and tab.is_disabled()
        page.unroute('**/api/auth-status')
        page.route('**/api/auth-status',lambda r:r.fulfill(json={'connected':True,'id':42,'username':'SyntheticReady','socialWrite':True}))
        for route in pending:route.fulfill(json={'connected':True,'id':42,'username':'SyntheticReady','socialWrite':True})
        page.wait_for_function('()=>!document.querySelector("#tabCommunity").disabled')
        tab.click()
        page.wait_for_function('()=>document.querySelector("#tabCommunity").getAttribute("aria-pressed")==="true"')
        assert page.evaluate('sessionStorage.getItem("civitai-active-view")')=='community'
        assert not errors,errors
        page.close()
    browser.close()
finally:server.shutdown();server.server_close()
print({'delayedAuthDesktopMobile':True,'tabDisabledUntilReady':True,'firstEnabledClickNavigates':True})

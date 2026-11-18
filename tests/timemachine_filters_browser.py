"""Time Machine shares supported content controls and rejects stale filter replies."""
import test_safety
from functools import partial
from http.server import ThreadingHTTPServer
import json,threading
from playwright.sync_api import sync_playwright
from v2_visual_baseline import ROOT,StaticHandler,install_fixture,creator

http=ThreadingHTTPServer(('127.0.0.1',0),partial(StaticHandler,directory=str(ROOT/'static')))
threading.Thread(target=http.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{http.server_port}'
try:
 with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True);page=browser.new_page(viewport={'width':1440,'height':1000})
    install_fixture(page,base,cached_tags=True)
    settings={'browsingLevels':[1,2],'contentRating':'Soft','applyTagFilters':True,'applyAccountFilters':True,'applyImageFilters':True,'mediaType':'video'}
    def config(route):
        if route.request.method=='POST':settings.update(json.loads(route.request.post_data))
        route.fulfill(json=settings)
    page.route('**/api/settings',config)
    status={'creators':1,'primed':1,'images':1,'priming':False,'progress':100}
    def payload(i):
        c=creator(i);c['representative']['tagState']={'known':True,'tags':[]}
        return {'cards':[c], 'status':status}
    phase={'hold':False};held=[]
    def machine(route):
        if phase['hold']:held.append(route)
        else:route.fulfill(json=payload(2 if settings['applyImageFilters'] else 3))
    page.route('**/api/timemachine',machine)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base,wait_until='networkidle');page.locator('.creator-card').first.wait_for()
    page.locator('#tabTimeMachine').click();page.locator('#timeMachineGrid [data-id="2"]').wait_for()
    page.locator('#filterToggle').click()
    assert page.locator('#filterPanel').is_visible()
    assert page.locator('#mediaType').is_hidden() and page.locator('#modelMenu').is_hidden()
    assert page.locator('#calendarToggle').is_hidden()
    assert page.locator('#filterToggle').get_attribute('data-active-count')=='0'
    page.locator('#applyImageFilters').uncheck()
    page.locator('#timeMachineGrid [data-id="3"]').wait_for()
    assert settings['applyImageFilters'] is False
    page.keyboard.press('Escape');assert page.locator('#filterPanel').is_hidden()
    # An earlier slow read must not replace the post-filter response.
    phase['hold']=True;page.evaluate('() => {refreshTimeMachine()}')
    page.wait_for_timeout(100);assert held
    page.locator('#filterToggle').click();phase['hold']=False
    page.locator('#applyImageFilters').check()
    page.locator('#timeMachineGrid [data-id="2"]').wait_for()
    held.pop().fulfill(json=payload(99))
    page.wait_for_timeout(200)
    assert page.locator('#timeMachineGrid [data-id="99"]').count()==0
    page.locator('#tabGallery').click()
    assert page.locator('#filterPanel').is_hidden()
    page.locator('#filterToggle').click();assert page.locator('#mediaType').is_visible()
    assert page.locator('#mediaType').input_value()=='video','Time Machine must preserve Daily media selection'
    page.locator('#tabTimeMachine').click()
    # A creator view invalidated while on another tab must refill when returning.
    creator_reads=[]
    def history(route):
        creator_reads.append(route.request.url)
        entry=payload(2)['cards'][0]
        route.fulfill(json={'username':'Studio_002','images':[{**entry['representative'],'position':1}],
                           'cards':[entry],'knownCount':1,'seenCount':0,'complete':True,'hasMore':False})
    page.route('**/api/timemachine/creator?**',history)
    page.evaluate("() => openTimeMachineCreator('Studio_002')")
    page.locator('#timeMachineCreatorGrid .creator-card').wait_for()
    page.locator('#tabGallery').click()
    page.evaluate("() => {window.dispatchEvent(new Event('content-controls-changing'));window.dispatchEvent(new Event('community-controls-changed'))}")
    assert page.locator('#timeMachineCreatorGrid .creator-card').count()==0
    reads=len(creator_reads)
    page.locator('#tabTimeMachine').click()
    page.locator('#timeMachineCreatorGrid .creator-card').wait_for()
    assert len(creator_reads)>reads
    page.set_viewport_size({'width':390,'height':844});page.locator('#filterToggle').click()
    assert page.locator('#applyTagFilters').is_visible()
    assert not page.evaluate('() => document.documentElement.scrollWidth > innerWidth + 1')
    assert not errors,errors
    browser.close()
finally:http.shutdown();http.server_close()
print({'timeMachineFiltersVisible':True,'savedToggleReloads':True,'lateReadRejected':True,'desktopAndMobile':True,'mediaScopePreserved':True})

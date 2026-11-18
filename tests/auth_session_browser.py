"""Open-page auth reconciliation; real UI/local server, synthetic auth/social HTTP."""
import test_safety
import json
import socket
from playwright.sync_api import sync_playwright
from community_harness import Harness

with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
h = Harness(port=port, sample=False)
try:
    h.start()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(extra_http_headers=h.headers(), viewport={'width': 1280, 'height': 900})
        page.clock.install()
        ready = {'connected': True, 'cachedSession': True, 'id': 42, 'username': 'SyntheticRenewal',
                 'socialWrite': True, 'sessionState': 'ready'}
        state = {'auth': ready, 'fail': False, 'hold': False}
        timeline, held, writes, pending_writes, errors = [], [], [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def reply(route, value, status=200):
            route.fulfill(status=status, content_type='application/json', body=json.dumps(value))
        def auth(route):
            timeline.append({'at': page.evaluate('Date.now()'), 'state': state['auth']['sessionState'], 'failed': state['fail']})
            if state['hold']:
                held.append(route); return
            reply(route, {'error': 'Synthetic offline'} if state['fail'] else state['auth'], 503 if state['fail'] else 200)
        page.route('**/api/auth-status', auth)
        page.route('**/api/discovery/summary', lambda r: reply(r, {'hasData': False, 'lastSyncAt': '2099-01-01T00:00:00Z'}))
        page.route('**/api/creator-metadata?*', lambda r: reply(r, {'creators': {}}))
        page.route('**/api/reaction-status?*', lambda r: reply(r, {'images': {}}))
        page.route('**/api/community/beta', lambda r: reply(r, {'production': False, 'enabled': True}))
        page.route('**/api/community/status*', lambda r: reply(r, {'member': {'id': 42}, 'quota': {'remaining': 3}}))
        page.route('**/api/community/gallery*', lambda r: reply(r, {'items': [], 'nextCursor': None}))
        def write(route):
            writes.append(route.request.url); pending_writes.append(route)
        page.route('**/api/follow', write)
        page.route('**/api/reaction', write)
        page.goto(h.base, wait_until='networkidle')
        page.wait_for_function('() => oauthConnected && socialWrite')
        page.evaluate('''() => {
          const image = {id:1001,type:'image',url:'/community-fixture.svg?id=1001',thumbnailUrl:'/community-fixture.svg?id=1001',
            stats:{likeCount:5,reactionCount:5},tagState:{known:true,tags:[]}};
          imageReactionState.set('1001',{reactions:['Like'],stats:image.stats});
          const card=window.DiscoveryGallery.createCard({username:'SyntheticArtist',userId:101,imageCount:1,representative:image},
            {images:[image],seenObserver:{observe(){},unobserve(){}}});
          card.id='authRecoveryCard';document.querySelector('main').append(card);
          window.__authCard=card;
        }''')
        card = page.locator('#authRecoveryCard')
        reaction = card.locator('[data-reaction="Like"]')
        follow = card.locator('.follow-button')
        page.wait_for_function('() => !document.querySelector("#authRecoveryCard [data-reaction=Like]").disabled')
        page.wait_for_function('''() => {
          const card=document.querySelector('#authRecoveryCard');
          return card.getAttribute('aria-busy') !== 'true' && !card.querySelector('[data-reaction=Like]').disabled && !card.querySelector('.follow-button').disabled;
        }''')
        assert reaction.is_enabled() and follow.is_enabled(), page.evaluate('({socialWrite, busy:window.__authCard.getAttribute("aria-busy"), html:window.__authCard.outerHTML})')
        page.evaluate("document.body.style.minHeight='2500px';scrollTo(0,700)")
        scroll_before = page.evaluate('scrollY')
        state['auth'] = {**ready, 'socialWrite': False, 'sessionState': 'refreshing'}
        page.evaluate('refreshAuth()')
        assert reaction.is_disabled() and follow.is_disabled()
        assert 'Refreshing' in reaction.get_attribute('title')
        state['auth'] = ready
        page.clock.fast_forward(1000)
        page.wait_for_function('() => socialWrite')
        assert reaction.is_enabled() and follow.is_enabled()
        assert page.evaluate('window.__authCard===document.querySelector("#authRecoveryCard")')
        assert 'selected' in reaction.get_attribute('class')
        assert reaction.locator('b').inner_text() == '5'
        assert page.evaluate('scrollY') == scroll_before
        assert not writes, 'Polling must never replay user actions'
        page.screenshot(path=str(test_safety.REPORTS/'auth-renewal-restored.png'))

        state['auth'] = {**ready, 'socialWrite': False, 'sessionState': 'refreshing'}
        page.evaluate('refreshAuth()')
        state['auth'] = {**ready, 'socialWrite': False, 'sessionState': 'offline'}
        page.clock.fast_forward(1000)
        page.wait_for_function('() => document.querySelector("#accountStatus").textContent.includes("offline")')
        assert reaction.is_disabled() and follow.is_disabled()
        state['auth'] = ready; page.clock.fast_forward(2000)
        page.wait_for_function('() => socialWrite')

        state['fail'] = True
        page.evaluate('refreshAuth().catch(()=>{})')
        assert reaction.is_disabled() and follow.is_disabled()
        assert 'offline' in page.locator('#accountStatus').inner_text()
        state['fail'] = False
        page.clock.fast_forward(1000); page.wait_for_function('() => socialWrite')
        for auth_state, expected in [
            ({**ready, 'socialWrite': False, 'needsReauthentication': True, 'sessionState': 'reauthentication_required'}, 'Session expired'),
            ({**ready, 'socialWrite': False}, 'not grant'),
        ]:
            state['auth'] = auth_state; page.evaluate('refreshAuth()')
            assert reaction.is_disabled() and expected in reaction.get_attribute('title')
            before = len(timeline); page.clock.fast_forward(2000)
            assert len(timeline) == before
        state['auth'] = ready; page.evaluate('refreshAuth()')

        # Reconciliation must preserve the lock on a pending explicit mutation.
        follow.click(); assert len(pending_writes) == 1
        page.evaluate('refreshAuth()'); assert follow.is_disabled()
        reply(pending_writes.pop(), {'following': True, 'userId': 101})
        page.wait_for_function('() => !document.querySelector("#authRecoveryCard .follow-button").disabled')
        reaction.click(); assert len(pending_writes) == 1
        page.evaluate('refreshAuth()'); assert reaction.is_disabled()
        state['auth'] = {**ready, 'socialWrite': False, 'sessionState': 'offline'}
        page.evaluate('refreshAuth()')
        reply(pending_writes.pop(), {'error': 'Synthetic write failure'}, 503)
        page.wait_for_function('() => !document.querySelector("#authRecoveryCard [data-reaction=Like]").dataset.reactionPending')
        assert reaction.is_disabled(), 'Failed writes cannot re-enable unusable grants'

        state['hold'] = True
        page.evaluate('void refreshAuth().catch(()=>{}); void refreshAuth().catch(()=>{})')
        page.wait_for_timeout(50); assert len(held) == 1
        page.evaluate('authSession.reset({connected:false})')
        try: reply(held.pop(), ready)
        except Exception: pass  # Aborted transport is expected; unit test also covers ignored abort.
        page.wait_for_timeout(50)
        assert page.evaluate('!oauthConnected && !socialWrite')
        state['hold'] = False; state['auth'] = {**ready, 'id': 43, 'username': 'OtherSyntheticAccount'}
        page.evaluate('refreshAuth()')
        assert page.evaluate('tagAccount === "43" && !imageReactionState.size')
        assert not card.locator('[data-reaction].selected').count()
        assert len(writes) == 2 and not errors, (writes, errors)
        (test_safety.REPORTS/'auth-timeline.json').write_text(json.dumps(timeline, indent=2), encoding='utf-8')
        browser.close()
    print('PASS: existing DOM refreshing/ready, offline/failed polls, revoked/missing grants, pending writes, stale reply and account switch; auth/social transport simulated, no external writes')
finally:
    h.stop()

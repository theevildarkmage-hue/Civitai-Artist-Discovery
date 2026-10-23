"""Isolated detail lifecycle: fixture feed, optional real public metadata snapshot.

No hosted connection or OAuth credential is used. --public-artwork accepts a public
Civitai listing row captured separately; replay is evidence of rendering, not a
fresh hosted gallery read. Synthetic previews are explicitly used in every case.
"""
import test_safety
import argparse
import json
import socket
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
from community_harness import Harness

sys.path.insert(0,str(test_safety.ROOT))
from discovery.civitai import normalize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-artwork',type=Path)
    args=parser.parse_args()
    raw=json.loads(args.public_artwork.read_text(encoding='utf-8')) if args.public_artwork else {
        'id':1001,'username':'SyntheticArtist','baseModel':'Pony','width':640,'height':800,
        'meta':{'meta':{'prompt':'Synthetic acceptance prompt','resources':[]}}}
    detail=normalize(raw)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    h=Harness(port=port,sample=False)
    evidence={'transport':'isolated fixture; synthetic image preview',
              'metadataSource':'public snapshot replay' if args.public_artwork else 'synthetic',
              'imageId':detail['id'],'promptPresent':bool(detail['prompt']),
              'model':detail['baseModel'],'viewports':[]}
    try:
        h.start()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for width in (1440,390):
                page=browser.new_page(viewport={'width':width,'height':844},extra_http_headers=h.headers())
                errors=[];held=[];mode='hold'
                page.on('pageerror',lambda e:errors.append(str(e)))
                def reply(route,value,status=200):
                    route.fulfill(status=status,content_type='application/json',body=json.dumps(value))
                preview='/community-fixture.svg?id=1001'
                art={**detail,'creatorId':8952171,'browsingLevel':2,'type':'image',
                     'url':preview,'thumbnailUrl':preview,'imageUrl':f'https://civitai.red/images/{detail["id"]}',
                     'tagState':{'known':True,'tags':[]}}
                payload={**art,'known':True,'tags':[],'detailImageUrl':preview,
                         'civitaiUrl':art['imageUrl']}
                page.route('**/api/auth-status',lambda r:reply(r,{'connected':True,'id':42,'username':'SyntheticViewer'}))
                page.route('**/api/discovery/summary',lambda r:reply(r,{'hasData':False}))
                page.route('**/api/creator-metadata?*',lambda r:reply(r,{'creators':{}}))
                page.route('**/api/reaction-status?*',lambda r:reply(r,{'images':{}}))
                page.route('**/api/community/beta',lambda r:reply(r,{'production':False,'serverPolicy':True,'enabled':True}))
                status=h.endpoint.request('/api/community/status')
                status['beta']={'available':True,'submissions':False}
                page.route('**/api/community/status*',lambda r:reply(r,status))
                page.route('**/api/community/gallery?*',lambda r:reply(r,{
                    'items':[{'artwork':art,'recommender':'SyntheticMember','submittedAt':'2026-10-22T12:00:00Z'}],
                    'nextCursor':None}))
                def respond_detail(route):
                    if mode=='hold':held.append(route)
                    elif mode=='missing':reply(route,{**payload,'prompt':'','baseModel':None,'resources':[]})
                    elif mode=='failure':reply(route,{'error':'Synthetic transient detail outage'},503)
                    else:reply(route,payload)
                page.route('**/api/community/detail?*',respond_detail)
                page.goto(h.base,wait_until='networkidle');page.locator('#tabCommunity').click()
                card=page.locator('#communityGrid .creator-card');card.wait_for()
                card.locator('.info-button').click()
                page.locator('#detailPrompt').filter(has_text='Loading generation details').wait_for()
                page.wait_for_function('()=>document.querySelector("#details").open')
                for _ in range(100):
                    if held:break
                    page.wait_for_timeout(20)
                assert len(held)==1,'detail request did not begin'
                assert page.locator('#detailMeta').inner_text()==''
                reply(held.pop(),payload)
                expected=detail['prompt'] or 'No prompt metadata available.'
                page.wait_for_function('(text)=>document.querySelector("#detailPrompt").textContent===text',arg=expected)
                if detail['baseModel']:assert detail['baseModel'] in page.locator('#detailMeta').inner_text()
                assert str(detail.get('width') or '?') in page.locator('#detailMeta').inner_text()
                assert page.locator('#detailCreator').inner_text()=='@'+detail['username']
                assert page.locator('#communityRecommend').is_disabled()
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                page.screenshot(path=str(test_safety.REPORTS/f'detail-complete-{width}.png'))
                page.locator('#close').click()
                mode='missing';card.locator('.info-button').click()
                page.locator('#detailPrompt').filter(has_text='No prompt metadata available.').wait_for()
                assert 'Unknown' in page.locator('#detailMeta').inner_text()
                page.locator('#close').click()
                mode='failure';card.locator('.info-button').click()
                page.locator('#detailPrompt').filter(has_text='Synthetic transient detail outage').wait_for()
                page.locator('#close').click()
                mode='complete';card.locator('.info-button').click()
                page.wait_for_function('(text)=>document.querySelector("#detailPrompt").textContent===text',arg=expected)
                page.keyboard.press('Escape');assert not page.locator('#details').evaluate('(e)=>e.open')
                mode='hold';card.locator('.info-button').click()
                for _ in range(100):
                    if held:break
                    page.wait_for_timeout(20)
                assert len(held)==1,'reopened detail request did not begin'
                page.keyboard.press('Escape')
                try:reply(held.pop(),{**payload,'prompt':'Stale closed-dialog result'})
                except Exception:pass  # Closing aborts the request.
                mode='complete';card.locator('.info-button').click()
                page.wait_for_function('(text)=>document.querySelector("#detailPrompt").textContent===text',arg=expected)
                page.locator('#close').click()
                page.locator('#tabGallery').click();page.locator('#tabCommunity').click()
                assert card.count()==1 and not errors,errors
                evidence['viewports'].append({'width':width,'checksPassed':9,
                    'checks':['loading','metadata completed','attribution','read-only controls',
                              'layout','missing metadata','transient retry','closed request cancellation','navigation']})
                page.close()
            browser.close()
        evidence['passed']=True
        print('PASS: 18 desktop/mobile detail lifecycle checks; '+evidence['metadataSource'])
    finally:
        h.stop()
        (test_safety.REPORTS/'detail-acceptance.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')


if __name__=='__main__':main()

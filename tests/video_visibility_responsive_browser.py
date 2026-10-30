"""Independent local browser probe of production video visibility adapter."""
from pathlib import Path
import sys, json, threading, re
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from functools import partial
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'), str(ROOT)]
import test_safety
from playwright.sync_api import sync_playwright

HTML = b'''<!doctype html><html><head><style>
body{margin:0;--header-h:64px;height:3000px}
body>header{position:fixed;top:0;height:64px;width:100%;background:#222;z-index:2}
.view-tabs{position:fixed;top:var(--header-h);height:48px;width:100%;background:#333;z-index:2}
.creator-card{position:absolute;top:132px;left:20px;width:300px}
.image-stage{position:relative;width:300px;height:400px;background:#111}
.image-button{display:block;height:100%;width:100%}.image-button img{width:100%;height:100%}
.creator-footer{height:140px;background:#777}.card-video{position:absolute;inset:0;width:100%;height:100%}
</style></head><body><header>Header</header><nav class="view-tabs">Navigation</nav>
<div class="creator-card" data-images-active="1"><div class="image-stage"><div class="image-button"><img></div></div><div class="creator-footer">Footer</div></div>
<script type="module">
import {attachCardVideo} from '/ui/video.js';
window.card=document.querySelector('.creator-card');
window.select=attachCardVideo(card,card.querySelector('img'));
select({id:'risk',type:'video',videoUrl:location.origin+'/media.mp4'});
const header=document.querySelector('body>header');
const measureHeader=()=>document.body.style.setProperty('--header-h',Math.ceil(header.getBoundingClientRect().height)+'px');
new ResizeObserver(measureHeader).observe(header);
window.ready=true;
</script></body></html>'''

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/risk':
            self.send_response(200); self.send_header('Content-Type','text/html'); self.end_headers(); self.wfile.write(HTML)
        elif self.path == '/media.mp4':
            content=(ROOT/'tests/fixtures/recovery.mp4').read_bytes()
            match=re.match(r'bytes=(\d+)-(\d*)', self.headers.get('Range',''))
            start=int(match[1]) if match else 0
            end=min(int(match[2]),len(content)-1) if match and match[2] else len(content)-1
            self.send_response(206 if match else 200); self.send_header('Content-Type','video/mp4'); self.send_header('Accept-Ranges','bytes'); self.send_header('Content-Length',str(end-start+1))
            if match:self.send_header('Content-Range',f'bytes {start}-{end}/{len(content)}')
            self.end_headers()
            try:self.wfile.write(content[start:end+1])
            except (BrokenPipeError, ConnectionResetError):pass
        else:super().do_GET()
    def log_message(self,*args):pass

server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT/'static')))
threading.Thread(target=server.serve_forever,daemon=True).start()
results=[]
try:
    with sync_playwright() as pw:
        for name in ('chromium','firefox'):
            browser=getattr(pw,name).launch(headless=True)
            for width in (390,768,1440):
                page=browser.new_page(viewport={'width':width,'height':900}); errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                def wait(condition):
                    try:page.wait_for_function(condition,timeout=7000)
                    except Exception:
                        print('PROBE FAILURE',name,width,condition,page.evaluate('({state:card.dataset.videoState,video:card.querySelector("video")&&{time:card.querySelector("video").currentTime,paused:card.querySelector("video").paused,seeking:card.querySelector("video").seeking,ready:card.querySelector("video").readyState,seekable:card.querySelector("video").seekable.length},nav:document.querySelector(".view-tabs").getBoundingClientRect().bottom,stage:card.querySelector(".image-stage").getBoundingClientRect().toJSON(),trace:window.visibilityTrace})'),errors,flush=True)
                        raise
                page.goto(f'http://127.0.0.1:{server.server_port}/risk')
                wait('window.ready && card.querySelector("video")?.currentTime>.1')
                # 49% of artwork visible while footer remains visible: no autoplay.
                page.evaluate('card.style.top=(112-204)+"px"')
                wait('!card.querySelector("video")')
                assert page.evaluate('card.getBoundingClientRect().bottom>112')
                # The app measures header height in a separate ResizeObserver.
                # At 51% visibility, growing the header leaves only 41% visible.
                page.evaluate('card.style.top=(112-196)+"px"')
                page.wait_for_timeout(700)
                wait('card.querySelector("video")?.currentTime>.1')
                page.evaluate('document.querySelector("header").style.height="104px"')
                wait('!card.querySelector("video")')
                page.evaluate('document.querySelector("header").style.height="64px"')
                page.wait_for_timeout(700)
                wait('card.querySelector("video")?.currentTime>.1')
                # Native resize crosses lower viewport threshold in both directions.
                page.evaluate('card.style.top="500px"')
                wait('card.querySelector("video")?.currentTime>.1')
                page.set_viewport_size({'width':width,'height':690})
                wait('!card.querySelector("video")')
                page.set_viewport_size({'width':width,'height':900})
                wait('card.querySelector("video")?.currentTime>.1')
                # Explicit user pause survives observer reconstruction on resize.
                page.locator('.video-toggle').click()
                assert page.evaluate('!card.querySelector("video") || card.querySelector("video").paused')
                page.set_viewport_size({'width':width,'height':910})
                page.wait_for_timeout(500)
                assert page.evaluate('!card.querySelector("video") || card.querySelector("video").paused')
                page.emulate_media(reduced_motion='reduce')
                wait('!card.querySelector("video")')
                page.locator('.video-toggle').click()
                wait('card.querySelector("video")?.currentTime>.1')
                # Removing a live card releases media and unregisters ownership safely.
                page.evaluate('card.releaseVideo();card.remove()')
                page.set_viewport_size({'width':width,'height':900})
                page.wait_for_timeout(350)
                assert not errors, errors
                assert page.locator('video').count()==0
                results.append({'browser':name,'width':width,'thresholds':True,'headerResize':True,'viewportResize':True,'pausePreserved':True,'reducedMotionManualPlay':True,'release':True})
                page.close()
            browser.close()
finally:
    server.shutdown();server.server_close()
output=test_safety.REPORTS/'video-visibility-responsive.json'
output.write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results))

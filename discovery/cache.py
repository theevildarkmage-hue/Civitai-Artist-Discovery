"""Persistent, bounded metadata and thumbnail caches. No OAuth credentials."""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

FRESH_SECONDS = 24 * 60 * 60
THUMBNAIL_LANE=threading.BoundedSemaphore(3)


class MetadataCache:
    def __init__(self, path, *, clock=time.time, freshness=FRESH_SECONDS):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.clock,self.freshness=clock,freshness
        self.lock=threading.Lock();self.pending=set()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS metadata_cache (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, verified_at REAL NOT NULL,
                attempt_at REAL, error TEXT, unavailable INTEGER NOT NULL DEFAULT 0)''')
            db.execute('CREATE TABLE IF NOT EXISTS metadata_failures(key TEXT PRIMARY KEY, attempt_at REAL, error TEXT)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def get(self,key):
        with self.connect() as db:row=db.execute('SELECT * FROM metadata_cache WHERE key=?',(key,)).fetchone()
        if not row:return None
        value=dict(row);value['value']=json.loads(value['value'])
        value['stale']=self.clock()-value['verified_at']>=self.freshness
        with self.lock:value['refreshing']=key in self.pending
        return value

    def put(self,key,value):
        now=self.clock()
        with self.connect() as db:
            db.execute('DELETE FROM metadata_failures WHERE key=?',(key,))
            db.execute('''INSERT INTO metadata_cache(key,value,verified_at,attempt_at) VALUES(?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,verified_at=excluded.verified_at,
                attempt_at=excluded.attempt_at,error=NULL,unavailable=0''',(key,json.dumps(value),now,now))

    def failed(self,key,error,*,unavailable=False):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO metadata_failures VALUES(?,?,?)',(key,self.clock(),error))
            db.execute('UPDATE metadata_cache SET attempt_at=?,error=?,unavailable=? WHERE key=?',
                (self.clock(),error,int(unavailable),key))

    def refresh(self,key,loader,*,background=False):
        with self.lock:
            if key in self.pending:return False
            prior=self.get_attempt(key)
            if background and prior and prior['error'] and self.clock()-(prior['attempt_at'] or 0)<60:return False
            self.pending.add(key)
        def work():
            try:self.put(key,loader())
            except Exception as error:
                code=getattr(error,'code','UNAVAILABLE')
                self.failed(key,code,unavailable=code in ('INVALID_ARTWORK','UNVERIFIED_OWNER'))
            finally:
                with self.lock:self.pending.discard(key)
        if background:threading.Thread(target=work,daemon=True,name='metadata-refresh').start()
        else:work()
        return True

    def get_attempt(self,key):
        with self.connect() as db:
            row=db.execute('SELECT error,attempt_at FROM metadata_cache WHERE key=?',(key,)).fetchone()
            if not row:row=db.execute('SELECT error,attempt_at FROM metadata_failures WHERE key=?',(key,)).fetchone()
            return dict(row) if row else None


class ArtworkCache:
    """Browse from cache; submissions always call the underlying verifier."""
    def __init__(self,store,verifier,namespace):
        self.store,self.verifier,self.namespace=store,verifier,namespace

    def key(self,image):return f'{self.namespace}:artwork:{image}'

    def remember(self,art):self.store.put(self.key(art.id),asdict(art))

    def browse(self,image,*,force=False):
        from .community import Artwork,CommunityError,FixtureVerifier
        key=self.key(image);entry=self.store.get(key)
        if isinstance(self.verifier,FixtureVerifier) and (image in self.verifier.artworks or not hasattr(self.verifier,'live_verifier')):
            if image not in self.verifier.artworks:raise CommunityError('INVALID_ARTWORK',404)
            art=self.verifier.artworks[image]
            if not entry or entry['value']!=json.loads(json.dumps(asdict(art))):self.remember(art)
            return art,{'state':'fixture','verifiedAt':(entry or {}).get('verified_at',self.store.clock())}
        def load():return asdict(self.verifier.artwork(image))
        if not entry:
            art=self.verifier.artwork(image);self.remember(art);return art,{'state':'current','verifiedAt':self.store.clock()}
        if entry['unavailable']:raise CommunityError('INVALID_ARTWORK',404)
        if entry['stale'] or force:self.store.refresh(key,load,background=True)
        value=entry['value'];value['tags']=tuple(value.get('tags',()));value['tag_ids']=tuple(value.get('tag_ids',()))
        return Artwork(**value),{'state':'stale' if entry['stale'] else 'cached',
            'verifiedAt':entry['verified_at'],'refreshing':entry['stale'] or force,'lastError':entry['error']}


def thumbnail_source(url,width=512):
    from urllib.parse import urlsplit
    from .history import preview_url
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or parsed.hostname!='image.civitai.com' or parsed.port
            or parsed.username or parsed.password):raise ValueError('Unapproved thumbnail source')
    resized=preview_url(url,width=width)
    if f'/width={width}/' not in resized:raise ValueError('Unsupported thumbnail transform')
    return resized


class ThumbnailCache:
    def __init__(self,root,fetch=None,*,allow_downscale=False):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.fetch=fetch or (lambda url:self._fetch(url,allow_downscale=allow_downscale))
        self.lock=threading.Lock();self.pending=set()
        self.first_locks={}

    @staticmethod
    def _fetch(url,*,allow_downscale=False):
        import urllib.request
        import urllib.error
        from urllib.parse import urlsplit
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args):return None
        # The CDN rejects non-browser clients (1010). Match the desktop gallery's
        # image request headers; send no cookies, credentials or account referer.
        request=urllib.request.Request(url,headers={'Accept':'image/avif,image/webp,image/png,image/*;q=0.8',
            'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Referer':'https://civitai.com/'})
        opener=urllib.request.build_opener(NoRedirect)
        redirected=False
        try:response=opener.open(request,timeout=6)
        except urllib.error.HTTPError as error:
            location=error.headers.get('Location','');parsed=urlsplit(location)
            if (error.code not in (301,302,307,308) or parsed.scheme!='https'
                    or parsed.hostname!='blobs-b2.civitai.com' or parsed.port or parsed.username or parsed.password
                    or not parsed.path.startswith('/file/blobs-managed-public/') or parsed.query):raise
            error.close();redirected=True
            response=opener.open(urllib.request.Request(location,headers={key:value for key,value in request.header_items()
                if key.casefold() not in ('host','content-length')}),timeout=6)
        with response:
            kind=response.headers.get_content_type()
            if redirected or allow_downscale:
                # Legacy CDN links sometimes resolve to originals. Read only a
                # bounded header first and accept images already thumbnail-sized.
                import io
                from PIL import Image
                head=response.read(65536)
                with Image.open(io.BytesIO(head)) as header:
                    width,height=header.size
                if allow_downscale and (width*height>16_000_000 or max(width,height)>8192):
                    raise ValueError('Source exceeds thumbnail decode limit')
                if not allow_downscale and (width>512 or height>1024 or width*height>524288):
                    raise ValueError('Legacy source is not thumbnail-sized')
                body=head+response.read(2_097_153-len(head))
            else:body=response.read(2_097_153)
        if kind not in ('image/jpeg','image/png','image/webp','image/avif') or not body or len(body)>2_097_152:
            raise ValueError('Invalid thumbnail response')
        if allow_downscale:
            import io
            from PIL import Image,ImageOps
            with Image.open(io.BytesIO(body)) as original:
                if original.width*original.height>16_000_000:raise ValueError('Source exceeds thumbnail decode limit')
                preview=ImageOps.exif_transpose(original)
                preview.thumbnail((512,1024),Image.Resampling.LANCZOS)
                output=io.BytesIO();preview.save(output,format='WEBP',quality=82)
            body,kind=output.getvalue(),'image/webp'
        return body,kind

    def get(self,url,width=512):
        source=thumbnail_source(url,width)
        key=hashlib.sha256(source.encode()).hexdigest()
        data=self.root/(key+'.image');meta=self.root/(key+'.json')
        def download():
            with THUMBNAIL_LANE:body,kind=self.fetch(source)
            temporary=data.with_suffix('.tmp');temporary.write_bytes(body);temporary.replace(data)
            metadata=meta.with_suffix('.tmp');metadata.write_text(json.dumps({'contentType':kind,'cachedAt':time.time()}));metadata.replace(meta)
            return body,kind
        if data.exists() and meta.exists():
            info=json.loads(meta.read_text())
            if time.time()-info['cachedAt']>=FRESH_SECONDS:
                with self.lock:
                    if key not in self.pending:
                        self.pending.add(key)
                        def refresh():
                            try:download()
                            except Exception:pass
                            finally:
                                with self.lock:self.pending.discard(key)
                        threading.Thread(target=refresh,daemon=True).start()
            return data.read_bytes(),info['contentType']
        # Serialize first fetches so simultaneous cards don't download twice.
        with self.lock:first_lock=self.first_locks.setdefault(key,threading.Lock())
        with first_lock:
            if data.exists() and meta.exists():return data.read_bytes(),json.loads(meta.read_text())['contentType']
            return download()

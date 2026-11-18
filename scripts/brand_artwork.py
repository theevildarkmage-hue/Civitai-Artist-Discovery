"""Editable vector reconstruction of the owner-approved Discovery logo.

Original geometric paths; no downloaded fonts, raster tracing or icon-library assets.
The SVGs and ICO raster share the same D, sweep and four-point star geometry.
"""
from pathlib import Path

D = [('M',44,6),('L',94,6),('C',128,6,145,34,134,64),
     ('C',125,91,104,106,75,106),('L',35,106),
     ('C',47,81,65,69,88,62),('C',105,57,116,50,114,42),
     ('C',112,30,101,26,81,26),('L',59,26),('L',48,59),
     ('L',56,58),('L',19,73),('Z',)]
SWEEP = [('M',0,106),('C',20,77,48,65,87,60),
         ('C',64,69,45,83,35,106),('Z',)]
STAR = [('M',90,34),('C',93,44,94,45,104,48),
        ('C',94,51,93,52,90,63),('C',87,52,86,51,76,48),
        ('C',86,45,87,44,90,34),('Z',)]
COUNTER = [('M',59,26),('L',81,26),('C',101,26,112,30,114,42),
           ('C',119,61,100,75,76,81),('L',42,91),('L',48,59),('Z',)]

CAPS = {
 'C':(33,'M32 0H12Q1 0 1 12V20Q1 32 13 32H32V24H14Q10 24 10 20V12Q10 8 14 8H28Z'),
 'I':(11,'M1 0H10V32H1Z'),
 'V':(39,'M0 0H10L20 23L30 0H40L24 32H15Z'),
 'T':(35,'M0 0H36L32 8H23V32H13V8H0Z'),
 'A':(43,'M0 32L18 0H28L46 32H35L23 10L11 32Z'),
}
THIN = {
 'D':(24,'M3 1H11C27 1 27 29 11 29H3Z'),
 'I':(8,'M4 1V29'),
 'S':(23,'M21 4C15 -2 3 0 3 7C3 15 21 14 21 22C21 31 7 32 2 26'),
 'C':(24,'M22 4C10 -4 2 2 2 15C2 28 12 35 22 26'),
 'O':(27,'M25 15C25 -4 2 -4 2 15C2 34 25 34 25 15Z'),
 'V':(25,'M1 1L12.5 29L24 1'),
 'E':(23,'M21 1H3V29H21M3 15H18'),
 'R':(25,'M3 29V1H13C27 1 27 15 13 15H3M13 15L25 29'),
 'Y':(25,'M1 1L12.5 16L24 1M12.5 16V29'),
}


def path(commands):
    return ' '.join(c[0]+' '.join(str(n) for n in c[1:]) for c in commands)


def definitions():
    return '''<defs><linearGradient id="discovery-D" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#35efd1"/><stop offset=".42" stop-color="#00cfbb"/><stop offset=".68" stop-color="#18bde5"/><stop offset="1" stop-color="#4075ff"/></linearGradient><linearGradient id="discovery-sweep" x1="0" y1="1" x2="0" y2="0"><stop stop-color="#00cce4"/><stop offset="1" stop-color="#55f8d5"/></linearGradient></defs>'''


def mark(*, counter=False):
    background=f'<path id="star-counter" fill="#0a0d13" d="{path(COUNTER)}"/>' if counter else ''
    return f'''<g id="D-with-star">{background}<path id="D-body" fill="url(#discovery-D)" d="{path(D)}"/><path id="discovery-sweep-path" fill="url(#discovery-sweep)" d="{path(SWEEP)}"/><path id="discovery-star" fill="#f8fbff" d="{path(STAR)}"/></g>'''


def caps(x,y,scale,ink):
    letters=[]
    for i,ch in enumerate('CIVITAI'):
        width,outline=CAPS[ch]
        extra='<path fill="#16d9d1" d="M18 32L24 21L30 32Z"/>' if ch=='A' else ''
        letters.append(f'<g id="brand-capital-{i}-{ch}" transform="translate({x} {y}) scale({scale}) skewX(-9)"><path fill="{ink}" d="{outline}"/>{extra}</g>')
        x+=(width+2.4)*scale
    return ''.join(letters),x


def thin(x,y,scale,ink,spacing=5):
    letters=[]
    for i,ch in enumerate('DISCOVERY'):
        width,outline=THIN[ch]
        extra=''
        if ch=='O':
            extra='<path fill="#18d9c5" stroke="none" d="M13.5 5Q15 12 20.5 15Q15 17 13.5 25Q12 18 6.5 15Q12 12 13.5 5Z"/>'
        letters.append(f'<g id="brand-discovery-{i}-{ch}" transform="translate({x} {y}) scale({scale}) skewX(-9)"><path fill="none" stroke="{ink}" stroke-width="2.2" stroke-linecap="square" stroke-linejoin="round" d="{outline}"/>{extra}</g>')
        x+=(width+spacing)*scale
    return ''.join(letters),x


def svg(kind, *, light=False):
    ink='#202735' if light else '#f8fbff'
    if kind=='icon':
        view='0 0 160 160';body='<g transform="translate(12 24)">'+mark(counter=True)+'</g>'
    elif kind=='mark':
        view='0 0 140 112';body=mark(counter=True)
    elif kind=='header':
        title,end=caps(160,24,1.65,ink)
        subtitle,end=thin(end+22,34,1.22,ink,5.6)
        view=f'0 0 {end+12:.3f} 112'
        body=mark()+title+subtitle
    elif kind=='primary':
        title,end=caps(160,17,1.65,ink)
        # Match the lower line to the strong top word rather than adding a tagline.
        subtitle,_=thin(160,80,(end-160)/258,ink,5)
        view=f'0 0 {end+12:.3f} 122';body=mark()+title+subtitle
    else:raise ValueError(kind)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" role="img" aria-labelledby="brand-title"><title id="brand-title">{'Civitai Discovery app icon' if kind=='icon' else 'Civitai Discovery'}</title><desc>Owner-approved D with a four-point discovery star and sweeping trail. Original editable geometric lettering and shapes.</desc>{definitions()}<g id="brand-artwork">{body}</g></svg>'''


def _points(commands):
    """Sample canonical vector curves for Pillow; no SVG runtime dependency."""
    points=[];current=(0,0);start=None
    for command in commands:
        op,*values=command
        if op in ('M','L'):
            current=tuple(values);points.append(current)
            if op=='M':start=current
        elif op=='C':
            x0,y0=current;x1,y1,x2,y2,x3,y3=values
            for i in range(1,49):
                t=i/48;u=1-t
                points.append((u**3*x0+3*u*u*t*x1+3*u*t*t*x2+t**3*x3,
                               u**3*y0+3*u*u*t*y1+3*u*t*t*y2+t**3*y3))
            current=(x3,y3)
        elif op=='Z':points.append(start)
    return points


def render_icon(size):
    from PIL import Image,ImageDraw
    scale=size*8/160;dimension=size*8
    image=Image.new('RGBA',(dimension,dimension))
    def polygon(commands):return [((x+12)*scale,(y+24)*scale) for x,y in _points(commands)]
    ImageDraw.Draw(image).polygon(polygon(COUNTER),fill='#0a0d13')
    def gradient(commands,stops,diagonal=False):
        mask=Image.new('L',image.size);ImageDraw.Draw(mask).polygon(polygon(commands),fill=255)
        layer=Image.new('RGBA',image.size);draw=ImageDraw.Draw(layer)
        ys=[point[1] for point in _points(commands)];top=min(ys)+24;span=max(ys)-min(ys)
        for y in range(dimension):
            value=max(0,min(1,(y/scale-top)/span))
            if diagonal:value=1-value
            for (a,c1),(b,c2) in zip(stops,stops[1:]):
                if a<=value<=b:
                    t=(value-a)/(b-a);color=tuple(round(v1*(1-t)+v2*t) for v1,v2 in zip(c1,c2));break
            draw.line((0,y,dimension,y),fill=(*color,255))
        image.paste(layer,(0,0),mask)
    gradient(D,[(0,(53,239,209)),(.42,(0,207,187)),(.68,(24,189,229)),(1,(64,117,255))])
    gradient(SWEEP,[(0,(0,204,228)),(1,(85,248,213))],diagonal=True)
    ImageDraw.Draw(image).polygon(polygon(STAR),fill='#f8fbff')
    return image.resize((size,size),Image.Resampling.LANCZOS)


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]/'static'
    for kind,name in [('header','discovery-logo'),('primary','discovery-logo-primary'),('mark','discovery-mark'),('icon','discovery-icon')]:
        (root/(name+'.svg')).write_text(svg(kind),encoding='utf-8')
        if kind in ('header','primary'):
            (root/(name+'-light.svg')).write_text(svg(kind,light=True),encoding='utf-8')
    print('Generated approved Discovery vector logo family; no profile data accessed.')

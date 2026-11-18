"""Approved vector assets and actual multiresolution Windows ICO output."""
import test_safety
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET
from PIL import Image

ROOT=test_safety.ROOT
NS='{http://www.w3.org/2000/svg}'


class BrandingAssets(unittest.TestCase):
    def test_owner_approved_transparent_header_is_exact_asset(self):
        import hashlib
        path=ROOT/'static/discovery-header.png'
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
            '987d15db1efa6d3bf37a0150d0f57a35cb782134672c2fea0c9115ffd9eee0ec')
        with Image.open(path) as image:
            self.assertEqual(image.mode,'RGBA')
            self.assertEqual(image.size,(2172,724))
            alpha=image.getchannel('A')
            self.assertEqual(alpha.getextrema(),(0,255))
            for point in ((0,0),(2171,0),(0,723),(2171,723)):
                self.assertEqual(alpha.getpixel(point),0)
            self.assertGreaterEqual(alpha.getpixel((580,275)),250)

    def test_vectors_are_self_contained_and_editable(self):
        files=sorted((ROOT/'static').glob('discovery-*.svg'))
        self.assertEqual(len(files),6)
        for file in files:
            with self.subTest(file=file.name):
                tree=ET.parse(file);svg=tree.getroot()
                self.assertIn('Civitai Discovery',svg.find(NS+'title').text)
                self.assertIsNotNone(svg.find('.//*[@id="discovery-star"]'))
                self.assertIsNotNone(svg.find('.//*[@id="D-body"]'))
                for tag in ('image','script','foreignObject','text'):
                    self.assertFalse(svg.findall('.//'+NS+tag))
                for element in svg.iter():
                    for key,value in element.attrib.items():
                        self.assertFalse(key.lower().startswith('on'))
                        if key.endswith('href'):self.assertTrue(value.startswith('#'))
                self.assertNotIn('Beta',file.read_text())
                self.assertNotIn('3.0.',file.read_text())

    def test_windows_icon_frames_keep_transparency_and_white_star(self):
        with tempfile.TemporaryDirectory(prefix='cad3-branding-') as temporary:
            output=Path(temporary)/'discovery.ico'
            subprocess.run([sys.executable,str(ROOT/'scripts/make_icon.py'),'discovery',str(output)],check=True,cwd=ROOT)
            with Image.open(output) as ico:
                self.assertEqual(ico.format,'ICO')
                sizes={16,20,24,32,40,48,64,96,128,256}
                self.assertEqual(ico.ico.sizes(),{(s,s) for s in sizes})
                for size in sizes:
                    frame=ico.ico.getimage((size,size)).convert('RGBA')
                    self.assertEqual(frame.size,(size,size))
                    for corner in ((0,0),(size-1,0),(0,size-1),(size-1,size-1)):
                        self.assertEqual(frame.getpixel(corner)[3],0)
                    x,y=round(size*102/160),round(size*72/160)
                    region=[frame.getpixel((px,py)) for px in range(max(0,x-1),min(size,x+2)) for py in range(max(0,y-1),min(size,y+2))]
                    self.assertTrue(any(min(pixel[:3])>180 and pixel[3]>180 for pixel in region),(size,region))
                    alpha=frame.getchannel('A')
                    self.assertIsNotNone(alpha.getbbox())
                    self.assertGreater(sum(a>128 for a in alpha.getdata()),size*size*.15)


if __name__=='__main__':unittest.main()

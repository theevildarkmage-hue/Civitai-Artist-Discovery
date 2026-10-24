# Third-party notices

The Windows build includes these unmodified Python libraries:

- **pystray 0.19.5** — GNU Lesser General Public License v3.0; source and project details:
  <https://pypi.org/project/pystray/0.19.5/>
- **Pillow 12.3.0** — HPND License; source and project details:
  <https://pypi.org/project/pillow/12.3.0/>

The release folder includes each library's complete license text under `licenses/`.
pystray provides the optional Windows notification-area icon. Pillow also supports
bounded local thumbnail resizing in Discovery 3.0 and is required on every platform.


The identity-only renewal candidate also includes **cryptography 50.0.2**
(Apache-2.0 OR BSD-3-Clause), **cffi 2.0.0** (MIT), and **pycparser 3.0** (BSD).
They implement per-install P-256 Discovery device proofs; no hand-written elliptic
curve implementation is used. See https://pypi.org/project/cryptography/50.0.2/,
https://pypi.org/project/cffi/2.0.0/ and https://pypi.org/project/pycparser/3.0/.
Their distributed license directories, including bundled-library notices, are
copied under licenses/ in the Windows candidate.

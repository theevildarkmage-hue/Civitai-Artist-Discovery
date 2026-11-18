"""Real local Community HTTP filtering with synthetic accounts and artwork only."""
import test_safety
import socket
from community_harness import Harness

with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0))
    port = probe.getsockname()[1]

h = Harness(port=port, sample=False)
try:
    h.start()
    for image in (1002, 1007, 1012):
        h.endpoint.request('/api/community/recommend', {'artwork': image})
    controls = {
        'hiddenTags': [{'id': 12, 'name': 'portrait', 'hidden': True}],
        'hiddenUsers': [{'id': 102, 'username': 'DemoArtist2', 'hidden': True}],
        'blockedUsers': [{'id': 103, 'username': 'DemoArtist3'}],
        'blockedByUsers': [], 'hiddenImages': [],
    }
    h.control('hidden', member=1, controls=controls)

    def images():
        return {row['artwork']['id'] for row in h.endpoint.request('/api/community/gallery')['items']}

    assert images() == set()
    h.endpoint.request('/api/settings', {'applyTagFilters': False})
    assert images() == {1002}
    h.endpoint.request('/api/settings', {'applyTagFilters': True, 'applyAccountFilters': False})
    assert images() == {1007}
    h.endpoint.request('/api/settings', {'applyTagFilters': False})
    assert images() == {1002, 1007, 1012}
    # Explicit artwork hides and rating selection still apply with both switches off.
    h.control('hidden', member=1, controls={**controls, 'hiddenImages': [1002]})
    assert images() == {1007, 1012}
    h.endpoint.request('/api/settings', {'applyImageFilters': False})
    assert images() == {1002, 1007, 1012}
    h.endpoint.request('/api/settings', {'browsingLevels': [1]})
    assert images() == set()
    h.endpoint.request('/api/settings', {'browsingLevels': [1, 2, 4, 8, 16],
                                         'applyTagFilters': True, 'applyAccountFilters': True, 'applyImageFilters': True})
    assert images() == set()
    # Unknown implicit sources stay excluded even with every local switch paused.
    h.control('hidden', member=1, controls={**controls, 'hiddenImagesImplicit': [1002,
        {'id': 1007, 'tagId': 999}, {'id': 1012, 'tagId': 12}]})
    h.endpoint.request('/api/settings', {'applyTagFilters': False, 'applyAccountFilters': False,
                                       'applyImageFilters': False})
    assert images() == {1012}
    assert len(h.snapshot()['recommendations']) == 3
    print('PASS: independent Community tag/account switches, direct hides and ratings preserved, ledger unchanged; synthetic transport only')
finally:
    h.stop()

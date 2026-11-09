"""User-facing source launch guidance, without changing service selection."""


DISMISS = ('<button type="button" class="source-launch-dismiss" data-source-notice-dismiss '
           'aria-label="Dismiss this notice" title="Dismiss">\u00d7</button>')


def pill(*, environment):
    """Always-visible Community selection beside the version, for packages and source."""
    if environment == 'production':
        return ('<span id="appChannel" class="app-channel app-channel-public" '
                'title="Public Community on the Production service">public</span>')
    return ('<span id="appChannel" class="app-channel app-channel-beta" '
            'title="Development version with a separate beta Community">beta</span>')


def render(*, frozen, environment):
    if frozen:
        return ''
    if environment == 'production':
        return (
            '<aside id="sourceLaunchNotice" class="source-launch-notice" data-environment="production" '
            'aria-labelledby="sourceLaunchTitle">' + DISMISS +
            '<strong id="sourceLaunchTitle">Public Community is selected</strong>'
            '<p>This source session uses the public Community gallery and a separate local profile. '
            'Use your Civitai account for shared recommendations.</p></aside>'
        )
    return (
        '<aside id="sourceLaunchNotice" class="source-launch-notice source-launch-development" '
        'data-environment="development" aria-labelledby="sourceLaunchTitle">' + DISMISS +
        '<strong id="sourceLaunchTitle">Public Community needs the stable launch</strong>'
        '<p>This session uses the development version and a separate Community gallery. '
        'To see the public gallery, close this app and run:</p>'
        '<code>uv run --with-requirements requirements.txt server.py --stable</code>'
        '<p class="source-launch-alternative">Already installed the requirements? Use '
        '<code>python server.py --stable</code>.</p>'
        '<p>You will sign in again in a separate profile. Your existing data will be kept.</p>'
        '</aside>'
    )

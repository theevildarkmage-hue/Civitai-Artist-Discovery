"""User-facing source launch guidance, without changing service selection."""


def render(*, frozen, environment):
    if frozen:
        return ''
    if environment == 'production':
        return (
            '<aside id="sourceLaunchNotice" class="source-launch-notice" aria-labelledby="sourceLaunchTitle">'
            '<strong id="sourceLaunchTitle">Public Community is selected</strong>'
            '<p>This source session uses the public Community gallery and a separate local profile. '
            'Use your Civitai account for shared recommendations.</p></aside>'
        )
    return (
        '<aside id="sourceLaunchNotice" class="source-launch-notice source-launch-development" '
        'aria-labelledby="sourceLaunchTitle">'
        '<strong id="sourceLaunchTitle">Public Community needs the stable launch</strong>'
        '<p>This session uses the development version and a separate Community gallery. '
        'To see the public gallery, close this app and run:</p>'
        '<code>uv run --with-requirements requirements.txt server.py --stable</code>'
        '<p class="source-launch-alternative">Already installed the requirements? Use '
        '<code>python server.py --stable</code>.</p>'
        '<p>You will sign in again in a separate profile. Your existing data will be kept.</p>'
        '</aside>'
    )

# Removable Community Test Lab

This directory is an unpackaged development extension. Ten **simulated** local
members map to real public uploader IDs and 40 verified still images; they do not
represent those creators' OAuth sessions. See [architecture, fixtures and exact
validation commands](../../docs/3.0-local-community-service.md).

`launch.py` is the single opt-in registration point around the normal development
server. `scripts/dev-launcher.py --test-lab-connection <external file>` selects it.
The external file is created by the owned native runner's `--manual-test-lab` option;
it contains a localhost manager capability and must never enter source control.
No production server, bundled static file or deployed Edge source imports this
directory. The normal development launch omits the extension entirely.

In the normal account, open My Profile > Development diagnostics > Community Test
Lab. Each row shows a public profile, simulated label, quota and four artwork links.
Open two members to use different local origins/profile databases simultaneously.
In each window use the existing Community Gallery and Recommend Art modal. Try a
member's own artwork (rejected), another member's artwork (eligible subject to quotas)
and the reciprocal submission. The inspector uses the actual HTTP verification
operation, including the SQL preview; it does not implement its own ownership rule.
Unknown artwork outside the 40-image snapshot is rejected rather than guessed.
Use Refresh member quotas after submissions. Close an isolated window to return
to your unchanged normal account. Social writes are unavailable in member windows.

Manual state is disposable: restarting the owned runner creates a fresh database.
Do not expect member browser caches to survive a new Lab instance. Short-lived
local credentials are renewed through the capability-protected manager; keys stay
in private process environments. All service/profile state is outside the checkout.

To disable, close member windows and use **Stop isolated Lab services**. The owned
runner then stops Deno/PostgREST/PostgreSQL and deletes its private connection file.
Close the normal development app through its existing control and relaunch with
`scripts/dev-3.0.ps1` without the Lab flag. This restores the regular diagnostics
module and normal Community operation without changing OAuth, settings or ledgers.

To remove permanently, disable first, remove this directory, and optionally remove
the `--test-lab-connection` option/dispatch from `scripts/dev-launcher.py`. No schema
migration or production authentication edit is required. The release exclusion
check works with this directory absent; `community_lab_exclusion` also accommodates
removal. The HTTP suite skips the optional Lab segment when the directory is absent.

`scripts/check-test-lab-exclusion.py` is wired into the release build and checks
production Python imports, bundled static assets, release inputs and deployed Edge
sources. Tests additionally verify frozen entry rejection before credential access.
This is a source/build-input gate, not a claim that a new executable was built here.

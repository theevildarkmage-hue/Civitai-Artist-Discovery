// Shared follow transaction for artwork cards and compact Profile creator rows.
// Read permissions at click/completion time, not when the component was constructed.
export async function toggleCreatorFollow(button, creator, { api, canWrite, accountEpoch, toast, onChange }) {
  if (!canWrite()) { toast('Civitai did not grant follow access.'); return; }
  if (button.dataset.followPending) return;
  button.dataset.followPending = 'true';
  button.disabled = true;
  const epoch = accountEpoch?.();
  try {
    const result = await api('/api/follow', { method: 'POST', body: JSON.stringify({
      userId: creator.userId, username: creator.username, following: !creator.following,
    }) });
    if (epoch !== accountEpoch?.()) return;
    creator.following = !!result.following;
    if (result.userId != null) creator.userId = result.userId;
    button.classList.toggle('is-following', creator.following);
    button.textContent = creator.following ? '✓ Following' : '+ Follow';
    toast(creator.following ? `Now following @${creator.username}` : `Unfollowed @${creator.username}`);
    for (const card of document.querySelectorAll('.creator-card')) {
      if (card.dataset.username === creator.username.toLowerCase()) {
        card.dispatchEvent(new CustomEvent('creatorfollow', {detail:{following:creator.following,userId:creator.userId}}));
      }
    }
    await onChange?.(creator);
  } catch (error) { if (epoch === accountEpoch?.()) toast(error.message); }
  finally { delete button.dataset.followPending; button.disabled = !canWrite(); }
}

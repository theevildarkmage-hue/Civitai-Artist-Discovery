// Native dialogs provide focus containment, Escape, and return focus. Share naming,
// close controls, and true backdrop clicks across pages without recreating those rules.
export function bindDialog(dialog, { labelledBy, closeButtons = [] }) {
  dialog.setAttribute('aria-labelledby', labelledBy);
  for (const button of closeButtons) button.onclick = () => dialog.close();
  const outside = event => {
    const bounds = dialog.getBoundingClientRect();
    return event.clientX < bounds.left || event.clientX > bounds.right ||
      event.clientY < bounds.top || event.clientY > bounds.bottom;
  };
  let beganOutside = false;
  dialog.addEventListener('pointerdown', event => {
    beganOutside = event.target === dialog && outside(event);
  });
  dialog.onclick = event => {
    if (beganOutside && event.target === dialog && outside(event)) dialog.close();
    beganOutside = false;
  };
}

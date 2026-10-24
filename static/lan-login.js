// The fragment is never sent in an HTTP URL or Referer. Remove it before pairing.
const key = new URLSearchParams(location.hash.slice(1)).get('pair');
history.replaceState(null, '', '/');
if (key) {
  fetch('/api/lan/pair', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({key})}).then(response => {
      if (!response.ok) throw new Error('Pairing failed. Get a new link from the computer running Discovery.');
      location.replace('/');
    }).catch(error => {document.getElementById('status').textContent=error.message;});
}

export async function resizeImage(file, maxSide = 1600, quality = 0.85) {
  let src, w, h, revoke = null;
  if (typeof createImageBitmap === 'function') {
    src = await createImageBitmap(file);
    w = src.width; h = src.height;
  } else {
    const url = URL.createObjectURL(file);
    revoke = url;
    src = await new Promise((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = url; });
    w = src.naturalWidth; h = src.naturalHeight;
  }
  const scale = Math.min(1, maxSide / Math.max(w, h));
  const cw = Math.max(1, Math.round(w * scale)), ch = Math.max(1, Math.round(h * scale));
  const canvas = document.createElement('canvas');
  canvas.width = cw; canvas.height = ch;
  const g = canvas.getContext('2d');
  g.fillStyle = '#fff'; g.fillRect(0, 0, cw, ch);
  g.drawImage(src, 0, 0, cw, ch);
  if (src.close) src.close();
  if (revoke) URL.revokeObjectURL(revoke);
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error('encode'))), 'image/jpeg', quality));
}

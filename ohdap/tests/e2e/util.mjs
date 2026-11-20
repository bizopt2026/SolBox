// Shared e2e helpers.
export async function pngBuffer(page, w, h) {
  const dataUrl = await page.evaluate(([w, h]) => {
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, w, h); g.fillStyle = '#000'; g.fillRect(10, 10, 50, 50);
    return c.toDataURL('image/png');
  }, [w, h]);
  return Buffer.from(dataUrl.split(',')[1], 'base64');
}

export function seedSettings(grade) {
  return `localStorage.setItem('__mockdb', JSON.stringify(Object.assign(JSON.parse(localStorage.getItem('__mockdb') || '{}'), { 'data/users/u1/settings': { type: 'settings', grade: '${grade}', onboarded: true, stars: 0 } })));`;
}

export async function pickPhoto(page, buf) {
  await page.locator('input[type=file][data-src=camera]').setInputFiles({ name: 'p.png', mimeType: 'image/png', buffer: buf });
}

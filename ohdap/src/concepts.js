const conceptKey = (name) => String(name || '').replace(/\s+/g, '').toLowerCase();

export function groupConcepts(notes) {
  const groups = new Map();
  for (const n of notes) {
    const cs = (n.result && n.result.concepts) || [];
    const seen = new Set();
    for (const c of cs) {
      const k = conceptKey(c.name);
      if (!k || seen.has(k)) continue;
      seen.add(k);
      if (!groups.has(k)) groups.set(k, { name: String(c.name).trim(), notes: [] });
      groups.get(k).notes.push(n);
    }
  }
  return [...groups.values()].sort((a, b) => b.notes.length - a.notes.length);
}

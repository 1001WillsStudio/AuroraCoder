/**
 * Quick smoke test for mergePanelFiles() — pure function extracted from
 * useFileTracking.js.  Run with:  node tests/test_mergePanelFiles.mjs
 */

function mergePanelFiles(prevFiles, apiFiles, closedFiles) {
  const existing = prevFiles.filter(f => !closedFiles.has(f.id))
  if (!apiFiles || apiFiles.length === 0) return existing

  const byPath = new Map(
    apiFiles.filter(f => !closedFiles.has(f.id)).map(f => [f.path, f])
  )
  const merged = existing.map(ef => {
    const af = byPath.get(ef.path)
    if (af) { byPath.delete(ef.path); return { ...af, id: ef.id } }
    return ef
  })
  for (const af of byPath.values()) merged.push(af)
  return merged
}

function assert(cond, msg) { if (!cond) throw new Error(`FAIL: ${msg}`); else console.log(`  ✓ ${msg}`) }

// ── Test 1: empty prev, new API files ──────────────────────────────
{
  const result = mergePanelFiles(
    [],
    [{ id: 'a.py', path: 'a.py', lines: [] }],
    new Set(),
  )
  assert(result.length === 1, 'empty prev → 1 file')
  assert(result[0].id === 'a.py', 'empty prev → correct id')
}

// ── Test 2: closed file excluded ───────────────────────────────────
{
  const result = mergePanelFiles(
    [],
    [{ id: 'a.py', path: 'a.py' }, { id: 'b.py', path: 'b.py' }],
    new Set(['a.py']),
  )
  assert(result.length === 1, 'closed file excluded → 1 file')
  assert(result[0].id === 'b.py', 'closed file excluded → remaining is b.py')
}

// ── Test 3: existing file merged (path match, ID preserved) ───────
{
  const result = mergePanelFiles(
    [{ id: 'view:a.py', path: 'a.py', lines: [{ type: 'added', content: 'old' }] }],
    [{ id: 'a.py', path: 'a.py', lines: [{ type: 'added', content: 'NEW' }] }],
    new Set(),
  )
  assert(result.length === 1, 'merge → still 1 file')
  assert(result[0].id === 'view:a.py', 'merge → preserves existing ID')
  assert(result[0].lines[0].content === 'NEW', 'merge → uses fresh diff data')
}

// ── Test 4: existing file not in API kept (no changes) ─────────────
{
  const result = mergePanelFiles(
    [{ id: 'a.py', path: 'a.py', lines: [] }],
    [{ id: 'b.py', path: 'b.py', lines: [] }],
    new Set(),
  )
  assert(result.length === 2, 'stale file kept → 2 files')
  assert(result.find(f => f.id === 'a.py'), 'stale file still present')
  assert(result.find(f => f.id === 'b.py'), 'new file added')
}

// ── Test 5: nil API files → keeps existing ─────────────────────────
{
  const result = mergePanelFiles(
    [{ id: 'a.py', path: 'a.py' }],
    null,
    new Set(),
  )
  assert(result.length === 1, 'null apiFiles → keeps existing')
}

// ── Test 6: API files empty array → keeps existing ─────────────────
{
  const result = mergePanelFiles(
    [{ id: 'a.py', path: 'a.py' }],
    [],
    new Set(),
  )
  assert(result.length === 1, 'empty apiFiles → keeps existing')
}

// ── Test 7: stale closedFiles in closure (the bug scenario) ────────
// User previously closed 'a.py'; new code result fires for ['a.py','b.py'];
// stale closedFiles still has 'a.py' → 'a.py' excluded, 'b.py' included.
{
  const result = mergePanelFiles(
    [],  // empty prev (code panel was closed)
    [{ id: 'a.py', path: 'a.py' }, { id: 'b.py', path: 'b.py' }],
    new Set(['a.py']),  // stale closedFiles
  )
  assert(result.length === 1, 'stale closed: → 1 file (a.py excluded)')
  assert(result[0].id === 'b.py', 'stale closed: → b.py present')
}

// ── Test 8: file tree view replaced by diff data ───────────────────
{
  const result = mergePanelFiles(
    [{ id: 'hello.py', path: 'hello.py', isViewOnly: true, lines: [{ type: null, content: 'plain' }] }],
    [{ id: 'hello.py', path: 'hello.py', hasChanges: true, lines: [{ type: 'added', content: 'diffed' }] }],
    new Set(),
  )
  assert(result.length === 1, 'view file replaced → 1 file')
  assert(result[0].hasChanges === true, 'view file replaced → hasChanges from diff')
  assert(result[0].lines[0].content === 'diffed', 'view file replaced → uses diff data')
  assert(result[0].isViewOnly === undefined, 'view file replaced → isViewOnly gone')
}

// ── Test 9: closedFiles empty (ref synced correctly) ───────────────
// After setClosedFiles(new Set()) takes effect, ref reads empty set.
{
  const result = mergePanelFiles(
    [{ id: 'a.py', path: 'a.py' }],
    [{ id: 'b.py', path: 'b.py' }],
    new Set(),  // ref is now empty
  )
  assert(result.length === 2, 'empty closedFiles → both files')
}
console.log('\nAll mergePanelFiles tests passed.')
